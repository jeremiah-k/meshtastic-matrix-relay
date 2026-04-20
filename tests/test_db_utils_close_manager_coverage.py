"""
Tests for _close_manager_safely and _get_db_manager edge cases in db_utils.py.

Covers:
- _close_manager_safely lines 594-595: KeyboardInterrupt/SystemExit re-raise
- _get_db_manager lines 778-779: RuntimeError when manager_to_return is None
"""

import pytest

from mmrelay.db_utils import (
    _close_manager_safely,
    _get_db_manager,
    _reset_db_manager,
    clear_db_path_cache,
)


@pytest.fixture(autouse=True)
def _reset_state():
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
        mock_mgr = type(
            "FakeMgr",
            (),
            {"close": lambda self: (_ for _ in ()).throw(KeyboardInterrupt("ctrl-c"))},
        )()
        with pytest.raises(KeyboardInterrupt):
            _close_manager_safely(mock_mgr)

    def test_system_exit_is_reraised(self):
        mock_mgr = type(
            "FakeMgr", (), {"close": lambda self: (_ for _ in ()).throw(SystemExit(1))}
        )()
        with pytest.raises(SystemExit):
            _close_manager_safely(mock_mgr)

    def test_generic_exception_is_logged(self):
        from unittest.mock import MagicMock

        mock_mgr = MagicMock()
        mock_mgr.close.side_effect = RuntimeError("close failed")
        _close_manager_safely(mock_mgr)
        mock_mgr.close.assert_called_once()

    def test_none_manager_is_noop(self):
        _close_manager_safely(None)


class TestGetDbManagerNullReturn:
    """Tests for _get_db_manager RuntimeError on null manager_to_return."""

    def test_raises_runtime_error_when_manager_cannot_be_created(self):
        from unittest.mock import patch

        with (
            patch(
                "mmrelay.db_utils.get_db_path",
                return_value="/nonexistent/path/db.sqlite",
            ),
            patch(
                "mmrelay.db_utils._resolve_database_options",
                return_value=(True, 5000, {}),
            ),
        ):
            with patch(
                "mmrelay.db_utils.DatabaseManager",
                side_effect=RuntimeError("init failed"),
            ):
                _reset_db_manager()
                with pytest.raises(
                    RuntimeError, match="init failed|initialization failed"
                ):
                    _get_db_manager()
