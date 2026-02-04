"""Tests to improve coverage for paths.py.

Docstrings are necessary: Test docstrings follow pytest conventions and document the purpose
of each test case. Inline comments explain test assertions and expected behavior for clarity.
"""

import os

import pytest

from mmrelay.paths import (
    ensure_directories,
    get_home_dir,
    get_plugin_data_dir,
    is_deprecation_window_active,
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
