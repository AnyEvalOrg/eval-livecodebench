import asyncio
import json
from types import SimpleNamespace

import pytest
from inspect_ai.scorer import CORRECT, INCORRECT, Target
from inspect_ai.util import ExecResult, OutputLimitExceededError

import livecodebench.scoring as scoring


class FakeSandbox:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []
        self.writes = []

    async def write_file(self, path, contents):
        self.writes.append((path, contents))

    async def exec(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


def state(kind, completion="```python\n# synthetic candidate\npass\n```", expected="2"):
    test = {"testtype": kind, "input": "1", "output": expected}
    return SimpleNamespace(
        output=SimpleNamespace(completion=completion),
        metadata={
            "public_test_cases": json.dumps([test]),
            "private_test_cases": json.dumps([test]),
            "func_name": "solve" if kind == "functional" else None,
        },
    )


def result(stdout="2", returncode=0):
    return ExecResult(success=returncode == 0, returncode=returncode, stdout=stdout, stderr="")


@pytest.mark.parametrize("kind", ["stdin", "functional"])
@pytest.mark.parametrize("outcome,reason", [("correct", "All 2"), ("wrong", "wrong answer"), ("timeout", "timeout"), ("runtime", "runtime error")])
def test_scorer_results_with_fake_sandbox(monkeypatch, kind, outcome, reason):
    response = {
        "correct": result(), "wrong": result("3"),
        "timeout": TimeoutError(), "runtime": result(returncode=1),
    }[outcome]
    fake = FakeSandbox([response, response])
    monkeypatch.setattr(scoring, "sandbox", lambda: fake)
    score = asyncio.run(scoring.livecodebench_scorer()(state(kind), Target("")))
    assert score.value == (CORRECT if outcome == "correct" else INCORRECT)
    assert reason in score.explanation
    assert len(fake.calls) == (2 if outcome == "correct" else 1)
    assert len(fake.writes) == len(fake.calls)
    for cmd, kwargs in fake.calls:
        assert cmd == ["python3", "solution.py"]
        assert kwargs == {"cwd": "/tmp", "input": "1", "timeout": 6, "timeout_retry": False}
    for path, contents in fake.writes:
        assert path == "/tmp/solution.py"
        assert "private_test_cases" not in contents
        assert "public_test_cases" not in contents


@pytest.mark.parametrize("kind", ["stdin", "functional"])
def test_private_failure_prevents_pass(monkeypatch, kind):
    fake = FakeSandbox([result(), result("9")])
    monkeypatch.setattr(scoring, "sandbox", lambda: fake)
    score = asyncio.run(scoring.livecodebench_scorer()(state(kind), Target("")))
    assert score.value == INCORRECT
    assert score.explanation == "Test 2: wrong answer."


def test_last_closed_python_block_is_used():
    completion = "```python\nwrong\n```\nThen:\n```python\nright\n```\n```text\nignored\n```"
    assert scoring.extract_code(completion) == "right\n"
    assert scoring.extract_code("```python\nunterminated") is None
    assert scoring.extract_code("```\nplain\n```") is None
    assert scoring.extract_code("```python\n \n```") is None
    assert scoring.extract_code("```python\r\nright\r\n```\r\n") == "right\r\n"


def test_missing_code_never_starts_sandbox(monkeypatch):
    def forbidden():
        raise AssertionError("No sandbox should be requested")
    monkeypatch.setattr(scoring, "sandbox", forbidden)
    score = asyncio.run(scoring.livecodebench_scorer()(state("stdin", completion="no code"), Target("")))
    assert score.value == INCORRECT


@pytest.mark.parametrize("prediction,expected,match", [
    ("  1 2\n\n", "1 2", True),
    ("1.0  2e0", "1 2", True),
    ("50000000000000000", "50000000000000001", False),
    ("1.00000001", "1", False),
    ("a  b", "a b", False),
    ("1\n\n2", "1\n2", False),
    ("1 2", "1\n2", False),
    ("NaN", "nan", False),
    ("", "  \n", True),
])
def test_exact_upstream_stdio_rules(prediction, expected, match):
    assert scoring.stdio_matches(prediction, expected) is match


@pytest.mark.parametrize("prediction,expected,match", [
    ("(1, 2)", "[1, 2]", True),
    ("[(1, 2)]", "[[1, 2]]", False),
    ("{'a': 1}", '{"a": 1}', True),
    ("True", "true", True),
    ("None", "null", True),
    ("1.00000001", "1", False),
    ("__import__('os').getcwd()", '"anything"', False),
    ("debug\n2", "2", False),
])
def test_exact_upstream_functional_rules(prediction, expected, match):
    assert scoring.functional_matches(prediction, expected) is match


@pytest.mark.parametrize("kind", ["stdin", "functional"])
def test_custom_timeout_and_no_expected_output_in_sandbox(monkeypatch, kind):
    fake = FakeSandbox([TimeoutError()])
    monkeypatch.setattr(scoring, "sandbox", lambda: fake)
    score = asyncio.run(scoring.livecodebench_scorer(12)(state(kind, expected='"EXPECTED_SECRET_SENTINEL"'), Target("")))
    assert score.value == INCORRECT
    assert fake.calls[0][1]["timeout"] == 12
    assert all("EXPECTED_SECRET_SENTINEL" not in text for _, text in fake.writes)
    assert "EXPECTED_SECRET_SENTINEL" not in score.explanation


def test_empty_test_suite_is_an_error(monkeypatch):
    task_state = state("stdin")
    task_state.metadata.update(public_test_cases="[]", private_test_cases="[]")
    with pytest.raises(ValueError, match="no tests"):
        asyncio.run(scoring.livecodebench_scorer()(task_state, Target("")))


def test_infrastructure_errors_propagate(monkeypatch):
    fake = FakeSandbox([ConnectionError("cluster unavailable")])
    monkeypatch.setattr(scoring, "sandbox", lambda: fake)
    with pytest.raises(ConnectionError):
        asyncio.run(scoring.livecodebench_scorer()(state("stdin"), Target("")))


@pytest.mark.parametrize("kind", ["stdin", "functional"])
def test_output_limit_is_incorrect(monkeypatch, kind):
    fake = FakeSandbox([OutputLimitExceededError("fixture limit", None)])
    monkeypatch.setattr(scoring, "sandbox", lambda: fake)
    score = asyncio.run(scoring.livecodebench_scorer()(state(kind), Target("")))
    assert score.value == INCORRECT
    assert "output limit" in score.explanation
