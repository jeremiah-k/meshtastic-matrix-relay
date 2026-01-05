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
from mmrelay.log_utils import get_logger
from mmrelay.tools import get_service_template_path

# Resolve systemctl path dynamically with fallback
SYSTEMCTL = shutil.which("systemctl") or "/usr/bin/systemctl"
logger = get_logger(name="Setup")


def _quote_if_needed(path: str) -> str:
    """
    Quote a filesystem path or command if it contains spaces to make it safe for systemd unit files.

    Parameters:
        path (str): The path or command string to evaluate.

    Returns:
        str: The original path if it contains no spaces; otherwise the path wrapped in double quotes.
    """
    return f'"{path}"' if " " in path else path


def get_resolved_exec_cmd() -> str:
    """
    Determine the command to invoke MMRelay for inclusion in a systemd ExecStart line.

    Prefers an `mmrelay` executable found on PATH; if none is available, falls back to the current Python interpreter with the `-m mmrelay` module.

    Returns:
        A command string suitable for a systemd `ExecStart` line: the `mmrelay` executable path (quoted if it contains spaces) when available, otherwise the current Python interpreter path followed by `-m mmrelay` (interpreter path quoted if needed).
    """
    mmrelay_path = shutil.which("mmrelay")
    if mmrelay_path:
        return _quote_if_needed(mmrelay_path)
    py = _quote_if_needed(sys.executable)
    return f"{py} -m mmrelay"


def get_executable_path() -> str:
    """
    Resolve the command used to invoke MMRelay and report whether a standalone executable was found.

    Logs a warning if falling back to running MMRelay via the current Python interpreter; otherwise logs the resolved executable path.

    Returns:
        str: The filesystem path to the `mmrelay` executable, or a Python invocation string using the current interpreter (e.g. `"<python> -m mmrelay"`).
    """
    resolved_cmd = get_resolved_exec_cmd()
    if " -m mmrelay" in resolved_cmd:
        logger.warning(
            "Could not find mmrelay executable in PATH. Using current Python interpreter."
        )
    else:
        logger.info("Found mmrelay executable at: %s", resolved_cmd)
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
    Compute the path to the current user's MMRelay systemd unit file.

    Returns:
        Path: Path to the user unit file, typically '~/.config/systemd/user/mmrelay.service'.
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


def log_service_commands() -> None:
    """Log the commands for controlling the systemd user service."""
    logger.info("  systemctl --user start mmrelay.service    # Start the service")
    logger.info("  systemctl --user stop mmrelay.service     # Stop the service")
    logger.info("  systemctl --user restart mmrelay.service  # Restart the service")
    logger.info("  systemctl --user status mmrelay.service   # Check service status")


def wait_for_service_start() -> None:
    """
    Waits up to ten seconds for the per-user mmrelay systemd service to become active.

    When running interactively, displays a spinner and elapsed-time indicator; when not interactive, performs the same timed checks without UI. The function exits early if the service becomes active (checks allow early exit starting after five seconds); otherwise it returns after roughly ten seconds.
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
        with Progress(  # type: ignore[misc]
            SpinnerColumn(),  # type: ignore[misc]
            TextColumn("[bold green]Starting mmrelay service..."),  # type: ignore[misc]
            TimeElapsedColumn(),  # type: ignore[misc]
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

    If no template is found, the function logs a warning listing all
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
    logger.warning("Could not find mmrelay.service in any of these locations:")
    for path in template_paths:
        logger.warning("  - %s", path)

    # If we get here, we couldn't find the template
    return None


def get_template_service_content() -> str:
    """
    Provide the systemd service unit content to install for the user-level mmrelay service.

    Attempts to load a template from disk or package resources and, if none are available or readable, falls back to a built-in default service unit that includes a resolved ExecStart and sane Environment settings. Read/access errors are logged.

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
        except (OSError, UnicodeDecodeError):
            logger.exception("Error reading service template file")

    # If the helper function failed, try using importlib.resources directly
    try:
        service_template = (
            importlib.resources.files("mmrelay.tools")
            .joinpath("mmrelay.service")
            .read_text(encoding="utf-8")
        )
        return service_template
    except (FileNotFoundError, ImportError, OSError, UnicodeDecodeError):
        logger.exception("Error accessing mmrelay.service via importlib.resources")

        # Fall back to the file path method
        fallback_template_path = get_template_service_path()
        if fallback_template_path:
            # Read the template from file
            try:
                with open(fallback_template_path, "r", encoding="utf-8") as f:
                    service_template = f.read()
                return service_template
            except (OSError, UnicodeDecodeError):
                logger.exception("Error reading service template file")

    # If we couldn't find or read the template file, use a default template
    logger.warning("Using default service template")
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
    Check whether the user systemd unit 'mmrelay.service' is enabled to start at login.

    Performs `systemctl --user is-enabled mmrelay.service` and treats the service as enabled only if the command exits with status 0 and its stdout equals "enabled".

    Returns:
        `True` if the service is enabled to start at login, `False` otherwise.
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
        logger.warning("Failed to check service enabled status: %s", e)
        return False


