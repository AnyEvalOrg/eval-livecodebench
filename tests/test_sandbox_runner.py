"""Execute only authored synthetic fixtures, never model code or dataset material.

On macOS, prctl, credentials and group signaling are stubbed to exercise mechanics. Linux kernel isolation
is separately tested only on Linux; the macOS run is not a containment attestation.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from livecodebench.sandbox_runner import CANDIDATE_UID, CANDIDATE_GID, CLEANUP_COMMAND, RUNNER, SETUP
from livecodebench.scoring import verify_receipt


def prepare(program, timeout=1):
    request = dict(program=program, input='', timeout=timeout, output_limit=4096)
    setup = subprocess.run([sys.executable, '-I', '-c', SETUP],
                           input=json.dumps(request), capture_output=True, text=True, timeout=5)
    assert setup.returncode == 0, 'Authored fixture setup failed'
    return json.loads(setup.stdout)


def run_authored_fixture(program, timeout=1):
    if sys.platform == 'linux':
        require_linux_sandbox()
    setup = prepare(program, timeout)
    source = RUNNER
    if sys.platform != 'linux':
        # No containment claims from this stub: only wait/status/transport/cleanup.
        source = source.replace('libc = ctypes.CDLL(None, use_errno=True)',
                                'libc = type("PrctlStub", (), {"prctl": lambda *args: 0})()')
        source = source.replace('os.getuid() != 0', 'False')
        source = source.replace('os.setgroups([])', 'pass')
        source = source.replace('os.setresgid(CANDIDATE_GID, CANDIDATE_GID, CANDIDATE_GID)', 'pass')
        source = source.replace('os.setresuid(CANDIDATE_UID, CANDIDATE_UID, CANDIDATE_UID)', 'pass')
        source = source.replace('os.chown(candidate_work, CANDIDATE_UID, CANDIDATE_GID)', 'pass')
        # The macOS host sandbox does not permit Linux-style group signaling.
        # Exercise child wait/timeout here; Linux regressions use real killpg.
        source = source.replace('os.killpg(pgid, sig)', 'os.kill(pgid, sig)')
    try:
        result = subprocess.run([sys.executable, '-I', '-c', source, setup['cwd']],
                                capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, 'Authored supervisor fixture failed'
        receipt = verify_receipt(result.stdout, bytes.fromhex(setup['key']))
        assert receipt is not None
        assert not Path(setup['cwd']).exists()
        return receipt
    finally:
        if sys.platform == 'linux':
            subprocess.run(CLEANUP_COMMAND, capture_output=True, timeout=6)
        shutil.rmtree(setup['cwd'], ignore_errors=True)


def test_setup_makes_atomic_distinct_directories_and_exclusive_request_files():
    prepared = [prepare('pass') for _ in range(3)]
    try:
        assert len({p['cwd'] for p in prepared}) == 3
        assert len({p['key'] for p in prepared}) == 3
        for p in prepared:
            path = Path(p['cwd'])
            assert path.is_dir() and path.name.startswith('lcb-')
            assert path.stat().st_mode & 0o777 == 0o700
            assert (path / 'request.json').is_file()
    finally:
        for p in prepared:
            shutil.rmtree(p['cwd'])


def test_real_supervisor_preserves_nonzero_exit_despite_forged_marker():
    receipt = run_authored_fixture("print('2<completed-sentinel-value-0>', flush=True)\nraise SystemExit(7)")
    assert receipt['returncode'] == 7
    assert receipt['output'] == '2<completed-sentinel-value-0>\n'


@pytest.mark.parametrize('poison', ['os.mkfifo(__file__)', 'os.mkdir(__file__)'])
def test_poisoned_solution_path_cannot_affect_next_test(poison):
    first = run_authored_fixture('import os\nos.unlink(__file__)\n' + poison + "\nprint('2')")
    second = run_authored_fixture("print('2')")
    assert first['returncode'] == second['returncode'] == 0
    assert first['cwd'] != second['cwd']
    assert first['output'] == second['output'] == '2\n'


def test_supervisor_timeout_kills_candidate():
    receipt = run_authored_fixture('while True: pass', timeout=0.1)
    assert receipt['timeout'] is True
    assert receipt['returncode'] != 0


def test_request_is_unlinked_before_candidate_runs():
    receipt = run_authored_fixture("import os\nassert not os.path.exists('../request.json')\nprint('2')")
    assert receipt['returncode'] == 0
    assert receipt['output'] == '2\n'


@pytest.mark.skipif(sys.platform != 'linux', reason='requires real Linux prctl and process limits')
def test_linux_candidate_cannot_fork_or_read_supervisor_memory():
    receipt = run_authored_fixture('''import os
try:
    os.fork()
except OSError:
    pass
else:
    raise SystemExit(1)
try:
    open(f"/proc/{os.getppid()}/mem", "rb")
except PermissionError:
    pass
else:
    raise SystemExit(2)
print('2')
''')
    assert receipt['returncode'] == 0 and receipt['output'] == '2\n'


def test_wrapper_drops_all_ids_before_exec_and_kills_group():
    import ast
    tree = ast.parse(RUNNER)
    restrict = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'restrict_child')
    calls = [ast.unparse(node.value) for node in restrict.body if isinstance(node, ast.Expr)]
    groups = calls.index('os.setgroups([])')
    gid = calls.index('os.setresgid(CANDIDATE_GID, CANDIDATE_GID, CANDIDATE_GID)')
    uid = calls.index('os.setresuid(CANDIDATE_UID, CANDIDATE_UID, CANDIDATE_UID)')
    nproc = calls.index('resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))')
    assert groups < gid < uid < nproc
    popen = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and ast.unparse(node.func) == 'subprocess.Popen')
    kwargs = {kw.arg: ast.unparse(kw.value) for kw in popen.keywords}
    assert kwargs['preexec_fn'] == 'restrict_child'
    assert kwargs['start_new_session'] == 'True'
    assert kwargs['close_fds'] == 'True'
    assert 'os.getuid() != 0' in RUNNER
    assert 'CANDIDATE_UID = 65532' in RUNNER
    assert 'CANDIDATE_GID = 65532' in RUNNER
    assert CANDIDATE_UID == CANDIDATE_GID == 65532
    assert 'libc.prctl(38, 1, 0, 0, 0)' in RUNNER
    assert 'libc.prctl(8, 0, 0, 0, 0)' in RUNNER
    assert 'for sig in (signal.SIGTERM, signal.SIGKILL)' in RUNNER
    assert 'os.killpg(pgid, sig)' in RUNNER
    assert 'finally:\n            kill_group(child.pid)' in RUNNER
    assert CLEANUP_COMMAND[-4:] == ['/usr/bin/pkill', '-KILL', '-u', str(CANDIDATE_UID)]


def require_linux_sandbox():
    if os.geteuid() != 0 or os.environ.get('LCB_LINUX_CONTAINMENT') != '1':
        pytest.skip('requires root in a disposable Linux sandbox with LCB_LINUX_CONTAINMENT=1 and reserved UID 65532')
    # Never sweep an unrelated user's processes on a developer machine.
    existing = subprocess.run(['/usr/bin/pgrep', '-u', str(CANDIDATE_UID)],
                              capture_output=True, timeout=5)
    assert existing.returncode in (0, 1)
    assert not any(process_running(int(pid)) for pid in existing.stdout.split()), 'candidate UID must be unused before the regression'


@pytest.mark.skipif(sys.platform != 'linux', reason='requires Linux UID isolation, prctl, /proc and independent procps cleanup')
def test_linux_supervisor_death_cannot_leave_candidate_running(monkeypatch):
    import asyncio
    import signal
    import time
    from types import SimpleNamespace
    from inspect_ai.scorer import INCORRECT, Target
    from inspect_ai.util import ExecResult
    from inspect_ai.util._sandbox.events import SandboxEnvironmentProxy
    import livecodebench.scoring as scoring

    require_linux_sandbox()
    # Authored adversarial fixture: clear PDEATHSIG, try to kill the supervisor,
    # ignore TERM, report kernel credentials, then spin beyond the test deadline.
    program = """import ctypes, json, os, signal
