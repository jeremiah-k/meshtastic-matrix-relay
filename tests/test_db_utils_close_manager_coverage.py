"""
Tests for _close_manager_safely and _get_db_manager edge cases in db_utils.py.

Covers:
- _close_manager_safely lines 594-595: KeyboardInterrupt/SystemExit re-raise
- _get_db_manager lines 778-779: RuntimeError when manager_to_return is None
"""

from unittest.mock import MagicMock, patch

import pytest

from mmrelay.db_utils import (
    _close_manager_safely,
    _get_db_manager,
    _reset_db_manager,
    clear_db_path_cache,
)


@pytest.fixture(autouse=True)
def _reset_state():
    """
    Reset shared database-related global state before a test and restore it after the test.

    Performs setup by clearing the database path cache, resetting the cached DatabaseManager, and setting mmrelay.db_utils.config to None; after the test, clears the cache and resets the manager again to ensure a clean state for subsequent tests.
    """
    clear_db_path_cache()
    _reset_db_manager()
    import mmrelay.db_utils

    mmrelay.db_utils.config = None
    yield
    clear_db_path_cache()
    _reset_db_manager()


class TestCloseManagerSafelyKeyboardInterrupt:
    """Tests for _close_manager_safely re-raising KeyboardInterrupt."""

    def test_keyboard_interrupt_is_reraised(self):
        mock_mgr = MagicMock()
        mock_mgr.close.side_effect = KeyboardInterrupt("ctrl-c")
        with pytest.raises(KeyboardInterrupt):
            _close_manager_safely(mock_mgr)

    def test_system_exit_is_reraised(self):
        mock_mgr = MagicMock()
        mock_mgr.close.side_effect = SystemExit(1)
        with pytest.raises(SystemExit):
            _close_manager_safely(mock_mgr)

    def test_generic_exception_is_logged(self, caplog):
        import logging
        import sqlite3

        import mmrelay.db_utils

        mock_mgr = MagicMock()
        mock_mgr.close.side_effect = sqlite3.Error("close failed")
        with caplog.at_level(logging.WARNING, logger=mmrelay.db_utils.logger.name):
            mmrelay.db_utils.logger.addHandler(caplog.handler)
            _close_manager_safely(mock_mgr)
        mock_mgr.close.assert_called_once()
        assert any(
            "Failed to close DatabaseManager" in rec.getMessage()
            for rec in caplog.records
        )

    def test_runtime_error_is_reraised(self):
        mock_mgr = MagicMock()
        mock_mgr.close.side_effect = RuntimeError("close failed")
        with pytest.raises(RuntimeError, match="close failed"):
            _close_manager_safely(mock_mgr)

    def test_none_manager_is_noop(self):
        _close_manager_safely(None)


class TestGetDbManagerNullReturn:
    """Tests for _get_db_manager RuntimeError on null manager_to_return."""

    def test_raises_runtime_error_when_manager_cannot_be_created(self):
        """
        Verifies that _get_db_manager raises a RuntimeError when DatabaseManager initialization fails.

        Patches get_db_path and _resolve_database_options to fixed values and forces DatabaseManager construction to raise RuntimeError; resets the cached manager state and asserts that calling _get_db_manager() raises a RuntimeError with a message matching "init failed" or "initialization failed".
        """
        with (
            patch(
                "mmrelay.db_utils.get_db_path",
                return_value="/nonexistent/path/db.sqlite",
            ),
            patch(
                "mmrelay.db_utils._resolve_database_options",
                return_value=(True, 5000, {}),
            ),
            patch(
                "mmrelay.db_utils.DatabaseManager",
                side_effect=RuntimeError("init failed"),
            ),
        ):
            _reset_db_manager()
            with pytest.raises(RuntimeError, match="init failed|initialization failed"):
                _get_db_manager()
