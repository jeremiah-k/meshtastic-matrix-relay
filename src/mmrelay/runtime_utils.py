"""Runtime environment helpers for MMRelay."""

from __future__ import annotations

import os

from mmrelay.constants.network import SYSTEMD_INIT_SYSTEM


def is_running_as_service() -> bool:
    """Return True when running under a systemd service."""

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
