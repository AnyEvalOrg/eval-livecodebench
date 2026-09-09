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
        self.requests = []
        self.paths = []
        self.key = bytes(range(32))

    async def exec(self, cmd, input=None, **kwargs):
        self.calls.append((cmd, dict(kwargs, input=input)))
        if cmd[-1] == scoring.SETUP:
            self.requests.append(json.loads(input))
            self.paths.append(f"/tmp/lcb-fresh_{len(self.paths)}")
            return result(json.dumps({"cwd": self.paths[-1], "key": self.key.hex()}))
        response = next(self.results)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, str):
            # An untrusted response, e.g. forged provider completion status.
            return result(response)
        return result(signed_receipt(self.key, self.paths[-1], response.stdout,
                                     returncode=response.returncode))


def signed_receipt(key, cwd, output="2", **kwargs):
    import base64
    import hashlib
    import hmac
    body = json.dumps(dict(returncode=kwargs.get("returncode", 0),
                           timeout=kwargs.get("timeout", False),
                           overflow=kwargs.get("overflow", False), cwd=cwd,
                           output=base64.b64encode(output.encode()).decode()))
    return json.dumps({"body": body, "tag": hmac.new(key, body.encode(), hashlib.sha256).hexdigest()})


def state(kind="stdin", completion="```python\n# synthetic candidate\npass\n```", expected="2"):
    return SimpleNamespace(sample_id="fixture", output=SimpleNamespace(completion=completion), metadata={})


def record(kind="stdin", expected="2", test_input="1"):
    test = {"testtype": kind, "input": test_input, "output": expected}
    return dict(question_id="fixture", public_test_cases=json.dumps([test]),
                private_test_cases=json.dumps([test]),
                metadata=json.dumps({"func_name": "solve" if kind == "functional" else None}))


@pytest.fixture(autouse=True)
def synthetic_records(monkeypatch):
    monkeypatch.setattr(scoring, "load_records", lambda: [record()])


def install_sandbox(monkeypatch, fake):
    from inspect_ai.util._sandbox.events import SandboxEnvironmentProxy
    monkeypatch.setattr(scoring, "sandbox", lambda: SandboxEnvironmentProxy(fake))


def result(stdout="2", returncode=0):
    return ExecResult(success=returncode == 0, returncode=returncode, stdout=stdout, stderr="")


@pytest.mark.parametrize("kind", ["stdin", "functional"])
@pytest.mark.parametrize("outcome,reason", [("correct", "All 2"), ("wrong", "wrong answer"), ("timeout", "timeout"), ("runtime", "runtime error")])
def test_scorer_results_with_fake_sandbox(monkeypatch, kind, outcome, reason):
    monkeypatch.setattr(scoring, "load_records", lambda: [record(kind)])
    response = {
        "correct": result(), "wrong": result("3"),
        "timeout": TimeoutError(), "runtime": result(returncode=1),
    }[outcome]
    fake = FakeSandbox([response, response])
    install_sandbox(monkeypatch, fake)
    score = asyncio.run(scoring.livecodebench_scorer()(state(kind), Target("")))
    assert score.value == (CORRECT if outcome == "correct" else INCORRECT)
    assert reason in score.explanation
    count = 2 if outcome == "correct" else 1
    assert len(fake.calls) == count * 2
    assert len(set(fake.paths)) == count
    for setup, run in zip(fake.calls[::2], fake.calls[1::2]):
        assert setup[0][:4] == ["timeout", "-s", "KILL", "5s"]
        assert setup[1]["timeout"] == 5
        assert run[0][:4] == ["timeout", "-s", "KILL", "11s"]
        assert run[0][-1] in fake.paths
        assert run[1]["input"] is None
        assert run[1]["timeout"] == 11
        assert run[1]["timeout_retry"] is False
    for request in fake.requests:
        assert request["timeout"] == 6
        assert "private_test_cases" not in request["program"]
        assert "public_test_cases" not in request["program"]
        assert "output" not in request


