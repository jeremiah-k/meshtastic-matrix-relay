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
from types import ModuleType
from typing import Any, Iterator, NamedTuple, NoReturn
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

from mmrelay.config import (
    get_app_path,
    get_base_dir,
    get_data_dir,
)
from mmrelay.constants.plugins import (
    COMMIT_HASH_PATTERN,
    DEFAULT_ALLOWED_COMMUNITY_HOSTS,
    DEFAULT_BRANCHES,
    PIP_SOURCE_FLAGS,
    PIPX_ENVIRONMENT_KEYS,
    REF_NAME_PATTERN,
    RISKY_REQUIREMENT_PREFIXES,
)
from mmrelay.log_utils import get_logger

schedule: ModuleType | None
try:
    import schedule as _schedule

    schedule = _schedule
except ImportError:
    schedule = None

# Global config variable that will be set from main.py
config = None

logger = get_logger(name="Plugins")
sorted_active_plugins: list[Any] = []
plugins_loaded = False


class ValidationResult(NamedTuple):
    """Result of validating clone inputs with normalized values."""

    is_valid: bool
    repo_url: str | None
    ref_type: str | None
    ref_value: str | None
    repo_name: str | None


# Global scheduler management
_global_scheduler_thread: threading.Thread | None = None
_global_scheduler_stop_event: threading.Event | None = None


# Plugin dependency directory (may not be set if base dir can't be resolved)
_PLUGIN_DEPS_DIR: str | None = None


def _get_plugin_root_dirs() -> list[str]:
    """
    Compute an ordered list of candidate plugin root directories.

    Returns base_dir/plugins as primary location. Only includes additional
    plugin directories when overrides are explicitly set to avoid unexpected behavior.

    Returns:
        list[str]: Ordered list of plugin root directory paths.
    """
    base_dir = get_base_dir()
    roots: list[str] = []

    # Always include base_dir/plugins as primary location
    if base_dir:
        roots.append(os.path.join(base_dir, "plugins"))

    # Only include data_dir/plugins when data override is explicitly set
    # (for legacy Docker compatibility)
    from mmrelay.config import _get_env_data_dir, custom_data_dir

    if custom_data_dir or _get_env_data_dir():
        data_dir = get_data_dir(create=False)
        if data_dir and data_dir != base_dir:
            data_root = os.path.join(data_dir, "plugins")
            if data_root not in roots:
                roots.append(data_root)

    return roots


try:
    deps_roots = _get_plugin_root_dirs()
except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover
    logger.debug("Unable to resolve base dir for plugin deps at import time: %s", exc)
    _PLUGIN_DEPS_DIR = None
else:
    for deps_root in deps_roots:
        deps_dir = os.path.join(deps_root, "deps")
        try:
            os.makedirs(deps_dir, exist_ok=True)
        except (
            OSError
        ) as exc:  # pragma: no cover - logging only in unusual environments
            logger.debug(
                "Unable to create plugin dependency directory '%s': %s", deps_dir, exc
            )
            continue
        _PLUGIN_DEPS_DIR = deps_dir
        deps_path = os.fspath(_PLUGIN_DEPS_DIR)
        if deps_path not in sys.path:
            sys.path.append(deps_path)
        break


def _collect_requirements(
    requirements_file: str, visited: set[str] | None = None
) -> list[str]:
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
    requirements: list[str] = []
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
def _temp_sys_path(path: str) -> Iterator[None]:
    """
    Temporarily prepend a directory to sys.path for the lifetime of the context manager.

    On entry the given filesystem path is inserted at the front of sys.path; on exit the first matching occurrence is removed if present. The function accepts path-like objects (converted via os.fspath).
    Parameters:
        path (str | os.PathLike): Directory path to add to sys.path for the duration of the context.
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


def _get_security_settings() -> dict[str, Any]:
    """
    Return the `security` mapping from the module-level `config`.

    If the module-level `config` is falsy, lacks a `"security"` key, or the `"security"` value is not a mapping, an empty dict is returned.

    Returns:
        dict: Security settings mapping from module config, or an empty dict when unavailable or invalid.
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


def _redact_url(url: str) -> str:
    """
    Redact credentials from a URL for safe logging.

    If URL contains username or password, they are replaced with '***'.
    Also redacts sensitive query parameters.
    """
    try:
        s = urlsplit(url)
        # Build netloc (only redact credentials if present)
        if s.username or s.password:
            host = s.hostname or ""
            # Bracket IPv6 literals in netloc to keep URL valid
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            netloc = (
                f"{'***' if s.username else ''}{':***' if s.password else ''}@{host}"
            )
            if s.port:
                netloc += f":{s.port}"
        else:
            netloc = s.netloc

        # Always redact sensitive query parameters
        sensitive = {
            "token",
            "access_token",
            "auth",
            "key",
            "password",
            "pwd",
            "private_token",
            "oauth_token",
            "x-access-token",
        }
        q = parse_qsl(s.query, keep_blank_values=True)
        redacted = [(k, "***" if k.lower() in sensitive else v) for k, v in q]
        query = urlencode(redacted)
        return urlunsplit((s.scheme, netloc, s.path, query, s.fragment))
    except (ValueError, TypeError, AttributeError) as exc:
        logger.debug("URL redaction failed: %s", exc)
        return "<URL redaction failed>"


