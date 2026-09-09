import base64
import gzip
import hashlib
import json
import pickle
import zlib
from importlib.resources import files

import pytest

from livecodebench.dataset import decode_tests, load_records, manifest
from livecodebench.prompts import user_prompt
from livecodebench.task import load_dataset, record_to_sample

COUNT = 144
IDS_HASH = "64a04b1a6dfa6dd99a72ab0a46746a1a509692f15d4df99b17de67d2562bbaed"
ARTIFACT_HASH = "d123666099d6b9b400857bbe8c152fd28d4b1ccacfd0743bcc0ecd063026ebb6"


def test_package_data_exact_count_ids_and_integrity():
    data = files("livecodebench").joinpath("data/problems.jsonl.gz").read_bytes()
    assert len(data) == 62_487_410
    assert hashlib.sha256(data).hexdigest() == ARTIFACT_HASH
    samples = load_dataset()
    assert len(samples) == COUNT
    ids = [s.id for s in samples]
    assert len(set(ids)) == COUNT
    assert hashlib.sha256(("\n".join(ids) + "\n").encode()).hexdigest() == IDS_HASH
    assert ids == manifest()["question_ids"]
    records = [json.loads(line) for line in gzip.decompress(data).splitlines()]
    assert ids == [r["question_id"] for r in records]
    assert samples.name == "livecodebench-code_generation_lite-release_v6-20250125-20250406"


def test_inputs_derive_only_from_statement_and_starter():
    """Statements contain upstream examples; structured test fields must never leak."""
    for record in load_records():
        sample = record_to_sample(record)
        if sample.input != user_prompt(record["question_content"], record["starter_code"] or ""):
            pytest.fail("Input differs from the statement/starter-only template")
        if sample.target:
            pytest.fail("A sample unexpectedly exposes an answer target")
        # Taint every field other than the two input sources and the exact id.
        tainted = dict(record)
        for key in tainted.keys() - {"question_content", "starter_code", "question_id"}:
            tainted[key] = "METADATA_SECRET_SENTINEL"
        tainted["metadata"] = json.dumps({"func_name": "METADATA_SECRET_SENTINEL"})
        if record_to_sample(tainted).input != sample.input:
            pytest.fail("Metadata changed the model input")
        assert "METADATA_SECRET_SENTINEL" not in record_to_sample(tainted).input
        assert sample.metadata["private_test_cases"] == record["private_test_cases"]
        assert sample.metadata["public_test_cases"] == record["public_test_cases"]


def test_all_packaged_tests_decode_and_have_consistent_kinds():
    public = private = 0
    kinds = {"stdin": 0, "functional": 0}
    for record in load_records():
        public_tests = decode_tests(record["public_test_cases"])
        private_tests = decode_tests(record["private_test_cases"])
        assert public_tests and private_tests
        kind = "functional" if json.loads(record["metadata"]).get("func_name") else "stdin"
        if any(test["testtype"] != kind for test in public_tests + private_tests):
            pytest.fail("Test kind does not match problem metadata")
        kinds[kind] += 1
        public += len(public_tests)
        private += len(private_tests)
    assert kinds == {"stdin": 92, "functional": 52}
    assert (public, private) == (382, 5324)


def test_private_decoder_rejects_executable_pickle():
    payload = base64.b64encode(zlib.compress(pickle.dumps(eval))).decode()
    with pytest.raises(pickle.UnpicklingError, match="globals"):
        decode_tests(payload)