def is_service_active() -> bool:
    """
    Determine whether the per-user systemd unit 'mmrelay.service' is currently active.

    Returns:
        True if the service's state is "active", False otherwise. If an OS-level or subprocess error occurs while checking, the function returns False.
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
        logger.warning("Failed to check service active status: %s", e)
        return False


def create_service_file() -> bool:
    """
    Create or update the per-user systemd unit file for MMRelay.

    Ensures required user service and log directories exist, loads a service template, replaces placeholders with resolved paths (including normalizing the ExecStart line to the resolved MMRelay invocation), and writes the resulting unit file to the current user's systemd directory.

    Returns:
        bool: `True` if the service file was written successfully, `False` if a template could not be obtained or writing the file failed.
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
        logger.error("Could not find service template file")
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

    service_path = get_user_service_path()
    try:
        service_path.write_text(service_content, encoding="utf-8")
    except OSError:
        logger.exception("Error creating service file")
        return False
    else:
        logger.info("Service file created at %s", service_path)
        return True


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
    except subprocess.CalledProcessError as e:
        logger.exception("Error reloading systemd daemon (exit code %d)", e.returncode)
        return False
    except OSError:
        logger.exception("Error running systemctl daemon-reload")
        return False
    else:
        logger.info("Systemd user daemon reloaded")
        return True


