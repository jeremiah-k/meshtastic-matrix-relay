# trunk-ignore-all(bandit)
import hashlib
import importlib
import importlib.util
import os
import re
import shlex
import shutil
import site
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import List, Set
from urllib.parse import urlparse

try:
    import schedule
except ImportError:
    schedule = None

from mmrelay.config import get_app_path, get_base_dir
from mmrelay.constants.plugins import (
    DEFAULT_ALLOWED_COMMUNITY_HOSTS,
    PIP_SOURCE_FLAGS,
    RISKY_REQUIREMENT_PREFIXES,
)
from mmrelay.log_utils import get_logger

# Global config variable that will be set from main.py
config = None

logger = get_logger(name="Plugins")
sorted_active_plugins = []
plugins_loaded = False

# Global scheduler management
_global_scheduler_thread = None
_global_scheduler_stop_event = None


try:
    _PLUGIN_DEPS_DIR = os.path.join(get_base_dir(), "plugins", "deps")
except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover
    logger.debug("Unable to resolve base dir for plugin deps at import time: %s", exc)
    _PLUGIN_DEPS_DIR = None
else:
    try:
        os.makedirs(_PLUGIN_DEPS_DIR, exist_ok=True)
    except OSError as exc:  # pragma: no cover - logging only in unusual environments
        logger.debug(
            f"Unable to create plugin dependency directory '{_PLUGIN_DEPS_DIR}': {exc}"
        )
        _PLUGIN_DEPS_DIR = None
    else:
        deps_path = os.fspath(_PLUGIN_DEPS_DIR)
        if deps_path not in sys.path:
            sys.path.append(deps_path)


