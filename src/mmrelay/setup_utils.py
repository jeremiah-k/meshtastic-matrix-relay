"""
Setup utilities for MMRelay.

This module provides simple functions for managing the systemd user service
and generating configuration files.
"""

import importlib.resources

# Import version from package
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from mmrelay.constants.database import PROGRESS_COMPLETE, PROGRESS_TOTAL_STEPS
from mmrelay.tools import get_service_template_path

# Resolve systemctl path dynamically with fallback
SYSTEMCTL = shutil.which("systemctl") or "/usr/bin/systemctl"


def _quote_if_needed(path: str) -> str:
    """Quote executable paths that contain spaces for systemd compatibility."""
    return f'"{path}"' if " " in path else path


def get_resolved_exec_cmd() -> str:
    """
    Resolve the command string to invoke MMRelay for use in a systemd ExecStart line.

    Prefers an `mmrelay` executable found on PATH; if none is found, falls back to invoking the current Python interpreter with `-m mmrelay`.

    Returns:
        Command string suitable for a systemd `ExecStart` line: the `mmrelay` executable path (quoted if it contains spaces) when available, otherwise the current Python interpreter path followed by `-m mmrelay` (interpreter path quoted if needed).
    """
    mmrelay_path = shutil.which("mmrelay")
    if mmrelay_path:
        return _quote_if_needed(mmrelay_path)
    py = _quote_if_needed(sys.executable)
    return f"{py} -m mmrelay"


def get_executable_path() -> str:
    """
    Determines the command used to invoke mmrelay and prints a user-visible message indicating whether a standalone executable was found.

    If no executable is found the function prints a warning to stderr about falling back to invoking mmrelay as a Python module; if an executable is found it prints the executable path to stdout.

    Returns:
        str: Filesystem path to the `mmrelay` executable, or a Python invocation string using the current interpreter (e.g. `"<python> -m mmrelay"`).
    """
    resolved_cmd = get_resolved_exec_cmd()
    if " -m mmrelay" in resolved_cmd:
        print(
            "Warning: Could not find mmrelay executable in PATH. Using current Python interpreter.",
            file=sys.stderr,
        )
    else:
        print(f"Found mmrelay executable at: {resolved_cmd}")
    return resolved_cmd


def get_resolved_exec_start(
    args_suffix: str = " --config %h/.mmrelay/config.yaml --logfile %h/.mmrelay/logs/mmrelay.log",
) -> str:
    """
    Construct the systemd `ExecStart=` line for the mmrelay service.

    Parameters:
        args_suffix (str): Command-line arguments appended to the resolved mmrelay command.
            May include systemd specifiers such as `%h` for the user home directory.
            Defaults to " --config %h/.mmrelay/config.yaml --logfile %h/.mmrelay/logs/mmrelay.log".

    Returns:
        str: A single-line string beginning with `ExecStart=` containing the resolved executable
             invocation followed by the provided argument suffix.
    """
    return f"ExecStart={get_resolved_exec_cmd()}{args_suffix}"


def get_user_service_path() -> Path:
    """
    Return the filesystem path for the current user's mmrelay systemd unit file.

    Returns:
        Path: Path to the user unit, typically '~/.config/systemd/user/mmrelay.service'.
    """
    service_dir = Path.home() / ".config" / "systemd" / "user"
    return service_dir / "mmrelay.service"


def service_exists() -> bool:
    """
    Determine whether the per-user systemd unit file for mmrelay is present.

    Returns:
        True if the user's mmrelay.service file exists, False otherwise.
    """
    return get_user_service_path().exists()


def print_service_commands() -> None:
    """Print the commands for controlling the systemd user service."""
    print("  systemctl --user start mmrelay.service    # Start the service")
    print("  systemctl --user stop mmrelay.service     # Stop the service")
    print("  systemctl --user restart mmrelay.service  # Restart the service")
    print("  systemctl --user status mmrelay.service   # Check service status")


