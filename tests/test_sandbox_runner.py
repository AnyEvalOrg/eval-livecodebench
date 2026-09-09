"""Execute only authored synthetic fixtures, never model code or dataset material.

On macOS, prctl is stubbed to exercise supervisor mechanics. Linux kernel isolation
is separately tested only on Linux; the macOS run is not a containment attestation.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from livecodebench.sandbox_runner import RUNNER, SETUP
from livecodebench.scoring import verify_receipt


def prepare(program, timeout=1):
    request = dict(program=program, input='', timeout=timeout, output_limit=4096)
    setup = subprocess.run([sys.executable, '-I', '-c', SETUP],
                           input=json.dumps(request), capture_output=True, text=True, timeout=5)
    assert setup.returncode == 0, 'Authored fixture setup failed'
    return json.loads(setup.stdout)


def run_authored_fixture(program, timeout=1):
    setup = prepare(program, timeout)
    source = RUNNER
    if sys.platform != 'linux':
        # No containment claims from this stub: only wait/status/transport/cleanup.
        source = source.replace('libc = ctypes.CDLL(None, use_errno=True)',
                                'libc = type("PrctlStub", (), {"prctl": lambda *args: 0})()')
    try:
        result = subprocess.run([sys.executable, '-I', '-c', source, setup['cwd']],
                                capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, 'Authored supervisor fixture failed'
        receipt = verify_receipt(result.stdout, bytes.fromhex(setup['key']))
        assert receipt is not None
        assert not Path(setup['cwd']).exists()
        return receipt
    finally:
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
    receipt = run_authored_fixture("import os\nassert not os.path.exists('request.json')\nprint('2')")
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