def _collect_requirements(
    requirements_file: str, visited: Set[str] | None = None
) -> List[str]:
    """
    Parse a requirements file into a flattened list of installable requirement lines.

    Ignores blank lines and full-line or inline comments, preserves PEP 508 requirement syntax,
    and resolves nested includes and constraint files. Supported include forms:
      - "-r <file>" or "--requirement <file>"
      - "-c <file>" or "--constraint <file>"
      - "--requirement=<file>" and "--constraint=<file>"
    Relative include paths are resolved relative to the directory containing the given file.

    Returns:
        A list of requirement lines suitable for passing to pip. Returns an empty list if the
        file cannot be read or if a nested include recursion is detected (the latter is logged
        and the duplicate include is skipped).
    """
    normalized_path = os.path.abspath(requirements_file)
    visited = visited or set()

    if normalized_path in visited:
        logger.warning(
            "Requirements file recursion detected for %s; skipping duplicate include.",
            normalized_path,
        )
        return []

    visited.add(normalized_path)
    requirements: List[str] = []
    base_dir = os.path.dirname(normalized_path)

    try:
        with open(normalized_path, encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if " #" in line:
                    line = line.split(" #", 1)[0].strip()
                    if not line:
                        continue

                lower_line = line.lower()

                def _resolve_nested(path_str: str) -> None:
                    nested_path = (
                        path_str
                        if os.path.isabs(path_str)
                        else os.path.join(base_dir, path_str)
                    )
                    requirements.extend(
                        _collect_requirements(nested_path, visited=visited)
                    )

                is_req_eq = lower_line.startswith("--requirement=")
                is_con_eq = lower_line.startswith("--constraint=")

                if is_req_eq or is_con_eq:
                    nested = line.split("=", 1)[1].strip()
                    _resolve_nested(nested)
                    continue

                is_req = lower_line.startswith(("-r ", "--requirement "))
                is_con = lower_line.startswith(("-c ", "--constraint "))

                if is_req or is_con:
                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        _resolve_nested(parts[1].strip())
                    else:
                        directive_type = (
                            "requirement include" if is_req else "constraint"
                        )
                        logger.warning(
                            "Ignoring malformed %s directive in %s: %s",
                            directive_type,
                            normalized_path,
                            raw_line.rstrip(),
                        )
                    continue

                # Check for malformed standalone directives
                if lower_line in ("-r", "-c", "--requirement", "--constraint"):
                    logger.warning(
                        "Malformed directive, missing file: %s",
                        raw_line.rstrip(),
                    )
                    continue

                requirements.append(line)
    except (FileNotFoundError, OSError) as e:
        logger.warning("Error reading requirements file %s: %s", normalized_path, e)
        return []

    return requirements


@contextmanager
def _temp_sys_path(path: str):
    """
    Temporarily prepends a directory to Python's import search path for the duration of a with-block.

    The given path is inserted at the front of sys.path on entry. On exit the first occurrence of the path is removed; if the path is not present, no error is raised.

    Parameters:
        path (str | os.PathLike): Directory to temporarily add to sys.path.
    """
    path = os.fspath(path)
    sys.path.insert(0, path)
    try:
        yield
    finally:
        try:
            sys.path.remove(path)
        except ValueError:
            pass


def _get_security_settings() -> dict:
    """
    Return the security settings mapping from the global configuration.

    If the global `config` is unset or does not contain a `"security"` mapping, or if `"security"` exists but is not a dict, this returns an empty dict.

    Returns:
        dict: The security configuration dictionary, or an empty dict when unavailable or invalid.
    """
    if not config:
        return {}
    security_config = config.get("security", {})
    return security_config if isinstance(security_config, dict) else {}


def _get_allowed_repo_hosts() -> list[str]:
    """
    Determine the normalized allowlist of community plugin repository hosts.

    Reads the security configuration's "community_repo_hosts" value and returns a list
    of lowercase host strings with surrounding whitespace removed. If the setting is
    missing or not a list, returns a copy of DEFAULT_ALLOWED_COMMUNITY_HOSTS. Non-string
    or empty entries in the configured list are ignored.

    Returns:
        list[str]: A list of allowed repository hostnames in lowercase.
    """
    security_config = _get_security_settings()
    hosts = security_config.get("community_repo_hosts")

    if hosts is None:
        return list(DEFAULT_ALLOWED_COMMUNITY_HOSTS)

    if isinstance(hosts, str):
        hosts = [hosts]

    if not isinstance(hosts, list):
        return list(DEFAULT_ALLOWED_COMMUNITY_HOSTS)

    return [
        host.strip().lower() for host in hosts if isinstance(host, str) and host.strip()
    ]


def _allow_local_plugin_paths() -> bool:
    """
    Determine whether local filesystem plugin paths are permitted for community plugins.

    Returns:
        True if the security setting `"allow_local_plugin_paths"` is enabled, False otherwise.
    """
    return bool(_get_security_settings().get("allow_local_plugin_paths", False))


def _host_in_allowlist(host: str, allowlist: list[str]) -> bool:
    """
    Determine whether a host matches or is a subdomain of any hostname in an allowlist.

    Parameters:
        host (str): Hostname to check.
        allowlist (list[str]): List of allowed hostnames; comparison is case-insensitive.

    Returns:
        bool: `True` if `host` equals or is a subdomain of any entry in `allowlist`, `False` otherwise.
    """
    host = (host or "").lower()
    if not host:
        return False
    for allowed in allowlist:
        allowed = allowed.lower()
        if host == allowed or host.endswith(f".{allowed}"):
            return True
    return False


def _normalize_repo_target(repo_url: str) -> tuple[str, str]:
    """
    Normalize a repository URL or SSH spec into a tuple of (scheme, host).

    Returns:
        tuple[str, str]: `scheme` normalized to lowercase (uses "ssh" for `git@` SSH specs and `git+ssh`/`ssh+git` schemes), and `host` lowercased or an empty string if no host is present.
    """
    repo_url = (repo_url or "").strip()
    if repo_url.startswith("git@"):
        _, _, host_and_path = repo_url.partition("@")
        host, _, _ = host_and_path.partition(":")
        return "ssh", host.lower()
    parsed = urlparse(repo_url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if scheme in {"git+ssh", "ssh+git"}:
        scheme = "ssh"
    return scheme, host


def _is_repo_url_allowed(repo_url: str) -> bool:
    """
    Determine whether a repository URL or local filesystem path is permitted for community plugins.

    Accepts a repository specification (URL or local path). Rejects empty values and entries beginning with '-'. Local filesystem paths are permitted only when configured security settings allow local plugin paths and the path exists; `file://` schemes follow the same restriction. Plain `http://` URLs are disallowed. Only `https` and `ssh` repository URLs are permitted, and the repository host must be included in the configured allowlist.

    Returns:
        bool: `true` if the repository is allowed, `false` otherwise.
    """
    repo_url = (repo_url or "").strip()
    if not repo_url:
        return False

    if repo_url.startswith("-"):
        return False

    scheme, host = _normalize_repo_target(repo_url)

    if not scheme:
        if _allow_local_plugin_paths():
            if os.path.exists(repo_url):
                return True
            logger.error("Local repository path does not exist: %s", repo_url)
            return False
        logger.error(
            "Invalid repository '%s'. Local paths are disabled, and remote URLs must include a scheme (e.g., 'https://').",
            repo_url,
        )
        return False

    if scheme == "file":
        if _allow_local_plugin_paths():
            return True
        logger.error("file:// repositories are disabled for security reasons.")
        return False

    if scheme == "http":
        logger.error("Plain HTTP community plugin URLs are not allowed: %s", repo_url)
        return False

    if scheme not in {"https", "ssh"}:
        logger.error("Unsupported repository scheme '%s' for %s", scheme, repo_url)
        return False

    allowed_hosts = _get_allowed_repo_hosts()
    if not _host_in_allowlist(host, allowed_hosts):
        logger.error(
            "Repository host '%s' is not in the allowed community host list %s",
            host or "unknown",
            allowed_hosts,
        )
        return False

    return True


def _is_requirement_risky(req_string: str) -> bool:
    """
    Determine whether a requirement line references a VCS or URL source and should be treated as risky.

    Checks for known risky prefixes (VCS/URL specifiers) or the presence of both `@` and `://`, which indicate a URL-based requirement.

    Returns:
        `true` if the requirement references a VCS or URL source, `false` otherwise.
    """
    lowered = req_string.lower()
    return any(lowered.startswith(prefix) for prefix in RISKY_REQUIREMENT_PREFIXES) or (
        "@" in req_string and "://" in req_string
    )


# Pre-compute short-form flag characters for efficiency
PIP_SHORT_SOURCE_FLAGS = {
    f[1] for f in PIP_SOURCE_FLAGS if len(f) == 2 and f.startswith("-")
}


def _filter_risky_requirement_lines(
    requirement_lines: List[str],
) -> tuple[List[str], List[str]]:
    """
    Categorizes requirement lines into safe and flagged groups based on whether they reference VCS or URL sources.

    This function purely classifies lines without checking configuration. The caller should decide
    whether to install flagged requirements based on security settings.

    Returns:
        safe_lines (List[str]): Requirement lines considered safe for installation.
        flagged_lines (List[str]): Requirement lines that reference VCS/URL sources and were flagged as risky.
    """
    safe_lines: List[str] = []
    flagged_lines: List[str] = []

    for line in requirement_lines:
        # Tokenize line for validation
        tokens = shlex.split(line, posix=True, comments=True)
        if not tokens:
            continue

        # Check if any token in line is risky
        line_is_risky = False
        for token in tokens:
            # Handle editable flags with values (--editable=url)
            if token.startswith("-") and "=" in token:
                flag_name, _, flag_value = token.partition("=")
                if flag_name.lower() in PIP_SOURCE_FLAGS and _is_requirement_risky(
                    flag_value
                ):
                    line_is_risky = True
                continue

            # Handle short-form flags with attached values (-iflagvalue, -ivalue)
            if token.startswith("-") and not token.startswith("--") and len(token) > 2:
                flag_char = token[1]
                if flag_char in PIP_SHORT_SOURCE_FLAGS:
                    flag_value = token[
                        2:
                    ]  # Extract everything after the flag character
                    if _is_requirement_risky(flag_value):
                        line_is_risky = True
                    continue

            # Handle flags that take values
            if token.lower() in PIP_SOURCE_FLAGS:
                continue  # Skip flag tokens, as they don't indicate risk by themselves

            # Check if token itself is risky
            if _is_requirement_risky(token):
                line_is_risky = True

        if line_is_risky:
            flagged_lines.append(line)
        else:
            safe_lines.append(line)

    return safe_lines, flagged_lines


def _filter_risky_requirements(
    requirements: List[str],
) -> tuple[List[str], List[str], bool]:
    """
    Remove requirement tokens that point to VCS/URL sources unless explicitly allowed.

    Deprecated: Use _filter_risky_requirement_lines for line-based filtering.
    """
    # For backward compatibility, assume requirements are lines
    safe_lines, flagged_lines = _filter_risky_requirement_lines(requirements)
    allow_untrusted = bool(
        _get_security_settings().get("allow_untrusted_dependencies", False)
    )
    return safe_lines, flagged_lines, allow_untrusted


def _clean_python_cache(directory: str) -> None:
    """
    Remove Python bytecode cache files and __pycache__ directories under the given directory.

    Walks the directory tree rooted at `directory` and deletes any `__pycache__` directories and `.pyc` files it finds; removal errors are logged and ignored so the operation is non-fatal.

    Parameters:
        directory (str): Path to the directory to clean of Python cache files.
    """
    if not os.path.isdir(directory):
        return

    cache_dirs_removed = 0
    pyc_files_removed = 0
    for root, dirs, files in os.walk(directory):
        # Remove __pycache__ directories
        if "__pycache__" in dirs:
            cache_path = os.path.join(root, "__pycache__")
            try:
                shutil.rmtree(cache_path)
                logger.debug(f"Removed Python cache directory: {cache_path}")
                cache_dirs_removed += 1
            except OSError as e:
                logger.debug(f"Could not remove cache directory {cache_path}: {e}")
            # Remove from dirs list to prevent walking into it
            dirs.remove("__pycache__")

        # Also remove any .pyc files in the current directory
        pyc_files = (f for f in files if f.endswith(".pyc"))
        for pyc_file in pyc_files:
            pyc_path = os.path.join(root, pyc_file)
            try:
                os.remove(pyc_path)
                logger.debug(f"Removed .pyc file: {pyc_path}")
                pyc_files_removed += 1
            except OSError as e:
                logger.debug(f"Could not remove .pyc file {pyc_path}: {e}")

    if cache_dirs_removed > 0 or pyc_files_removed > 0:
        log_parts = []
        if cache_dirs_removed > 0:
            log_parts.append(
                f"{cache_dirs_removed} Python cache director{'y' if cache_dirs_removed == 1 else 'ies'}"
            )
        if pyc_files_removed > 0:
            log_parts.append(
                f"{pyc_files_removed} .pyc file{'' if pyc_files_removed == 1 else 's'}"
            )
        logger.info(f"Cleaned {' and '.join(log_parts)} from {directory}")


def _reset_caches_for_tests():
    """
    Reset the global plugin loader caches to their initial state for testing purposes.

    Clears cached plugin instances and loading state to ensure test isolation and prevent interference between test runs.
    """
    global sorted_active_plugins, plugins_loaded
    sorted_active_plugins = []
    plugins_loaded = False


def _refresh_dependency_paths() -> None:
    """
    Ensure packages installed into user or site directories become importable.

    This function collects candidate site paths from site.getusersitepackages() and
    site.getsitepackages() (when available), and registers each directory with the
    import system. It prefers site.addsitedir(path) but falls back to appending the
    path to sys.path if addsitedir fails. After modifying the import paths it calls
    importlib.invalidate_caches() so newly installed packages are discoverable.

    Side effects:
    - May modify sys.path and the interpreter's site directories.
    - Calls importlib.invalidate_caches() to refresh import machinery.
    - Logs warnings if adding a directory via site.addsitedir fails.
    """

    candidate_paths = []

    try:
        user_site = site.getusersitepackages()
        if isinstance(user_site, str):
            candidate_paths.append(user_site)
        else:
            candidate_paths.extend(user_site)
    except AttributeError:
        logger.debug("site.getusersitepackages() not available in this environment.")

    try:
        site_packages = site.getsitepackages()
        candidate_paths.extend(site_packages)
    except AttributeError:
        logger.debug("site.getsitepackages() not available in this environment.")

    if _PLUGIN_DEPS_DIR:
        candidate_paths.append(os.fspath(_PLUGIN_DEPS_DIR))

    for path in dict.fromkeys(candidate_paths):  # dedupe while preserving order
        if not path:
            continue
        if path not in sys.path:
            try:
                site.addsitedir(path)
            except OSError as e:
                logger.warning(
                    f"site.addsitedir failed for '{path}': {e}. Falling back to sys.path.insert(0, ...)."
                )
                sys.path.insert(0, path)

    # Ensure import machinery notices new packages
    importlib.invalidate_caches()


def _install_requirements_for_repo(repo_path: str, repo_name: str) -> None:
    """
    Install Python dependencies for a community plugin repository from a requirements.txt file.

    If a requirements.txt file exists at repo_path, this function will attempt to install the listed
    dependencies and then refresh interpreter import paths so newly installed packages become importable.

    Behavior highlights:
    - No-op if requirements.txt is missing or empty.
    - Respects the global auto-install configuration; if auto-install is disabled, the function logs and returns.
    - In a pipx-managed environment (detected via PIPX_* env vars) it uses `pipx inject mmrelay ...` to
      add dependencies to the application's pipx venv.
    - Otherwise it uses `python -m pip install -r requirements.txt` and adds `--user` when not running
      inside a virtual environment.
    - After a successful install it calls the path refresh routine so the interpreter can import newly
      installed packages.

    Parameters that need extra context:
    - repo_path: filesystem path to the plugin repository directory (the function looks for
      repo_path/requirements.txt).
    - repo_name: human-readable repository name used in log messages.

    Side effects:
    - Installs packages (via pipx or pip) and updates interpreter import paths.
    - Logs on success or failure; on installation failure it logs an exception and a warning that the
      plugin may not work correctly without its dependencies.
    """

    requirements_path = os.path.join(repo_path, "requirements.txt")
    if not os.path.isfile(requirements_path):
        return

    if not _check_auto_install_enabled(config):
        logger.warning(
            "Auto-install of requirements for %s disabled by config; skipping.",
            repo_name,
        )
        return

    try:
        in_pipx = any(
            key in os.environ
            for key in ("PIPX_HOME", "PIPX_LOCAL_VENVS", "PIPX_BIN_DIR")
        )

        # Collect requirements as full lines to preserve PEP 508 compliance
        # (version specifiers, environment markers, etc.)
        requirements_lines = _collect_requirements(requirements_path)

        safe_requirements, flagged_requirements = _filter_risky_requirement_lines(
            requirements_lines
        )

        # Check security configuration for handling flagged requirements
        allow_untrusted = bool(
            _get_security_settings().get("allow_untrusted_dependencies", False)
        )

        if flagged_requirements:
            if allow_untrusted:
                logger.warning(
                    "Allowing %d flagged dependency entries for %s due to security.allow_untrusted_dependencies=True",
                    len(flagged_requirements),
                    repo_name,
                )
                # Include flagged requirements when allowed
                safe_requirements.extend(flagged_requirements)
            else:
                logger.warning(
                    "Skipping %d flagged dependency entries for %s. Set security.allow_untrusted_dependencies=True to override.",
                    len(flagged_requirements),
                    repo_name,
                )
        else:
            pass

        installed_packages = False

        if in_pipx:
            logger.info("Installing requirements for plugin %s with pipx", repo_name)
            pipx_path = shutil.which("pipx")
            if not pipx_path:
                raise FileNotFoundError("pipx executable not found on PATH")
            # Check if there are actual packages to install (not just flags)
            packages = [r for r in safe_requirements if not r.startswith("-")]
            if packages:
                # Write safe requirements to a temporary file to handle hashed requirements
                # and environment markers properly
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".txt", delete=False
                ) as temp_file:
                    temp_path = temp_file.name
                    for entry in safe_requirements:
                        temp_file.write(entry + "\n")

                try:
                    cmd = [
                        pipx_path,
                        "inject",
                        "mmrelay",
                        "--requirement",
                        temp_path,
                    ]
                    _run(cmd, timeout=600)
                    installed_packages = True
                finally:
                    # Clean up the temporary file
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        logger.debug(
                            "Failed to clean up temporary requirements file: %s",
                            temp_path,
                        )
            else:
                logger.info(
                    "No dependencies listed in %s; skipping pipx injection.",
                    requirements_path,
                )
        else:
            in_venv = (sys.prefix != getattr(sys, "base_prefix", sys.prefix)) or (
                "VIRTUAL_ENV" in os.environ
            )
            logger.info("Installing requirements for plugin %s with pip", repo_name)
            packages = [r for r in safe_requirements if not r.startswith("-")]
            if not packages:
                logger.info(
                    "Requirements in %s provided no installable packages; skipping pip install.",
                    requirements_path,
                )
            else:
                cmd = [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-input",
                ]
                if not in_venv:
                    cmd.append("--user")

                # Write safe requirements to a temporary file to handle hashed requirements properly
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".txt", delete=False
                ) as temp_file:
                    temp_path = temp_file.name
                    for entry in safe_requirements:
                        temp_file.write(entry + "\n")

                try:
                    cmd.extend(["-r", temp_path])
                    _run(cmd, timeout=600)
                    installed_packages = True
                finally:
                    # Clean up the temporary file
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        logger.debug(
                            "Failed to clean up temporary requirements file: %s",
                            temp_path,
                        )

        if installed_packages:
            logger.info("Successfully installed requirements for plugin %s", repo_name)
            _refresh_dependency_paths()
        else:
            logger.info("No dependency installation run for plugin %s", repo_name)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.exception(
            "Error installing requirements for plugin %s (requirements: %s)",
            repo_name,
            requirements_path,
        )
        logger.warning(
            "Plugin %s may not work correctly without its dependencies",
            repo_name,
        )


