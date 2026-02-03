"""
Tests for CLI path diagnostics and legacy warnings.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mmrelay import paths as paths_module
from mmrelay.cli import handle_paths_command, handle_config_command


def test_handle_paths_command_basic(capsys, monkeypatch):
    """Test handle_paths_command prints basic info."""
    home = Path("/tmp/fake_home")
    monkeypatch.setenv("MMRELAY_HOME", str(home))
    # reset_home_override is handled by conftest.py autouse fixture

    with patch("mmrelay.paths.get_home_dir", return_value=home):
        exit_code = handle_paths_command(SimpleNamespace())

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "MMRelay Path Configuration" in captured.out
        assert str(home) in captured.out
        assert "HOME Directory" in captured.out


def test_handle_paths_command_with_legacy(capsys, monkeypatch):
    """Test handle_paths_command prints legacy warning when detected."""
    home = Path("/tmp/fake_home")
    legacy_root = Path("/tmp/legacy_root")

    monkeypatch.setenv("MMRELAY_HOME", str(home))

    with (
        patch("mmrelay.paths.get_home_dir", return_value=home),
        patch("mmrelay.paths.get_legacy_dirs", return_value=[legacy_root])
    ):
        exit_code = handle_paths_command(SimpleNamespace())

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "⚠️  Legacy data detected!" in captured.out
        assert str(legacy_root) in captured.out
        assert "mmrelay migrate" in captured.out


def test_handle_config_paths_subcommand(capsys, monkeypatch):
    """Test that 'mmrelay config paths' dispatches correctly."""
    home = Path("/tmp/fake_home")
    monkeypatch.setenv("MMRELAY_HOME", str(home))

    args = SimpleNamespace(command="config", config_command="paths")

    with patch("mmrelay.cli.handle_paths_command", return_value=0) as mock_handle:
        exit_code = handle_config_command(args)

        assert exit_code == 0
        mock_handle.assert_called_once_with(args)


def test_handle_paths_command_top_level(capsys, monkeypatch):
    """Test that 'mmrelay paths' dispatches correctly from handle_subcommand."""
    from mmrelay.cli import handle_subcommand

    home = Path("/tmp/fake_home")
    monkeypatch.setenv("MMRELAY_HOME", str(home))

    args = SimpleNamespace(command="paths")

    with patch("mmrelay.cli.handle_paths_command", return_value=0) as mock_handle:
        exit_code = handle_subcommand(args)

        assert exit_code == 0
        mock_handle.assert_called_once_with(args)