def _is_repo_url_allowed(repo_url: str) -> bool:
    """
    Determine whether a repository URL or local filesystem path is permitted for community plugins.

    Validates the repository target against security policy: empty or dash-prefixed values are rejected; local filesystem paths (and file:// URLs) are allowed only when configured and the path exists; plain http URLs are disallowed; only https and ssh schemes are permitted and the repository host must be present in the configured allowlist.

    Returns:
        True if the repository is allowed, False otherwise.
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
            logger.error(
                "Local repository path does not exist: %s", _redact_url(repo_url)
            )
            return False
        logger.error(
            "Invalid repository '%s'. Local paths are disabled, and remote URLs must include a scheme (e.g., 'https://').",
            _redact_url(repo_url),
        )
        return False

    if scheme == "file":
        if _allow_local_plugin_paths():
            return True
        logger.error("file:// repositories are disabled for security reasons.")
        return False

    if scheme == "http":
        logger.error(
            "Plain HTTP community plugin URLs are not allowed: %s",
            _redact_url(repo_url),
        )
        return False

    if scheme not in {"https", "ssh"}:
        logger.error(
            "Unsupported repository scheme '%s' for %s", scheme, _redact_url(repo_url)
        )
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
    Determine if a requirement line references a version-control or URL-based source.

    Returns:
        True if the requirement references a VCS or URL source, False otherwise.
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
    requirement_lines: list[str],
) -> tuple[list[str], list[str]]:
    """
    Categorizes requirement lines into safe and flagged groups based on whether they reference VCS or URL sources.

    This function purely classifies lines without checking configuration. The caller should decide
    whether to install flagged requirements based on security settings.

    Returns:
        safe_lines (list[str]): Requirement lines considered safe for installation.
        flagged_lines (list[str]): Requirement lines that reference VCS/URL sources and were flagged as risky.
    """
    safe_lines: list[str] = []
    flagged_lines: list[str] = []

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
    requirements: list[str],
) -> tuple[list[str], list[str], bool]:
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
    Remove Python bytecode caches under the given directory.

    Walks the directory tree rooted at `directory` and deletes any `__pycache__` directories and `.pyc` files it finds; deletion errors are logged and ignored so the operation is non-fatal.

    Parameters:
        directory (str): Path whose Python cache files and directories will be removed.
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


def _reset_caches_for_tests() -> None:
    """
    Reset global plugin loader caches to their initial state for testing.

    Sets the module globals `sorted_active_plugins` to an empty list and `plugins_loaded` to False to ensure test isolation.
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
    Install dependencies listed in repo_path/requirements.txt for a community plugin and refresh import paths.

    This function is a no-op if no requirements file exists or if automatic installation is disabled by configuration.
    When enabled, it will install allowed dependency entries either into the application's pipx environment (when pipx is in use)
    or into the current Python environment (using pip). After a successful installation the interpreter import/search paths are refreshed
    so newly installed packages become importable. Failures are logged and do not raise from this function.

    Parameters:
        repo_path: Filesystem path to the plugin repository (looks for a requirements.txt file at this location).
        repo_name: Human-readable repository name used in log messages and warnings.
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
        in_pipx = any(key in os.environ for key in PIPX_ENVIRONMENT_KEYS)

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


def _get_plugin_dirs(plugin_type: str) -> list[str]:
    """
    Compute ordered plugin directories for the given plugin type.

    Prefers per-root user plugin directories (created if missing) for each discovered plugin root and includes the local application `plugins/<type>` directory for backward compatibility; any directory that cannot be created or accessed is omitted.

    Parameters:
        plugin_type (str): Plugin category, e.g. "custom" or "community".

    Returns:
        list[str]: Ordered list of filesystem paths to plugin directories (per-root user dirs first, then the local app directory).
    """
    dirs = []

    for root_dir in _get_plugin_root_dirs():
        if os.path.basename(root_dir) == "plugins":
            user_dir = os.path.join(root_dir, plugin_type)
        else:
            user_dir = root_dir
        if user_dir in dirs:
            continue
        try:
            os.makedirs(user_dir, exist_ok=True)
            dirs.append(user_dir)
        except (OSError, PermissionError) as e:
            logger.warning("Cannot create user plugin directory %s: %s", user_dir, e)

    # Check local directory (backward compatibility)
    local_dir = os.path.join(get_app_path(), "plugins", plugin_type)
    try:
        os.makedirs(local_dir, exist_ok=True)
        dirs.append(local_dir)
    except (OSError, PermissionError):
        # Skip local directory if we can't create it (e.g., in Docker)
        logger.debug(f"Cannot create local plugin directory {local_dir}, skipping")

    return dirs


def get_custom_plugin_dirs() -> list[str]:
    """
    Return the list of directories to search for custom plugins, ordered by priority.

    The directories include the user-specific custom plugins directory and a local directory for backward compatibility.
    """
    return _get_plugin_dirs("custom")


def get_community_plugin_dirs() -> list[str]:
    """
    List community plugin directories in priority order.

    Includes the per-user community plugins directory and a legacy local application directory for backward compatibility; directories that cannot be accessed or created are omitted.

    Returns:
        list[str]: Filesystem paths to search for community plugins, ordered from highest to lowest priority.
    """
    return _get_plugin_dirs("community")