def _get_plugin_dirs(plugin_type):
    """
    Return a prioritized list of existing plugin directories for the given plugin type.

    Attempts to ensure and prefer a per-user plugins directory (base_dir/plugins/<type>) and also includes a local application plugins directory (app_path/plugins/<type>) for backward compatibility. Each directory is created if possible; directories that cannot be created or accessed are omitted from the result.

    Parameters:
        plugin_type (str): Plugin category, e.g. "custom" or "community".

    Returns:
        list[str]: Ordered list of plugin directories to search (user directory first when available, then local directory).
    """
    dirs = []

    # Check user directory first (preferred location)
    user_dir = os.path.join(get_base_dir(), "plugins", plugin_type)
    try:
        os.makedirs(user_dir, exist_ok=True)
        dirs.append(user_dir)
    except (OSError, PermissionError) as e:
        logger.warning(f"Cannot create user plugin directory {user_dir}: {e}")

    # Check local directory (backward compatibility)
    local_dir = os.path.join(get_app_path(), "plugins", plugin_type)
    try:
        os.makedirs(local_dir, exist_ok=True)
        dirs.append(local_dir)
    except (OSError, PermissionError):
        # Skip local directory if we can't create it (e.g., in Docker)
        logger.debug(f"Cannot create local plugin directory {local_dir}, skipping")

    return dirs


def get_custom_plugin_dirs():
    """
    Return the list of directories to search for custom plugins, ordered by priority.

    The directories include the user-specific custom plugins directory and a local directory for backward compatibility.
    """
    return _get_plugin_dirs("custom")


def get_community_plugin_dirs():
    """
    List community plugin directories in priority order.

    Includes the per-user community plugins directory and a legacy local application directory for backward compatibility; directories that cannot be accessed or created are omitted.

    Returns:
        list[str]: Filesystem paths to search for community plugins, ordered from highest to lowest priority.
    """
    return _get_plugin_dirs("community")


def _run(cmd, timeout=120, retry_attempts=1, retry_delay=1, **kwargs):
    # Validate command to prevent shell injection
    """
    Run a subprocess command with validated arguments, optional retries, and a configurable timeout.

    Validates that `cmd` is a non-empty list of non-empty strings, disallows `shell=True`, sets `text=True` by default, and optionally retries failed attempts with a delay.

    Parameters:
        cmd (list[str]): Command and arguments to execute; must be a non-empty list of non-empty strings.
        timeout (int | float): Maximum seconds to allow the process to run before raising TimeoutExpired.
        retry_attempts (int): Number of execution attempts (minimum 1).
        retry_delay (int | float): Seconds to wait between retry attempts.
        **kwargs: Additional keyword arguments forwarded to subprocess.run (e.g., cwd, env). `text=True` is used by default if not provided.

    Returns:
        subprocess.CompletedProcess: The completed process object returned by subprocess.run.

    Raises:
        TypeError: If `cmd` is not a list or any element of `cmd` is not a string.
        ValueError: If `cmd` is empty, contains empty/whitespace-only arguments, or if `shell=True` is provided.
        subprocess.CalledProcessError: If the subprocess exits with a non-zero status.
        subprocess.TimeoutExpired: If the process exceeds the specified timeout.
    """
    if not isinstance(cmd, list):
        raise TypeError("cmd must be a list of str")
    if not cmd:
        raise ValueError("Command list cannot be empty")
    if not all(isinstance(arg, str) for arg in cmd):
        raise TypeError("all command arguments must be strings")
    if any(not arg.strip() for arg in cmd):
        raise ValueError("command arguments cannot be empty/whitespace")
    if kwargs.get("shell"):
        raise ValueError("shell=True is not allowed in _run")
    # Ensure text mode by default
    kwargs.setdefault("text", True)

    attempts = max(int(retry_attempts or 1), 1)
    delay = max(float(retry_delay or 0), 0.0)

    for attempt in range(1, attempts + 1):
        try:
            return subprocess.run(cmd, check=True, timeout=timeout, **kwargs)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            if attempt >= attempts:
                raise
            logger.warning(
                "Command %s failed on attempt %d/%d: %s",
                cmd[0],
                attempt,
                attempts,
                exc,
            )
            if delay:
                time.sleep(delay)


