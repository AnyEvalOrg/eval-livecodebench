"""Build the frozen, newest date window; no upstream dataset code is executed.

Run with the AnyEval venv's Python. Downloads stay in the ignored .build directory;
no Hugging Face cache or other files outside this repository are written.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import io
import json
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
REVISION = "0fe84c3912ea0c4d4a78037083943e8f0c4dd505"
RELEASE = "release_v6"
SHARD = "test6.jsonl"
BUDGET = 64_000_000


def encode(row: dict) -> bytes:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def compress(data: bytes) -> bytes:
    # Avoid timestamp, filename and platform-dependent OS byte in the gzip header.
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", filename="", mtime=0, compresslevel=9) as gz:
        gz.write(data)
    return out.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Require the local compressed download")
    args = parser.parse_args()
    cache = ROOT / ".build/latest.jsonl.gz"
    if not cache.exists():
        if args.offline:
            raise SystemExit("No local compressed download")
        cache.parent.mkdir(exist_ok=True)
        partial = cache.with_suffix(".partial")
        url = f"https://huggingface.co/datasets/livecodebench/code_generation_lite/resolve/{REVISION}/{SHARD}"
        with urlopen(url, timeout=120) as response, partial.open("wb") as out:
            with gzip.GzipFile(fileobj=out, mode="wb", filename="", mtime=0, compresslevel=9) as gz:
                for line in response:
                    gz.write(encode(json.loads(line)))
        partial.replace(cache)

    with gzip.open(cache, "rt", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    by_date: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_date[row["contest_date"][:10]].append(row)
    selected: list[dict] = []
    members: list[bytes] = []
    size = 0
    next_date = None
    next_bytes = 0
    for date in sorted(by_date, reverse=True):
        day = sorted(by_date[date], key=lambda r: (r["contest_date"], r["question_id"]))
        member = compress(b"".join(map(encode, day)))
        if size + len(member) > BUDGET:
            next_date, next_bytes = date, len(member)
            break  # Never skip a large day to include older, smaller problems.
        members.insert(0, member)
        selected[0:0] = day
        size += len(member)
    if not selected:
        raise SystemExit("The newest complete date does not fit the budget")
    assert len({r["question_id"] for r in selected}) == len(selected)
    data_dir = ROOT / "livecodebench/data"
    data_dir.mkdir(parents=True, exist_ok=True)
    artifact = b"".join(members)
    (data_dir / "problems.jsonl.gz").write_bytes(artifact)
    ids = [r["question_id"] for r in selected]
    manifest = {
        "dataset": "livecodebench/code_generation_lite",
        "revision": REVISION,
        "release": RELEASE,
        "source_shard": SHARD,
        "source_shard_count": len(rows),
        "source_shard_compressed_bytes": cache.stat().st_size,
        "selection": "Newest contiguous suffix of complete contest dates within a 64000000-byte budget; all tests retained",
        "count": len(selected),
        "date_min": min(r["contest_date"] for r in selected),
        "date_max": max(r["contest_date"] for r in selected),
        "artifact_bytes": len(artifact),
        "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
        "ids_sha256": hashlib.sha256(("\n".join(ids) + "\n").encode()).hexdigest(),
        "question_ids": ids,
        "next_excluded_date": next_date,
        "next_excluded_date_compressed_bytes": next_bytes,
        "platform_counts": dict(sorted(collections.Counter(r["platform"] for r in selected).items())),
        "test_kind_problem_counts": dict(sorted(collections.Counter(
            "functional" if json.loads(r["metadata"]).get("func_name") else "stdin" for r in selected
        ).items())),
    }
    (data_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # Deliberately exclude ids, statements, and tests from build output.
    print(json.dumps({k: v for k, v in manifest.items() if k != "question_ids"}, indent=2))


if __name__ == "__main__":
    main()
