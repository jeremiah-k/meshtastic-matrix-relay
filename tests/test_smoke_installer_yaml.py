"""Portable compiler discovery for the Windows installer YAML smoke test."""

import pytest

from scripts.ci import smoke_installer_yaml


def test_uses_explicit_compiler_when_provided(tmp_path, monkeypatch):
    compiler = tmp_path / "ISCC.exe"
    compiler.write_bytes(b"compiler")
    monkeypatch.setenv("ISCC_PATH", str(compiler))
    assert smoke_installer_yaml.find_inno_compiler() == compiler


def test_invalid_explicit_compiler_fails_instead_of_falling_back(tmp_path, monkeypatch):
    monkeypatch.setenv("ISCC_PATH", str(tmp_path / "absent.exe"))
    with pytest.raises(RuntimeError, match="ISCC_PATH"):
        smoke_installer_yaml.find_inno_compiler()


def test_program_files_fallback_without_actions_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("ISCC_PATH", raising=False)
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)
    monkeypatch.setattr(smoke_installer_yaml.shutil, "which", lambda _: None)
    compiler = tmp_path / "Inno Setup 6" / "ISCC.exe"
    compiler.parent.mkdir()
    compiler.write_bytes(b"compiler")
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.delenv("ProgramFiles", raising=False)
    assert smoke_installer_yaml.find_inno_compiler() == compiler
