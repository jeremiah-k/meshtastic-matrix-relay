"""
Setup utilities for MMRelay.

This module provides simple functions for managing the systemd user service
and generating configuration files.
"""

import importlib.resources

# Import version from package
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from mmrelay.constants.app import (
    APP_NAME,
    LOGS_DIRNAME,
    SERVICE_RESTART_SECONDS,
    SYSTEMD_SERVICE_FILENAME,
    SYSTEMD_USER_DIR,
)
from mmrelay.constants.database import PROGRESS_COMPLETE, PROGRESS_TOTAL_STEPS
from mmrelay.constants.migration import DEFAULT_SERVICE_ARGS_SUFFIX
from mmrelay.constants.network import SYSTEMCTL_FALLBACK
from mmrelay.log_utils import get_logger

# Resolve systemctl path dynamically with fallback
SYSTEMCTL = shutil.which("systemctl") or SYSTEMCTL_FALLBACK
logger = get_logger(name="Setup")


def _quote_if_needed(path: str) -> str:
    """
    Wrap the input path in double quotes when it contains spaces so it is safe for embedding in systemd unit files.

    Parameters:
        path (str): Filesystem path or command string to evaluate.

    Returns:
        str: The original `path` if it contains no spaces; otherwise `path` wrapped in double quotes.
    """
    return f'"{path}"' if " " in path else path


def _get_service_template_candidates() -> list[str]:
    """
    Get the list of candidate paths for the service template file.

    Returns paths in priority order:
    1. MMRELAY_SERVICE_OVERRIDE environment variable (if set)
    2. Package directory
    3. Package tools subdirectory
    4. sys.prefix share paths
    5. User local share paths
    6. Development paths
    """
    package_dir = os.path.dirname(__file__)

    candidates = [
        os.path.join(package_dir, SYSTEMD_SERVICE_FILENAME),
        os.path.join(package_dir, "tools", SYSTEMD_SERVICE_FILENAME),
        os.path.join(sys.prefix, "share", APP_NAME, SYSTEMD_SERVICE_FILENAME),
        os.path.join(sys.prefix, "share", APP_NAME, "tools", SYSTEMD_SERVICE_FILENAME),
        os.path.join(
            os.path.expanduser("~"),
            ".local",
            "share",
            APP_NAME,
            SYSTEMD_SERVICE_FILENAME,
        ),
        os.path.join(
            os.path.expanduser("~"),
            ".local",
            "share",
            APP_NAME,
            "tools",
            SYSTEMD_SERVICE_FILENAME,
        ),
        os.path.join(os.path.dirname(package_dir), "tools", SYSTEMD_SERVICE_FILENAME),
        os.path.join(
            os.path.dirname(os.path.dirname(package_dir)),
            "tools",
            SYSTEMD_SERVICE_FILENAME,
        ),
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "tools",
            SYSTEMD_SERVICE_FILENAME,
        ),
    ]

    override_path = os.environ.get("MMRELAY_SERVICE_OVERRIDE")
    if override_path:
        candidates.insert(
            0,
            os.path.abspath(os.path.expanduser(os.path.expandvars(override_path))),
        )

    return candidates


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
    args_suffix: str = DEFAULT_SERVICE_ARGS_SUFFIX,
) -> str:
    """
    Construct the systemd `ExecStart=` line for the mmrelay service.

    Parameters:
        args_suffix (str): Command-line arguments appended to the resolved mmrelay command.
            May include systemd specifiers such as `%h` for the user home directory.
            Defaults to DEFAULT_SERVICE_ARGS_SUFFIX (e.g., "--home %h/.mmrelay").

    Returns:
        str: A single-line string beginning with `ExecStart=` containing the resolved executable
             invocation followed by the provided argument suffix.
    """
    stripped_suffix = args_suffix.lstrip()
    cmd = get_resolved_exec_cmd()
    if stripped_suffix:
        return f"ExecStart={cmd} {stripped_suffix}"
    return f"ExecStart={cmd}"