def _run_git(cmd, timeout=120, **kwargs):
    """
    Run a git command using the module's safe subprocess runner with conservative retry defaults.

    Parameters:
        cmd (list[str]): Command and arguments to run (e.g., ['git', 'clone', '...']).
        timeout (int): Maximum seconds to wait for each attempt.
        **kwargs: Additional options forwarded to `_run` (can override retries).

    Returns:
        subprocess.CompletedProcess: The completed process result containing `returncode`, `stdout`, and `stderr`.
    """
    kwargs.setdefault("retry_attempts", 3)
    kwargs.setdefault("retry_delay", 2)
    return _run(cmd, timeout=timeout, **kwargs)


def _check_auto_install_enabled(config):
    """
    Determine if automatic dependency installation is enabled in the provided configuration.

    Parameters:
        config (dict|None): Configuration mapping; expected to contain a "security" dict with an
            optional boolean "auto_install_deps" key.

    Returns:
        bool: `True` if automatic installation is enabled, `False` otherwise. If `config` is falsy
        or the key is missing, automatic installation is enabled by default.
    """
    if not config:
        return True
    return bool(config.get("security", {}).get("auto_install_deps", True))


def _raise_install_error(pkg_name):
    """
    Log a warning about disabled auto-install and raise a CalledProcessError.

    Parameters:
        pkg_name (str): Name of the package that could not be installed (used in the log message).

    Raises:
        subprocess.CalledProcessError: Always raised to signal an installation failure when auto-install is disabled.
    """
    logger.warning(
        f"Auto-install disabled; cannot install {pkg_name}. See docs for enabling."
    )
    raise subprocess.CalledProcessError(1, "pip/pipx")


def _update_existing_repo_to_commit(repo_path, ref_value, repo_name):
    """
    Update an existing repository to a specific commit.

    Parameters:
        repo_path (str): Path to the existing repository.
        ref_value (str): Commit hash to checkout.
        repo_name (str): Name of the repository for logging.

    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        # First check if the commit exists locally before fetching
        try:
            _run_git(
                [
                    "git",
                    "-C",
                    repo_path,
                    "cat-file",
                    "-e",
                    f"{ref_value}^{{commit}}",
                ],
                timeout=120,
            )
        except subprocess.CalledProcessError:
            # Commit doesn't exist locally, try to fetch it specifically
            logger.info(f"Commit {ref_value} not found locally, attempting to fetch")
            try:
                _run_git(
                    ["git", "-C", repo_path, "fetch", "origin", ref_value],
                    timeout=120,
                )
            except subprocess.CalledProcessError:
                logger.warning(
                    f"Could not fetch commit {ref_value} from remote, trying general fetch"
                )
                # Fall back to fetching everything
                try:
                    _run_git(
                        ["git", "-C", repo_path, "fetch", "origin"],
                        timeout=120,
                    )
                except subprocess.CalledProcessError as e:
                    logger.warning(f"Fallback fetch also failed: {e}")
                    return False

        # Checkout the specific commit
        _run_git(["git", "-C", repo_path, "checkout", ref_value], timeout=120)
        logger.info(f"Updated repository {repo_name} to commit {ref_value}")
        return True
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "Failed to checkout commit %s for %s: %s",
            ref_value,
            repo_name,
            exc,
        )
        return False


def _clone_new_repo_to_commit(repo_url, repo_path, ref_value, repo_name, plugins_dir):
    """
    Clone a new repository and checkout a specific commit.

    Parameters:
        repo_url (str): URL of the repository to clone.
        repo_path (str): Path where the repository should be cloned.
        ref_value (str): Commit hash to checkout.
        repo_name (str): Name of the repository for logging.
        plugins_dir (str): Directory containing the repository.

    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        os.makedirs(plugins_dir, exist_ok=True)
    except (OSError, PermissionError):
        logger.exception(f"Cannot create plugin directory {plugins_dir}")
        logger.error(f"Skipping repository {repo_name} due to permission error")
        return False

    try:
        # First clone the repository (default branch)
        _run_git(["git", "clone", repo_url], cwd=plugins_dir, timeout=120)
        logger.info(f"Cloned repository {repo_name} from {repo_url}")

        # Then checkout the specific commit
        try:
            # Try direct checkout first (commit might be available from clone)
            _run_git(["git", "-C", repo_path, "checkout", ref_value], timeout=120)
        except subprocess.CalledProcessError:
            # If direct checkout fails, try to fetch the specific commit
            logger.info(f"Commit {ref_value} not available, attempting to fetch")
            try:
                _run_git(
                    ["git", "-C", repo_path, "fetch", "origin", ref_value],
                    timeout=120,
                )
                # Try checkout again after fetch
                _run_git(["git", "-C", repo_path, "checkout", ref_value], timeout=120)
            except subprocess.CalledProcessError:
                # If specific commit fetch fails, try fetching all
                logger.warning(
                    f"Could not fetch commit {ref_value} from remote, trying general fetch"
                )
                try:
                    _run_git(["git", "-C", repo_path, "fetch", "origin"], timeout=120)
                    # Try checkout again after general fetch
                    _run_git(
                        ["git", "-C", repo_path, "checkout", ref_value], timeout=120
                    )
                except subprocess.CalledProcessError as e:
                    logger.warning(f"Fallback fetch also failed: {e}")
                    return False
        logger.info(f"Checked out repository {repo_name} to commit {ref_value}")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.exception(f"Error cloning repository {repo_name}")
        logger.error(
            f"Please manually git clone repository {repo_url} into {repo_path}"
        )
        return False


def _try_checkout_and_pull_ref(repo_path, ref_value, repo_name, ref_type="branch"):
    """
    Attempt to checkout a ref and pull from origin.

    Parameters:
        repo_path (str): Path to the repository.
        ref_value (str): The ref to checkout and pull.
        repo_name (str): Name of the repository for logging.
        ref_type (str): Type of ref ("branch" or "tag").

    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        _run_git(["git", "-C", repo_path, "checkout", ref_value], timeout=120)
        _run_git(["git", "-C", repo_path, "pull", "origin", ref_value], timeout=120)
        logger.info(f"Updated repository {repo_name} to {ref_type} {ref_value}")
        return True
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "Failed to update %s %s for %s: %s",
            ref_type,
            ref_value,
            repo_name,
            exc,
        )
        return False


def _try_fetch_and_checkout_tag(repo_path, ref_value, repo_name):
    """
    Attempt to fetch and checkout a specific tag.

    Parameters:
        repo_path (str): Path to the repository.
        ref_value (str): The tag to fetch and checkout.
        repo_name (str): Name of the repository for logging.

    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        # Try to fetch the tag
        try:
            _run_git(
                ["git", "-C", repo_path, "fetch", "origin", f"refs/tags/{ref_value}"],
                timeout=120,
            )
        except subprocess.CalledProcessError:
            # If that fails, try to fetch the tag without the refs/tags/ prefix
            _run_git(
                [
                    "git",
                    "-C",
                    repo_path,
                    "fetch",
                    "origin",
                    f"refs/tags/{ref_value}:refs/tags/{ref_value}",
                ],
                timeout=120,
            )

        # Checkout the tag
        _run_git(["git", "-C", repo_path, "checkout", ref_value], timeout=120)
        logger.info(f"Successfully fetched and checked out tag {ref_value}")
        return True
    except subprocess.CalledProcessError:
        return False


