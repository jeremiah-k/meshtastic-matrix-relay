"""Runtime environment helpers for MMRelay."""

from __future__ import annotations

import logging
import os

from mmrelay.constants.app import PROC_COMM_PATH_TEMPLATE, PROC_SELF_STATUS_PATH
from mmrelay.constants.formats import DEFAULT_TEXT_ENCODING
from mmrelay.constants.network import SYSTEMD_INIT_SYSTEM

_logger: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    """Get or create the module logger lazily to avoid import cycles."""
    global _logger
    if _logger is None:
        from mmrelay.log_utils import get_logger

        _logger = get_logger(__name__)
    return _logger


def is_running_as_service() -> bool:
    """
    Return True if the current process appears to be running as a systemd service.

    Checks whether the INVOCATION_ID environment variable is set (systemd-provided) and, if not,
    inspects /proc/self/status to find the parent PID and then /proc/<ppid>/comm to compare the
    parent process name against the expected systemd init binary name. Invalid parent PIDs and
    proc-filesystem access failures return False.
    Returns:
        bool: True when running under a systemd service, otherwise False.
    """

    if os.environ.get("INVOCATION_ID"):
        return True

    try:
        with open(PROC_SELF_STATUS_PATH, encoding=DEFAULT_TEXT_ENCODING) as status_file:
            for line in status_file:
                if line.startswith("PPid:"):
                    ppid = int(line.split()[1])
                    if ppid <= 0:
                        return False
                    with open(
                        PROC_COMM_PATH_TEMPLATE.format(ppid=ppid),
                        encoding=DEFAULT_TEXT_ENCODING,
                    ) as comm_file:
                        return comm_file.read().strip() == SYSTEMD_INIT_SYSTEM
    except (FileNotFoundError, PermissionError) as e:
        _get_logger().debug(
            "Service detection unavailable via proc filesystem",
            extra={"error_type": type(e).__name__},
        )
    except (ValueError, IndexError) as e:
        _get_logger().debug(
            "Service detection failed via proc filesystem",
            exc_info=True,
            extra={"error_type": type(e).__name__},
        )

    return False
