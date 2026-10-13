"""Interpreter selection and ABI isolation for plugin dependencies."""

import sys
from pathlib import Path

import pytest

from mmrelay import plugin_runtime


def test_source_install_uses_active_python(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert plugin_runtime.pip_install_command() == [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
    ]
    assert plugin_runtime.standard_library_paths() == []
    assert plugin_runtime.dependency_directory(Path("plugins")) == Path("plugins/deps")


def test_frozen_installer_requires_persistent_target(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(OSError, match="writable plugin directory"):
        plugin_runtime.pip_install_command()


def test_frozen_missing_bundle_has_actionable_failure(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    with pytest.raises(OSError, match="reinstall MMRelay"):
        plugin_runtime.pip_install_command("deps")


def test_frozen_paths_exclude_private_pip_and_separate_abis(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    runtime = tmp_path / "plugin-python"
    runtime.mkdir()
    (runtime / "python.exe").touch()
    assert plugin_runtime.standard_library_paths() == [
        str(runtime / "Lib"),
        str(runtime / "DLLs"),
    ]
    monkeypatch.setattr(plugin_runtime.sysconfig, "get_platform", lambda: "win-amd64")
    monkeypatch.setattr(sys.implementation, "cache_tag", "cpython-314")
    first = plugin_runtime.dependency_directory(tmp_path)
    monkeypatch.setattr(sys.implementation, "cache_tag", "cpython-315")
    second = plugin_runtime.dependency_directory(tmp_path)
    assert first == tmp_path / "deps" / "cpython-314-win-amd64"
    assert first != second
