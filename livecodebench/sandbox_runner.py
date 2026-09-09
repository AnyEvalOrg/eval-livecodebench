"""Trusted supervisor source, executed ONLY in the external Linux sandbox.

A completed setup exec writes the request into a fresh directory. The supervisor
reads and unlinks it before starting the candidate. The setup Python process
generates the key locally; no ancestor shell receives it as stdin or an argument. The key is then memory-only.
PR_SET_DUMPABLE protects supervisor memory/fds from the same-UID candidate.
The child has separate stdio, no inherited supervisor descriptors, no core dumps,
no privilege gains, and a hard zero process limit (fork/clone/threads fail).
Only the supervisor can authenticate the wait() status. Provider stdout markers
can truncate/destroy the receipt, but cannot manufacture a valid passing receipt.
"""

# Setup runs before any candidate exists. Both execs are bounded by the scorer.
SETUP = r'''
import json, os, secrets, sys, tempfile
request = json.load(sys.stdin)
key = secrets.token_hex(32)
request["key"] = key
work = tempfile.mkdtemp(prefix="lcb-", dir="/tmp")
with open(os.path.join(work, "request.json"), "x", encoding="utf-8") as f:
    json.dump(request, f)
sys.stdout.write(json.dumps({"cwd": work, "key": key}))
'''

# Kept as source: importing this module never starts a process or executes code.
RUNNER = r'''
import base64
import ctypes
import hashlib
import hmac
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile

libc = ctypes.CDLL(None, use_errno=True)
if os.getuid() == 0 or libc.prctl(4, 0, 0, 0, 0) != 0:
    raise RuntimeError("nonroot Linux supervisor with protected memory required")
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
work = sys.argv[1]
request_path = os.path.join(work, "request.json")
with open(request_path, encoding="utf-8") as f:
    request = json.load(f)
os.unlink(request_path)
key = bytes.fromhex(request.pop("key"))
limit = request["output_limit"]
supervisor_pid = os.getpid()


def restrict_child():
    # Kill the child even if the supervisor is killed or its outer timeout fires.
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != supervisor_pid:
        os._exit(125)
    # Hard limits survive exec, and no_new_privs prevents setuid escape.
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        os._exit(125)
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


try:
    program = os.path.join(work, "solution.py")
    with open(program, "x", encoding="utf-8") as f:
        f.write(request["program"])
    # Anonymous files avoid pipe deadlocks and candidate-named receipt files.
    with tempfile.TemporaryFile(dir=work) as stdin, tempfile.TemporaryFile(dir=work) as stdout, tempfile.TemporaryFile(dir=work) as stderr:
        stdin.write(request["input"].encode("utf-8"))
        stdin.seek(0)
        child = subprocess.Popen(
            [sys.executable, "-I", program], cwd=work,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": work, "TMPDIR": work},
            stdin=stdin, stdout=stdout, stderr=stderr, close_fds=True,
            start_new_session=True, preexec_fn=restrict_child,
        )
        timed_out = False
        try:
            returncode = child.wait(timeout=request["timeout"])
        except subprocess.TimeoutExpired:
            timed_out = True
            child.kill()
            returncode = child.wait()
        finally:
            # NPROC=0 prevents descendants; also clean the original process group.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        stdout.seek(0)
        output = stdout.read(limit + 1)
        overflow = len(output) >= limit or os.fstat(stderr.fileno()).st_size >= limit
        body = json.dumps({
            "returncode": returncode, "timeout": timed_out, "overflow": overflow,
            "output": base64.b64encode(output).decode("ascii"), "cwd": work,
        }, separators=(",", ":"))
        tag = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
        sys.stdout.write(json.dumps({"body": body, "tag": tag}))
finally:
    shutil.rmtree(work, ignore_errors=True)
'''
