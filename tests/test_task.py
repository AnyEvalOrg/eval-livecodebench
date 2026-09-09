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
    assert Path(task.sandbox.config).name == "values.yaml"
    values = yaml.safe_load(Path(task.sandbox.config).read_text())
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
    task = livecodebench(anyeval_chart=True)
    assert task.sandbox.type == "k8s"
    assert Path(task.sandbox.config.chart).joinpath("Chart.yaml").is_file()
    assert task.sandbox.config.values.is_file()
    chart = files("livecodebench").joinpath("chart/templates/pod.yaml").read_text()
    assert "restartPolicy: Never" in chart
    assert "inspect/service: default" in chart
    assert "coredns" not in chart


@pytest.mark.parametrize("kwargs", [{"sandbox_type": "local"}, {"per_test_timeout": 0}, {"sandbox_type": "docker", "anyeval_chart": True}])
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
