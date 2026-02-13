"""
Tests for CLI path diagnostics and legacy warnings.
"""

from types import SimpleNamespace
from unittest.mock import patch

from mmrelay.cli import handle_config_command, handle_paths_command
from mmrelay.constants.app import APP_DISPLAY_NAME, APP_NAME


def test_handle_paths_command_basic(capsys, monkeypatch, tmp_path):
    """Test handle_paths_command prints basic info."""
    home = tmp_path / "fake_home"
    monkeypatch.setenv("MMRELAY_HOME", str(home))
    # reset_home_override is handled by conftest.py autouse fixture

    with patch(
        "mmrelay.paths.resolve_all_paths",
        return_value={
            "home": str(home),
            "home_source": "env",
            "credentials_path": str(home / "matrix" / "credentials.json"),
            "database_dir": str(home / "database"),
            "store_dir": str(home / "matrix" / "store"),
            "logs_dir": str(home / "logs"),
            "log_file": str(home / "logs" / "mmrelay.log"),
            "plugins_dir": str(home / "plugins"),
            "custom_plugins_dir": str(home / "plugins" / "custom"),
            "community_plugins_dir": str(home / "plugins" / "community"),
            "legacy_sources": [],
            "env_vars_detected": {},
            "cli_override": None,
        },
    ):
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

    with patch(
        "mmrelay.paths.resolve_all_paths",
        return_value={
            "home": str(home),
            "home_source": "env",
            "credentials_path": str(home / "matrix" / "credentials.json"),
            "database_dir": str(home / "database"),
            "store_dir": str(home / "matrix" / "store"),
            "logs_dir": str(home / "logs"),
            "log_file": str(home / "logs" / "mmrelay.log"),
            "plugins_dir": str(home / "plugins"),
            "custom_plugins_dir": str(home / "plugins" / "custom"),
            "community_plugins_dir": str(home / "plugins" / "community"),
            "legacy_sources": [str(legacy_root)],
            "env_vars_detected": {},
            "cli_override": None,
        },
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


class TestDetectSameHomeLegacyItems:
    """Tests for _detect_same_home_legacy_items function."""

    def test_no_legacy_items_when_clean(self, tmp_path):
        """Test returns empty list when no legacy items exist."""
        from mmrelay.cli import _detect_same_home_legacy_items

        home = tmp_path / "home"
        home.mkdir()
        (home / "matrix").mkdir()

        paths_info = {
            "home": str(home),
            "credentials_path": str(home / "matrix" / "credentials.json"),
            "store_dir": str(home / "matrix" / "store"),
        }

        result = _detect_same_home_legacy_items(paths_info)
        assert result == []

    def test_detects_legacy_credentials_at_root(self, tmp_path):
        """Test detects credentials.json at HOME root (should be in matrix/)."""
        from mmrelay.cli import _detect_same_home_legacy_items

        home = tmp_path / "home"
        home.mkdir()
        (home / "matrix").mkdir()
        # Create legacy credentials at root
        (home / "credentials.json").write_text("{}")

        paths_info = {
            "home": str(home),
            "credentials_path": str(home / "matrix" / "credentials.json"),
            "store_dir": str(home / "matrix" / "store"),
        }

        result = _detect_same_home_legacy_items(paths_info)
        assert len(result) == 1
        assert result[0]["type"] == "credentials"
        assert result[0]["path"] == str(home / "credentials.json")

    def test_detects_legacy_store_at_root(self, tmp_path):
        """Test detects store/ at HOME root (should be in matrix/store/)."""
        from mmrelay.cli import _detect_same_home_legacy_items

        home = tmp_path / "home"
        home.mkdir()
        (home / "matrix").mkdir()
        # Create legacy store at root
        (home / "store").mkdir()

        paths_info = {
            "home": str(home),
            "credentials_path": str(home / "matrix" / "credentials.json"),
            "store_dir": str(home / "matrix" / "store"),
        }

        result = _detect_same_home_legacy_items(paths_info)
        assert len(result) == 1
        assert result[0]["type"] == "e2ee_store"
        assert result[0]["path"] == str(home / "store")

    def test_detects_both_legacy_items(self, tmp_path):
        """Test detects both legacy credentials and store at root."""
        from mmrelay.cli import _detect_same_home_legacy_items

        home = tmp_path / "home"
        home.mkdir()
        (home / "matrix").mkdir()
        # Create both legacy items at root
        (home / "credentials.json").write_text("{}")
        (home / "store").mkdir()

        paths_info = {
            "home": str(home),
            "credentials_path": str(home / "matrix" / "credentials.json"),
            "store_dir": str(home / "matrix" / "store"),
        }

        result = _detect_same_home_legacy_items(paths_info)
        assert len(result) == 2
        types = {item["type"] for item in result}
        assert types == {"credentials", "e2ee_store"}

    def test_no_detection_when_at_correct_location(self, tmp_path):
        """Test doesn't flag items that are already in correct locations."""
        from mmrelay.cli import _detect_same_home_legacy_items

        home = tmp_path / "home"
        home.mkdir()
        (home / "matrix").mkdir()
        (home / "matrix" / "store").mkdir()
        (home / "matrix" / "credentials.json").write_text("{}")

        paths_info = {
            "home": str(home),
            "credentials_path": str(home / "matrix" / "credentials.json"),
            "store_dir": str(home / "matrix" / "store"),
        }

        result = _detect_same_home_legacy_items(paths_info)
        assert result == []

    def test_windows_store_dir_na(self, tmp_path):
        """Test handles Windows case where store_dir is N/A."""
        from mmrelay.cli import _detect_same_home_legacy_items

        home = tmp_path / "home"
        home.mkdir()
        (home / "matrix").mkdir()
        (home / "credentials.json").write_text("{}")

        paths_info = {
            "home": str(home),
            "credentials_path": str(home / "matrix" / "credentials.json"),
            "store_dir": "N/A (Windows)",
        }

        result = _detect_same_home_legacy_items(paths_info)
        # Should only detect credentials, not try to check store
        assert len(result) == 1
        assert result[0]["type"] == "credentials"


def test_handle_paths_command_with_same_home_legacy(capsys, monkeypatch, tmp_path):
    """Test handle_paths_command shows same-home legacy items."""
    home = tmp_path / "fake_home"
    monkeypatch.setenv("MMRELAY_HOME", str(home))

    # Create the home directory structure with legacy items
    home.mkdir()
    (home / "matrix").mkdir()
    # Create legacy store at root (not in matrix/)
    (home / "store").mkdir()

    with patch(
        "mmrelay.paths.resolve_all_paths",
        return_value={
            "home": str(home),
            "home_source": "env",
            "credentials_path": str(home / "matrix" / "credentials.json"),
            "database_dir": str(home / "database"),
            "store_dir": str(home / "matrix" / "store"),
            "logs_dir": str(home / "logs"),
            "log_file": str(home / "logs" / "mmrelay.log"),
            "plugins_dir": str(home / "plugins"),
            "custom_plugins_dir": str(home / "plugins" / "custom"),
            "community_plugins_dir": str(home / "plugins" / "community"),
            "legacy_sources": [],  # No external legacy, but same-home legacy exists
            "env_vars_detected": {},
            "cli_override": None,
        },
    ):
        exit_code = handle_paths_command(SimpleNamespace())

        assert exit_code == 0
        captured = capsys.readouterr()
        # Should show legacy layout section
        assert "Legacy Layout in HOME" in captured.out
        assert "e2ee_store" in captured.out
        assert str(home / "store") in captured.out
