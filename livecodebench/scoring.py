"""All-tests pass@1; only the external sandbox runs candidate programs."""
from __future__ import annotations

import ast
import json
import re
from decimal import Decimal, InvalidOperation

from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, scorer
from inspect_ai.solver import TaskState
from inspect_ai.util import OutputLimitExceededError, sandbox

from .dataset import decode_tests
from .execution import functional_program, stdin_program

# grade_stdio/get_stripped_lines/convert_line_to_decimals and grade_call_based,
# https://github.com/LiveCodeBench/LiveCodeBench/blob/
# 28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24/lcb_runner/evaluation/testing_util.py


def extract_code(completion: str) -> str | None:
    blocks = re.findall(r"^```python[^\S\n]*\r?\n(.*?)^```[^\S\n]*\r?$", completion, re.M | re.S)
    return blocks[-1] if blocks and blocks[-1].strip() else None


def stdio_matches(prediction: str, expected: str) -> bool:
    predicted_lines = [line.strip() for line in prediction.strip().split("\n")]
    expected_lines = [line.strip() for line in expected.strip().split("\n")]
    if len(predicted_lines) != len(expected_lines):
        return False
    for predicted, actual in zip(predicted_lines, expected_lines):
        if predicted == actual:
            continue
        try:
            if [Decimal(x) for x in predicted.split()] == [Decimal(x) for x in actual.split()]:
                continue
        except (InvalidOperation, ValueError):
            pass
        return False
    return True


def functional_matches(prediction: str, expected: str) -> bool:
    try:
        value = ast.literal_eval(prediction.strip())
        if isinstance(value, tuple):
            value = list(value)  # Upstream normalizes only the top-level tuple.
        return value == json.loads(expected)
    except (ValueError, SyntaxError, TypeError, RecursionError):
        return False


@scorer(metrics=[accuracy()])
def livecodebench_scorer(per_test_timeout: int = 6):
    """A timeout, runtime error, missing code, or any wrong answer fails a problem."""
    if per_test_timeout <= 0:
        raise ValueError("per_test_timeout must be positive")

    async def score(state: TaskState, target: Target) -> Score:
        code = extract_code(state.output.completion)
        if code is None:
            return Score(value=INCORRECT, explanation="No nonempty, closed python fenced block.")
        metadata = state.metadata
        tests = decode_tests(metadata["public_test_cases"]) + decode_tests(metadata["private_test_cases"])
        if not tests:
            raise ValueError("Packaged problem has no tests")
        fn_name = metadata.get("func_name")
        env = sandbox()
        for index, test in enumerate(tests, 1):
            functional = test["testtype"] == "functional"
            if functional and not fn_name:
                raise ValueError("Functional problem is missing func_name")
            program = functional_program(code, fn_name) if functional else stdin_program(code)
            # Rewrite for every test: candidate modifications cannot replace the next
            # solution. No expected output, test suite or grading code is written here.
            await env.write_file("/tmp/solution.py", program)
            try:
                result = await env.exec(
                    ["python3", "solution.py"],
                    cwd="/tmp",
                    input=test["input"],
                    timeout=per_test_timeout,
                    timeout_retry=False,
                )
            except TimeoutError:
                return Score(value=INCORRECT, explanation=f"Test {index}: timeout ({per_test_timeout}s).")
            except OutputLimitExceededError:
                return Score(value=INCORRECT, explanation=f"Test {index}: output limit exceeded.")
            if not result.success:
                # Do not echo stderr: it can contain private inputs or candidate echoes.
                return Score(value=INCORRECT, explanation=f"Test {index}: runtime error (exit {result.returncode}).")
            matches = functional_matches if functional else stdio_matches
            if not matches(result.stdout, test["output"]):
                return Score(value=INCORRECT, explanation=f"Test {index}: wrong answer.")
        return Score(value=CORRECT, explanation=f"All {len(tests)} tests passed.")

    return score