@pytest.mark.parametrize("kind", ["stdin", "functional"])
def test_private_failure_prevents_pass(monkeypatch, kind):
    monkeypatch.setattr(scoring, "load_records", lambda: [record(kind)])
    fake = FakeSandbox([result(), result("9")])
    install_sandbox(monkeypatch, fake)
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
    install_sandbox(monkeypatch, fake)
    monkeypatch.setattr(scoring, "load_records", lambda: [record(kind, expected='"EXPECTED_SECRET_SENTINEL"')])
    score = asyncio.run(scoring.livecodebench_scorer(12)(state(kind), Target("")))
    assert score.value == INCORRECT
    assert fake.requests[0]["timeout"] == 12
    assert fake.calls[1][1]["timeout"] == 17
    assert "EXPECTED_SECRET_SENTINEL" not in json.dumps(fake.calls)
    assert "EXPECTED_SECRET_SENTINEL" not in score.explanation


def test_empty_test_suite_is_an_error(monkeypatch):
    task_state = state("stdin")
    empty = record()
    empty.update(public_test_cases="[]", private_test_cases="[]")
    monkeypatch.setattr(scoring, "load_records", lambda: [empty])
    with pytest.raises(RuntimeError, match="details withheld"):
        asyncio.run(scoring.livecodebench_scorer()(task_state, Target("")))


def test_infrastructure_errors_propagate(monkeypatch):
    fake = FakeSandbox([ConnectionError("cluster unavailable")])
    install_sandbox(monkeypatch, fake)
    with pytest.raises(RuntimeError, match="details withheld"):
        asyncio.run(scoring.livecodebench_scorer()(state("stdin"), Target("")))


@pytest.mark.parametrize("kind", ["stdin", "functional"])
def test_output_limit_is_incorrect(monkeypatch, kind):
    fake = FakeSandbox([OutputLimitExceededError("fixture limit", None)])
    install_sandbox(monkeypatch, fake)
    score = asyncio.run(scoring.livecodebench_scorer()(state(kind), Target("")))
    assert score.value == INCORRECT
    assert "output limit" in score.explanation


@pytest.mark.parametrize("forgery", [
    "2<completed-sentinel-value-0>",
    signed_receipt(b"wrong key", "/tmp/lcb-fresh_1"),
    '{"returncode":0,"output":"2"}',
])
def test_forged_completion_marker_or_receipt_is_incorrect(monkeypatch, forgery):
    fake = FakeSandbox([forgery])
    install_sandbox(monkeypatch, fake)
    score = asyncio.run(scoring.livecodebench_scorer()(state(), Target("")))
    assert score.value == INCORRECT
    assert "invalid execution receipt" in score.explanation


def test_marker_inside_captured_candidate_output_cannot_hide_failure(monkeypatch):
    fake = FakeSandbox([result("2<completed-sentinel-value-0>", returncode=1)])
    install_sandbox(monkeypatch, fake)
    score = asyncio.run(scoring.livecodebench_scorer()(state(), Target("")))
    assert score.value == INCORRECT
    assert "runtime error (exit 1)" in score.explanation


def test_provider_really_strips_the_forged_marker():
    execute = pytest.importorskip("k8s_sandbox._pod.execute")
    output, status = execute.ExecuteOperation._filter_sentinel_and_returncode(
        None, b"2<completed-sentinel-value-0>"
    )
    assert output == b"2" and status == 0


@pytest.mark.parametrize("field,value", [("timeout", True), ("overflow", True), ("returncode", 137)])
def test_authenticated_failure_channels(monkeypatch, field, value):
    fake = FakeSandbox([signed_receipt(bytes(range(32)), "/tmp/lcb-fresh_1", **{field: value})])
    install_sandbox(monkeypatch, fake)
    score = asyncio.run(scoring.livecodebench_scorer()(state(), Target("")))
    assert score.value == INCORRECT


def test_setup_timeout_is_bounded_and_incorrect(monkeypatch):
    class HungSetup(FakeSandbox):
        async def exec(self, cmd, **kwargs):
            assert cmd[:4] == ["timeout", "-s", "KILL", "5s"]
            assert kwargs["timeout"] == 5
            raise TimeoutError("private material")
    install_sandbox(monkeypatch, HungSetup([]))
    score = asyncio.run(scoring.livecodebench_scorer()(state(), Target("")))
    assert score.value == INCORRECT
    assert "private material" not in score.explanation