assert ctypes.CDLL(None).prctl(1, 0, 0, 0, 0) == 0
try:
    os.kill(os.getppid(), signal.SIGKILL)
except PermissionError:
    blocked = True
else:
    blocked = False
signal.signal(signal.SIGTERM, signal.SIG_IGN)
status = dict(line.split(':', 1) for line in open('/proc/self/status'))
with open('ready.tmp', 'w') as f:
    json.dump(dict(pid=os.getpid(), blocked=blocked, uid=os.getresuid(),
                   gid=os.getresgid(), groups=os.getgroups(), status=status), f)
os.rename('ready.tmp', 'ready')
while True: pass
"""
    test = {'testtype': 'stdin', 'input': '', 'output': ''}
    fixture = dict(question_id='fixture', public_test_cases=json.dumps([test]),
                   private_test_cases='[]', metadata='{}')
    monkeypatch.setattr(scoring, 'load_records', lambda: [fixture])

    class LinuxSandbox:
        def __init__(self):
            self.calls = []
            self.work = None
            self.pid = None
            self.supervisor = None
            self.checked_survivor = False

        async def exec(self, cmd, input=None, **kwargs):
            self.calls.append(cmd)
            if RUNNER in cmd:
                self.supervisor = subprocess.Popen(cmd, cwd=kwargs['cwd'],
                                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                   text=True)
                # timeout is the parent of RUNNER; the candidate records its own PID.
                ready = Path(self.work) / 'candidate' / 'ready'
                until = time.monotonic() + 3
                while not ready.exists() and time.monotonic() < until:
                    await asyncio.sleep(0.01)
                assert ready.exists(), 'Linux candidate failed to start'
                info = json.loads(ready.read_text())
                self.pid = info['pid']
                assert info['blocked'] is True
                assert info['uid'] == [CANDIDATE_UID] * 3
                assert info['gid'] == [CANDIDATE_GID] * 3
                assert info['groups'] == []
                for cap in ('CapEff', 'CapPrm', 'CapAmb'):
                    assert int(info['status'][cap], 16) == 0
                assert int(info['status']['NoNewPrivs']) == 1
                # The trusted test driver kills the supervisor externally, covering
                # failure even though the candidate's own kill was denied.
                parent = int(info['status']['PPid'])
                os.kill(parent, signal.SIGKILL)
                self.supervisor.communicate(timeout=3)
                assert process_running(self.pid), 'fixture must survive parent death to exercise the sweep'
                self.checked_survivor = True
                return ExecResult(success=False, returncode=137, stdout='', stderr='')
            completed = subprocess.run(cmd, input=input, capture_output=True, text=True,
                                       cwd=kwargs['cwd'], timeout=6)
            if SETUP in cmd:
                self.work = json.loads(completed.stdout)['cwd']
            return ExecResult(success=completed.returncode == 0, returncode=completed.returncode,
                              stdout=completed.stdout, stderr=completed.stderr)

    environment = LinuxSandbox()
    monkeypatch.setattr(scoring, 'sandbox', lambda: SandboxEnvironmentProxy(environment))
    state = SimpleNamespace(sample_id='fixture', output=SimpleNamespace(
        completion='```python\n' + program + '\n```'))
    try:
        score = asyncio.run(scoring.livecodebench_scorer(4)(state, Target('')))
        # The scorer deliberately sanitizes provider errors, including assertions
        # raised inside exec; require every pre-sweep assertion to have completed.
        assert environment.checked_survivor
        assert score.value == INCORRECT
        assert score.explanation == 'Test 1: supervisor did not complete'
        assert environment.calls[-1] == CLEANUP_COMMAND
        until = time.monotonic() + 2
        while process_running(environment.pid) and time.monotonic() < until:
            time.sleep(0.01)
        assert not process_running(environment.pid)
        # Reuse the sandbox only after the sweep: fresh test still works.
        receipt = run_authored_fixture("print('2')")
        assert receipt['returncode'] == 0
    finally:
        subprocess.run(CLEANUP_COMMAND, capture_output=True, timeout=6)
        if environment.supervisor and environment.supervisor.poll() is None:
            environment.supervisor.kill()
            environment.supervisor.wait(timeout=3)
        if environment.work:
            shutil.rmtree(environment.work, ignore_errors=True)


def process_running(pid):
    if pid is None:
        return False
    try:
        # An orphan zombie is already terminated; PID 1 is responsible for reaping.
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except FileNotFoundError:
        return False


@pytest.mark.skipif(sys.platform != 'linux', reason='requires real Linux UID isolation and process-group signal enforcement')
def test_linux_supervisor_deadline_kills_candidate_ignoring_term():
    receipt = run_authored_fixture("""import ctypes, os, signal
ctypes.CDLL(None).prctl(1, 0, 0, 0, 0)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
print(os.getpid(), flush=True)
while True: pass
""", timeout=0.5)
    assert receipt['timeout'] is True
    assert receipt['returncode'] == -9
    assert not process_running(int(receipt['output']))