def wait_for_service_start() -> None:
    """
    Wait up to ten seconds for the per-user mmrelay systemd service to become active.

    Periodically checks the service state and exits early if the service becomes active (checks allow early exit after approximately five seconds). When running interactively a Rich spinner and elapsed-time display are shown; when running without a TTY the function performs the same timed checks without UI.
    """
    import time

    from mmrelay.runtime_utils import is_running_as_service

    running_as_service = is_running_as_service()
    if not running_as_service:
        try:
            from rich.progress import (
                Progress,
                SpinnerColumn,
                TextColumn,
                TimeElapsedColumn,
            )
        except Exception:
            running_as_service = True

    # Create a Rich progress display with spinner and elapsed time
    if not running_as_service:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold green]Starting mmrelay service..."),
            TimeElapsedColumn(),
            transient=True,
        ) as progress:
            # Add a task that will run for approximately 10 seconds
            task = progress.add_task("Starting", total=PROGRESS_TOTAL_STEPS)

            # Update progress over ~10 seconds
            step = max(1, PROGRESS_TOTAL_STEPS // 10)
            for i in range(10):
                time.sleep(1)
                progress.update(
                    task, completed=min(PROGRESS_TOTAL_STEPS, step * (i + 1))
                )

                # Check if service is active after 5 seconds to potentially finish early
                if i >= 5 and is_service_active():
                    progress.update(task, completed=PROGRESS_COMPLETE)
                    break
    else:
        # Simple fallback when running as service
        for i in range(10):
            time.sleep(1)
            if i >= 5 and is_service_active():
                break


def read_service_file() -> str | None:
    """
    Retrieve the contents of the user's mmrelay systemd service unit file.

    Returns:
        The file contents decoded as UTF-8, or `None` if the service file does not exist.
    """
    service_path = get_user_service_path()
    if service_path.exists():
        return service_path.read_text(encoding="utf-8")
    return None


def get_template_service_path() -> str | None:
    """
    Locate the mmrelay systemd service template on disk.

    Searches a deterministic list of candidate locations (package directory, package/tools,
    sys.prefix share paths, user local share (~/.local/share), parent-directory development
    paths, and ./tools) and returns the first existing path.

    If no template is found, the function prints a warning to stderr listing all
    attempted locations and returns None.

    Returns:
        str | None: Path to the found mmrelay.service template, or None if not found.
    """
    # Try to find the service template file
    package_dir = os.path.dirname(__file__)

    # Try to find the service template file in various locations
    template_paths = [
        # Check in the package directory (where it should be after installation)
        os.path.join(package_dir, "mmrelay.service"),
        # Check in a tools subdirectory of the package
        os.path.join(package_dir, "tools", "mmrelay.service"),
        # Check in the data files location (where it should be after installation)
        os.path.join(sys.prefix, "share", "mmrelay", "mmrelay.service"),
        os.path.join(sys.prefix, "share", "mmrelay", "tools", "mmrelay.service"),
        # Check in the user site-packages location
        os.path.join(
            os.path.expanduser("~"), ".local", "share", "mmrelay", "mmrelay.service"
        ),
        os.path.join(
            os.path.expanduser("~"),
            ".local",
            "share",
            "mmrelay",
            "tools",
            "mmrelay.service",
        ),
        # Check one level up from the package directory
        os.path.join(os.path.dirname(package_dir), "tools", "mmrelay.service"),
        # Check two levels up from the package directory (for development)
        os.path.join(
            os.path.dirname(os.path.dirname(package_dir)), "tools", "mmrelay.service"
        ),
        # Check in the repository root (for development)
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "tools",
            "mmrelay.service",
        ),
        # Check in the current directory (fallback)
        os.path.join(os.getcwd(), "tools", "mmrelay.service"),
    ]

    # Try each path until we find one that exists
    for path in template_paths:
        if os.path.exists(path):
            return path

    # If we get here, we couldn't find the template
    # Warning output to help diagnose issues
    print(
        "Warning: Could not find mmrelay.service in any of these locations:",
        file=sys.stderr,
    )
    for path in template_paths:
        print(f"  - {path}", file=sys.stderr)

    # If we get here, we couldn't find the template
    return None


