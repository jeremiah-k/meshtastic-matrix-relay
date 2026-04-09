"""Tests to improve coverage for paths.py.

Docstrings are necessary: Test docstrings follow pytest conventions and document the purpose
of each test case. Inline comments explain test assertions and expected behavior for clarity.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from mmrelay.constants.app import (
    DATABASE_FILENAME,
    LOG_FILENAME,
    WINDOWS_INSTALLER_DIR_NAME,
)
from mmrelay.constants.config import DEFAULT_CONFIG_FILENAME
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

    def test_get_home_dir_windows_installer_path_preferred(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Windows installer path with MMRelay artifacts should be preferred."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_app_data = Path(tmp_dir)
            installer_path = local_app_data / "Programs" / WINDOWS_INSTALLER_DIR_NAME
            installer_path.mkdir(parents=True, exist_ok=True)
            (installer_path / DEFAULT_CONFIG_FILENAME).write_text(
                "test: true", encoding="utf-8"
            )

            monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
            monkeypatch.delenv("MMRELAY_HOME", raising=False)
            monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
            monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)
            with patch("sys.platform", "win32"):
                result = get_home_dir()

            assert result == installer_path

    def test_get_home_dir_windows_installer_path_preferred_on_first_run(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Installer path markers should be enough on first run before data exists."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_app_data = Path(tmp_dir)
            installer_path = local_app_data / "Programs" / WINDOWS_INSTALLER_DIR_NAME
            installer_path.mkdir(parents=True, exist_ok=True)
            (installer_path / "mmrelay.bat").write_text("@echo off\n", encoding="utf-8")

            monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
            monkeypatch.delenv("MMRELAY_HOME", raising=False)
            monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
            monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)
            with patch("sys.platform", "win32"):
                result = get_home_dir()

            assert result == installer_path

    def test_get_home_dir_windows_prefers_existing_platform_data_over_marker_only_installer(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Existing platform data should win over a marker-only installer directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            local_app_data = root / "LocalAppData"
            installer_path = local_app_data / "Programs" / WINDOWS_INSTALLER_DIR_NAME
            installer_path.mkdir(parents=True, exist_ok=True)
            (installer_path / "mmrelay.bat").write_text("@echo off\n", encoding="utf-8")

            platform_home = root / "PlatformData" / "mmrelay"
            platform_home.mkdir(parents=True, exist_ok=True)
            (platform_home / DEFAULT_CONFIG_FILENAME).write_text(
                "test: true\n", encoding="utf-8"
            )

            monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
            monkeypatch.delenv("MMRELAY_HOME", raising=False)
            monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
            monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)
            with (
                patch("sys.platform", "win32"),
                patch(
                    "mmrelay.paths.platformdirs.user_data_dir",
                    return_value=str(platform_home),
                ),
            ):
                result = get_home_dir()

            assert result == platform_home

    def test_get_home_dir_windows_prefers_stronger_platform_state_over_weak_installer_artifacts(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Weak installer artifacts (like logs) should not outrank established platform state."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            local_app_data = root / "LocalAppData"
            installer_path = local_app_data / "Programs" / WINDOWS_INSTALLER_DIR_NAME
            installer_logs = installer_path / "logs"
            installer_logs.mkdir(parents=True, exist_ok=True)
            (installer_logs / LOG_FILENAME).write_text("log\n", encoding="utf-8")

            platform_home = root / "PlatformData" / "mmrelay"
            platform_db = platform_home / "database"
            platform_db.mkdir(parents=True, exist_ok=True)
            (platform_home / DEFAULT_CONFIG_FILENAME).write_text(
                "test: true\n", encoding="utf-8"
            )
            (platform_db / DATABASE_FILENAME).write_text("", encoding="utf-8")

            monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
            monkeypatch.delenv("MMRELAY_HOME", raising=False)
            monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
            monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)
            with (
                patch("sys.platform", "win32"),
                patch(
                    "mmrelay.paths.platformdirs.user_data_dir",
                    return_value=str(platform_home),
                ),
            ):
                result = get_home_dir()

            assert result == platform_home

    def test_get_home_dir_windows_prefers_stronger_installer_state_over_weak_platform_artifacts(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Installer path should win when it clearly has stronger MMRelay state."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            local_app_data = root / "LocalAppData"
            installer_path = local_app_data / "Programs" / WINDOWS_INSTALLER_DIR_NAME
            installer_db = installer_path / "database"
            installer_db.mkdir(parents=True, exist_ok=True)
            (installer_path / DEFAULT_CONFIG_FILENAME).write_text(
                "test: true\n", encoding="utf-8"
            )
            (installer_db / DATABASE_FILENAME).write_text("", encoding="utf-8")

            platform_home = root / "PlatformData" / "mmrelay"
            platform_logs = platform_home / "logs"
            platform_logs.mkdir(parents=True, exist_ok=True)
            (platform_logs / LOG_FILENAME).write_text("log\n", encoding="utf-8")

            monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
            monkeypatch.delenv("MMRELAY_HOME", raising=False)
            monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
            monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)
            with (
                patch("sys.platform", "win32"),
                patch(
                    "mmrelay.paths.platformdirs.user_data_dir",
                    return_value=str(platform_home),
                ),
            ):
                result = get_home_dir()

            assert result == installer_path

    def test_get_home_dir_windows_prefers_platform_when_installer_advantage_is_only_logs(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Small installer score bumps from logs should not steal precedence."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            local_app_data = root / "LocalAppData"
            installer_path = local_app_data / "Programs" / WINDOWS_INSTALLER_DIR_NAME
            installer_db = installer_path / "database"
            installer_logs = installer_path / "logs"
            installer_db.mkdir(parents=True, exist_ok=True)
            installer_logs.mkdir(parents=True, exist_ok=True)
            (installer_path / DEFAULT_CONFIG_FILENAME).write_text(
                "test: true\n", encoding="utf-8"
            )
            (installer_db / DATABASE_FILENAME).write_text("", encoding="utf-8")
            (installer_logs / LOG_FILENAME).write_text("log\n", encoding="utf-8")

            platform_home = root / "PlatformData" / "mmrelay"
            platform_db = platform_home / "database"
            platform_db.mkdir(parents=True, exist_ok=True)
            (platform_home / DEFAULT_CONFIG_FILENAME).write_text(
                "test: true\n", encoding="utf-8"
            )
            (platform_db / DATABASE_FILENAME).write_text("", encoding="utf-8")

            monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
            monkeypatch.delenv("MMRELAY_HOME", raising=False)
            monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
            monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)
            with (
                patch("sys.platform", "win32"),
                patch(
                    "mmrelay.paths.platformdirs.user_data_dir",
                    return_value=str(platform_home),
                ),
            ):
                result = get_home_dir()

            assert result == platform_home

    def test_get_home_dir_windows_platformdirs_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ):
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
        patch.object(Path, "cwd", return_value=Path("/same")),
        patch.object(Path, "home", return_value=Path("/same")),
        patch.object(Path, "exists", autospec=True, side_effect=lambda self: False),
    ):
        candidates = get_config_paths(explicit=f"/same/{DEFAULT_CONFIG_FILENAME}")

    assert len(candidates) == 1
    assert candidates[0] == Path(f"/same/{DEFAULT_CONFIG_FILENAME}")


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


def test_get_legacy_env_vars_and_deprecation_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy env vars should activate deprecation window once and be discoverable."""
    import mmrelay.paths as paths_module

    paths_module._reset_deprecation_warning_flag()
    try:
        reset_home_override()
        monkeypatch.delenv("MMRELAY_HOME", raising=False)
        monkeypatch.setenv("MMRELAY_BASE_DIR", "/legacy/base")
        monkeypatch.setenv("MMRELAY_DATA_DIR", "/legacy/data")
        with patch("mmrelay.paths.logger") as mock_logger:
            legacy_vars = get_legacy_env_vars()
            assert sorted(legacy_vars) == ["MMRELAY_BASE_DIR", "MMRELAY_DATA_DIR"]
            assert is_deprecation_window_active() is True
            # Verify deprecation flag remains active on repeated calls
            assert is_deprecation_window_active() is True

        warning_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if "Deprecated" in str(call)
        ]
        assert (
            len(warning_calls) == 1
        ), f"Expected exactly 1 deprecation warning, got {len(warning_calls)}: {warning_calls}"
    finally:
        paths_module._reset_deprecation_warning_flag()


