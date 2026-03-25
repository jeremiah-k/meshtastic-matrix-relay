"""Tests to improve coverage for paths.py.

Docstrings are necessary: Test docstrings follow pytest conventions and document the purpose
of each test case. Inline comments explain test assertions and expected behavior for clarity.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from mmrelay.constants.app import WINDOWS_INSTALLER_DIR_NAME
from mmrelay.paths import (
    E2EENotSupportedError,
    ensure_directories,
    get_config_paths,
    get_diagnostics,
    get_home_dir,
    get_legacy_dirs,
    get_legacy_env_vars,
    get_plugin_code_dir,
    get_plugin_data_dir,
    is_deprecation_window_active,
    reset_home_override,
    resolve_all_paths,
    set_home_override,
)


@pytest.fixture(autouse=True)
def reset_home_override_before_and_after_tests():
    """Reset home override state before and after each test.

    This autouse fixture ensures that tests that modify global state
    via set_home_override() have a clean state and remain independent.
    """
    from mmrelay.paths import reset_home_override

    reset_home_override()
    yield
    reset_home_override()


class TestGetHomeDir:
    """Test get_home_dir function coverage."""

    def test_get_home_dir_with_override(self):
        """Test CLI override takes precedence."""
        set_home_override("/cli_path", source="--home")
        result = get_home_dir()
        assert os.path.normpath(str(result)) == os.path.normpath("/cli_path")

    def test_get_home_dir_with_env_var(self, monkeypatch):
        """Test MMRELAY_HOME environment variable."""
        monkeypatch.setenv("MMRELAY_HOME", "/env_home")
        result = get_home_dir()
        assert os.path.normpath(str(result)) == os.path.normpath("/env_home")

    def test_get_home_dir_with_legacy_base_dir_and_home(self, monkeypatch):
        """Test MMRELAY_BASE_DIR with MMRELAY_HOME - should warn and prefer HOME."""
        monkeypatch.setenv("MMRELAY_HOME", "/new_home")
        monkeypatch.setenv("MMRELAY_BASE_DIR", "/old_base")

        # MMRELAY_HOME takes precedence; legacy var is ignored with warning
        result = get_home_dir()
        assert os.path.normpath(str(result)) == os.path.normpath("/new_home")

    def test_get_home_dir_windows_installer_path_preferred(self, monkeypatch):
        """Windows installer path with MMRelay artifacts should be preferred."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_app_data = Path(tmp_dir)
            installer_path = local_app_data / "Programs" / WINDOWS_INSTALLER_DIR_NAME
            installer_path.mkdir(parents=True, exist_ok=True)
            (installer_path / "config.yaml").write_text("test: true", encoding="utf-8")

            monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
            monkeypatch.delenv("MMRELAY_HOME", raising=False)
            monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
            monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)
            with patch("sys.platform", "win32"):
                result = get_home_dir()

            assert result == installer_path

    def test_get_home_dir_windows_platformdirs_fallback(self, monkeypatch):
        """Windows should fall back to platformdirs when installer path is unavailable."""
        monkeypatch.delenv("MMRELAY_HOME", raising=False)
        monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
        monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        with (
            patch("sys.platform", "win32"),
            patch(
                "mmrelay.paths.platformdirs.user_data_dir", return_value="C:/pd/mmrelay"
            ),
        ):
            result = get_home_dir()
        assert os.path.normpath(str(result)) == os.path.normpath("C:/pd/mmrelay")


def test_get_config_paths_dedupes_when_explicit_matches_home() -> None:
    """Explicit config equal to home config should not be duplicated."""
    with (
        patch("mmrelay.paths.get_home_dir", return_value=Path("/same")),
        patch("mmrelay.paths.Path.cwd", return_value=Path("/same")),
        patch("mmrelay.paths.Path.home", return_value=Path("/same")),
        patch.object(Path, "exists", return_value=False),
    ):
        candidates = get_config_paths(explicit="/same/config.yaml")

    assert candidates == [Path("/same/config.yaml").absolute()]


