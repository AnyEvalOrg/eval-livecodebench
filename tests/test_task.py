from importlib.resources import files
from pathlib import Path

import pytest
import yaml

from livecodebench import livecodebench


def test_docker_task_builds_offline():
    task = livecodebench(sandbox_type="docker")
    assert len(task.dataset) == 144
    assert task.epochs == 1
    assert task.sandbox.type == "docker"
    assert Path(task.sandbox.config).is_file()
    config = yaml.safe_load(Path(task.sandbox.config).read_text())
    service = config["services"]["default"]
    assert service["image"] == "python:3.12-slim"
    assert service["network_mode"] == "none"
    assert service["user"] == "1000:1000"
    assert not task.dataset[0].target


def test_default_k8s_values_match_anyeval_contract():
    task = livecodebench()
    assert task.sandbox.type == "k8s"
    assert task.sandbox.config.values.name == "values.yaml"
    values = yaml.safe_load(task.sandbox.config.values.read_text())
    service = values["services"]["default"]
    assert values["automountServiceAccountToken"] is False
    assert service["runtimeClassName"] == "gvisor"
    assert service["nodeSelector"] == {"anyeval.io/tier": "sandbox"}
    assert service["image"] == "python:3.12-slim"
    assert service["networkIsolated"] is True
    assert service["securityContext"]["runAsNonRoot"] is True
    assert service["securityContext"]["allowPrivilegeEscalation"] is False
    assert service["securityContext"]["capabilities"]["drop"] == ["ALL"]
    for key in ("allowDomains", "allowEntities", "allowCIDR"):
        assert values[key] == []
    policy = values["additionalResources"][0]["spec"]
    assert policy["policyTypes"] == ["Ingress", "Egress"]
    assert policy["egress"] == []


def test_anyeval_chart_is_packaged_and_selected():
    pytest.importorskip("k8s_sandbox")
    task = livecodebench()
    assert task.sandbox.type == "k8s"
    assert Path(task.sandbox.config.chart).joinpath("Chart.yaml").is_file()
    assert task.sandbox.config.values.is_file()
    chart = files("livecodebench").joinpath("chart/templates/pod.yaml").read_text()
    assert "restartPolicy: Never" in chart
    assert "inspect/service: default" in chart
    assert "coredns" not in chart


@pytest.mark.parametrize("kwargs", [{"sandbox_type": "local"}, {"per_test_timeout": 0}])
def test_invalid_parameters_fail_early(kwargs):
    with pytest.raises(ValueError):
        livecodebench(**kwargs)


def test_values_follow_installed_pinned_provider_schema():
    provider = pytest.importorskip("k8s_sandbox")
    import json
    import jsonschema

    schema = Path(provider.__file__).parent / "resources/helm/agent-env/values.schema.json"
    values = yaml.safe_load(files("livecodebench").joinpath("values.yaml").read_text())
    jsonschema.validate(values, json.loads(schema.read_text()))


def test_builtin_chart_is_explicit_exception():
    task = livecodebench(anyeval_chart=False)
    assert Path(task.sandbox.config).name == "values.yaml"


def test_render_default_chart_matches_anyeval_pod_contract():
    import os
    import shutil
    import subprocess
    helm = os.environ.get("HELM") or shutil.which("helm")
    if not helm:
        bundled = Path(__file__).resolve().parents[1] / ".build/helm"
        helm = str(bundled) if bundled.is_file() else None
    if not helm:
        pytest.skip("Helm binary not available")
    config = livecodebench().sandbox.config
    rendered = subprocess.run([
        helm, "template", "lcb-fixture", config.chart, "--namespace", "anyeval-sandbox",
        "--values", str(config.values),
    ], check=True, capture_output=True, text=True).stdout
    resources = list(yaml.safe_load_all(rendered))
    assert {r["kind"] for r in resources if r} == {"Pod", "NetworkPolicy"}
    pod = next(r for r in resources if r and r["kind"] == "Pod")
    spec = pod["spec"]
    assert pod["metadata"]["namespace"] == "anyeval-sandbox"
    assert spec["runtimeClassName"] == "gvisor"
    assert spec["nodeSelector"] == {"anyeval.io/tier": "sandbox"}
    assert spec["automountServiceAccountToken"] is False
    assert spec["restartPolicy"] == "Never"
    assert len(spec["containers"]) == 1
    security = spec["containers"][0]["securityContext"]
    assert security["runAsNonRoot"] is True
    assert security["allowPrivilegeEscalation"] is False
    assert security["capabilities"]["drop"] == ["ALL"]
    policy = next(r for r in resources if r and r["kind"] == "NetworkPolicy")
    assert policy["metadata"]["namespace"] == "anyeval-sandbox"
    assert policy["spec"]["egress"] == []
    assert "Egress" in policy["spec"]["policyTypes"]
    assert policy["spec"]["podSelector"] == {}
    assert "Cilium" not in rendered and "coredns" not in rendered


def test_default_namespace_is_anyeval_sandbox(monkeypatch):
    import os
    monkeypatch.delenv("INSPECT_K8S_DEFAULT_NAMESPACE", raising=False)
    livecodebench()
    assert os.environ["INSPECT_K8S_DEFAULT_NAMESPACE"] == "anyeval-sandbox"
