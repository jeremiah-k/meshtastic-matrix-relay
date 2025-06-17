import os

def is_running_as_service():
    """
    Check if the application is running as a systemd service.
    This is used to determine whether to show Rich progress indicators.

    Returns:
        bool: True if running as a service, False otherwise
    """
    # Check for INVOCATION_ID environment variable (set by systemd)
    if os.environ.get("INVOCATION_ID"):
        return True

    # Check if parent process is systemd
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("PPid:"):
                    ppid = int(line.split()[1])
                    with open(f"/proc/{ppid}/comm") as p:
                        return p.read().strip() == "systemd"
    except (FileNotFoundError, PermissionError, ValueError):
        pass

    return False