def test_plugin_data_dir_prefers_discovered_community_plugin() -> None:
    """Unknown plugin_type should prefer discovered community plugin path."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        custom_root = root / "custom"
        community_root = root / "community"
        community_plugin = community_root / "demo"
        community_plugin.mkdir(parents=True, exist_ok=True)

        with (
            patch("mmrelay.paths.get_custom_plugins_dir", return_value=custom_root),
            patch(
                "mmrelay.paths.get_community_plugins_dir", return_value=community_root
            ),
            patch("mmrelay.paths.get_core_plugins_dir", return_value=root / "core"),
        ):
            resolved = get_plugin_data_dir("demo")

        assert resolved == community_plugin / "data"


def test_e2ee_not_supported_error_message() -> None:
    """E2EENotSupportedError should expose its canonical message."""
    assert str(E2EENotSupportedError()) == "E2EE not supported on Windows"


def test_get_legacy_env_vars_and_deprecation_window(monkeypatch) -> None:
    """Legacy env vars should activate deprecation window once and be discoverable."""
    import mmrelay.paths as paths_module

    paths_module._reset_deprecation_warning_flag()
    reset_home_override()
    monkeypatch.delenv("MMRELAY_HOME", raising=False)
    monkeypatch.setenv("MMRELAY_BASE_DIR", "/legacy/base")
    monkeypatch.setenv("MMRELAY_DATA_DIR", "/legacy/data")
    with patch("mmrelay.paths.logger") as mock_logger:
        legacy_vars = get_legacy_env_vars()
        assert sorted(legacy_vars) == ["MMRELAY_BASE_DIR", "MMRELAY_DATA_DIR"]
        assert is_deprecation_window_active() is True
        assert is_deprecation_window_active() is True

    warning_calls = [
        call for call in mock_logger.warning.call_args_list if "Deprecated" in str(call)
    ]
    assert len(warning_calls) == 1


def test_get_legacy_dirs_includes_windows_installer_path(monkeypatch) -> None:
    """Windows installer directory should be reported as a legacy source."""
    monkeypatch.setenv("LOCALAPPDATA", "/la")
    monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
    monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)

    installer_path = Path("/la") / "Programs" / WINDOWS_INSTALLER_DIR_NAME

    with (
        patch("sys.platform", "win32"),
        patch("mmrelay.paths.get_home_dir", return_value=Path("/current/home")),
        patch("mmrelay.paths.platformdirs.user_data_dir", return_value="/platform"),
        patch.object(installer_path, "exists", return_value=True),
    ):
        legacy_dirs = get_legacy_dirs()

    assert installer_path in legacy_dirs


def test_get_legacy_dirs_includes_env_and_docker_sources(monkeypatch) -> None:
    """Legacy env paths and Docker legacy paths with artifacts should be detected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        base_dir = tmp_root / "base"
        data_dir = tmp_root / "data"
        docker_dir = tmp_root / "docker"
        base_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        docker_dir.mkdir(parents=True, exist_ok=True)
        (docker_dir / "config.yaml").write_text("", encoding="utf-8")

        monkeypatch.setenv("MMRELAY_BASE_DIR", str(base_dir))
        monkeypatch.setenv("MMRELAY_DATA_DIR", str(data_dir))
        with (
            patch("mmrelay.paths.get_home_dir", return_value=tmp_root / "current"),
            patch("mmrelay.paths.DOCKER_LEGACY_PATHS", [str(docker_dir)]),
        ):
            legacy_dirs = get_legacy_dirs()

    assert base_dir in legacy_dirs
    assert data_dir in legacy_dirs
    assert docker_dir in legacy_dirs


def test_resolve_all_paths_tracks_env_vars_and_home_source(monkeypatch) -> None:
    """resolve_all_paths should expose detected env vars and select expected home source."""
    monkeypatch.delenv("MMRELAY_HOME", raising=False)
    monkeypatch.setenv("MMRELAY_BASE_DIR", "/legacy/base")
    monkeypatch.setenv("MMRELAY_DATA_DIR", "/legacy/data")
    monkeypatch.setenv("MMRELAY_LOG_PATH", "/legacy/logs/mmrelay.log")

    resolved = resolve_all_paths()
    detected = resolved["env_vars_detected"]
    assert detected["MMRELAY_BASE_DIR"] == "/legacy/base"
    assert detected["MMRELAY_DATA_DIR"] == "/legacy/data"
    assert detected["MMRELAY_LOG_PATH"] == "/legacy/logs/mmrelay.log"
    assert resolved["home_source"] == "MMRELAY_BASE_DIR env var"


