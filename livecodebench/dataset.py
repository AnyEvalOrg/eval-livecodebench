"""Read immutable package data; evaluation never contacts Hugging Face."""
from __future__ import annotations

import base64
import gzip
import io
import json
import pickle
import zlib
from importlib.resources import files
from typing import Iterator


def manifest() -> dict:
    return json.loads(files("livecodebench").joinpath("data/manifest.json").read_text(encoding="utf-8"))


def load_records() -> Iterator[dict]:
    with files("livecodebench").joinpath("data/problems.jsonl.gz").open("rb") as raw:
        with gzip.open(raw, "rt", encoding="utf-8") as stream:
            for line in stream:
                yield json.loads(line)


class _DataOnlyUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str):
        raise pickle.UnpicklingError("Executable pickle globals are forbidden")

    def persistent_load(self, pid):
        raise pickle.UnpicklingError("Persistent pickle references are forbidden")


def decode_tests(encoded: str) -> list[dict]:
    """Port CodeGenerationProblem.__post_init__ without executable pickle globals.

    LiveCodeBench/LiveCodeBench, lcb_runner/benchmarks/code_generation.py,
    commit 28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24: JSON, or base64 -> zlib
    -> pickled JSON string -> JSON. The on-disk private string stays unchanged.
    """
    try:
        result = json.loads(encoded)
    except json.JSONDecodeError:
        payload = zlib.decompress(base64.b64decode(encoded, validate=True))
        decoded = _DataOnlyUnpickler(io.BytesIO(payload)).load()
        if not isinstance(decoded, (str, bytes)):
            raise ValueError("Private test payload must be a JSON string")
        result = json.loads(decoded)
    if not isinstance(result, list) or any(
        not isinstance(t, dict)
        or t.get("testtype") not in {"stdin", "functional"}
        or not isinstance(t.get("input"), str)
        or not isinstance(t.get("output"), str)
        for t in result
    ):
        raise ValueError("Invalid packaged test data")
    return result
