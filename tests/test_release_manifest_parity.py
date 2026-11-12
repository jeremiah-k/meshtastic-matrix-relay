"""Catch stale deployment defaults in release candidates."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_deployment_defaults_retain_the_published_release():
    # Source candidates may land before their deployable release image exists.
    # Advance this expectation only with the published deployment release.
    version = "1.4.0"
    with (ROOT / "deploy/helm/mmrelay/Chart.yaml").open(encoding="utf-8") as f:
        chart = yaml.safe_load(f)
    with (ROOT / "deploy/helm/mmrelay/values.yaml").open(encoding="utf-8") as f:
        values = yaml.safe_load(f)
    with (ROOT / "deploy/k8s/kustomization.yaml").open(encoding="utf-8") as f:
        kustomize = yaml.safe_load(f)
    assert chart["version"] == version
    assert chart["appVersion"] == version
    assert values["image"]["tag"] == version
    mmrelay_images = [
        item
        for item in kustomize["images"]
        if item.get("name") == "ghcr.io/jeremiah-k/mmrelay"
    ]
    assert len(mmrelay_images) == 1
    assert mmrelay_images[0].get("newTag") == version