def test_get_legacy_dirs_includes_windows_installer_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows installer directory should be reported as a legacy source."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        local_app_data = Path(tmp_dir) / "la"
        local_app_data.mkdir()
        installer_path = local_app_data / "Programs" / WINDOWS_INSTALLER_DIR_NAME
        installer_path.mkdir(parents=True)

        monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
        monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
        monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)

        with (
            patch("sys.platform", "win32"),
            patch("mmrelay.paths.get_home_dir", return_value=Path("/current/home")),
            patch("mmrelay.paths.platformdirs.user_data_dir", return_value="/platform"),
        ):
            legacy_dirs = get_legacy_dirs()

        assert installer_path in legacy_dirs


def test_get_legacy_dirs_includes_env_and_docker_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy env paths and Docker legacy paths with artifacts should be detected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        base_dir = tmp_root / "base"
        data_dir = tmp_root / "data"
        docker_dir = tmp_root / "docker"
        base_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        docker_dir.mkdir(parents=True, exist_ok=True)
        (docker_dir / DEFAULT_CONFIG_FILENAME).write_text("", encoding="utf-8")

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


def test_resolve_all_paths_tracks_env_vars_and_home_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_all_paths should expose detected env vars and select expected home source."""
    import mmrelay.paths as paths_module

    paths_module._reset_deprecation_warning_flag()
    monkeypatch.delenv("MMRELAY_HOME", raising=False)
    monkeypatch.setenv("MMRELAY_BASE_DIR", "/legacy/base")
    monkeypatch.setenv("MMRELAY_DATA_DIR", "/legacy/data")
    monkeypatch.setenv("MMRELAY_LOG_PATH", "/legacy/logs/mmrelay.log")

    try:
        resolved = resolve_all_paths()
        detected = resolved["env_vars_detected"]
        assert detected["MMRELAY_BASE_DIR"] == "/legacy/base"
        assert detected["MMRELAY_DATA_DIR"] == "/legacy/data"
        assert detected["MMRELAY_LOG_PATH"] == "/legacy/logs/mmrelay.log"
        assert resolved["home_source"] == "MMRELAY_BASE_DIR env var"
    finally:
        paths_module._reset_deprecation_warning_flag()


def test_ensure_directories_creates_missing_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ensure_directories(create_missing=True) should create required paths."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        home = Path(tmp_dir)
        monkeypatch.setenv("MMRELAY_HOME", str(home))
        monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
        monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)
        monkeypatch.delenv("MMRELAY_LOG_PATH", raising=False)

        with patch("sys.platform", "linux"):
            ensure_directories(create_missing=True)

        matrix = home / "matrix"
        db = home / "database"
        logs = home / "logs"
        store = matrix / "store"
        plugins = home / "plugins"
        custom_plugins = plugins / "custom"
        community_plugins = plugins / "community"
        core_plugins = plugins / "core"

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


def test_ensure_directories_warns_when_missing_and_create_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        core_plugins = plugins / "core"

        monkeypatch.setenv("MMRELAY_HOME", str(home))
        monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
        monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)
        monkeypatch.delenv("MMRELAY_LOG_PATH", raising=False)

        with (
            patch("sys.platform", "linux"),
            patch("mmrelay.paths.logger") as mock_logger,
        ):
            ensure_directories(create_missing=False)

        expected_missing_dirs = [
            home,
            matrix,
            db,
            logs,
            store,
            plugins,
            custom_plugins,
            community_plugins,
            core_plugins,
        ]
        warning_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if "Directory missing" in str(call)
        ]
        assert len(warning_calls) == len(
            expected_missing_dirs
        ), f"Expected {len(expected_missing_dirs)} warnings, got {len(warning_calls)}"


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
            patch("mmrelay.paths.get_core_plugins_dir", return_value=root / "core"),
        ):
            assert get_plugin_code_dir("x", plugin_type="custom") == custom_root / "x"
            assert (
                get_plugin_code_dir("x", plugin_type="community")
                == community_root / "x"
            )
            discovered = get_plugin_code_dir("demo")
            assert discovered == community_root / "demo"
            core_path = get_plugin_code_dir("mesh_relay", plugin_type="core")
            # Core plugins are resolved from the bundled package, not MMRELAY_HOME
            assert core_path.name == "mesh_relay"
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
        "log_file": f"/h/logs/{LOG_FILENAME}",
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


def test_has_mmrelay_artifacts_oserror_on_directory_scan() -> None:
    """OSError during scandir of a directory marker should be caught gracefully."""
    import os as _os

    from mmrelay.paths import _has_mmrelay_artifacts

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        plugin_dir = root / "plugins"
        plugin_dir.mkdir(parents=True, exist_ok=True)

        original_scandir = _os.scandir

        def _faulty_scandir(path):
            if str(path) == str(plugin_dir):
                raise OSError("permission denied")
            return original_scandir(path)

        with patch("os.scandir", side_effect=_faulty_scandir):
            result = _has_mmrelay_artifacts(root)

        assert result is False


def test_dir_has_entries_oserror() -> None:
    """_dir_has_entries returns False when scandir raises OSError."""
    from mmrelay.paths import _dir_has_entries

    with tempfile.TemporaryDirectory() as tmp_dir:
        p = Path(tmp_dir)
        with patch("os.scandir", side_effect=OSError("fail")):
            assert _dir_has_entries(p) is False


def test_dir_has_entries_not_a_directory() -> None:
    """_dir_has_entries returns False for non-directory paths."""
    from mmrelay.paths import _dir_has_entries

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "file.txt"
        file_path.write_text("hello")
        assert _dir_has_entries(file_path) is False


def test_score_home_credentials_and_store_and_plugins() -> None:
    """_score_mmrelay_home_state should score credentials, store content, and plugins content."""
    from mmrelay.constants.app import (
        DATABASE_DIRNAME,
        DATABASE_FILENAME,
        LOG_FILENAME,
        LOGS_DIRNAME,
        MATRIX_DIRNAME,
        PLUGINS_DIRNAME,
        STORE_DIRNAME,
    )
    from mmrelay.constants.config import DEFAULT_CONFIG_FILENAME
    from mmrelay.paths import _score_mmrelay_home_state

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / DEFAULT_CONFIG_FILENAME).write_text("test: true")
        (root / MATRIX_DIRNAME / "credentials.json").parent.mkdir(parents=True)
        (root / MATRIX_DIRNAME / "credentials.json").write_text("{}")
        (root / DATABASE_DIRNAME).mkdir()
        (root / DATABASE_DIRNAME / DATABASE_FILENAME).write_text("")
        (root / MATRIX_DIRNAME / STORE_DIRNAME).mkdir(parents=True)
        (root / MATRIX_DIRNAME / STORE_DIRNAME / "key.db").write_bytes(b"\x00")
        (root / PLUGINS_DIRNAME).mkdir()
        (root / PLUGINS_DIRNAME / "my_plugin.py").write_text("")
        (root / LOGS_DIRNAME).mkdir()
        (root / LOGS_DIRNAME / LOG_FILENAME).write_text("log")

        score = _score_mmrelay_home_state(root)
        assert score >= 6 + 6 + 6 + 3 + 2 + 1 + 1


def test_score_home_nonexistent() -> None:
    """_score_mmrelay_home_state returns 0 for nonexistent directory."""
    from mmrelay.paths import _score_mmrelay_home_state

    assert _score_mmrelay_home_state(Path("/nonexistent_dir_xyz_123")) == 0


def test_get_e2ee_store_dir_on_windows() -> None:
    """get_e2ee_store_dir should raise E2EENotSupportedError on Windows."""
    from mmrelay.paths import E2EENotSupportedError, get_e2ee_store_dir

    with patch("mmrelay.paths.sys.platform", "win32"):
        with pytest.raises(E2EENotSupportedError):
            get_e2ee_store_dir()


def test_validate_plugin_name_empty() -> None:
    """_validate_plugin_name raises ValueError for empty string."""
    from mmrelay.paths import _validate_plugin_name

    with pytest.raises(ValueError, match="Invalid plugin name"):
        _validate_plugin_name("")


def test_validate_plugin_name_dot() -> None:
    """_validate_plugin_name raises ValueError for '.' name."""
    from mmrelay.paths import _validate_plugin_name

    with pytest.raises(ValueError, match="Invalid plugin name"):
        _validate_plugin_name(".")


def test_validate_plugin_name_dotdot() -> None:
    """_validate_plugin_name raises ValueError for '..' name."""
    from mmrelay.paths import _validate_plugin_name

    with pytest.raises(ValueError, match="Invalid plugin name"):
        _validate_plugin_name("..")


def test_validate_plugin_name_absolute() -> None:
    """_validate_plugin_name raises ValueError for absolute path."""
    from mmrelay.paths import _validate_plugin_name

    with pytest.raises(ValueError, match="Invalid plugin name"):
        _validate_plugin_name("/etc/passwd")


def test_validate_plugin_name_traversal() -> None:
    """_validate_plugin_name raises ValueError for path traversal."""
    from mmrelay.paths import _validate_plugin_name

    with pytest.raises(ValueError, match="Invalid plugin name"):
        _validate_plugin_name("../etc/passwd")


def test_validate_plugin_subdir_traversal() -> None:
    """_validate_plugin_subdir raises ValueError for path traversal."""
    from mmrelay.paths import _validate_plugin_subdir

    with pytest.raises(ValueError, match="Invalid plugin subdir"):
        _validate_plugin_subdir("../../etc")


def test_validate_plugin_subdir_dot_component() -> None:
    """_validate_plugin_subdir raises ValueError for path with '..' component."""
    from mmrelay.paths import _validate_plugin_subdir

    with pytest.raises(ValueError, match="Invalid plugin subdir"):
        _validate_plugin_subdir("foo/../bar")


def test_get_plugin_code_dir_fallback_to_core() -> None:
    """get_plugin_code_dir falls back to core when plugin not found in custom/community."""
    from mmrelay.paths import get_plugin_code_dir

    with (
        patch("mmrelay.paths.get_custom_plugins_dir", return_value=Path("/custom")),
        patch(
            "mmrelay.paths.get_community_plugins_dir", return_value=Path("/community")
        ),
    ):
        result = get_plugin_code_dir("nonexistent_plugin")
        assert "plugins" in str(result)
        assert "nonexistent_plugin" in str(result)


def test_get_plugin_data_dir_core_type() -> None:
    """get_plugin_data_dir with core type uses core plugins dir."""
    from mmrelay.paths import get_plugin_data_dir

    with patch("mmrelay.paths.get_core_plugins_dir", return_value=Path("/core")):
        result = get_plugin_data_dir("test", plugin_type="core")
        assert result == Path("/core/test/data")


def test_get_plugin_data_dir_fallback_core() -> None:
    """get_plugin_data_dir falls back to core when plugin not found."""
    from mmrelay.paths import get_plugin_data_dir

    with (
        patch("mmrelay.paths.get_custom_plugins_dir", return_value=Path("/custom")),
        patch(
            "mmrelay.paths.get_community_plugins_dir", return_value=Path("/community")
        ),
        patch("mmrelay.paths.get_core_plugins_dir", return_value=Path("/core")),
    ):
        result = get_plugin_data_dir("nonexistent_plugin")
        assert result == Path("/core/nonexistent_plugin/data")


def test_get_legacy_dirs_platform_user_data_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing platform user data dir should be reported as a legacy dir."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        platform_data = Path(tmp_dir) / "platform_data"
        platform_data.mkdir()
        (platform_data / "config.yaml").write_text("test: true")

        monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
        monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)

        with (
            patch("mmrelay.paths.get_home_dir", return_value=Path("/current/home")),
            patch(
                "mmrelay.paths.platformdirs.user_data_dir",
                return_value=str(platform_data),
            ),
        ):
            legacy = get_legacy_dirs()

        assert platform_data in legacy


def test_get_legacy_dirs_platform_user_data_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RuntimeError from platformdirs should be handled gracefully."""
    monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
    monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)

    with (
        patch("mmrelay.paths.get_home_dir", return_value=Path("/current/home")),
        patch(
            "mmrelay.paths.platformdirs.user_data_dir",
            side_effect=RuntimeError("fail"),
        ),
    ):
        legacy = get_legacy_dirs()

    assert isinstance(legacy, list)