def _run(
    cmd: list[str],
    timeout: float = 120,
    retry_attempts: int = 1,
    retry_delay: float = 1,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    # Validate command to prevent shell injection
    """
    Execute a validated subprocess command with an optional retry loop and timeout.

    Validates that `cmd` is a non-empty list of non-empty strings and disallows `shell=True`. Uses text mode by default unless overridden. On failure, retries up to `retry_attempts` with `retry_delay` seconds between attempts.

    Parameters:
        cmd (list[str]): Command and arguments to execute.
        timeout (float): Maximum seconds to allow the process to run before raising TimeoutExpired.
        retry_attempts (int): Number of execution attempts (minimum 1).
        retry_delay (float): Seconds to wait between retry attempts.
        **kwargs: Additional keyword arguments forwarded to subprocess.run; `text=True` is set by default.

    Returns:
        subprocess.CompletedProcess[str]: The completed process result.

    Raises:
        TypeError: If `cmd` is not a list or any element of `cmd` is not a string.
        ValueError: If `cmd` is empty, contains empty/whitespace-only arguments, or if `shell=True` is provided.
        subprocess.CalledProcessError: If the subprocess exits with a non-zero status on the final attempt.
        subprocess.TimeoutExpired: If the process exceeds `timeout` on the final attempt.
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
    raise RuntimeError("Should not reach here")


def _run_git(
    cmd: list[str], timeout: float = 120, **kwargs: Any
) -> subprocess.CompletedProcess[str]:
    """
    Execute a git command with conservative retry defaults and a non-interactive environment.

    Parameters:
        cmd (list[str]): Command and arguments to run (e.g., ['git', 'clone', '...']).
        timeout (float): Maximum seconds to wait for each attempt.
        **kwargs: Additional subprocess options (for example `env`, `retry_attempts`, `retry_delay`) that modify execution.

    Returns:
        subprocess.CompletedProcess[str]: Completed process containing `returncode`, `stdout`, and `stderr`.
    """
    kwargs.setdefault("retry_attempts", 3)
    kwargs.setdefault("retry_delay", 2)
    # Ensure non-interactive git by default
    env = dict(os.environ)
    if "env" in kwargs:
        env.update(kwargs["env"])
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    kwargs["env"] = env
    return _run(cmd, timeout=timeout, **kwargs)


def _check_auto_install_enabled(config: Any) -> bool:
    """
    Determine whether automatic dependency installation is enabled for the given configuration.

    Parameters:
        config (dict|Any): Configuration mapping expected to contain a "security" dict with an optional
            boolean "auto_install_deps" key. If `config` is falsy or missing the key, automatic
            installation is considered enabled by default.

    Returns:
        True if automatic installation is enabled, False otherwise.
    """
    if not config:
        return True
    return bool(config.get("security", {}).get("auto_install_deps", True))


def _raise_install_error(pkg_name: str) -> NoReturn:
    """
    Emit a warning that automatic dependency installation is disabled and raise a subprocess.CalledProcessError.

    Parameters:
        pkg_name (str): Package name referenced in the warning message.

    Raises:
        subprocess.CalledProcessError: Always raised to indicate installation cannot proceed because auto-install is disabled.
    """
    logger.warning(
        f"Auto-install disabled; cannot install {pkg_name}. See docs for enabling."
    )
    raise subprocess.CalledProcessError(1, "pip/pipx")


def _fetch_commit_with_fallback(repo_path: str, ref_value: str, repo_name: str) -> bool:
    """
    Ensure a specific commit is fetched from the repository's origin, falling back to a general fetch if the targeted fetch fails.

    Parameters:
        repo_path (str): Filesystem path to the git repository.
        ref_value (str): Commit hash to fetch from origin.
        repo_name (str): Human-readable repository name used for logging.

    Returns:
        bool: `True` if the targeted fetch succeeded or a subsequent general fetch succeeded, `False` otherwise.
    """
    try:
        _run_git(
            ["git", "-C", repo_path, "fetch", "--depth=1", "origin", ref_value],
            timeout=120,
        )
    except subprocess.CalledProcessError:
        logger.warning(
            "Could not fetch commit %s for %s from remote; trying general fetch",
            ref_value,
            repo_name,
        )
        # Fall back to fetching everything
        try:
            _run_git(
                ["git", "-C", repo_path, "fetch", "origin"],
                timeout=120,
            )
        except subprocess.CalledProcessError as e:
            logger.warning("Fallback fetch also failed for %s: %s", repo_name, e)
            return False
    return True


def _update_existing_repo_to_commit(
    repo_path: str, ref_value: str, repo_name: str
) -> bool:
    """
    Update the repository at repo_path to the specified commit.

    If the repository is already at the commit (supports short hashes) this is a no-op.
    If the commit is not present locally, attempts to fetch it from remotes before checking it out.

    Parameters:
        repo_path (str): Filesystem path to the existing git repository.
        ref_value (str): Commit hash to checkout.
        repo_name (str): Repository name used for logging.

    Returns:
        bool: `True` if the repository was updated (or already at) the commit, `False` otherwise.
    """
    try:
        # If already at the requested commit, skip work (support short hashes)
        try:
            # Resolve both HEAD and the ref_value to full commit hashes for a safe comparison.
            current_full = _run_git(
                ["git", "-C", repo_path, "rev-parse", "HEAD"], capture_output=True
            ).stdout.strip()
            # Using ^{commit} ensures we're resolving to a commit object.
            target_full = _run_git(
                ["git", "-C", repo_path, "rev-parse", f"{ref_value}^{{commit}}"],
                capture_output=True,
            ).stdout.strip()

            if current_full == target_full:
                logger.info(
                    "Repository %s is already at commit %s", repo_name, ref_value
                )
                return True
        except subprocess.CalledProcessError:
            # This can happen if ref_value is not a local commit.
            # We can proceed to the more robust checking and fetching logic below.
            pass

        # Try a direct checkout first (commit may already be available locally)
        try:
            _run_git(["git", "-C", repo_path, "checkout", ref_value], timeout=120)
        except subprocess.CalledProcessError:
            logger.info("Commit %s not found locally, attempting to fetch", ref_value)
            if not _fetch_commit_with_fallback(repo_path, ref_value, repo_name):
                return False
            _run_git(["git", "-C", repo_path, "checkout", ref_value], timeout=120)
        logger.info("Updated repository %s to commit %s", repo_name, ref_value)
        return True
    except subprocess.CalledProcessError:
        logger.exception(
            "Failed to checkout commit %s for %s",
            ref_value,
            repo_name,
        )
        return False
    except FileNotFoundError:
        logger.exception("Error updating repository %s; git not found.", repo_name)
        return False


def _clone_new_repo_to_commit(
    repo_url: str, repo_path: str, ref_value: str, repo_name: str, plugins_dir: str
) -> bool:
    """
    Clone a repository into the plugins directory and ensure the repository is checked out to the specified commit.

    Creates the plugins_dir if necessary, clones the repository into plugins_dir/repo_name, and checks out ref_value; if the commit is not present after clone it will attempt to fetch it. Returns False if any filesystem, cloning, or git operations fail.
    Returns:
        `True` if the repository exists at the target path and is checked out to ref_value, `False` otherwise.
    """
    try:
        os.makedirs(plugins_dir, exist_ok=True)
    except (OSError, PermissionError):
        logger.exception(
            f"Cannot create plugin directory {plugins_dir}; skipping repository {repo_name}"
        )
        return False

    try:
        # First clone the repository (default branch)
        _run_git(
            ["git", "clone", "--filter=blob:none", repo_url, repo_name],
            cwd=plugins_dir,
            timeout=120,
        )
        logger.info(f"Cloned repository {repo_name} from {_redact_url(repo_url)}")

        # If we're already at the requested commit, skip extra work
        try:
            current_full = _run_git(
                ["git", "-C", repo_path, "rev-parse", "HEAD"], capture_output=True
            ).stdout.strip()
            target_full = _run_git(
                ["git", "-C", repo_path, "rev-parse", f"{ref_value}^{{commit}}"],
                capture_output=True,
            ).stdout.strip()
            if current_full == target_full:
                logger.info(
                    "Repository %s is already at commit %s", repo_name, ref_value
                )
                return True
        except subprocess.CalledProcessError:
            pass

        # Then checkout the specific commit
        try:
            # Try direct checkout first (commit might be available from clone)
            _run_git(["git", "-C", repo_path, "checkout", ref_value], timeout=120)
        except subprocess.CalledProcessError:
            # If direct checkout fails, try to fetch the specific commit
            logger.info(f"Commit {ref_value} not available, attempting to fetch")
            if not _fetch_commit_with_fallback(repo_path, ref_value, repo_name):
                return False
            # Try checkout again after fetch
            _run_git(["git", "-C", repo_path, "checkout", ref_value], timeout=120)
        logger.info(f"Checked out repository {repo_name} to commit {ref_value}")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.exception(
            f"Error cloning repository {repo_name}; please manually clone into {repo_path}"
        )
        return False


def _try_checkout_and_pull_ref(
    repo_path: str, ref_value: str, repo_name: str, ref_type: str = "branch"
) -> bool:
    """
    Checkout the given ref and pull updates from origin (branch-oriented).

    This helper runs `git checkout <ref_value>` followed by `git pull origin <ref_value>` and is intended for updating branches rather than tags.

    Parameters:
        ref_type (str): Type of ref to update — `"branch"` or `"tag"`. Defaults to `"branch"`.

    Returns:
        True if the checkout and pull succeeded, False otherwise.
    """
    try:
        _run_git(["git", "-C", repo_path, "checkout", ref_value], timeout=120)
        _run_git(["git", "-C", repo_path, "pull", "origin", ref_value], timeout=120)
        logger.info("Updated repository %s to %s %s", repo_name, ref_type, ref_value)
        return True
    except subprocess.CalledProcessError:
        logger.warning(
            "Failed to update %s %s for %s",
            ref_type,
            ref_value,
            repo_name,
        )
        return False
    except FileNotFoundError:
        logger.exception(f"Error updating repository {repo_name}; git not found.")
        return False


def _try_fetch_and_checkout_tag(repo_path: str, ref_value: str, repo_name: str) -> bool:
    """
    Attempt to fetch the given tag from origin and check it out.

    Parameters:
        repo_path (str): Filesystem path of the git repository.
        ref_value (str): Tag name to fetch and checkout.
        repo_name (str): Repository name used for logging context.

    Returns:
        bool: `True` if the tag was fetched and checked out successfully, `False` otherwise.
    """
    try:
        # Try to fetch the tag
        try:
            _run_git(
                ["git", "-C", repo_path, "fetch", "origin", f"refs/tags/{ref_value}"],
                timeout=120,
            )
        except subprocess.CalledProcessError:
            try:
                _run_git(["git", "-C", repo_path, "fetch", "--tags"], timeout=120)
            except subprocess.CalledProcessError:
                # If that fails, try fetching with an explicit refspec to force updating the local tag
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
        logger.info(
            "Successfully fetched and checked out tag %s for %s", ref_value, repo_name
        )
        return True
    except subprocess.CalledProcessError:
        return False
    except FileNotFoundError:
        logger.exception(
            "Error fetching/checking out tag %s for %s; git not found.",
            ref_value,
            repo_name,
        )
        return False


def _try_checkout_as_branch(repo_path: str, ref_value: str, repo_name: str) -> bool:
    """
    Attempt to fetch and switch the repository to the given branch name.

    Parameters:
        repo_path (str): Filesystem path to the local git repository.
        ref_value (str): Branch name to fetch and check out.
        repo_name (str): Human-readable repository name used in logs.

    Returns:
        bool: `True` if the repository was successfully fetched, checked out, and pulled to the specified branch; `False` otherwise.
    """
    try:
        _run_git(["git", "-C", repo_path, "fetch", "origin", ref_value], timeout=120)
        _run_git(["git", "-C", repo_path, "checkout", ref_value], timeout=120)
        _run_git(["git", "-C", repo_path, "pull", "origin", ref_value], timeout=120)
        logger.info(f"Updated repository {repo_name} to branch {ref_value}")
        return True
    except subprocess.CalledProcessError:
        return False
    except FileNotFoundError:
        logger.exception("Error updating repository %s; git not found.", repo_name)
        return False


def _fallback_to_default_branches(
    repo_path: str, default_branches: list[str], ref_value: str, repo_name: str
) -> bool:
    """
    Try each name in `default_branches` in order to check out and pull that branch in the repository; leave the repository unchanged if none succeed.

    Parameters:
        repo_path (str): Filesystem path to the git repository.
        default_branches (list[str]): Ordered branch names to try (e.g., ["main", "master"]).
        ref_value (str): Original ref that failed (used for context in messages).
        repo_name (str): Repository name used for context.

    Returns:
        bool: `True` if a default branch was successfully checked out and pulled, `False` otherwise.
    """
    for default_branch in default_branches:
        try:
            _run_git(["git", "-C", repo_path, "checkout", default_branch], timeout=120)
            _run_git(
                ["git", "-C", repo_path, "pull", "origin", default_branch], timeout=120
            )
            logger.info(
                f"Using {default_branch} instead of {ref_value} for {repo_name}"
            )
            return True
        except subprocess.CalledProcessError:
            continue

    logger.warning(
        f"Could not checkout any branch for {repo_name}, using current state"
    )
    return False


def _update_existing_repo_to_branch_or_tag(
    repo_path: str,
    ref_type: str,
    ref_value: str,
    repo_name: str,
    is_default_branch: bool,
    default_branches: list[str],
) -> bool:
    """
    Update an existing Git repository to the specified branch or tag.

    Parameters:
        repo_path (str): Filesystem path to the existing repository.
        ref_type (str): Either "branch" or "tag".
        ref_value (str): Name of the branch or tag to check out.
        repo_name (str): Repository name used for logging.
        is_default_branch (bool): True when the requested branch is a default branch (e.g., "main" or "master"); enables fallback between default names.
        default_branches (list[str]): Ordered list of branch names to try as fallbacks if the requested ref cannot be checked out.

    Returns:
        bool: `True` if the repository was updated to the requested ref (or an accepted fallback), `False` otherwise.
    """
    try:
        _run_git(["git", "-C", repo_path, "fetch", "origin"], timeout=120)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"Error fetching from remote: {e}")
        if isinstance(e, FileNotFoundError):
            logger.exception("Error updating repository %s; git not found.", repo_name)
            return False

    if is_default_branch:
        if _try_checkout_and_pull_ref(repo_path, ref_value, repo_name, ref_type):
            return True
        other_default = "main" if ref_value == "master" else "master"
        logger.warning(f"Branch {ref_value} not found, trying {other_default}")
        if _try_checkout_and_pull_ref(repo_path, other_default, repo_name, "branch"):
            return True
        logger.warning(
            "Could not checkout any default branch, repository update failed"
        )
        return False

    if ref_type == "branch":
        return _try_checkout_and_pull_ref(repo_path, ref_value, repo_name, "branch")

    # Handle tags
    try:
        current_commit = _run_git(
            ["git", "-C", repo_path, "rev-parse", "HEAD"], capture_output=True
        ).stdout.strip()
        tag_commit = _run_git(
            ["git", "-C", repo_path, "rev-parse", f"{ref_value}^{{commit}}"],
            capture_output=True,
        ).stdout.strip()
        if current_commit == tag_commit:
            logger.info(f"Repository {repo_name} is already at tag {ref_value}")
            return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass  # Tag doesn't exist locally or git not found

    if _try_fetch_and_checkout_tag(repo_path, ref_value, repo_name):
        return True

    logger.warning(f"Could not fetch tag {ref_value}, trying as a branch")
    if _try_checkout_as_branch(repo_path, ref_value, repo_name):
        return True

    logger.warning(
        f"Could not checkout {ref_value} as tag or branch, trying default branches"
    )
    return _fallback_to_default_branches(
        repo_path, default_branches, ref_value, repo_name
    )


def _validate_clone_inputs(repo_url: str, ref: dict[str, str]) -> ValidationResult:
    """
    Validate a repository URL and a ref specification for cloning or updating.

    Parameters:
        repo_url (str): Repository URL or SSH spec to validate.
        ref (dict): Reference specification with keys:
            - "type": one of "tag", "branch", or "commit".
            - "value": the ref identifier (tag name, branch name, or commit hash).

    Returns:
        ValidationResult: NamedTuple with fields:
            - is_valid (bool): `True` if inputs are valid, `False` otherwise.
            - repo_url (str|None): The normalized repository URL on success, `None` on failure.
            - ref_type (str|None): One of "tag", "branch", or "commit" on success, `None` on failure.
            - ref_value (str|None): The validated ref value on success, `None` on failure.
            - repo_name (str|None): Derived repository name (basename without extension) on success, `None` on failure.

    Notes:
        - Commit `value` must be 7-40 hexadecimal characters.
        - Branch and tag `value` must start with an alphanumeric character and may contain letters, digits, dot, underscore, slash, or hyphen.
        - A `value` that starts with "-" is considered invalid.
    """
    repo_url = (repo_url or "").strip()
    ref_type = ref.get("type")  # expected: "tag", "branch", or "commit"
    ref_value = (ref.get("value") or "").strip()

    if not _is_repo_url_allowed(repo_url):
        return ValidationResult(False, None, None, None, None)
    allowed_ref_types = {"tag", "branch", "commit"}
    if ref_type not in allowed_ref_types:
        logger.error(
            "Invalid ref type %r (expected 'tag', 'branch', or 'commit') for %r",
            ref_type,
            _redact_url(repo_url),
        )
        return ValidationResult(False, None, None, None, None)
    if not ref_value:
        logger.error("Missing ref value for %s on %r", ref_type, _redact_url(repo_url))
        return ValidationResult(False, None, None, None, None)
    if ref_value.startswith("-"):
        logger.error("Ref value looks invalid (starts with '-'): %r", ref_value)
        return ValidationResult(False, None, None, None, None)

    # Validate ref value based on type
    if ref_type == "commit":
        # Commit hashes should be 7-40 hex characters
        if not COMMIT_HASH_PATTERN.fullmatch(ref_value):
            logger.error(
                "Invalid commit hash supplied: %r (must be 7-40 hex characters)",
                ref_value,
            )
            return ValidationResult(False, None, None, None, None)
    else:
        # For tag and branch, use existing validation
        if not REF_NAME_PATTERN.fullmatch(ref_value):
            logger.error("Invalid %s name supplied: %r", ref_type, ref_value)
            return ValidationResult(False, None, None, None, None)

    # Extract repository name for later use
    repo_name = _get_repo_name_from_url(repo_url)
    if not repo_name:
        return ValidationResult(False, None, None, None, None)

    return ValidationResult(True, repo_url, ref_type, ref_value, repo_name)


def _get_repo_name_from_url(repo_url: str) -> str | None:
    """
    Extract repository name from a URL or SSH spec without validation.

    This is a lightweight function that only extracts the repository name
    from URLs and SSH specs. It performs no security validation.

    Parameters:
        repo_url (str): Repository URL or SSH spec.

    Returns:
        str | None: Repository name (basename without .git extension) or None if extraction fails.
    """
    if not repo_url:
        return None

    # Support both https URLs and git@host:owner/repo.git SCP-like specs
    parsed = urlsplit(repo_url)
    raw_path = parsed.path or (
        repo_url.split(":", 1)[1]
        if repo_url.startswith("git@") and ":" in repo_url
        else repo_url
    )
    repo_name = os.path.splitext(os.path.basename(raw_path.rstrip("/")))[0]
    return repo_name if repo_name else None


def _clone_new_repo_to_branch_or_tag(
    repo_url: str,
    repo_path: str,
    ref_type: str,
    ref_value: str,
    repo_name: str,
    plugins_dir: str,
    is_default_branch: bool,
) -> bool:
    """
    Clone a repository into the plugins directory and ensure it is checked out to the specified branch or tag.

    Attempts clone strategies that prefer the given ref and falls back to alternate/default branches when appropriate; performs post-clone checkout for tags or non-default branches.

    Parameters:
        repo_url: Repository URL to clone.
        repo_path: Full filesystem path where the repository should be created.
        ref_type: Either "branch" or "tag", indicating the kind of ref to check out.
        ref_value: Name of the branch or tag to check out.
        repo_name: Short repository directory name used under plugins_dir.
        plugins_dir: Parent directory under which the repository directory will be created.
        is_default_branch: True when ref_value should be treated as a repository's default branch (e.g., "main" or "master"); this enables attempting alternate default branch names.

    Returns:
        True if the repository was successfully cloned and placed on the requested ref, False otherwise.
    """
    redacted_url = _redact_url(repo_url)
    clone_commands = []

    if is_default_branch:
        clone_commands.append(
            (
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "--branch",
                    ref_value,
                    repo_url,
                    repo_name,
                ],
                ref_value,
            )
        )
        other_default = "main" if ref_value == "master" else "master"
        clone_commands.append(
            (
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "--branch",
                    other_default,
                    repo_url,
                    repo_name,
                ],
                other_default,
            )
        )
        clone_commands.append(
            (
                ["git", "clone", "--filter=blob:none", repo_url, repo_name],
                "default branch",
            )
        )
    elif ref_type == "branch":
        clone_commands.append(
            (
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "--branch",
                    ref_value,
                    repo_url,
                    repo_name,
                ],
                ref_value,
            )
        )
        clone_commands.append(
            (
                ["git", "clone", "--filter=blob:none", repo_url, repo_name],
                "default branch",
            )
        )
    else:  # tag
        # For tags, it's simpler to just clone default branch
        # and then handle tag checkout in post-clone step.
        clone_commands.append(
            (
                ["git", "clone", "--filter=blob:none", repo_url, repo_name],
                "default branch",
            )
        )

    for command, branch_name in clone_commands:
        try:
            if os.path.isdir(repo_path):
                shutil.rmtree(repo_path, ignore_errors=True)
            _run_git(command, cwd=plugins_dir, timeout=120)
            logger.info(
                "Cloned repository %s from %s at %s %s",
                repo_name,
                redacted_url,
                ref_type,
                branch_name,
            )

            success = True
            if ref_type != "branch" or not is_default_branch:
                # Post-clone operations for tags and non-default branches
                if ref_type == "tag":
                    # If already at the tag's commit, skip extra work
                    try:
                        _cp = _run_git(
                            ["git", "-C", repo_path, "rev-parse", "HEAD"],
                            capture_output=True,
                        )
                        current = _cp.stdout.strip()
                        _cp = _run_git(
                            [
                                "git",
                                "-C",
                                repo_path,
                                "rev-parse",
                                f"{ref_value}^{{commit}}",
                            ],
                            capture_output=True,
                        )
                        tag_commit = _cp.stdout.strip()
                        if current == tag_commit:
                            return True
                    except subprocess.CalledProcessError:
                        pass
                    success = _try_fetch_and_checkout_tag(
                        repo_path, ref_value, repo_name
                    )
                elif ref_type == "branch":
                    success = _try_checkout_as_branch(repo_path, ref_value, repo_name)

            if success:
                return True
        except subprocess.CalledProcessError:
            logger.warning(
                f"Could not clone with {ref_type} {branch_name}, trying next option."
            )
            continue
        except FileNotFoundError:
            logger.exception(f"Error cloning repository {repo_name}; git not found.")
            return False

    logger.exception(
        f"Error cloning repository {repo_name}; please manually clone into {repo_path}"
    )
    return False


def _clone_or_update_repo_validated(
    repo_url: str, ref_type: str, ref_value: str, repo_name: str, plugins_dir: str
) -> bool:
    """
    Internal clone/update function that assumes inputs are already validated.

    This is the core logic of clone_or_update_repo, but skips validation
    to avoid redundant checks when inputs are pre-validated.

    Parameters:
        repo_url (str): Validated repository URL or SSH spec.
        ref_type (str): Validated ref type: "branch", "tag", or "commit".
        ref_value (str): Validated ref value (branch name, tag name, or commit hash).
        repo_name (str): Validated repository name.
        plugins_dir (str): Directory under which repository should be placed.

    Returns:
        bool: `True` if repository was successfully cloned or updated to the requested ref, `False` otherwise.
    """
    repo_path = os.path.join(plugins_dir, repo_name)

    # Use module-level constant for default branch names
    default_branches = DEFAULT_BRANCHES

    # Log what we're trying to do
    logger.info("Using %s '%s' for repository %s", ref_type, ref_value, repo_name)

    # If it's a branch and one of the default branches, we'll handle it specially
    is_default_branch = ref_type == "branch" and ref_value in default_branches

    # Commits are handled differently from branches and tags
    is_commit = ref_type == "commit"

    if os.path.isdir(repo_path):
        # Repository exists, update it
        try:
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
                )
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.exception(
                "Error updating repository %s; please check or update %s manually",
                repo_name,
                repo_path,
            )
            return False
    else:
        # Repository doesn't exist, clone it
        try:
            # Handle commits differently from branches and tags
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
            logger.exception(
                "Error cloning repository %s; please manually clone into %s",
                repo_name,
                repo_path,
            )
            return False


def clone_or_update_repo(repo_url: str, ref: dict[str, str], plugins_dir: str) -> bool:
    """
    Ensure a repository exists under plugins_dir and is checked out to the specified ref.

    Parameters:
        repo_url (str): URL or SSH spec of the git repository to clone or update.
        ref (dict): Reference specification with keys:
            - type (str): One of "branch", "tag", or "commit".
            - value (str): The branch name, tag name, or commit hash to check out.
        plugins_dir (str): Directory under which the repository should be placed.

    Returns:
        bool: `True` if the repository was successfully cloned or updated to the requested ref, `False` otherwise.
    """
    # Validate inputs
    validation_result = _validate_clone_inputs(repo_url, ref)
    if not validation_result.is_valid:
        return False

    # Delegate to internal function that assumes inputs are already validated
    assert validation_result.repo_url is not None
    assert validation_result.ref_type is not None
    assert validation_result.ref_value is not None
    assert validation_result.repo_name is not None
    return _clone_or_update_repo_validated(
        validation_result.repo_url,
        validation_result.ref_type,
        validation_result.ref_value,
        validation_result.repo_name,
        plugins_dir,
    )


def load_plugins_from_directory(directory: str, recursive: bool = False) -> list[Any]:
    """
    Discover and instantiate top-level Plugin classes from Python modules in a directory.

    Scans the given directory (optionally recursively) for .py modules, imports each module in an isolated namespace, and returns instantiated top-level `Plugin` objects found. On import failure due to a missing dependency and when automatic installation is enabled, the function may attempt to install the missing package and refresh import paths before retrying. The function does not raise on individual plugin load failures; it returns only successfully instantiated plugins.

    Parameters:
        directory (str): Path to the directory containing plugin Python files.
        recursive (bool): If True, scan subdirectories recursively; otherwise scan only the top-level directory.

    Returns:
        list[Any]: Instances of discovered `Plugin` classes; returns an empty list if none are found.
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

                    # Critical: Alias base_plugin to prevent double-loading and config reset
                    # This ensures that plugins importing 'plugins.base_plugin' get the same
                    # module object (with configured global state) as 'mmrelay.plugins.base_plugin'.
                    if "plugins.base_plugin" not in sys.modules:
                        import mmrelay.plugins.base_plugin

                        sys.modules["plugins.base_plugin"] = mmrelay.plugins.base_plugin

                    plugin_dir = os.path.dirname(plugin_path)

                    try:
                        with _temp_sys_path(plugin_dir):
                            if spec.loader:
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
                                    if spec.loader:
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

                        except (subprocess.CalledProcessError, FileNotFoundError):
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


def schedule_job(plugin_name: str, interval: int = 1) -> Any:
    """
    Create and tag a scheduled job for a plugin at the given interval.

    Parameters:
        plugin_name (str): Plugin name used to tag the scheduled job.
        interval (int): Interval value for the schedule; the time unit is selected when configuring the job (e.g., `job.seconds`, `job.minutes`).

    Returns:
        job: The scheduled job object tagged with `plugin_name`, or `None` if the scheduling library is unavailable.
    """
    if schedule is None:
        return None

    job = schedule.every(interval)
    job.tag(plugin_name)
    return job


def clear_plugin_jobs(plugin_name: str) -> None:
    """
    Remove all scheduled jobs tagged with the given plugin name.

    Parameters:
        plugin_name (str): The tag used when scheduling jobs for the plugin; all jobs with this tag will be cleared.
    """
    if schedule is not None:
        schedule.clear(plugin_name)


def start_global_scheduler() -> None:
    """
    Start a single global scheduler thread to execute all plugin scheduled jobs.

    Creates and starts one daemon thread that periodically calls schedule.run_pending()
    to run pending jobs for all plugins. If the schedule library is unavailable or a
    global scheduler is already running, the function does nothing.
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

    stop_event = threading.Event()
    _global_scheduler_stop_event = stop_event

    def scheduler_loop() -> None:
        """
        Runs the global scheduler loop that executes scheduled jobs until stopped.

        Continuously calls `schedule.run_pending()` (if the `schedule` library is available) and waits up to 1 second between iterations. The loop exits when the module-level stop event is set.
        """
        logger.debug("Global scheduler thread started")
        # Capture stop_event locally to avoid races if globals are reset.
        while not stop_event.is_set():
            if schedule:
                schedule.run_pending()
            # Wait up to 1 second or until stop is requested
            stop_event.wait(1)
        logger.debug("Global scheduler thread stopped")

    _global_scheduler_thread = threading.Thread(
        target=scheduler_loop, name="global-plugin-scheduler", daemon=True
    )
    _global_scheduler_thread.start()
    logger.info("Global plugin scheduler started")


def stop_global_scheduler() -> None:
    """
    Stop the global scheduler thread.

    Signals the scheduler loop to stop, waits up to 5 seconds for the thread to terminate, clears all scheduled jobs, and resets the scheduler state.
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


def load_plugins(passed_config: Any = None) -> list[Any]:
    """
    Load, prepare, and start configured core, custom, and community plugins.

    Uses the module-global configuration unless `passed_config` is provided. Ensures community repositories and their dependencies are cloned/updated and installed as configured, starts plugins marked active (and the global scheduler), caches the loaded set for subsequent calls, and returns the active plugin instances ordered by their `priority`.

    Parameters:
        passed_config (dict | Any, optional): Configuration to use instead of the module-global `config`.

    Returns:
        list[Any]: Active plugin instances sorted by their `priority` attribute.
    """
    global sorted_active_plugins, plugins_loaded
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

                # Clone to the user directory by default (derive name using the same logic as the clone path)
                validation_result = _validate_clone_inputs(repo_url, ref)
                if not validation_result.is_valid or not validation_result.repo_name:
                    logger.error(
                        "Invalid repository URL for community plugin %s: %s",
                        plugin_name,
                        _redact_url(repo_url),
                    )
                    continue
                repo_name = validation_result.repo_name
                # validation_result.repo_url is guaranteed to be non-None when is_valid is True
                assert validation_result.repo_url is not None
                success = clone_or_update_repo(
                    validation_result.repo_url, ref, community_plugins_dir
                )
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
            # Extract repo name using lightweight function (no validation needed for loading)
            repo_name_candidate = _get_repo_name_from_url(repo_url)
            if not repo_name_candidate:
                logger.error(
                    "Invalid repository URL for community plugin: %s",
                    _redact_url(repo_url),
                )
                continue

            # Try each directory in order
            plugin_found = False
            for dir_path in community_plugin_dirs:
                plugin_path = os.path.join(dir_path, repo_name_candidate)
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
                            "Failed to load community plugin %s", plugin_name
                        )
                        continue

            if not plugin_found:
                logger.warning(
                    f"Community plugin '{plugin_name}' not found in any of the plugin directories"
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
