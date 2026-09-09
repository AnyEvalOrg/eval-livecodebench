"""All-tests pass@1; only the external sandbox runs candidate programs."""
from __future__ import annotations

import ast
import asyncio
import base64
import hashlib
import hmac
import json
import re
from decimal import Decimal, InvalidOperation

from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, scorer
from inspect_ai.solver import TaskState
from inspect_ai.util import OutputLimitExceededError, sandbox

from .dataset import decode_tests, load_records
from .publication import private_grading
from .sandbox_runner import CLEANUP_COMMAND, RUNNER, SETUP
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

    # Closure data is not a scorer argument (Inspect logs scorer arguments), sample
    # metadata, target, or store. Only the selected record is decoded when scoring.
    records = {record["question_id"]: record for record in load_records()}

    async def private_score(state: TaskState, target: Target) -> Score:
        code = extract_code(state.output.completion)
        if code is None:
            return Score(value=INCORRECT, explanation="No nonempty, closed python fenced block.")
        record = records[str(state.sample_id)]
        tests = decode_tests(record["public_test_cases"]) + decode_tests(record["private_test_cases"])
        if not tests:
            raise ValueError("Packaged problem has no tests")
        fn_name = json.loads(record["metadata"]).get("func_name")
        env = sandbox()
        for index, test in enumerate(tests, 1):
            functional = test["testtype"] == "functional"
            if functional and not fn_name:
                raise ValueError("Functional problem is missing func_name")
            program = functional_program(code, fn_name) if functional else stdin_program(code)
            request = json.dumps({
                "program": program, "input": test["input"],
                "timeout": per_test_timeout, "output_limit": 1024 * 1024,
            })
            # Both setup and execution have container and host deadlines.
            # The setup process generates the key; no ancestor shell receives it.
            # The extra five seconds permit startup and authenticated receipt cleanup.
            deadline = per_test_timeout + 5
            receipt = None
            cleanup_after = 0
            try:
                with private_grading(env) as private:
                    try:
                        async with asyncio.timeout(10):
                            setup = await private.exec(
                                ["timeout", "-s", "KILL", "5s",
                                 "/usr/local/bin/python3", "-I", "-c", SETUP],
                                cwd="/", input=request, timeout=5, timeout_retry=False,
                            )
                        setup_receipt = json.loads(setup.stdout)
                        work = setup_receipt["cwd"]
                        key = bytes.fromhex(setup_receipt["key"])
                        if len(key) != 32:
                            raise RuntimeError("Invalid setup key")
                        if not re.fullmatch(r"/tmp/lcb-[a-zA-Z0-9_-]+", work):
                            raise RuntimeError("Invalid setup directory")
                        # If exec returns early without a receipt, wait through
                        # the outer deadline before sweeping: the supervisor may
                        # still be starting. This uses the host monotonic clock.
                        cleanup_after = asyncio.get_running_loop().time() + deadline + 5
                        try:
                            async with asyncio.timeout(deadline + 5):
                                result = await private.exec(
                                    ["timeout", "-s", "KILL", f"{deadline}s",
                                     "/usr/local/bin/python3", "-I", "-c", RUNNER, work],
                                    cwd="/", timeout=deadline, timeout_retry=False,
                                )
                            receipt = verify_receipt(result.stdout, key)
                            if receipt is not None and receipt["cwd"] == work:
                                # Authenticated completion means no later spawn;
                                # sweep immediately before starting the next test.
                                cleanup_after = 0
                            else:
                                receipt = None
                        except Exception:
                            # No authenticated supervisor report is a failed test,
                            # including a killed supervisor or lost exec response.
                            receipt = None
                    finally:
                        # A separate sandbox exec, never the candidate's parent or
                        # session, enforces cleanup on EVERY path (also setup failure).
                        cleanup = asyncio.create_task(cleanup_candidate(private, cleanup_after))
                        try:
                            await asyncio.shield(cleanup)
                        except asyncio.CancelledError:
                            await cleanup
                            raise
            except TimeoutError:
                return Score(value=INCORRECT, explanation=f"Test {index}: supervisor did not complete")
            except OutputLimitExceededError:
                return Score(value=INCORRECT, explanation=f"Test {index}: supervisor did not complete")
            except Exception:
                # Provider exceptions may embed stdin or captured output. Do not
                # allow them (or their exception chain) into an Inspect error event.
                raise RuntimeError("Private sandbox operation failed; details withheld.") from None
            # Neither success nor returncode from the run provider is a verdict channel.
            if receipt is None:
                return Score(value=INCORRECT, explanation=f"Test {index}: supervisor did not complete")
            if receipt["timeout"]:
                return Score(value=INCORRECT, explanation=f"Test {index}: timeout ({per_test_timeout}s).")
            if receipt["overflow"]:
                return Score(value=INCORRECT, explanation=f"Test {index}: output limit exceeded.")
            if receipt["returncode"] != 0:
                return Score(value=INCORRECT, explanation=f"Test {index}: runtime error (exit {receipt['returncode']}).")
            matches = functional_matches if functional else stdio_matches
            if not matches(receipt["output"], test["output"]):
                return Score(value=INCORRECT, explanation=f"Test {index}: wrong answer.")
        return Score(value=CORRECT, explanation=f"All {len(tests)} tests passed.")

    async def score(state: TaskState, target: Target) -> Score:
        # Raise outside the private frame and except block: even Inspect's optional
        # traceback-locals display must not render records, requests, keys or output.
        try:
            return await private_score(state, target)
        except Exception:
            pass
        raise RuntimeError("Private scoring failed; details withheld.") from None

    return score


async def cleanup_candidate(environment, not_before: float = 0) -> None:
    """Trusted, independent UID sweep; never proceed if cleanup itself fails."""
    try:
        delay = not_before - asyncio.get_running_loop().time()
        if delay > 0:
            await asyncio.sleep(delay)
        async with asyncio.timeout(10):
            cleanup = await environment.exec(
                list(CLEANUP_COMMAND), cwd="/", timeout=5, timeout_retry=False,
            )
        if cleanup.returncode not in (0, 1):
            raise RuntimeError("UID cleanup failed")
    except Exception:
        # In particular do not turn a cleanup timeout into a candidate verdict.
        raise RuntimeError("Private sandbox cleanup failed; details withheld.") from None


def verify_receipt(stdout: str, key: bytes) -> dict | None:
    """Authenticate exact wrapper bytes before interpreting status or output."""
    try:
        envelope = json.loads(stdout)
        body, tag = envelope["body"], envelope["tag"]
        if not hmac.compare_digest(hmac.new(key, body.encode(), hashlib.sha256).hexdigest(), tag):
            return None
        receipt = json.loads(body)
        if (type(receipt["returncode"]) is not int
                or type(receipt["timeout"]) is not bool
                or type(receipt["overflow"]) is not bool
                or not re.fullmatch(r"/tmp/lcb-[a-zA-Z0-9_-]+", receipt["cwd"])):
            return None
        receipt["output"] = base64.b64decode(receipt["output"], validate=True).decode("utf-8")
        return receipt
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        return None
