"""Run only these authored synthetic fixtures in a child Python, never model code.

The scorer itself is covered using a fake sandbox, without Docker or Kubernetes.
"""
import subprocess
import sys

import pytest

from livecodebench.execution import functional_program, stdin_program
from livecodebench.scoring import functional_matches


def run_fixture(tmp_path, source, stdin):
    path = tmp_path / "solution.py"
    path.write_text(source)
    return subprocess.run([sys.executable, "-I", str(path)], input=stdin, text=True, capture_output=True, timeout=5)


@pytest.mark.parametrize("code", [
    "class Solution:\n    def solve(self, a: List[int], b: int):\n        print('ignored debugging')\n        return (sum(a), b)",
    "def solve(a: List[int], b: int):\n    return (sum(a), b)",
])
def test_call_harness_class_or_module_and_json_arguments(tmp_path, code):
    result = run_fixture(tmp_path, functional_program(code, "solve"), "[1, 2]\n4")
    assert result.returncode == 0
    assert functional_matches(result.stdout, "[3,4]")


def test_nested_tuple_is_not_silently_converted(tmp_path):
    code = "def solve(a):\n    return [(a, a)]"
    result = run_fixture(tmp_path, functional_program(code, "solve"), "2")
    assert result.returncode == 0
    assert not functional_matches(result.stdout, "[[2,2]]")


def test_real_stdin_future_import_main_and_upstream_prelude(tmp_path):
    code = "from __future__ import annotations\nif __name__ == '__main__':\n    print(gcd(*map(int, sys.stdin.buffer.read().split())))"
    result = run_fixture(tmp_path, stdin_program(code), "6 9")
    assert result.returncode == 0
    assert result.stdout == "3\n"


def test_functional_exception_becomes_process_failure(tmp_path):
    result = run_fixture(tmp_path, functional_program("def solve(a):\n    raise ValueError('fixture')", "solve"), "1")
    assert result.returncode != 0