def get_template_service_content() -> str:
    """
    Get the systemd service unit content to install for the user-level mmrelay service.

    Attempts to load a template in this order: 1) external template path returned by get_service_template_path(), 2) embedded package resource "mmrelay.service" from mmrelay.tools, 3) a second filesystem probe via get_template_service_path(). If none can be read, returns a built-in default service unit that includes a resolved ExecStart (from get_resolved_exec_start()), useful Environment settings, and standard Unit/Service/Install sections. Read/access errors are written to stderr.

    Returns:
        str: Complete service file content suitable for writing to the user service unit.
    """
    # Use the helper function to get the service template path
    template_path = get_service_template_path()

    if template_path and os.path.exists(template_path):
        # Read the template from file
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                service_template = f.read()
            return service_template
        except (OSError, IOError, UnicodeDecodeError) as e:
            print(f"Error reading service template file: {e}", file=sys.stderr)

    # If the helper function failed, try using importlib.resources directly
    try:
        service_template = (
            importlib.resources.files("mmrelay.tools")
            .joinpath("mmrelay.service")
            .read_text(encoding="utf-8")
        )
        return service_template
    except (FileNotFoundError, ImportError, OSError, UnicodeDecodeError) as e:
        print(
            f"Error accessing mmrelay.service via importlib.resources: {e}",
            file=sys.stderr,
        )

        # Fall back to the file path method
        template_path = get_template_service_path()
        if template_path:
            # Read the template from file
            try:
                with open(template_path, "r", encoding="utf-8") as f:
                    service_template = f.read()
                return service_template
            except (OSError, IOError, UnicodeDecodeError) as e:
                print(f"Error reading service template file: {e}", file=sys.stderr)

    # If we couldn't find or read the template file, use a default template
    print("Using default service template", file=sys.stderr)
    resolved_exec_start = get_resolved_exec_start()
    return f"""[Unit]
Description=MMRelay - Meshtastic <=> Matrix Relay
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# The mmrelay binary can be installed via pipx or pip
{resolved_exec_start}
WorkingDirectory=%h/.mmrelay
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1
Environment=LANG=C.UTF-8
# Ensure both pipx and pip environments are properly loaded
Environment=PATH=%h/.local/bin:%h/.local/pipx/venvs/mmrelay/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
"""


