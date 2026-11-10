"""Catch stale deployment defaults in release candidates."""

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_deployment_defaults_match_project_version():
    with (ROOT / "pyproject.toml").open("rb") as f:
        version = tomllib.load(f)["project"]["version"]
    with (ROOT / "deploy/helm/mmrelay/Chart.yaml").open(encoding="utf-8") as f:
        chart = yaml.safe_load(f)
    with (ROOT / "deploy/helm/mmrelay/values.yaml").open(encoding="utf-8") as f:
        values = yaml.safe_load(f)
    with (ROOT / "deploy/k8s/kustomization.yaml").open(encoding="utf-8") as f:
        kustomize = yaml.safe_load(f)
    assert chart["version"] == version
    assert chart["appVersion"] == version
    assert values["image"]["tag"] == version
    assert any(item.get("newTag") == version for item in kustomize["images"])
    helm_examples = (ROOT / "docs/HELM.md").read_text(encoding="utf-8")
    example_tags = set(re.findall(r"--set image\.tag=(\S+)", helm_examples))
    assert example_tags == {version}