def get_user_service_path() -> Path:
    """
    Compute the path to the current user's MMRelay systemd unit file.

    Returns:
        Path: Path to the user unit file, typically '~/.config/systemd/user/mmrelay.service'.
    """
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        xdg_path = Path(xdg_config_home).expanduser()
        if xdg_path.is_absolute():
            service_dir = xdg_path / "systemd" / "user"
        else:
            logger.warning(
                "Ignoring non-absolute XDG_CONFIG_HOME=%s; falling back to %s",
                xdg_config_home,
                SYSTEMD_USER_DIR,
            )
            service_dir = Path.home() / SYSTEMD_USER_DIR
    else:
        service_dir = Path.home() / SYSTEMD_USER_DIR
    return service_dir / SYSTEMD_SERVICE_FILENAME


def service_exists() -> bool:
    """
    Determine whether the per-user systemd unit file for mmrelay is present.

    Returns:
        True if the user's mmrelay.service file exists, False otherwise.
    """
    return get_user_service_path().exists()


def log_service_commands() -> None:
    """Log the commands for controlling the systemd user service."""
    logger.info(
        "  %s --user start %s      # Start the service",
        SYSTEMCTL,
        SYSTEMD_SERVICE_FILENAME,
    )
    logger.info(
        "  %s --user stop %s       # Stop the service",
        SYSTEMCTL,
        SYSTEMD_SERVICE_FILENAME,
    )
    logger.info(
        "  %s --user restart %s    # Restart the service",
        SYSTEMCTL,
        SYSTEMD_SERVICE_FILENAME,
    )
    logger.info(
        "  %s --user status %s     # Check service status",
        SYSTEMCTL,
        SYSTEMD_SERVICE_FILENAME,
    )