def _try_checkout_as_branch(repo_path, ref_value, repo_name):
    """
    Attempt to checkout a ref as a branch (fetch and checkout).

    Parameters:
        repo_path (str): Path to the repository.
        ref_value (str): The ref to try as a branch.
        repo_name (str): Name of the repository for logging.

    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        _run_git(["git", "-C", repo_path, "fetch", "origin", ref_value], timeout=120)
        _run_git(["git", "-C", repo_path, "checkout", ref_value], timeout=120)
        _run_git(["git", "-C", repo_path, "pull", "origin", ref_value], timeout=120)
        logger.info(f"Updated repository {repo_name} to branch {ref_value}")
        return True
    except subprocess.CalledProcessError:
        return False


def _fallback_to_default_branches(repo_path, default_branches, ref_value, repo_name):
    """
    Fallback to trying default branches when the specified ref fails.

    Parameters:
        repo_path (str): Path to the repository.
        default_branches (list): List of default branch names to try.
        ref_value (str): The original ref that failed.
        repo_name (str): Name of the repository for logging.

    Returns:
        bool: True if a default branch was successfully checked out, False otherwise.
    """
    for default_branch in default_branches:
        try:
            _run_git(["git", "-C", repo_path, "checkout", default_branch], timeout=120)
            _run_git(
                ["git", "-C", repo_path, "pull", "origin", default_branch], timeout=120
            )
            logger.info(f"Using {default_branch} instead of {ref_value}")
            return True
        except subprocess.CalledProcessError:
            continue

    logger.warning("Could not checkout any branch, using current state")
    return True


def _update_existing_repo_to_branch_or_tag(
    repo_path,
    ref_type,
    ref_value,
    repo_name,
    is_default_branch,
    default_branches,
    repo_url,
):
    """
    Update an existing repository to a specific branch or tag.

    Parameters:
        repo_path (str): Path to existing repository.
        ref_type (str): Either "branch" or "tag".
        ref_value (str): Name of branch or tag.
        repo_name (str): Name of repository for logging.
        is_default_branch (bool): Whether this is a default branch (main/master).
        default_branches (list): List of default branch names to try as fallback.

    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        # Fetch from remote
        try:
            _run_git(["git", "-C", repo_path, "fetch", "origin"], timeout=120)
        except subprocess.CalledProcessError as e:
            logger.warning(f"Error fetching from remote: {e}")
            # Continue anyway, we'll try to use what we have

        if is_default_branch:
            # Handle default branches
            try:
                current_branch = _run_git(
                    ["git", "-C", repo_path, "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True,
                ).stdout.strip()

                if current_branch == ref_value:
                    # Already on the right branch, just pull
                    try:
                        _run_git(
                            ["git", "-C", repo_path, "pull", "origin", ref_value],
                            timeout=120,
                        )
                        logger.info(
                            f"Updated repository {repo_name} branch {ref_value}"
                        )
                        return True
                    except subprocess.CalledProcessError as e:
                        logger.warning(f"Error pulling branch {ref_value}: {e}")
                        return True
                else:
                    # Switch to the right branch
                    return _try_checkout_and_pull_ref(
                        repo_path, ref_value, repo_name, ref_type
                    )
            except subprocess.CalledProcessError:
                # Try the other default branch
                other_default = "main" if ref_value == "master" else "master"
                logger.warning(f"Branch {ref_value} not found, trying {other_default}")
                if _try_checkout_and_pull_ref(
                    repo_path, other_default, repo_name, "branch"
                ):
                    return True
                logger.warning(
                    "Could not checkout any default branch, repository update failed"
                )
                return False
        else:
            # Handle non-default branches
            if ref_type == "branch":
                return _try_checkout_and_pull_ref(
                    repo_path, ref_value, repo_name, "branch"
                )

            # Handle tags
            try:
                # Check if we're already at the tag
                current_commit = _run_git(
                    ["git", "-C", repo_path, "rev-parse", "HEAD"],
                    capture_output=True,
                ).stdout.strip()

                try:
                    tag_commit = _run_git(
                        ["git", "-C", repo_path, "rev-parse", ref_value],
                        capture_output=True,
                    ).stdout.strip()
                    if tag_commit and current_commit == tag_commit:
                        logger.info(
                            f"Repository {repo_name} is already at tag {ref_value}"
                        )
                        return True
                except subprocess.CalledProcessError:
                    pass  # Tag doesn't exist locally

                # Try to checkout the tag directly
                _run_git(["git", "-C", repo_path, "checkout", ref_value], timeout=120)
                logger.info(f"Updated repository {repo_name} to tag {ref_value}")
                return True
            except subprocess.CalledProcessError:
                # Tag not found locally, try to fetch it
                logger.warning(
                    f"Tag {ref_value} not found locally, trying to fetch it specifically"
                )

                # Clean up any existing local tag that might conflict
                try:
                    _run_git(
                        ["git", "-C", repo_path, "tag", "-d", ref_value], timeout=120
                    )
                except subprocess.CalledProcessError:
                    pass  # Tag doesn't exist locally, which is fine

                if _try_fetch_and_checkout_tag(repo_path, ref_value, repo_name):
                    return True

                # If fetching as tag fails, try as branch
                logger.warning(f"Could not fetch tag {ref_value}, trying as a branch")
                if _try_checkout_as_branch(repo_path, ref_value, repo_name):
                    return True

                # Final fallback to default branches
                logger.warning(
                    f"Could not checkout {ref_value} as tag or branch, trying default branches"
                )
                return _fallback_to_default_branches(
                    repo_path, default_branches, ref_value, repo_name
                )

    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.exception(f"Error updating repository {repo_name}")
        logger.error(
            f"Please manually git clone the repository {repo_url} into {repo_path}"
        )
        return False


def _validate_clone_inputs(repo_url, ref):
    """
    Validate inputs for clone_or_update_repo function.

    Returns:
        tuple: (is_valid, repo_url, ref_type, ref_value, repo_name)
        or (False, None, None, None, None) if validation fails.
    """
    repo_url = (repo_url or "").strip()
    ref_type = ref.get("type")  # expected: "tag", "branch", or "commit"
    ref_value = (ref.get("value") or "").strip()

    if not _is_repo_url_allowed(repo_url):
        return False, None, None, None, None
    allowed_ref_types = {"tag", "branch", "commit"}
    if ref_type not in allowed_ref_types:
        logger.error(
            "Invalid ref type %r (expected 'tag', 'branch', or 'commit') for %r",
            ref_type,
            repo_url,
        )
        return False, None, None, None, None
    if not ref_value:
        logger.error("Missing ref value for %s on %r", ref_type, repo_url)
        return False, None, None, None, None
    if ref_value.startswith("-"):
        logger.error("Ref value looks invalid (starts with '-'): %r", ref_value)
        return False, None, None, None, None

    # Validate ref value based on type
    if ref_type == "commit":
        # Commit hashes should be 7-40 hex characters
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", ref_value):
            logger.error(
                "Invalid commit hash supplied: %r (must be 7-40 hex characters)",
                ref_value,
            )
            return False, None, None, None, None
    else:
        # For tag and branch, use existing validation
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", ref_value):
            logger.error("Invalid %s name supplied: %r", ref_type, ref_value)
            return False, None, None, None, None

    # Extract repository name for later use
    repo_name = os.path.splitext(os.path.basename(repo_url.rstrip("/")))[0]

    return True, repo_url, ref_type, ref_value, repo_name


def _clone_new_repo_to_branch_or_tag(
    repo_url, repo_path, ref_type, ref_value, repo_name, plugins_dir, is_default_branch
):
    """
    Clone a new repository and checkout a specific branch or tag.

    Parameters:
        repo_url (str): URL of the repository to clone.
        repo_path (str): Path where the repository should be cloned.
        ref_type (str): Type of reference ("branch" or "tag").
        ref_value (str): The branch or tag name to checkout.
        repo_name (str): Name of the repository for logging.
        plugins_dir (str): Directory containing the repository.
        is_default_branch (bool): Whether this is a default branch (main/master).

    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        # If it's a default branch, just clone it directly
        if is_default_branch:
            try:
                # Try to clone with the specified branch
                _run_git(
                    ["git", "clone", "--branch", ref_value, repo_url],
                    cwd=plugins_dir,
                    timeout=120,
                )
                if ref_type == "branch":
                    logger.info(
                        f"Cloned repository {repo_name} from {repo_url} at branch {ref_value}"
                    )
                else:
                    logger.info(
                        f"Cloned repository {repo_name} from {repo_url} at tag {ref_value}"
                    )
                return True
            except subprocess.CalledProcessError:
                # If that fails, try the other default branch
                other_default = "main" if ref_value == "master" else "master"
                try:
                    logger.warning(
                        f"Could not clone with branch {ref_value}, trying {other_default}"
                    )
                    _run_git(
                        ["git", "clone", "--branch", other_default, repo_url],
                        cwd=plugins_dir,
                        timeout=120,
                    )
                    logger.info(
                        f"Cloned repository {repo_name} from {repo_url} at branch {other_default}"
                    )
                    return True
                except subprocess.CalledProcessError:
                    # If that fails too, clone without specifying a branch
                    logger.warning(
                        f"Could not clone with branch {other_default}, cloning default branch"
                    )
                    _run_git(
                        ["git", "clone", repo_url],
                        cwd=plugins_dir,
                        timeout=120,
                    )
                    logger.info(
                        f"Cloned repository {repo_name} from {repo_url} (default branch)"
                    )
                    return True
        else:
            # It's a tag, try to clone with the tag
            try:
                # Try to clone with the specified tag
                _run_git(
                    ["git", "clone", "--branch", ref_value, repo_url],
                    cwd=plugins_dir,
                    timeout=120,
                )
                if ref_type == "branch":
                    logger.info(
                        f"Cloned repository {repo_name} from {repo_url} at branch {ref_value}"
                    )
                else:
                    logger.info(
                        f"Cloned repository {repo_name} from {repo_url} at tag {ref_value}"
                    )
                return True
            except subprocess.CalledProcessError:
                # If that fails, clone without specifying a tag
                logger.warning(
                    f"Could not clone with tag {ref_value}, cloning default branch"
                )
                _run_git(
                    ["git", "clone", repo_url],
                    cwd=plugins_dir,
                    timeout=120,
                )

            # Then try to fetch and checkout the tag
            try:
                # Try to fetch the tag
                try:
                    _run_git(
                        [
                            "git",
                            "-C",
                            repo_path,
                            "fetch",
                            "origin",
                            f"refs/tags/{ref_value}",
                        ]
                    )
                except subprocess.CalledProcessError:
                    # If that fails, try to fetch the tag without the refs/tags/ prefix
                    _run_git(
                        [
                            "git",
                            "-C",
                            repo_path,
                            "fetch",
                            "origin",
                            f"refs/tags/{ref_value}:refs/tags/{ref_value}",
                        ]
                    )

                # Now checkout the tag
                _run_git(
                    ["git", "-C", repo_path, "checkout", ref_value],
                    timeout=120,
                )
                if ref_type == "branch":
                    logger.info(
                        f"Cloned repository {repo_name} and checked out branch {ref_value}"
                    )
                else:
                    logger.info(
                        f"Cloned repository {repo_name} and checked out tag {ref_value}"
                    )
                return True
            except subprocess.CalledProcessError:
                # If that fails, try as a branch
                try:
                    logger.warning(
                        f"Could not checkout {ref_value} as a tag, trying as a branch"
                    )
                    _run_git(
                        ["git", "-C", repo_path, "fetch", "origin", ref_value],
                        timeout=120,
                    )
                    _run_git(
                        ["git", "-C", repo_path, "checkout", ref_value],
                        timeout=120,
                    )
                    logger.info(
                        f"Cloned repository {repo_name} and checked out branch {ref_value}"
                    )
                    return True
                except subprocess.CalledProcessError:
                    logger.warning(
                        f"Could not checkout {ref_value}, using default branch"
                    )
                    logger.info(
                        f"Cloned repository {repo_name} from {repo_url} (default branch)"
                    )
                    return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.exception(f"Error cloning repository {repo_name}")
        logger.error(
            f"Please manually git clone the repository {repo_url} into {repo_path}"
        )
        return False


def clone_or_update_repo(repo_url, ref, plugins_dir):
    """
    Ensure a community plugin git repository exists under plugins_dir and is checked out at the specified ref.

    Attempts to clone the repository into plugins_dir/<repo_name> or update an existing clone so that it is on the requested ref. The ref argument must be a dict with keys `"type"` (either `"tag"` or `"branch"`) and `"value"` (the tag or branch name). Falls back to common default branches ("main", "master") when appropriate.

    Parameters:
        repo_url (str): URL or SSH spec of the git repository to clone or update.
        ref (dict): Reference specification with keys:
            - type (str): "tag", "branch", or "commit".
            - value (str): The tag, branch, or commit hash to check out.
        plugins_dir (str): Directory under which the repository should be placed.

    Returns:
        bool: `True` if the repository was successfully cloned or updated, `False` otherwise.
    """
    # Validate inputs
    is_valid, repo_url, ref_type, ref_value, repo_name = _validate_clone_inputs(
        repo_url, ref
    )
    if not is_valid:
        return False

    repo_path = os.path.join(plugins_dir, repo_name)

    # Default branch names to try if ref is not specified
    default_branches = ["main", "master"]

    # Log what we're trying to do
    logger.info(f"Using {ref_type} '{ref_value}' for repository {repo_name}")

    # If it's a branch and one of the default branches, we'll handle it specially
    is_default_branch = ref_type == "branch" and ref_value in default_branches

    # Commits are handled differently from branches and tags
    is_commit = ref_type == "commit"

    try:
        if os.path.isdir(repo_path):
            # Handle commits differently from branches and tags
            if is_commit:
                return _update_existing_repo_to_commit(repo_path, ref_value, repo_name)
            else:
                return _update_existing_repo_to_branch_or_tag(
                    repo_path,
                    ref_type,
                    ref_value,
                    repo_name,
                    is_default_branch,
                    default_branches,
                    repo_url,
                )
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.exception(f"Error updating repository {repo_name}")
        logger.error(
            f"Please manually git clone the repository {repo_url} into {repo_path}"
        )
        return False
    else:
        # Repository doesn't exist yet, clone it
        try:
            os.makedirs(plugins_dir, exist_ok=True)
        except (OSError, PermissionError):
            logger.exception(f"Cannot create plugin directory {plugins_dir}")
            logger.error(f"Skipping repository {repo_name} due to permission error")
            return False

        # Now try to clone the repository
        try:
            # For commits, we need to clone and then checkout the specific commit
            if is_commit:
                return _clone_new_repo_to_commit(
                    repo_url, repo_path, ref_value, repo_name, plugins_dir
                )
            else:
                return _clone_new_repo_to_branch_or_tag(
                    repo_url,
                    repo_path,
                    ref_type,
                    ref_value,
                    repo_name,
                    plugins_dir,
                    is_default_branch,
                )
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.exception(f"Error cloning repository {repo_name}")
            logger.error(
                f"Please manually git clone the repository {repo_url} into {repo_path}"
            )
            return False


def load_plugins_from_directory(directory, recursive=False):
    """
    Discovers and instantiates Plugin classes from Python modules in the given directory.

    Searches directory (optionally recursively) for .py files, imports each module under an isolated name, and instantiates any top-level `Plugin` class found. On import failures for missing dependencies, the function may attempt to install those dependencies when auto-install is enabled; it also refreshes import paths and retries loading. The function may modify interpreter import state (e.g., entries in sys.modules) and can invoke external installers when auto-install is enabled.

    Parameters:
        directory (str): Path to the directory containing plugin Python files.
        recursive (bool): If True, scan subdirectories recursively; otherwise only the top-level directory.

    Returns:
        list: Instances of discovered plugin classes (may be empty).
    """
    plugins = []
    if os.path.isdir(directory):
        # Clean Python cache to ensure fresh code loading
        _clean_python_cache(directory)
        for root, _dirs, files in os.walk(directory):
            for filename in files:
                if filename.endswith(".py"):
                    plugin_path = os.path.join(root, filename)
                    module_name = (
                        "plugin_"
                        + hashlib.sha256(plugin_path.encode("utf-8")).hexdigest()
                    )
                    spec = importlib.util.spec_from_file_location(
                        module_name, plugin_path
                    )
                    if not spec or not getattr(spec, "loader", None):
                        logger.warning(
                            f"Skipping plugin {plugin_path}: no import spec/loader."
                        )
                        continue
                    plugin_module = importlib.util.module_from_spec(spec)

                    # Create a compatibility layer for plugins
                    # This allows plugins to import from 'plugins' or 'mmrelay.plugins'
                    if "mmrelay.plugins" not in sys.modules:
                        import mmrelay.plugins

                        sys.modules["mmrelay.plugins"] = mmrelay.plugins

                    # For backward compatibility with older plugins
                    if "plugins" not in sys.modules:
                        import mmrelay.plugins

                        sys.modules["plugins"] = mmrelay.plugins

                    plugin_dir = os.path.dirname(plugin_path)

                    try:
                        with _temp_sys_path(plugin_dir):
                            spec.loader.exec_module(plugin_module)
                        if hasattr(plugin_module, "Plugin"):
                            plugins.append(plugin_module.Plugin())
                        else:
                            logger.warning(
                                f"{plugin_path} does not define a Plugin class."
                            )
                    except ModuleNotFoundError as e:
                        missing_module = getattr(e, "name", None)
                        if not missing_module:
                            m = re.search(
                                r"No module named ['\"]([^'\"]+)['\"]", str(e)
                            )
                            missing_module = m.group(1) if m else str(e)
                        # Prefer top-level distribution name for installation
                        raw = (missing_module or "").strip()
                        top = raw.split(".", 1)[0]
                        m = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", top)
                        if not m:
                            logger.warning(
                                f"Refusing to auto-install suspicious dependency name from {plugin_path!s}: {raw!r}"
                            )
                            raise
                        missing_pkg = m.group(0)
                        logger.warning(
                            f"Missing dependency for plugin {plugin_path}: {missing_pkg}"
                        )

                        # Try to automatically install the missing dependency
                        try:
                            if not _check_auto_install_enabled(config):
                                _raise_install_error(missing_pkg)
                            # Check if we're running in a pipx environment
                            in_pipx = (
                                "PIPX_HOME" in os.environ
                                or "PIPX_LOCAL_VENVS" in os.environ
                            )

                            if in_pipx:
                                logger.info(
                                    f"Attempting to install missing dependency with pipx inject: {missing_pkg}"
                                )
                                pipx_path = shutil.which("pipx")
                                if not pipx_path:
                                    raise FileNotFoundError(
                                        "pipx executable not found on PATH"
                                    )
                                _run(
                                    [pipx_path, "inject", "mmrelay", missing_pkg],
                                    timeout=300,
                                )
                            else:
                                in_venv = (
                                    sys.prefix
                                    != getattr(sys, "base_prefix", sys.prefix)
                                ) or ("VIRTUAL_ENV" in os.environ)
                                logger.info(
                                    f"Attempting to install missing dependency with pip: {missing_pkg}"
                                )
                                cmd = [
                                    sys.executable,
                                    "-m",
                                    "pip",
                                    "install",
                                    missing_pkg,
                                    "--disable-pip-version-check",
                                    "--no-input",
                                ]
                                if not in_venv:
                                    cmd += ["--user"]
                                _run(cmd, timeout=300)

                            logger.info(
                                f"Successfully installed {missing_pkg}, retrying plugin load"
                            )
                            try:
                                _refresh_dependency_paths()
                            except (OSError, ImportError, AttributeError) as e:
                                logger.debug(
                                    f"Path refresh after auto-install failed: {e}"
                                )

                            # Try to load the module again
                            try:
                                with _temp_sys_path(plugin_dir):
                                    spec.loader.exec_module(plugin_module)

                                if hasattr(plugin_module, "Plugin"):
                                    plugins.append(plugin_module.Plugin())
                                else:
                                    logger.warning(
                                        f"{plugin_path} does not define a Plugin class."
                                    )
                            except ModuleNotFoundError:
                                logger.exception(
                                    f"Module {missing_module} still not available after installation. "
                                    f"The package name might be different from the import name."
                                )
                            except Exception:
                                logger.exception(
                                    "Error loading plugin %s after dependency installation",
                                    plugin_path,
                                )

                        except subprocess.CalledProcessError:
                            logger.exception(
                                f"Failed to automatically install {missing_pkg}. "
                                f"Please install manually:\n"
                                f"  pipx inject mmrelay {missing_pkg}  # if using pipx\n"
                                f"  pip install {missing_pkg}        # if using pip\n"
                                f"  pip install --user {missing_pkg}  # if not in a venv"
                            )
                    except Exception:
                        logger.exception(f"Error loading plugin {plugin_path}")
            if not recursive:
                break

    return plugins


def schedule_job(plugin_name: str, interval: int = 1):
    """
    Create a job that runs every specified interval for a plugin.

    Parameters:
        plugin_name (str): Name of the plugin for job tagging
        interval (int): Interval for job execution

    Returns:
        Job object that can be configured with time units and actions
    """
    if schedule is None:
        return None

    job = schedule.every(interval)
    job.tag(plugin_name)
    return job


def clear_plugin_jobs(plugin_name: str) -> None:
    """Clear all jobs for a specific plugin."""
    if schedule is not None:
        schedule.clear(plugin_name)


def start_global_scheduler():
    """
    Start the global scheduler thread for all plugins.

    Creates and starts a single daemon thread that runs schedule.run_pending()
    for all plugins. This eliminates race conditions from multiple threads
    accessing the schedule library's global state.
    """
    global _global_scheduler_thread, _global_scheduler_stop_event

    if schedule is None:
        logger.warning(
            "Schedule library not available, plugin background jobs disabled"
        )
        return

    if _global_scheduler_thread is not None and _global_scheduler_thread.is_alive():
        logger.debug("Global scheduler thread already running")
        return

    _global_scheduler_stop_event = threading.Event()

    def scheduler_loop():
        """Main scheduler loop that runs pending jobs."""
        logger.debug("Global scheduler thread started")
        while not _global_scheduler_stop_event.is_set():
            if schedule:
                schedule.run_pending()
            # Wait up to 1 second or until stop is requested
            _global_scheduler_stop_event.wait(1)
        logger.debug("Global scheduler thread stopped")

    _global_scheduler_thread = threading.Thread(
        target=scheduler_loop, name="global-plugin-scheduler", daemon=True
    )
    _global_scheduler_thread.start()
    logger.info("Global plugin scheduler started")


def stop_global_scheduler():
    """
    Stop the global scheduler thread.

    Signals the scheduler thread to stop and waits for it to terminate.
    Clears all scheduled jobs to prevent memory leaks.
    """
    global _global_scheduler_thread, _global_scheduler_stop_event

    if _global_scheduler_thread is None:
        return

    logger.debug("Stopping global scheduler thread")

    # Signal the thread to stop
    if _global_scheduler_stop_event:
        _global_scheduler_stop_event.set()

    # Wait for thread to finish
    if _global_scheduler_thread.is_alive():
        _global_scheduler_thread.join(timeout=5)
        if _global_scheduler_thread.is_alive():
            logger.warning("Global scheduler thread did not stop within timeout")

    # Clear all scheduled jobs
    if schedule:
        schedule.clear()

    _global_scheduler_thread = None
    _global_scheduler_stop_event = None
    logger.info("Global plugin scheduler stopped")


def load_plugins(passed_config=None):
    """
    Load, initialize, and return the application's active plugins according to the given or global configuration.

    Loads core, custom, and community plugins (cloning/updating community repositories and installing their dependencies as needed), starts each plugin that is configured active, and returns the resulting list sorted by plugin priority. Uses the global configuration when no configuration is passed and returns a cached result if plugins were already loaded.

    Parameters:
        passed_config (dict, optional): Configuration to use instead of the module-global config.

    Returns:
        list: Active plugin instances sorted by their `priority` attribute.
    """
    global sorted_active_plugins
    global plugins_loaded
    global config

    if plugins_loaded:
        return sorted_active_plugins

    logger.info("Checking plugin config...")

    # Update the global config if a config is passed
    if passed_config is not None:
        config = passed_config

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot load plugins.")
        return []

    # Import core plugins
    from mmrelay.plugins.debug_plugin import Plugin as DebugPlugin
    from mmrelay.plugins.drop_plugin import Plugin as DropPlugin
    from mmrelay.plugins.health_plugin import Plugin as HealthPlugin
    from mmrelay.plugins.help_plugin import Plugin as HelpPlugin
    from mmrelay.plugins.map_plugin import Plugin as MapPlugin
    from mmrelay.plugins.mesh_relay_plugin import Plugin as MeshRelayPlugin
    from mmrelay.plugins.nodes_plugin import Plugin as NodesPlugin
    from mmrelay.plugins.ping_plugin import Plugin as PingPlugin
    from mmrelay.plugins.telemetry_plugin import Plugin as TelemetryPlugin
    from mmrelay.plugins.weather_plugin import Plugin as WeatherPlugin

    # Initial list of core plugins
    core_plugins = [
        HealthPlugin(),
        MapPlugin(),
        MeshRelayPlugin(),
        PingPlugin(),
        TelemetryPlugin(),
        WeatherPlugin(),
        HelpPlugin(),
        NodesPlugin(),
        DropPlugin(),
        DebugPlugin(),
    ]

    plugins = core_plugins.copy()

    # Process and load custom plugins
    custom_plugins_config = config.get("custom-plugins", {})
    custom_plugin_dirs = get_custom_plugin_dirs()

    active_custom_plugins = [
        plugin_name
        for plugin_name, plugin_info in custom_plugins_config.items()
        if plugin_info.get("active", False)
    ]

    if active_custom_plugins:
        logger.debug(
            f"Loading active custom plugins: {', '.join(active_custom_plugins)}"
        )

    # Only load custom plugins that are explicitly enabled
    for plugin_name in active_custom_plugins:
        plugin_found = False

        # Try each directory in order
        for custom_dir in custom_plugin_dirs:
            plugin_path = os.path.join(custom_dir, plugin_name)
            if os.path.exists(plugin_path):
                logger.debug(f"Loading custom plugin from: {plugin_path}")
                try:
                    plugins.extend(
                        load_plugins_from_directory(plugin_path, recursive=False)
                    )
                    plugin_found = True
                    break
                except Exception:
                    logger.exception(f"Failed to load custom plugin {plugin_name}")
                    continue

        if not plugin_found:
            logger.warning(
                f"Custom plugin '{plugin_name}' not found in any of the plugin directories"
            )

    # Process and download community plugins
    community_plugins_config = config.get("community-plugins", {})
    community_plugin_dirs = get_community_plugin_dirs()

    if not community_plugin_dirs:
        logger.warning(
            "No writable community plugin directories available; clone/update operations will be skipped."
        )
        community_plugins_dir = None
    else:
        community_plugins_dir = community_plugin_dirs[0]

    # Create community plugins directory if needed
    active_community_plugins = [
        plugin_name
        for plugin_name, plugin_info in community_plugins_config.items()
        if plugin_info.get("active", False)
    ]

    if active_community_plugins:
        # Ensure all community plugin directories exist
        for dir_path in community_plugin_dirs:
            try:
                os.makedirs(dir_path, exist_ok=True)
            except (OSError, PermissionError) as e:
                logger.warning(
                    f"Cannot create community plugin directory {dir_path}: {e}"
                )

        logger.debug(
            f"Loading active community plugins: {', '.join(active_community_plugins)}"
        )

    # Only process community plugins if config section exists and is a dictionary
    if isinstance(community_plugins_config, dict):
        for plugin_name, plugin_info in community_plugins_config.items():
            if not plugin_info.get("active", False):
                logger.debug(
                    f"Skipping community plugin {plugin_name} - not active in config"
                )
                continue

            repo_url = plugin_info.get("repository")

            # Support commit, tag, and branch parameters
            commit = plugin_info.get("commit")
            tag = plugin_info.get("tag")
            branch = plugin_info.get("branch")

            # Determine what to use (commit, tag, branch, or default)
            # Priority: commit > tag > branch
            if commit:
                if tag or branch:
                    logger.warning(
                        f"Commit specified along with tag/branch for plugin {plugin_name}, using commit"
                    )
                ref = {"type": "commit", "value": commit}
            elif tag and branch:
                logger.warning(
                    f"Both tag and branch specified for plugin {plugin_name}, using tag"
                )
                ref = {"type": "tag", "value": tag}
            elif tag:
                ref = {"type": "tag", "value": tag}
            elif branch:
                ref = {"type": "branch", "value": branch}
            else:
                # Default to main branch if neither is specified
                ref = {"type": "branch", "value": "main"}

            if repo_url:
                if community_plugins_dir is None:
                    logger.warning(
                        "Skipping community plugin %s: no accessible plugin directory",
                        plugin_name,
                    )
                    continue

                # Clone to the user directory by default
                repo_name = os.path.splitext(os.path.basename(repo_url.rstrip("/")))[0]
                success = clone_or_update_repo(repo_url, ref, community_plugins_dir)
                if not success:
                    logger.warning(
                        f"Failed to clone/update plugin {plugin_name}, skipping"
                    )
                    continue
                repo_path = os.path.join(community_plugins_dir, repo_name)
                _install_requirements_for_repo(repo_path, repo_name)
            else:
                logger.error("Repository URL not specified for a community plugin")
                logger.error("Please specify the repository URL in config.yaml")
                continue

    # Only load community plugins that are explicitly enabled
    for plugin_name in active_community_plugins:
        plugin_info = community_plugins_config[plugin_name]
        repo_url = plugin_info.get("repository")
        if repo_url:
            # Extract repository name from URL
            repo_name = os.path.splitext(os.path.basename(repo_url.rstrip("/")))[0]

            # Try each directory in order
            plugin_found = False
            for dir_path in community_plugin_dirs:
                plugin_path = os.path.join(dir_path, repo_name)
                if os.path.exists(plugin_path):
                    logger.info(f"Loading community plugin from: {plugin_path}")
                    try:
                        plugins.extend(
                            load_plugins_from_directory(plugin_path, recursive=True)
                        )
                        plugin_found = True
                        break
                    except Exception:
                        logger.exception(
                            "Failed to load community plugin %s", repo_name
                        )
                        continue

            if not plugin_found:
                logger.warning(
                    f"Community plugin '{repo_name}' not found in any of the plugin directories"
                )
        else:
            logger.error(
                "Repository URL not specified for community plugin: %s",
                plugin_name,
            )

    # Start global scheduler for all plugins
    start_global_scheduler()

    # Filter and sort active plugins by priority
    active_plugins = []
    for plugin in plugins:
        plugin_name = getattr(plugin, "plugin_name", plugin.__class__.__name__)

        # Determine if the plugin is active based on the configuration
        if plugin in core_plugins:
            # Core plugins: default to inactive unless specified otherwise
            plugin_config = config.get("plugins", {}).get(plugin_name, {})
            is_active = plugin_config.get("active", False)
        else:
            # Custom and community plugins: default to inactive unless specified
            if plugin_name in config.get("custom-plugins", {}):
                plugin_config = config.get("custom-plugins", {}).get(plugin_name, {})
            elif plugin_name in community_plugins_config:
                plugin_config = community_plugins_config.get(plugin_name, {})
            else:
                plugin_config = {}

            is_active = plugin_config.get("active", False)

        if is_active:
            plugin.priority = plugin_config.get(
                "priority", getattr(plugin, "priority", 100)
            )
            try:
                plugin.start()
            except Exception:
                logger.exception(f"Error starting plugin {plugin_name}")
                stop_callable = getattr(plugin, "stop", None)
                if callable(stop_callable):
                    try:
                        stop_callable()
                    except Exception:
                        logger.debug(
                            "Error while running stop() for failed plugin %s",
                            plugin_name,
                        )
                continue
            active_plugins.append(plugin)

    sorted_active_plugins = sorted(active_plugins, key=lambda plugin: plugin.priority)

    # Log all loaded plugins
    if sorted_active_plugins:
        plugin_names = [
            getattr(plugin, "plugin_name", plugin.__class__.__name__)
            for plugin in sorted_active_plugins
        ]
        logger.info(f"Loaded: {', '.join(plugin_names)}")
    else:
        logger.info("Loaded: none")

    plugins_loaded = True  # Set the flag to indicate that plugins have been loaded
    return sorted_active_plugins


def shutdown_plugins() -> None:
    """
    Stop all active plugins and reset loader state to allow a clean reload.

    Calls each plugin's stop() method if present; exceptions from stop() are caught and logged. Plugins that do not implement stop() are skipped. After attempting to stop all plugins, clears the active plugin list and marks plugins as not loaded.
    """
    global sorted_active_plugins, plugins_loaded

    if not sorted_active_plugins:
        plugins_loaded = False
        return

    logger.info("Stopping %d plugin(s)...", len(sorted_active_plugins))
    for plugin in list(sorted_active_plugins):
        plugin_name = getattr(plugin, "plugin_name", plugin.__class__.__name__)
        stop_callable = getattr(plugin, "stop", None)
        if callable(stop_callable):
            try:
                stop_callable()
            except Exception:
                logger.exception("Error stopping plugin %s", plugin_name)
        else:
            logger.debug(
                "Plugin %s does not implement stop(); skipping lifecycle cleanup",
                plugin_name,
            )

    # Stop global scheduler after all plugins are stopped
    stop_global_scheduler()

    sorted_active_plugins = []
    plugins_loaded = False
