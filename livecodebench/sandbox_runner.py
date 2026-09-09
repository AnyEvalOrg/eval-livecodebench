"""Trusted supervisor source, executed ONLY in the external Linux sandbox.

A completed setup exec writes the request into a fresh directory. The supervisor
reads and unlinks it before starting the candidate. The setup Python process
generates the key locally; no ancestor shell receives it as stdin or an argument. The key is then memory-only.
The root supervisor drops the child to reserved UID/GID 65532 before exec.
PR_SET_DUMPABLE also protects supervisor memory/fds.
The child has separate stdio, no inherited supervisor descriptors, no core dumps,
no privilege gains, and a hard zero process limit (fork/clone/threads fail).
Only the supervisor can authenticate the wait() status. Provider stdout markers
can truncate/destroy the receipt, but cannot manufacture a valid passing receipt.
"""

CANDIDATE_UID = 65532
CANDIDATE_GID = 65532
# procps pkill returns 1 when there are no matching processes.
CLEANUP_COMMAND = ["timeout", "-s", "KILL", "5s",
                   "/usr/bin/pkill", "-KILL", "-u", str(CANDIDATE_UID)]

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
import time

CANDIDATE_UID = 65532
CANDIDATE_GID = 65532
libc = ctypes.CDLL(None, use_errno=True)
if os.getuid() != 0 or libc.prctl(4, 0, 0, 0, 0) != 0:
    raise RuntimeError("root Linux supervisor with protected memory required")
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
work = sys.argv[1]
request_path = os.path.join(work, "request.json")
with open(request_path, encoding="utf-8") as f:
    request = json.load(f)
os.unlink(request_path)
key = bytes.fromhex(request.pop("key"))
limit = request["output_limit"]


def restrict_child():
    # No parent-death signal is trusted: the scorer independently kills this UID.
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        os._exit(125)
    if libc.prctl(8, 0, 0, 0, 0) != 0:  # PR_SET_KEEPCAPS = 0
        os._exit(125)
    os.setgroups([])
    os.setresgid(CANDIDATE_GID, CANDIDATE_GID, CANDIDATE_GID)
    os.setresuid(CANDIDATE_UID, CANDIDATE_UID, CANDIDATE_UID)
    # Set NPROC AFTER changing UID, avoiding execve's PF_NPROC_EXCEEDED trap.
    # These hard limits and the irreversible credential drop survive exec.
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def kill_group(pgid):
    # Kill the entire original session's process group, even if the leader exited.
    # NPROC=0 prevents fork/clone/threads and the session leader cannot setsid again.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            time.sleep(0.1)


try:
    # Keep the launch/request directory root-owned; only its child is writable.
    candidate_work = os.path.join(work, "candidate")
    os.mkdir(candidate_work, 0o700)
    os.chown(candidate_work, CANDIDATE_UID, CANDIDATE_GID)
    os.chmod(work, 0o711)
    program = os.path.join(candidate_work, "solution.py")
    with open(program, "x", encoding="utf-8") as f:
        f.write(request["program"])
    os.chmod(program, 0o644)
    # Anonymous files avoid pipe deadlocks and candidate-named receipt files.
    with tempfile.TemporaryFile(dir=work) as stdin, tempfile.TemporaryFile(dir=work) as stdout, tempfile.TemporaryFile(dir=work) as stderr:
        stdin.write(request["input"].encode("utf-8"))
        stdin.seek(0)
        child = subprocess.Popen(
            [sys.executable, "-I", program], cwd=candidate_work,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": candidate_work, "TMPDIR": candidate_work},
            stdin=stdin, stdout=stdout, stderr=stderr, close_fds=True,
            start_new_session=True, preexec_fn=restrict_child,
        )
        timed_out = False
        try:
            returncode = child.wait(timeout=request["timeout"])
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            kill_group(child.pid)
            returncode = child.wait()
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