def test_get_legacy_dirs_windows_installer_path_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OSError when checking Windows installer path should be handled."""
    monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
    monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", "/fake/localappdata")

    installer_path_str = str(
        Path("/fake/localappdata") / "Programs" / WINDOWS_INSTALLER_DIR_NAME
    )

    original_exists = Path.exists

    def _selective_exists(self):
        if installer_path_str in str(self):
            raise OSError("access denied")
        return original_exists(self)

    with (
        patch("sys.platform", "win32"),
        patch("mmrelay.paths.get_home_dir", return_value=Path("/current/home")),
        patch("mmrelay.paths.platformdirs.user_data_dir", return_value="/platform"),
        patch.object(Path, "exists", _selective_exists),
    ):
        legacy = get_legacy_dirs()

    assert isinstance(legacy, list)


def test_get_legacy_dirs_base_dir_same_as_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MMRELAY_BASE_DIR pointing to same dir as home should be excluded."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        home = Path(tmp_dir)
        (home / "config.yaml").write_text("test: true")

        monkeypatch.setenv("MMRELAY_BASE_DIR", str(home))
        monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)

        with patch("mmrelay.paths.get_home_dir", return_value=home):
            legacy = get_legacy_dirs()

        assert home not in legacy


def test_get_legacy_dirs_data_dir_same_as_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MMRELAY_DATA_DIR pointing to same dir as home should be excluded."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        home = Path(tmp_dir)
        (home / "config.yaml").write_text("test: true")

        monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
        monkeypatch.setenv("MMRELAY_DATA_DIR", str(home))

        with patch("mmrelay.paths.get_home_dir", return_value=home):
            legacy = get_legacy_dirs()

        assert home not in legacy


def test_get_legacy_dirs_docker_path_same_as_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker legacy path equal to home should be excluded."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        home = Path(tmp_dir)
        (home / "config.yaml").write_text("test: true")

        monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
        monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)

        with (
            patch("mmrelay.paths.get_home_dir", return_value=home),
            patch("mmrelay.paths.DOCKER_LEGACY_PATHS", [str(home)]),
        ):
            legacy = get_legacy_dirs()

        assert home not in legacy


def test_get_config_paths_platformdirs_runtime_error() -> None:
    """RuntimeError from platformdirs in get_config_paths should be handled."""
    with (
        patch("mmrelay.paths.get_home_dir", return_value=Path("/home")),
        patch.object(Path, "cwd", return_value=Path("/home")),
        patch.object(Path, "home", return_value=Path("/home")),
        patch(
            "mmrelay.paths.platformdirs.user_data_dir",
            side_effect=RuntimeError("fail"),
        ),
    ):
        paths = get_config_paths()

    assert isinstance(paths, list)


def test_get_config_paths_platform_user_data_exists_and_differs() -> None:
    """Platform user data dir that exists and differs from home should be included."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        platform_data = Path(tmp_dir) / "platform_data"
        platform_data.mkdir()

        with (
            patch("mmrelay.paths.get_home_dir", return_value=Path("/home")),
            patch.object(Path, "cwd", return_value=Path("/cwd")),
            patch.object(Path, "home", return_value=Path("/home")),
            patch(
                "mmrelay.paths.platformdirs.user_data_dir",
                return_value=str(platform_data),
            ),
        ):
            paths = get_config_paths()

        assert isinstance(paths, list)
        assert any(str(platform_data) in str(p) for p in paths)


