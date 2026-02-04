"""
Tests for CLI path diagnostics and legacy warnings.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mmrelay.cli import handle_config_command, handle_paths_command
from mmrelay.constants.app import APP_DISPLAY_NAME, APP_NAME


def test_handle_paths_command_basic(capsys, monkeypatch, tmp_path):
    """Test handle_paths_command prints basic info."""
    home = tmp_path / "fake_home"
    monkeypatch.setenv("MMRELAY_HOME", str(home))
    # reset_home_override is handled by conftest.py autouse fixture

    with patch("mmrelay.paths.get_home_dir", return_value=home):
        exit_code = handle_paths_command(SimpleNamespace())

        assert exit_code == 0
        captured = capsys.readouterr()
        assert f"{APP_DISPLAY_NAME} Path Configuration" in captured.out
        assert str(home) in captured.out
        assert "HOME Directory" in captured.out


def test_handle_paths_command_with_legacy(capsys, monkeypatch, tmp_path):
    """Test handle_paths_command prints legacy warning when detected."""
    home = tmp_path / "fake_home"
    legacy_root = tmp_path / "legacy_root"

    monkeypatch.setenv("MMRELAY_HOME", str(home))

    with (
        patch("mmrelay.paths.get_home_dir", return_value=home),
        patch("mmrelay.paths.get_legacy_dirs", return_value=[legacy_root]),
    ):
        exit_code = handle_paths_command(SimpleNamespace())

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "Legacy data detected" in captured.out
        assert str(legacy_root) in captured.out
        assert f"{APP_NAME} migrate" in captured.out


def test_handle_config_paths_subcommand(monkeypatch, tmp_path):
    """Test that 'mmrelay config paths' dispatches correctly."""
    home = tmp_path / "fake_home"
    monkeypatch.setenv("MMRELAY_HOME", str(home))

    args = SimpleNamespace(command="config", config_command="paths")

    with patch("mmrelay.cli.handle_paths_command", return_value=0) as mock_handle:
        exit_code = handle_config_command(args)

        assert exit_code == 0
        mock_handle.assert_called_once_with(args)


def test_handle_paths_command_top_level(monkeypatch, tmp_path):
    """Test that 'mmrelay paths' dispatches correctly from handle_subcommand."""
    from mmrelay.cli import handle_subcommand

    home = tmp_path / "fake_home"
    monkeypatch.setenv("MMRELAY_HOME", str(home))

    args = SimpleNamespace(command="paths")

    with patch("mmrelay.cli.handle_paths_command", return_value=0) as mock_handle:
        exit_code = handle_subcommand(args)

        assert exit_code == 0
        mock_handle.assert_called_once_with(args)
