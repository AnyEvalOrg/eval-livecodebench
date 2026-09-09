"""LiveCodeBench code generation as an offline AnyEval/Inspect task."""
from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.solver import generate, system_message

from .dataset import load_records, manifest
from .prompts import SYSTEM_MESSAGE, user_prompt
from .scoring import livecodebench_scorer


def record_to_sample(record: dict) -> Sample:
    # Explicit allowlist: no metadata or test fields participate in rendering input.
    metadata = {key: value for key, value in record.items() if key not in {"question_content", "starter_code", "metadata"}}
    metadata.update(json.loads(record["metadata"]))
    return Sample(
        id=record["question_id"],
        input=user_prompt(record["question_content"], record["starter_code"] or ""),
        metadata=metadata,
    )


def load_dataset() -> MemoryDataset:
    return MemoryDataset(
        name="livecodebench-code_generation_lite-release_v6-20250125-20250406",
        samples=[record_to_sample(record) for record in load_records()],
    )


@task
def livecodebench(
    sandbox_type: str = "k8s",
    per_test_timeout: int = 6,
    anyeval_chart: bool = False,
) -> Task:
    """One generation, one epoch, all-tests pass@1.

    sandbox_type: k8s (default) or docker.
    per_test_timeout: Seconds per test, including process startup (dataset has no limits).
    anyeval_chart: Use the packaged, sidecar-free standard-NetworkPolicy chart on AnyEval's Calico cluster.
    """
    if sandbox_type not in {"k8s", "docker"}:
        raise ValueError("sandbox_type must be k8s or docker")
    resources = files("livecodebench")
    config = str(resources.joinpath("values.yaml" if sandbox_type == "k8s" else "compose.yaml"))
    if anyeval_chart:
        if sandbox_type != "k8s":
            raise ValueError("anyeval_chart requires sandbox_type=k8s")
        from k8s_sandbox import K8sSandboxEnvironmentConfig

        config = K8sSandboxEnvironmentConfig(
            chart=str(resources.joinpath("chart")),
            values=Path(config),
        )
    return Task(
        dataset=load_dataset(),
        solver=[system_message(SYSTEM_MESSAGE), generate()],
        scorer=livecodebench_scorer(per_test_timeout),
        sandbox=(sandbox_type, config),
        epochs=1,
        version="1.0.0",
        metadata={"dataset_provenance": {k: v for k, v in manifest().items() if k != "question_ids"}, "metric": "pass@1"},
    )
