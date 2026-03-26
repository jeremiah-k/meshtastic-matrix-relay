#!/usr/bin/env python3
"""Tests for runtime environment helpers."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from typing import IO, Any, Callable
from unittest.mock import mock_open, patch

import mmrelay.runtime_utils as runtime_utils


def _open_side_effect_for_proc(
    status_text: str, comm_text: str
) -> Callable[[str, Any, Any], IO[Any]]:
    """Build an open() side effect for /proc status and comm file reads."""
    status_handle = mock_open(read_data=status_text).return_value
    comm_handle = mock_open(read_data=comm_text).return_value
    target_comm_path = runtime_utils.PROC_COMM_PATH_TEMPLATE.format(ppid=1)

    def _side_effect(path: str, *args: Any, **kwargs: Any) -> IO[Any]:
        if path == runtime_utils.PROC_SELF_STATUS_PATH:
            return status_handle
        if path == target_comm_path:
            return comm_handle
        raise FileNotFoundError(path)

    return _side_effect


def test_is_running_as_service_true_when_invocation_id_set() -> None:
    """INVOCATION_ID should short-circuit to service-mode detection."""
    with patch.dict(os.environ, {"INVOCATION_ID": "unit-test"}, clear=True):
        assert runtime_utils.is_running_as_service() is True


def test_is_running_as_service_true_when_parent_is_systemd() -> None:
    """Parent process comm value of systemd should be detected as service mode."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch(
            "builtins.open",
            side_effect=_open_side_effect_for_proc(
                status_text="Name:\tpython\nPPid:\t1\n",
                comm_text=f"{runtime_utils.SYSTEMD_INIT_SYSTEM}\n",
            ),
        ),
    ):
        assert runtime_utils.is_running_as_service() is True


def test_is_running_as_service_false_when_parent_is_not_systemd() -> None:
    """Non-systemd parent process should not be treated as service mode."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch(
            "builtins.open",
            side_effect=_open_side_effect_for_proc(
                status_text="Name:\tpython\nPPid:\t1\n",
                comm_text="bash\n",
            ),
        ),
    ):
        assert runtime_utils.is_running_as_service() is False


def test_is_running_as_service_false_on_proc_parse_or_file_errors() -> None:
    """Parsing/file errors should safely return False."""
    with patch.dict(os.environ, {}, clear=True):
        with patch(
            "builtins.open",
            side_effect=_open_side_effect_for_proc(
                status_text="Name:\tpython\nPPid:\tnot-a-number\n",
                comm_text="ignored\n",
            ),
        ):
            assert runtime_utils.is_running_as_service() is False

        with patch("builtins.open", side_effect=FileNotFoundError("missing /proc")):
            assert runtime_utils.is_running_as_service() is False


def test_is_running_as_service_false_when_status_has_no_ppid_line() -> None:
    """Missing PPid field in /proc/self/status should return False."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch(
            "builtins.open",
            return_value=mock_open(read_data="Name:\tpython\n").return_value,
        ),
    ):
        assert runtime_utils.is_running_as_service() is False