def test_resolve_all_paths_home_source_data_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_all_paths should identify MMRELAY_DATA_DIR as home source."""
    import mmrelay.paths as paths_module

    paths_module._reset_deprecation_warning_flag()
    monkeypatch.delenv("MMRELAY_HOME", raising=False)
    monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
    monkeypatch.setenv("MMRELAY_DATA_DIR", "/legacy/data")

    try:
        resolved = resolve_all_paths()
        assert resolved["home_source"] == "MMRELAY_DATA_DIR env var"
    finally:
        paths_module._reset_deprecation_warning_flag()


def test_resolve_all_paths_home_source_platform_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_all_paths should identify Platform defaults when no env vars or CLI overrides."""
    import mmrelay.paths as paths_module

    paths_module._reset_deprecation_warning_flag()
    monkeypatch.delenv("MMRELAY_HOME", raising=False)
    monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
    monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)
    monkeypatch.delenv("MMRELAY_LOG_PATH", raising=False)

    try:
        resolved = resolve_all_paths()
        assert resolved["home_source"] == "Platform defaults"
    finally:
        paths_module._reset_deprecation_warning_flag()


def test_resolve_all_paths_cli_data_dir_source() -> None:
    """resolve_all_paths should identify CLI --data-dir as home source."""
    from mmrelay.paths import set_home_override

    set_home_override("/data/home", source="--data-dir")
    try:
        resolved = resolve_all_paths()
        assert resolved["home_source"] == "CLI (--data-dir)"
    finally:
        reset_home_override()


