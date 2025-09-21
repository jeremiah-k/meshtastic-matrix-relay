"""Runtime environment helpers for MMRelay."""

from __future__ import annotations

import os

from mmrelay.constants.network import SYSTEMD_INIT_SYSTEM


def is_running_as_service() -> bool:
    """
    Return True if the current process appears to be running as a systemd service.
    
    Checks whether the INVOCATION_ID environment variable is set (systemd-provided) and, if not,
    inspects /proc/self/status to find the parent PID and then /proc/<ppid>/comm to compare the
    parent process name against the expected systemd init binary name. If any file access,
    permission, or parsing errors occur, the function returns False.
    Returns:
        bool: True when running under a systemd service, otherwise False.
    """

    if os.environ.get("INVOCATION_ID"):
        return True

    try:
        with open("/proc/self/status", encoding="utf-8") as status_file:
            for line in status_file:
                if line.startswith("PPid:"):
                    ppid = int(line.split()[1])
                    with open(f"/proc/{ppid}/comm", encoding="utf-8") as comm_file:
                        return comm_file.read().strip() == SYSTEMD_INIT_SYSTEM
    except (FileNotFoundError, PermissionError, ValueError):
        pass

    return False
