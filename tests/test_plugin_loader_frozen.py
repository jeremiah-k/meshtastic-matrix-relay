"""Dependency installation through the frozen application's plugin loader."""

import json
import subprocess  # nosec B404 - subprocess calls are mocked at the process boundary
import sys
from pathlib import Path

import pytest

from mmrelay import plugin_loader as loader


@pytest.fixture
def frozen_bundle(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    runtime = bundle / "plugin-python"
    runtime.mkdir(parents=True)
    interpreter = runtime / "python.exe"
    interpreter.touch()
    deps = tmp_path / "home" / "plugins" / "deps"
    deps.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "mmrelay.exe"))
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setattr(loader, "_PLUGIN_DEPS_DIR", str(deps))
    monkeypatch.setattr(loader, "config", None)
    monkeypatch.setattr(loader, "_community_dep_install_warning_logged", False)
    return interpreter, deps


@pytest.mark.integration
def test_frozen_requirements_install_and_plugin_import(
    tmp_path, monkeypatch, frozen_bundle
):
    interpreter, deps = frozen_bundle
    repo = tmp_path / "plugin"
    repo.mkdir()
    (repo / "requirements.txt").write_text("frozen_probe==1.0\n", encoding="utf-8")
    (repo / "probe.py").write_text(
        "import frozen_probe\nclass Plugin:\n    value = frozen_probe.VALUE\n",
        encoding="utf-8",
    )

    def install(cmd, **kwargs):
        assert cmd[0] == str(interpreter)
        assert "--only-binary=:all:" in cmd
        assert "--isolated" in cmd
        target = Path(cmd[cmd.index("--target") + 1])
        assert target != deps
        (target / "frozen_probe.py").write_text("VALUE = 42\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", install)
    try:
        assert loader._install_requirements_for_repo(
            str(repo), "probe", plugin_type=loader.PLUGIN_TYPE_COMMUNITY
        )
        plugins = loader.load_plugins_from_directory(str(repo))
        assert len(plugins) == 1
        assert plugins[0].value == 42
        assert (deps / "frozen_probe.py").is_file()
        marker = Path(loader._requirements_install_marker_path(str(repo), "probe"))
        assert json.loads(marker.read_text(encoding="utf-8"))["requirements_hash"]
    finally:
        sys.modules.pop("frozen_probe", None)


def test_frozen_missing_runtime_fails_without_relaunching_app(
    tmp_path, monkeypatch, frozen_bundle
):
    interpreter, deps = frozen_bundle
    interpreter.unlink()
    repo = tmp_path / "plugin"
    repo.mkdir()
    (repo / "requirements.txt").write_text("frozen_probe==1.0\n", encoding="utf-8")

    def unexpected_run(*args, **kwargs):
        pytest.fail("A missing runtime must not fall back to mmrelay.exe or PATH")

    monkeypatch.setattr(subprocess, "run", unexpected_run)
    assert not loader._install_requirements_for_repo(str(repo), "probe")
    assert not list(deps.glob("*.json"))


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(1, "pip"),
        subprocess.TimeoutExpired("pip", 600),
    ],
)
def test_frozen_failed_install_preserves_dependencies(
    tmp_path, monkeypatch, frozen_bundle, error
):
    _, deps = frozen_bundle
    installed = deps / "installed.py"
    installed.write_text("VALUE = 1\n", encoding="utf-8")
    repo = tmp_path / "plugin"
    repo.mkdir()
    (repo / "requirements.txt").write_text("frozen_probe==1.0\n", encoding="utf-8")

    def fail_install(cmd, **kwargs):
        target = Path(cmd[cmd.index("--target") + 1])
        (target / "partial.py").touch()
        raise error

    monkeypatch.setattr(subprocess, "run", fail_install)
    assert not loader._install_requirements_for_repo(str(repo), "probe")
    assert installed.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (deps / "partial.py").exists()
    assert not list(deps.parent.glob(".mmrelay-deps-install-*"))
    assert not list(deps.glob("*.json"))


def test_frozen_install_requires_writable_target(tmp_path, monkeypatch, frozen_bundle):
    repo = tmp_path / "plugin"
    repo.mkdir()
    (repo / "requirements.txt").write_text("frozen_probe==1.0\n", encoding="utf-8")
    monkeypatch.setattr(loader, "_PLUGIN_DEPS_DIR", None)
    monkeypatch.setenv("PIPX_HOME", "must-not-select-pipx")

    def unexpected_run(*args, **kwargs):
        pytest.fail("Frozen installs cannot fall back to pipx or the user site")

    monkeypatch.setattr(subprocess, "run", unexpected_run)
    assert not loader._install_requirements_for_repo(str(repo), "probe")


@pytest.mark.integration
def test_frozen_custom_plugin_missing_dependency_uses_persistent_target(
    tmp_path, monkeypatch, frozen_bundle
):
    interpreter, deps = frozen_bundle
    repo = tmp_path / "custom"
    repo.mkdir()
    (repo / "probe.py").write_text(
        "import frozen_custom_probe\nclass Plugin:\n"
        "    value = frozen_custom_probe.VALUE\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PIPX_HOME", "must-not-select-pipx")

    def install(cmd, **kwargs):
        assert cmd[0] == str(interpreter)
        target = Path(cmd[cmd.index("--target") + 1])
        assert target != deps
        (target / "frozen_custom_probe.py").write_text("VALUE = 43\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", install)
    try:
        plugins = loader.load_plugins_from_directory(str(repo))
        assert len(plugins) == 1
        assert plugins[0].value == 43
        assert (deps / "frozen_custom_probe.py").is_file()
    finally:
        sys.modules.pop("frozen_custom_probe", None)
