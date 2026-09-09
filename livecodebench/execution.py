"""Construct sandbox-only Python programs. Never execute candidate code here.

Adapted from lcb_runner/evaluation/testing_util.py (import_string, compile_code,
grade_call_based), LiveCodeBench commit 28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24.
MIT upstream notice is in NOTICE.md.
"""
from __future__ import annotations

# Kept byte-for-byte equal to the upstream import_string, including its recursion limit.
IMPORT_PRELUDE = 'from string import *\nfrom re import *\nfrom datetime import *\nfrom collections import *\nfrom heapq import *\nfrom bisect import *\nfrom copy import *\nfrom math import *\nfrom random import *\nfrom statistics import *\nfrom itertools import *\nfrom functools import *\nfrom operator import *\nfrom io import *\nfrom sys import *\nfrom json import *\nfrom builtins import *\nfrom typing import *\nimport string\nimport re\nimport datetime\nimport collections\nimport heapq\nimport bisect\nimport copy\nimport math\nimport random\nimport statistics\nimport itertools\nimport functools\nimport operator\nimport io\nimport sys\nimport json\nsys.setrecursionlimit(50000)\n'


def stdin_program(code: str) -> str:
    # Run actual stdin/stdout in a fresh process as required by this package.
    # exec inside the sandbox preserves candidate __future__ imports and __main__.
    return IMPORT_PRELUDE + "\nexec(compile(" + repr(code) + ", 'candidate.py', 'exec'))\n"


def functional_program(code: str, function_name: str) -> str:
    # Everything below is source text written as solution.py inside the sandbox.
    # Keep harness names separate from the candidate module's import-star globals.
    # Only current inputs enter the sandbox; expected outputs remain on the host.
    return f'''import contextlib as _contextlib
import io as _io
import json as _json
import sys as _sys
import types as _types

_args = [_json.loads(line) for line in _sys.stdin.read().split("\\n")]
_module = _types.ModuleType("tmp_sol", "")
with _contextlib.redirect_stdout(_io.StringIO()):
    exec({IMPORT_PRELUDE!r}, _module.__dict__)
    exec(compile({code!r}, "candidate.py", "exec"), _module.__dict__)
    _solution = _module.Solution() if "class Solution" in {code!r} else _module
    _prediction = getattr(_solution, {function_name!r})(*_args)
# A literal preserves nested tuple/list distinctions that JSON would erase.
# The trusted scorer uses ast.literal_eval, never eval, on this output.
_sys.stdout.write(repr(_prediction))
'''