def wait_for_service_start() -> None:
    """
    Wait up to ten seconds for the per-user mmrelay systemd service to become active.

    When running in an interactive environment this may display a spinner and elapsed-time indicator; in non-interactive contexts it performs the same timed checks without UI. The function exits early if the service becomes active (checks allow early exit beginning after approximately five seconds).
    """
    import time

    from mmrelay.runtime_utils import is_running_as_service

    progress_cls: type[Any] | None = None
    spinner_cls: type[Any] | None = None
    text_cls: type[Any] | None = None
    elapsed_cls: type[Any] | None = None
    running_as_service = is_running_as_service()
    if not running_as_service:
        try:
            from rich.progress import Progress as rich_progress
            from rich.progress import SpinnerColumn as rich_spinner
            from rich.progress import TextColumn as rich_text
            from rich.progress import TimeElapsedColumn as rich_elapsed

            progress_cls = rich_progress
            spinner_cls = rich_spinner
            text_cls = rich_text
            elapsed_cls = rich_elapsed
        except ImportError:
            running_as_service = True

    # Create a Rich progress display with spinner and elapsed time
    if (
        not running_as_service
        and progress_cls is not None
        and spinner_cls is not None
        and text_cls is not None
        and elapsed_cls is not None
    ):
        with progress_cls(
            spinner_cls(),
            text_cls("[bold green]Starting mmrelay service..."),
            elapsed_cls(),
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


def get_service_template_path() -> str | None:
    """Alias for get_template_service_path for backward compatibility with tests."""
    return get_template_service_path()


def get_template_service_path() -> str | None:
    """
    Locate the mmrelay systemd service template on disk.

    Searches a deterministic list of candidate locations (package directory, package/tools,
    sys.prefix share paths, user local share (~/.local/share), and parent-directory development
    paths) and returns the first existing path.

    If no template is found, the function logs a warning listing all
    attempted locations and returns None.

    Returns:
        str | None: Path to the found mmrelay.service template, or None if not found.
    """
    template_paths = _get_service_template_candidates()

    for path in template_paths:
        if os.path.exists(path):
            return path

    logger.warning(
        "Could not find %s in any of these locations:", SYSTEMD_SERVICE_FILENAME
    )
    for path in template_paths:
        logger.warning("  - %s", path)

    return None


def get_template_service_content() -> str:
    """
    Provide the systemd service unit content to install for the user-level mmrelay service.

    Attempts to load a template from disk or package resources and, if none are available or readable, falls back to a built-in default service unit that includes a resolved ExecStart and sane Environment settings. Read/access errors are logged.

    Returns:
        str: Complete service file content suitable for writing to the user service unit.
    """
    # Use compatibility alias to support existing tests/callers that patch it.
    template_path = get_service_template_path()

    if template_path:
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
            .joinpath(SYSTEMD_SERVICE_FILENAME)
            .read_text(encoding="utf-8")
        )
        return service_template
    except (FileNotFoundError, ImportError, OSError, UnicodeDecodeError):
        logger.exception(
            "Error accessing %s via importlib.resources", SYSTEMD_SERVICE_FILENAME
        )

    # If we couldn't find or read the template file, use a default template
    logger.warning("Using default service template")
    resolved_exec_start = get_resolved_exec_start()
    return f"""[Unit]
Description=MMRelay - Meshtastic <=> Matrix Relay
After=network-online.target time-sync.target
Wants=network-online.target time-sync.target

[Service]
Type=simple
# The mmrelay binary can be installed via pipx or pip
{resolved_exec_start}
WorkingDirectory=%h/.mmrelay
Restart=on-failure
RestartSec={SERVICE_RESTART_SECONDS}
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
            [SYSTEMCTL, "--user", "is-enabled", SYSTEMD_SERVICE_FILENAME],
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
    Check whether the per-user systemd unit `mmrelay.service` is currently active.

    Returns:
        `True` if the service's state is "active", `False` otherwise. If an OS-level or subprocess error occurs while checking, the function returns `False`.
    """
    try:
        result = subprocess.run(
            [SYSTEMCTL, "--user", "is-active", SYSTEMD_SERVICE_FILENAME],
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

    Ensures the user's service and log directories exist, obtains a service template, substitutes the working directory and executable invocation (normalizing the ExecStart line to the resolved MMRelay command), and writes the resulting unit file into the current user's systemd user directory.

    Returns:
        bool: `True` if the unit file was written successfully, `False` if a template could not be obtained or writing the file failed.
    """
    # Get executable paths once to avoid duplicate calls and output
    executable_path = get_executable_path()

    # Create service directory if it doesn't exist
    service_dir = get_user_service_path().parent
    service_dir.mkdir(parents=True, exist_ok=True)

    # Create logs directory if it doesn't exist
    logs_dir = Path.home() / f".{APP_NAME}" / LOGS_DIRNAME
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
    Determine whether the per-user systemd unit file for mmrelay should be updated.

    This function is primarily a runtime-correctness validator: it checks whether the
    installed service file can actually work correctly with the current mmrelay version.
    Normalization guidance for common migration scenarios is a secondary concern; the
    function respects operator choice for custom unit shapes and does not enforce a
    house style beyond what is required for correct operation.

    Correctness checks (always enforced):
    - Legacy --config/--logfile flags are flagged because they indicate an incompatible
      configuration model that will not work with v1.3+.
    - Home configuration must be present (--home or MMRELAY_HOME) to ensure the service
      can locate state correctly.
    - A recognizable launcher or explicit custom path must be present.

    Normalization guidance (heuristic, not enforced):
    - PATH hardening for common user-bin locations is suggested when the unit relies on
      PATH-based mmrelay lookup, but custom launchers are not flagged.

    Policy notes:
    - Explicit custom launchers (absolute paths or systemd specifiers like %h) are trusted
      because the operator has made an intentional choice that we cannot meaningfully validate.
    - This function is NOT a style checker; it will not flag operationally valid custom
      units that do not match the template layout.

    Returns:
        tuple: (needs_update, reason)
            needs_update (bool): `True` if an update is recommended or required, `False` if the installed service appears up to date.
            reason (str): Short explanation for the decision or an error encountered.
    """
    # Check if service already exists
    existing_service = read_service_file()
    if not existing_service:
        return True, "No existing service file found"

    # Get the template service path
    template_path = get_template_service_path()

    # Check if the ExecStart line exists
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

    exec_start_value = exec_start_line.split("=", 1)[1].strip()
    if not exec_start_value:
        return True, "Service file has empty ExecStart command"

    environment_lines = [
        line
        for line in existing_service.splitlines()
        if line.strip().startswith("Environment=")
    ]

    # Tokenize ExecStart first for accurate flag detection
    try:
        exec_tokens = shlex.split(exec_start_value)
    except ValueError:
        return True, "Service file has invalid ExecStart command"
    if not exec_tokens:
        return True, "Service file has empty ExecStart command"

    # Check if the service file is using legacy flags (--config, --logfile) instead of --home
    # This ensures migration to v1.3 unified path model
    if "--config" in exec_start_line or "--logfile" in exec_start_line:
        return (
            True,
            "Service file uses legacy --config/--logfile flags (update to --home)",
        )

    # Accept either --home in ExecStart or MMRELAY_HOME in Environment for compatibility.
    # Use token-level matching to avoid false positives on flags like --home-dir and
    # require non-empty values for --home/MMRELAY_HOME.
    has_home_flag = False
    for index, token in enumerate(exec_tokens):
        if token == "--home":
            if index + 1 < len(exec_tokens):
                next_token = exec_tokens[index + 1].strip()
                if next_token and not next_token.startswith("-"):
                    has_home_flag = True
                    break
            continue
        if token.startswith("--home="):
            home_value = token.split("=", 1)[1].strip()
            if home_value:
                has_home_flag = True
                break

    def _iter_env_assignments(raw_line: str) -> list[tuple[str, str]]:
        line = raw_line.strip()
        if line.startswith("Environment="):
            line = line[len("Environment=") :].strip()
        if not line:
            return []
        try:
            tokens = shlex.split(line)
        except ValueError:
            tokens = line.split()

        assignments: list[tuple[str, str]] = []
        for token in tokens:
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            normalized_key = key.strip().strip('"').strip("'")
            normalized_value = value.strip().strip('"').strip("'")
            if normalized_key:
                assignments.append((normalized_key, normalized_value))
        return assignments

    has_home_env = any(
        key == "MMRELAY_HOME" and bool(value)
        for line in environment_lines
        for key, value in _iter_env_assignments(line)
    )

    cmd_token = exec_tokens[0]
    cmd_basename = os.path.basename(cmd_token)

    env_target_token: str | None = None
    env_path_assignment: str | None = None
    has_home_env_in_exec = False
    if cmd_basename == "env":
        for token in exec_tokens[1:]:
            # Skip env assignments/options and capture the first command token.
            if token == "--":
                continue
            if token.startswith("-"):
                continue
            if "=" in token:
                key, value = token.split("=", 1)
                normalized_key = key.strip().strip('"').strip("'")
                normalized_value = value.strip().strip('"').strip("'")
                if normalized_key == "PATH":
                    env_path_assignment = normalized_value
                if normalized_key == "MMRELAY_HOME" and normalized_value:
                    has_home_env_in_exec = True
                continue
            env_target_token = token
            break

    if cmd_basename == "env" and env_target_token is None:
        return True, "Service file has invalid ExecStart env launcher (missing command)"

    has_home_env = has_home_env or has_home_env_in_exec
    if not (has_home_flag or has_home_env):
        return (
            True,
            "Service file is missing home configuration (--home or MMRELAY_HOME)",
        )

    if cmd_basename == "env":
        # Safe due to explicit guard above; keep type checkers aware launcher is str.
        assert env_target_token is not None
        launcher_token = env_target_token
    else:
        launcher_token = cmd_token
    launcher_basename = os.path.basename(launcher_token)

    uses_python_module = any(
        token == "-m"
        and index + 1 < len(exec_tokens)
        and exec_tokens[index + 1] == "mmrelay"
        for index, token in enumerate(exec_tokens)
    )
    uses_env_mmrelay = cmd_basename == "env" and launcher_basename == "mmrelay"
    launches_mmrelay_binary = cmd_basename != "env" and cmd_basename == "mmrelay"
    launches_mmrelay = uses_python_module or uses_env_mmrelay or launches_mmrelay_binary

    # Allow explicit custom launchers (absolute paths or systemd specifier paths).
    # This is an intentional trust-the-operator path: we validate home semantics
    # above, but we do not attempt deep wrapper introspection here.
    is_explicit_custom_launcher = os.path.isabs(
        launcher_token
    ) or launcher_token.startswith("%")
    if not launches_mmrelay and not is_explicit_custom_launcher:
        return True, "Service file does not use a recognizable mmrelay launcher"

    # Only require PATH hardening when ExecStart depends on PATH lookup.
    uses_path_lookup = False
    if uses_env_mmrelay and env_target_token is not None:
        uses_path_lookup = not (
            os.path.isabs(env_target_token) or env_target_token.startswith("%")
        )
    elif launches_mmrelay_binary and not is_explicit_custom_launcher:
        uses_path_lookup = True

    if uses_path_lookup:

        def _normalize_path_entry(entry: str) -> str:
            normalized = entry.strip().strip('"').strip("'")
            if normalized.startswith("%h/"):
                normalized = os.path.join(os.path.expanduser("~"), normalized[3:])
            elif normalized == "%h":
                normalized = os.path.expanduser("~")
            normalized = os.path.expandvars(os.path.expanduser(normalized))
            return normalized.rstrip("/")

        def _split_path_entries(path_value: str) -> set[str]:
            entries: set[str] = set()
            for part in path_value.split(":"):
                normalized = _normalize_path_entry(part)
                if normalized:
                    entries.add(normalized)
            return entries

        expected_path_entries = {
            _normalize_path_entry("%h/.local/pipx/venvs/mmrelay/bin"),
            _normalize_path_entry("%h/.local/bin"),
            _normalize_path_entry("~/.local/pipx/venvs/mmrelay/bin"),
            _normalize_path_entry("~/.local/bin"),
        }

        environment_path_entries: set[str] = set()
        for line in environment_lines:
            for key, value in _iter_env_assignments(line):
                if key == "PATH" and value:
                    environment_path_entries.update(_split_path_entries(value))

        path_in_environment = bool(environment_path_entries & expected_path_entries)
        path_in_exec_env = False
        if env_path_assignment:
            path_in_exec_env = bool(
                _split_path_entries(env_path_assignment) & expected_path_entries
            )
        if not (path_in_environment or path_in_exec_env):
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

    # Check for required [Unit] dependencies (time-sync.target)
    # The service should have both After=time-sync.target and Wants=time-sync.target
    has_after_time_sync = any(
        "time-sync.target" in line
        for line in existing_service.splitlines()
        if line.strip().startswith("After=")
    )
    has_wants_time_sync = any(
        "time-sync.target" in line
        for line in existing_service.splitlines()
        if line.strip().startswith("Wants=")
    )
    if not has_after_time_sync or not has_wants_time_sync:
        return True, "Service file is missing time-sync.target dependency"

    return False, "Service file is up to date"


def check_loginctl_available() -> bool:
    """
    Check whether systemd's `loginctl` utility is available and runnable.

    Returns:
        True if a `loginctl` executable is found on PATH and invoking `loginctl --version` exits with code 0, False otherwise.
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

    Creates or updates the per-user systemd unit file, reloads the user systemd daemon, optionally enables user lingering and service enablement at boot, and optionally starts or restarts the service based on user confirmation. Progress and outcomes are logged; interactive prompts can be canceled to skip optional steps.

    Returns:
        True if the installation or update process completed (including cases where interactive prompts were canceled), False on fatal errors such as failing to create or write the service file.
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
                user_input = input("Do you want to update the service file? (Y/n): ")
                if user_input.strip().lower().startswith("n"):
                    logger.info("Skipping service file update at user request.")
                    update_needed = False
            except (EOFError, KeyboardInterrupt):
                logger.info("\nInput cancelled. Proceeding with service file update.")
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
            print("Service file updated successfully.")
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
                    [SYSTEMCTL, "--user", "enable", SYSTEMD_SERVICE_FILENAME],
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
                    [SYSTEMCTL, "--user", "restart", SYSTEMD_SERVICE_FILENAME],
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
    Start the per-user systemd unit "mmrelay.service".

    Attempts to start the user service and logs errors if the operation fails.

    Returns:
        True if the service was started successfully, False otherwise.
    """
    try:
        subprocess.run(
            [SYSTEMCTL, "--user", "start", SYSTEMD_SERVICE_FILENAME], check=True
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.exception("Error starting service (exit code %d)", e.returncode)
        return False
    except OSError:
        logger.exception("Error starting service")
        return False


def show_service_status() -> bool:
    """
    Display the user's systemd status for the mmrelay service.

    Logs the service status output (stdout or stderr).

    Returns:
        `True` if the status command executed and its output was logged, `False` if an OS-level error prevented running systemctl.
    """
    try:
        result = subprocess.run(
            [SYSTEMCTL, "--user", "status", SYSTEMD_SERVICE_FILENAME],
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