def test_ensure_directories_creates_missing_tree() -> None:
    """ensure_directories(create_missing=True) should create required paths."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        home = root / "home"
        matrix = home / "matrix"
        db = home / "database"
        logs = home / "logs"
        store = matrix / "store"
        plugins = home / "plugins"
        custom_plugins = plugins / "custom"
        community_plugins = plugins / "community"
        core_plugins = root / "core_plugins"

        with (
            patch("sys.platform", "linux"),
            patch("mmrelay.paths.get_home_dir", return_value=home),
            patch("mmrelay.paths.get_matrix_dir", return_value=matrix),
            patch("mmrelay.paths.get_database_dir", return_value=db),
            patch("mmrelay.paths.get_logs_dir", return_value=logs),
            patch("mmrelay.paths.get_e2ee_store_dir", return_value=store),
            patch("mmrelay.paths.get_plugins_dir", return_value=plugins),
            patch("mmrelay.paths.get_custom_plugins_dir", return_value=custom_plugins),
            patch(
                "mmrelay.paths.get_community_plugins_dir",
                return_value=community_plugins,
            ),
            patch("mmrelay.paths.get_core_plugins_dir", return_value=core_plugins),
        ):
            ensure_directories(create_missing=True)

        for path in (
            home,
            matrix,
            db,
            logs,
            store,
            plugins,
            custom_plugins,
            community_plugins,
            core_plugins,
        ):
            assert path.exists(), f"expected created path: {path}"


def test_ensure_directories_warns_when_missing_and_create_disabled() -> None:
    """ensure_directories(create_missing=False) should warn for missing paths."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        home = root / "home"
        matrix = home / "matrix"
        db = home / "database"
        logs = home / "logs"
        store = matrix / "store"
        plugins = home / "plugins"
        custom_plugins = plugins / "custom"
        community_plugins = plugins / "community"
        core_plugins = root / "core_plugins"

        with (
            patch("sys.platform", "linux"),
            patch("mmrelay.paths.get_home_dir", return_value=home),
            patch("mmrelay.paths.get_matrix_dir", return_value=matrix),
            patch("mmrelay.paths.get_database_dir", return_value=db),
            patch("mmrelay.paths.get_logs_dir", return_value=logs),
            patch("mmrelay.paths.get_e2ee_store_dir", return_value=store),
            patch("mmrelay.paths.get_plugins_dir", return_value=plugins),
            patch("mmrelay.paths.get_custom_plugins_dir", return_value=custom_plugins),
            patch(
                "mmrelay.paths.get_community_plugins_dir",
                return_value=community_plugins,
            ),
            patch("mmrelay.paths.get_core_plugins_dir", return_value=core_plugins),
            patch("mmrelay.paths.logger") as mock_logger,
        ):
            ensure_directories(create_missing=False)

        warning_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if "Directory missing" in str(call)
        ]
        assert len(warning_calls) >= 5


def test_get_plugin_code_dir_covers_type_and_discovery_branches() -> None:
    """get_plugin_code_dir should cover explicit-type and discovery branches."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        custom_root = root / "custom"
        community_root = root / "community"
        (community_root / "demo").mkdir(parents=True, exist_ok=True)

        with (
            patch("mmrelay.paths.get_custom_plugins_dir", return_value=custom_root),
            patch(
                "mmrelay.paths.get_community_plugins_dir", return_value=community_root
            ),
        ):
            assert get_plugin_code_dir("x", plugin_type="custom") == custom_root / "x"
            assert (
                get_plugin_code_dir("x", plugin_type="community")
                == community_root / "x"
            )
            discovered = get_plugin_code_dir("demo")
            assert discovered == community_root / "demo"
            core_path = get_plugin_code_dir("mesh_relay", plugin_type="core")
            assert "plugins" in str(core_path)


def test_get_diagnostics_maps_resolved_fields() -> None:
    """get_diagnostics should expose compatibility keys mapped from resolve_all_paths."""
    resolved = {
        "home": "/h",
        "matrix_dir": "/h/matrix",
        "legacy_sources": [],
        "credentials_path": "/h/matrix/credentials.json",
        "database_dir": "/h/database",
        "store_dir": "/h/matrix/store",
        "logs_dir": "/h/logs",
        "log_file": "/h/logs/mmrelay.log",
        "plugins_dir": "/h/plugins",
        "custom_plugins_dir": "/h/plugins/custom",
        "community_plugins_dir": "/h/plugins/community",
        "deps_dir": "/h/plugins/deps",
        "env_vars_detected": {"MMRELAY_HOME": "/h"},
        "cli_override": "--home",
        "home_source": "CLI (--home)",
    }
    with (
        patch("mmrelay.paths.resolve_all_paths", return_value=resolved),
        patch(
            "mmrelay.paths.get_database_path",
            return_value=Path("/h/database/db.sqlite"),
        ),
        patch("mmrelay.paths.is_deprecation_window_active", return_value=False),
    ):
        diagnostics = get_diagnostics()

    assert diagnostics["home_dir"] == "/h"
    assert diagnostics["matrix_dir"] == "/h/matrix"
    assert diagnostics["database_path"] == "/h/database/db.sqlite"
    assert diagnostics["sources_used"] == "CLI (--home)"