def service_needs_update() -> tuple[bool, str]:
    """
    Determine whether the per-user systemd unit for mmrelay requires updating.

    Performs the following checks (first failing condition wins):
    - No installed user service file exists.
    - The installed unit's ExecStart does not use an acceptable invocation (mmrelay on PATH, "/usr/bin/env mmrelay", or the current Python interpreter with "-m mmrelay").
    - The unit's Environment PATH lines do not include common user-bin locations ("%h/.local/pipx/venvs/mmrelay/bin" or "%h/.local/bin").
    - A template service file on disk exists and has a newer modification time than the installed unit.

    Returns:
        tuple: (needs_update, reason)
            needs_update (bool): `True` if an update is recommended or required, `False` if the installed service appears up to date.
            reason (str): A short explanation for the decision or any error encountered.
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
    Check whether loginctl is available and runnable on PATH.

    Locates the `loginctl` executable and invokes `loginctl --version` to verify it can be executed.

    Returns:
        `true` if `loginctl` is present on PATH and the command exits with code 0, `false` otherwise.
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
        logger.warning("Failed to check loginctl availability: %s", e)
        return False


def check_lingering_enabled() -> bool:
    """
    Determine whether systemd user lingering is enabled for the current user.

    Checks for a usable `loginctl` and queries the systemd user account; if the query reports `Linger=yes`, lingering is considered enabled.

    Returns:
        bool: `True` if lingering is enabled for the current user, `False` otherwise.
    """
    try:
        import getpass

        username = (
            os.environ.get("USER") or os.environ.get("USERNAME") or getpass.getuser()
        )
        if not username:
            logger.error(
                "Error checking lingering status: could not determine current user"
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
    except (OSError, subprocess.SubprocessError, KeyError, RuntimeError):
        logger.exception("Error checking lingering status")
        return False


def enable_lingering() -> bool:
    """
    Enable systemd user lingering for the current user.

    This attempts to determine the current username and runs `sudo loginctl enable-linger <user>`, logging progress and error messages.

    Returns:
        True if lingering was enabled successfully, False otherwise.
    """
    try:
        import getpass

        username = (
            os.environ.get("USER") or os.environ.get("USERNAME") or getpass.getuser()
        )
        if not username:
            logger.error("Error enabling lingering: could not determine current user")
            return False
        logger.info("Enabling lingering for user %s...", username)
        sudo_path = shutil.which("sudo")
        loginctl_path = shutil.which("loginctl")
        if not sudo_path or not loginctl_path:
            logger.error("Error enabling lingering: sudo or loginctl not found")
            return False
        result = subprocess.run(
            [sudo_path, loginctl_path, "enable-linger", username],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            logger.info("Lingering enabled successfully")
            return True
        else:
            logger.error("Error enabling lingering: %s", result.stderr)
            return False
    except (OSError, subprocess.SubprocessError):
        logger.exception("Error enabling lingering")
        return False


def install_service() -> bool:
    """
    Install or update the MMRelay systemd user service and guide interactive setup.

    Creates or updates the user unit file, reloads the systemd daemon, optionally enables user lingering, optionally enables the service at login, and optionally starts or restarts the service based on user confirmation. Logs progress and a final status summary.

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
        logger.info("A service file already exists at %s", service_path)

        if update_needed:
            logger.info("The service file needs to be updated: %s", reason)
            try:
                user_input = input("Do you want to update the service file? (y/n): ")
                if not user_input.lower().startswith("y"):
                    logger.info("Service update cancelled.")
                    log_service_commands()
                    return True
            except (EOFError, KeyboardInterrupt):
                logger.info("\nInput cancelled. Proceeding with default behavior.")
                logger.info("Service update cancelled.")
                log_service_commands()
                return True
        else:
            logger.info("No update needed for the service file: %s", reason)
    else:
        logger.info("No service file found at %s", service_path)
        logger.info("A new service file will be created.")

    # Create or update service file if needed
    if not existing_service or update_needed:
        if not create_service_file():
            return False

        # Reload daemon (continue even if this fails)
        if not reload_daemon():
            logger.warning(
                "Failed to reload systemd daemon. You may need to run 'systemctl --user daemon-reload' manually."
            )

        if existing_service:
            logger.info("Service file updated successfully")
        else:
            logger.info("Service file created successfully")

    # We don't need to validate the config here as it will be validated when the service starts

    # Check if loginctl is available
    loginctl_available = check_loginctl_available()
    if loginctl_available:
        # Check if user lingering is enabled
        lingering_enabled = check_lingering_enabled()
        if not lingering_enabled:
            logger.info(
                "\nUser lingering is not enabled. This is required for the service to start automatically at boot."
            )
            logger.info(
                "Lingering allows user services to run even when you're not logged in."
            )
            try:
                user_input = input(
                    "Do you want to enable lingering for your user? (requires sudo) (y/n): "
                )
                should_enable_lingering = user_input.lower().startswith("y")
            except (EOFError, KeyboardInterrupt):
                logger.info("\nInput cancelled. Skipping lingering setup.")
                should_enable_lingering = False

            if should_enable_lingering:
                enable_lingering()

    # Check if the service is already enabled
    service_enabled = is_service_enabled()
    if service_enabled:
        logger.info("The service is already enabled to start at boot.")
    else:
        logger.info("The service is not currently enabled to start at boot.")
        try:
            user_input = input(
                "Do you want to enable the service to start at boot? (y/n): "
            )
            enable_service = user_input.lower().startswith("y")
        except (EOFError, KeyboardInterrupt):
            logger.info("\nInput cancelled. Skipping service enable.")
            enable_service = False

        if enable_service:
            try:
                subprocess.run(
                    [SYSTEMCTL, "--user", "enable", "mmrelay.service"],
                    check=True,
                )
                logger.info("Service enabled successfully")
                service_enabled = True
            except subprocess.CalledProcessError as e:
                logger.exception("Error enabling service (exit code %d)", e.returncode)
            except OSError:
                logger.exception("OS error while enabling service")

    # Check if the service is already running
    service_active = is_service_active()
    if service_active:
        logger.info("The service is already running.")
        try:
            user_input = input("Do you want to restart the service? (y/n): ")
            restart_service = user_input.lower().startswith("y")
        except (EOFError, KeyboardInterrupt):
            logger.info("\nInput cancelled. Skipping service restart.")
            restart_service = False

        if restart_service:
            try:
                subprocess.run(
                    [SYSTEMCTL, "--user", "restart", "mmrelay.service"],
                    check=True,
                )
                logger.info("Service restarted successfully")
                # Wait for the service to restart
                wait_for_service_start()
                # Show service status
                show_service_status()
            except subprocess.CalledProcessError as e:
                logger.exception(
                    "Error restarting service (exit code %d)", e.returncode
                )
            except OSError:
                logger.exception("OS error while restarting service")
    else:
        logger.info("The service is not currently running.")
        try:
            user_input = input("Do you want to start the service now? (y/n): ")
            start_now = user_input.lower().startswith("y")
        except (EOFError, KeyboardInterrupt):
            logger.info("\nInput cancelled. Skipping service start.")
            start_now = False

        if start_now:
            if start_service():
                # Wait for the service to start
                wait_for_service_start()
                # Show service status
                show_service_status()
                logger.info("Service started successfully")
            else:
                logger.warning("\nFailed to start the service. Please check the logs.")

    # Log a summary of the service status
    logger.info("\nService Status Summary:")
    logger.info("  Service File: %s", service_path)
    logger.info("  Enabled at Boot: %s", "Yes" if service_enabled else "No")
    if loginctl_available:
        logger.info(
            "  User Lingering: %s",
            "Yes" if check_lingering_enabled() else "No",
        )
    logger.info("  Currently Running: %s", "Yes" if is_service_active() else "No")
    logger.info("\nService Management Commands:")
    log_service_commands()

    return True


def start_service() -> bool:
    """
    Start the user-level systemd service for MMRelay.

    Attempts to start the per-user unit "mmrelay.service" and reports whether the operation succeeded.

    Returns:
        bool: True if the service was started successfully, False otherwise.
    """
    try:
        subprocess.run([SYSTEMCTL, "--user", "start", "mmrelay.service"], check=True)
        return True
    except subprocess.CalledProcessError:
        logger.exception("Error starting service")
        return False
    except OSError:
        logger.exception("Error starting mmrelay service")
        return False


def show_service_status() -> bool:
    """
    Display the user's systemd status for the mmrelay service.

    Logs the service status output; if systemctl cannot be executed, an error is logged.

    Returns:
        True if the status command executed and its output was displayed, False if an OS-level error prevented running systemctl.
    """
    try:
        result = subprocess.run(
            [SYSTEMCTL, "--user", "status", "mmrelay.service"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        logger.exception("Error displaying service status")
        return False
    else:
        logger.info("\nService Status:")
        logger.info(result.stdout if result.stdout else result.stderr)
        return True