def test_get_home_dir_windows_weak_installer_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Weak installer artifacts (score < 6) with empty platform should prefer platform home."""
    from mmrelay.paths import get_home_dir

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        local_app_data = root / "LocalAppData"
        installer_path = local_app_data / "Programs" / WINDOWS_INSTALLER_DIR_NAME
        installer_logs = installer_path / "logs"
        installer_logs.mkdir(parents=True, exist_ok=True)
        (installer_logs / LOG_FILENAME).write_text("log\n", encoding="utf-8")

        platform_home = root / "PlatformData" / "mmrelay"

        monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
        monkeypatch.delenv("MMRELAY_HOME", raising=False)
        monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
        monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)
        with (
            patch("sys.platform", "win32"),
            patch(
                "mmrelay.paths.platformdirs.user_data_dir",
                return_value=str(platform_home),
            ),
        ):
            result = get_home_dir()

        assert result == platform_home


def test_get_home_dir_windows_installer_with_score_above_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installer with score >= 6 and empty platform should be preferred."""
    from mmrelay.paths import get_home_dir

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        local_app_data = root / "LocalAppData"
        installer_path = local_app_data / "Programs" / WINDOWS_INSTALLER_DIR_NAME
        installer_path.mkdir(parents=True, exist_ok=True)
        (installer_path / DEFAULT_CONFIG_FILENAME).write_text(
            "test: true\n", encoding="utf-8"
        )

        platform_home = root / "PlatformData" / "mmrelay"

        monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
        monkeypatch.delenv("MMRELAY_HOME", raising=False)
        monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
        monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)
        with (
            patch("sys.platform", "win32"),
            patch(
                "mmrelay.paths.platformdirs.user_data_dir",
                return_value=str(platform_home),
            ),
        ):
            result = get_home_dir()

        assert result == installer_path


def test_deprecation_window_not_active_when_home_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deprecation window should not be active when MMRELAY_HOME is set."""
    import mmrelay.paths as paths_module

    paths_module._reset_deprecation_warning_flag()
    monkeypatch.setenv("MMRELAY_HOME", "/home")
    monkeypatch.setenv("MMRELAY_BASE_DIR", "/base")

    try:
        assert is_deprecation_window_active() is False
    finally:
        paths_module._reset_deprecation_warning_flag()


def test_deprecation_window_not_active_no_legacy_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deprecation window should not be active when no legacy vars are set."""
    import mmrelay.paths as paths_module

    paths_module._reset_deprecation_warning_flag()
    monkeypatch.delenv("MMRELAY_HOME", raising=False)
    monkeypatch.delenv("MMRELAY_BASE_DIR", raising=False)
    monkeypatch.delenv("MMRELAY_DATA_DIR", raising=False)

    try:
        assert is_deprecation_window_active() is False
    finally:
        paths_module._reset_deprecation_warning_flag()