def is_service_enabled() -> bool:
    """
    Determine whether the user systemd service 'mmrelay.service' is enabled to start at login.

    This runs the user-level `systemctl --user is-enabled mmrelay.service` check and treats the service as enabled only if the command succeeds and its stdout is exactly "enabled".

    Returns:
        True if the service is enabled to start at login, False otherwise (including on errors).
    """
    try:
        result = subprocess.run(
            [SYSTEMCTL, "--user", "is-enabled", "mmrelay.service"],
            check=False,  # Don't raise an exception if the service is not enabled
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "enabled"
    except (OSError, subprocess.SubprocessError) as e:
        print(f"Warning: Failed to check service enabled status: {e}", file=sys.stderr)
        return False


def is_service_active() -> bool:
    """
    Determine whether the per-user systemd unit 'mmrelay.service' is currently active.

    If the check fails due to an OSError or subprocess error, a warning is printed to stderr and the function returns False.

    Returns:
        True if the service is active, False otherwise.
    """
    try:
        result = subprocess.run(
            [SYSTEMCTL, "--user", "is-active", "mmrelay.service"],
            check=False,  # Don't raise an exception if the service is not active
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError) as e:
        print(f"Warning: Failed to check service active status: {e}", file=sys.stderr)
        return False


def create_service_file() -> bool:
    """
    Create or update the per-user systemd unit file for MMRelay.

    Ensures the user systemd directory and MMRelay logs directory exist, obtains a service unit template, substitutes known placeholders (executable and config paths), normalizes the unit's ExecStart to the resolved MMRelay invocation while preserving any trailing arguments, and writes the final unit to ~/.config/systemd/user/mmrelay.service.

    Returns:
        bool: True if the service file was written successfully; False if a template could not be obtained or writing the file failed.
    """
    # Get executable paths once to avoid duplicate calls and output
    executable_path = get_executable_path()

    # Create service directory if it doesn't exist
    service_dir = get_user_service_path().parent
    service_dir.mkdir(parents=True, exist_ok=True)

    # Create logs directory if it doesn't exist
    logs_dir = Path.home() / ".mmrelay" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Get the template service content
    service_template = get_template_service_content()
    if not service_template:
        print("Error: Could not find service template file", file=sys.stderr)
        return False

    # Replace placeholders with actual values
    service_content = (
        service_template.replace(
            "WorkingDirectory=%h/meshtastic-matrix-relay",
            "# WorkingDirectory is not needed for installed package",
        )
        .replace(
            "%h/meshtastic-matrix-relay/.pyenv/bin/python %h/meshtastic-matrix-relay/main.py",
            executable_path,
        )
        .replace(
            "--config %h/.mmrelay/config/config.yaml",
            "--config %h/.mmrelay/config.yaml",
        )
    )

    # Normalize ExecStart: replace any mmrelay launcher with resolved command, preserving args
    pattern = re.compile(
        r'(?m)^\s*(ExecStart=)"?(?:'
        r"/usr/bin/env\s+mmrelay"
        r"|(?:\S*?[\\/])?mmrelay\b"
        r"|\S*\bpython(?:\d+(?:\.\d+)*)?(?:\.exe)?\b\s+-m\s+mmrelay"
        r')"?(\s.*)?$'
    )
    service_content = pattern.sub(
        lambda m: f"{m.group(1)}{executable_path}{m.group(2) or ''}",
        service_content,
    )

    # Write service file
    try:
        get_user_service_path().write_text(service_content, encoding="utf-8")
        print(f"Service file created at {get_user_service_path()}")
        return True
    except (IOError, OSError) as e:
        print(f"Error creating service file: {e}", file=sys.stderr)
        return False


def reload_daemon() -> bool:
    """
    Reload the current user's systemd manager to apply unit file changes.

    Runs the resolved systemctl with "--user daemon-reload" to request a daemon reload.

    Returns:
        bool: `True` if the daemon-reload command succeeded, `False` otherwise.
    """
    try:
        # Using resolved systemctl path
        subprocess.run([SYSTEMCTL, "--user", "daemon-reload"], check=True)
        print("Systemd user daemon reloaded")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error reloading systemd daemon: {e}", file=sys.stderr)
        return False
    except OSError as e:
        print(f"Error: {e}", file=sys.stderr)
        return False


def service_needs_update() -> tuple[bool, str]:
    """
    Determine whether the user's systemd unit for mmrelay should be updated.

    Performs these checks (in order) and reports the first failing condition:
    - No installed user service file exists.
    - The installed unit's ExecStart does not invoke mmrelay via an acceptable form (mmrelay on PATH, "/usr/bin/env mmrelay", or the current Python interpreter with "-m mmrelay").
    - The unit's Environment PATH lines do not include common user-bin locations ("%h/.local/pipx/venvs/mmrelay/bin" or "%h/.local/bin").
    - A template service file on disk exists and is newer than the installed unit.

    Returns:
        tuple: (needs_update: bool, reason: str) — `True` if an update is recommended or required, `False` if the installed service appears up to date. `reason` explains the decision or any error encountered.
    """
    # Check if service already exists
    existing_service = read_service_file()
    if not existing_service:
        return True, "No existing service file found"

    # Get the template service path
    template_path = get_template_service_path()

    # Get the acceptable executable paths
    mmrelay_path = shutil.which("mmrelay")
    acceptable_execs = [
        f"{_quote_if_needed(sys.executable)} -m mmrelay",
        "/usr/bin/env mmrelay",
    ]
    if mmrelay_path:
        acceptable_execs.append(_quote_if_needed(mmrelay_path))

    # Check if the ExecStart line in the existing service file contains an acceptable executable form
    exec_start_line = next(
        (
            line
            for line in existing_service.splitlines()
            if line.strip().startswith("ExecStart=")
        ),
        None,
    )

    if not exec_start_line:
        return True, "Service file is missing ExecStart line"

    if not any(exec_str in exec_start_line for exec_str in acceptable_execs):
        return (
            True,
            "Service file does not use an acceptable executable "
            f"({' or '.join(acceptable_execs)}).",
        )

    # Check if the PATH environment includes common user-bin locations
    # Look specifically in Environment lines, not the entire file
    environment_lines = [
        line
        for line in existing_service.splitlines()
        if line.strip().startswith("Environment=")
    ]
    path_in_environment = any(
        "%h/.local/pipx/venvs/mmrelay/bin" in line or "%h/.local/bin" in line
        for line in environment_lines
    )
    if not path_in_environment:
        return True, "Service PATH does not include common user-bin locations"

    # Check if the service file has been modified recently
    service_path = get_user_service_path()
    if template_path and os.path.exists(template_path) and os.path.exists(service_path):
        try:
            template_mtime = os.path.getmtime(template_path)
            service_mtime = os.path.getmtime(service_path)
        except OSError:
            return True, "Unable to stat template or service file"
        if template_mtime > service_mtime:
            return True, "Template service file is newer than installed service file"

    return False, "Service file is up to date"


def check_loginctl_available() -> bool:
    """
    Check whether the `loginctl` executable is present on PATH and runnable.

    Attempts to locate `loginctl` on PATH and invoke it with `--version`; reports unavailable if not found or if invocation fails.

    Returns:
        `true` if `loginctl` is present on PATH and `loginctl --version` exits with code 0, `false` otherwise.
    """
    path = shutil.which("loginctl")
    if not path:
        return False
    try:
        result = subprocess.run(
            [path, "--version"], check=False, capture_output=True, text=True
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        print(f"Warning: Failed to check loginctl availability: {e}", file=sys.stderr)
        return False


def check_lingering_enabled() -> bool:
    """
    Determine whether systemd user lingering is enabled for the current user.

    Checks for a usable `loginctl` and queries the current account; returns `True` only if the query succeeds and reports `Linger=yes`.

    Returns:
        bool: `True` if lingering is enabled for the current user, `False` otherwise.
    """
    try:
        import getpass

        username = (
            os.environ.get("USER") or os.environ.get("USERNAME") or getpass.getuser()
        )
        if not username:
            print(
                "Error checking lingering status: could not determine current user",
                file=sys.stderr,
            )
            return False
        loginctl = shutil.which("loginctl")
        if not loginctl:
            return False
        result = subprocess.run(
            [loginctl, "show-user", username, "--property=Linger"],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and "Linger=yes" in result.stdout
    except (OSError, subprocess.SubprocessError, KeyError, RuntimeError) as e:
        print(f"Error checking lingering status: {e}", file=sys.stderr)
        return False


def enable_lingering() -> bool:
    """
    Enable systemd user lingering for the current user.

    Determines the current username from environment variables or getpass and invokes
    `sudo loginctl enable-linger <user>`, printing status messages to stdout/stderr.

    Returns:
        True if lingering was enabled successfully, False otherwise.
    """
    try:
        import getpass

        username = (
            os.environ.get("USER") or os.environ.get("USERNAME") or getpass.getuser()
        )
        if not username:
            print(
                "Error enabling lingering: could not determine current user",
                file=sys.stderr,
            )
            return False
        print(f"Enabling lingering for user {username}...")
        result = subprocess.run(
            ["sudo", "loginctl", "enable-linger", username],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("Lingering enabled successfully")
            return True
        else:
            print(f"Error enabling lingering: {result.stderr}", file=sys.stderr)
            return False
    except (OSError, subprocess.SubprocessError) as e:
        print(f"Error enabling lingering: {e}", file=sys.stderr)
        return False


def install_service() -> bool:
    """
    Install or update the MMRelay systemd user service and interactively guide the user through creating or updating the unit file, enabling lingering, enabling the service at login, and starting or restarting the service.

    Prompts the user for confirmation before performing updates, enabling lingering (requires sudo), enabling the service to start at boot, and starting or restarting the service. Prints a summary of service status and suggested management commands on completion.

    Returns:
        True if the installation or update process completes successfully, False otherwise.
    """
    # Check if service already exists
    existing_service = read_service_file()
    service_path = get_user_service_path()

    # Check if the service needs to be updated
    update_needed, reason = service_needs_update()

    # Check if the service is already installed and if it needs updating
    if existing_service:
        print(f"A service file already exists at {service_path}")

        if update_needed:
            print(f"The service file needs to be updated: {reason}")
            try:
                user_input = input("Do you want to update the service file? (y/n): ")
                if not user_input.lower().startswith("y"):
                    print("Service update cancelled.")
                    print_service_commands()
                    return True
            except (EOFError, KeyboardInterrupt):
                print("\nInput cancelled. Proceeding with default behavior.")
                print("Service update cancelled.")
                print_service_commands()
                return True
        else:
            print(f"No update needed for the service file: {reason}")
    else:
        print(f"No service file found at {service_path}")
        print("A new service file will be created.")

    # Create or update service file if needed
    if not existing_service or update_needed:
        if not create_service_file():
            return False

        # Reload daemon (continue even if this fails)
        if not reload_daemon():
            print(
                "Warning: Failed to reload systemd daemon. You may need to run 'systemctl --user daemon-reload' manually.",
                file=sys.stderr,
            )

        if existing_service:
            print("Service file updated successfully")
        else:
            print("Service file created successfully")

    # We don't need to validate the config here as it will be validated when the service starts

    # Check if loginctl is available
    loginctl_available = check_loginctl_available()
    if loginctl_available:
        # Check if user lingering is enabled
        lingering_enabled = check_lingering_enabled()
        if not lingering_enabled:
            print(
                "\nUser lingering is not enabled. This is required for the service to start automatically at boot."
            )
            print(
                "Lingering allows user services to run even when you're not logged in."
            )
            try:
                user_input = input(
                    "Do you want to enable lingering for your user? (requires sudo) (y/n): "
                )
                should_enable_lingering = user_input.lower().startswith("y")
            except (EOFError, KeyboardInterrupt):
                print("\nInput cancelled. Skipping lingering setup.")
                should_enable_lingering = False

            if should_enable_lingering:
                enable_lingering()

    # Check if the service is already enabled
    service_enabled = is_service_enabled()
    if service_enabled:
        print("The service is already enabled to start at boot.")
    else:
        print("The service is not currently enabled to start at boot.")
        try:
            user_input = input(
                "Do you want to enable the service to start at boot? (y/n): "
            )
            enable_service = user_input.lower().startswith("y")
        except (EOFError, KeyboardInterrupt):
            print("\nInput cancelled. Skipping service enable.")
            enable_service = False

        if enable_service:
            try:
                subprocess.run(
                    [SYSTEMCTL, "--user", "enable", "mmrelay.service"],
                    check=True,
                )
                print("Service enabled successfully")
                service_enabled = True
            except subprocess.CalledProcessError as e:
                print(f"Error enabling service: {e}", file=sys.stderr)
            except OSError as e:
                print(f"Error: {e}", file=sys.stderr)

    # Check if the service is already running
    service_active = is_service_active()
    if service_active:
        print("The service is already running.")
        try:
            user_input = input("Do you want to restart the service? (y/n): ")
            restart_service = user_input.lower().startswith("y")
        except (EOFError, KeyboardInterrupt):
            print("\nInput cancelled. Skipping service restart.")
            restart_service = False

        if restart_service:
            try:
                subprocess.run(
                    [SYSTEMCTL, "--user", "restart", "mmrelay.service"],
                    check=True,
                )
                print("Service restarted successfully")
                # Wait for the service to restart
                wait_for_service_start()
                # Show service status
                show_service_status()
            except subprocess.CalledProcessError as e:
                print(f"Error restarting service: {e}", file=sys.stderr)
            except OSError as e:
                print(f"Error: {e}", file=sys.stderr)
    else:
        print("The service is not currently running.")
        try:
            user_input = input("Do you want to start the service now? (y/n): ")
            start_now = user_input.lower().startswith("y")
        except (EOFError, KeyboardInterrupt):
            print("\nInput cancelled. Skipping service start.")
            start_now = False

        if start_now:
            if start_service():
                # Wait for the service to start
                wait_for_service_start()
                # Show service status
                show_service_status()
                print("Service started successfully")
            else:
                print("\nWarning: Failed to start the service. Please check the logs.")

    # Print a summary of the service status
    print("\nService Status Summary:")
    print(f"  Service File: {service_path}")
    print(f"  Enabled at Boot: {'Yes' if service_enabled else 'No'}")
    if loginctl_available:
        print(f"  User Lingering: {'Yes' if check_lingering_enabled() else 'No'}")
    print(f"  Currently Running: {'Yes' if is_service_active() else 'No'}")
    print("\nService Management Commands:")
    print_service_commands()

    return True


def start_service() -> bool:
    """
    Start the user-level systemd service for MMRelay.

    Attempts to run `SYSTEMCTL --user start mmrelay.service`. Returns True if the command exits successfully.
    On failure the function prints an error message to stderr and returns False.

    Returns:
        bool: True when the service was started successfully; False on error.
    """
    try:
        subprocess.run([SYSTEMCTL, "--user", "start", "mmrelay.service"], check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error starting service: {e}", file=sys.stderr)
        return False
    except OSError as e:
        print(f"Error: {e}", file=sys.stderr)
        return False


def show_service_status() -> bool:
    """
    Display the user's systemd status for the mmrelay service.

    Prints the service status to stdout on success and prints an error message to stderr on failure.

    Returns:
        `true` if the status was printed successfully, `false` otherwise.
    """
    try:
        result = subprocess.run(
            [SYSTEMCTL, "--user", "status", "mmrelay.service"],
            check=True,
            capture_output=True,
            text=True,
        )
        print("\nService Status:")
        print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Could not get service status: {e}", file=sys.stderr)
        return False
    except OSError as e:
        print(f"Error: {e}", file=sys.stderr)
        return False
