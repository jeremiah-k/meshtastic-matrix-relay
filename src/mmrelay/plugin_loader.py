# trunk-ignore-all(bandit)
import hashlib
import importlib
import importlib.util
import json
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
from datetime import datetime, timedelta, timezone
from types import ModuleType
from typing import Any, Final, Iterator, NamedTuple, NoReturn, Sequence, cast
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

import mmrelay.paths as paths_module
from mmrelay.config import (
    get_app_path,
)
from mmrelay.constants.config import (
    CONFIG_SECTION_COMMUNITY_PLUGINS,
    CONFIG_SECTION_CUSTOM_PLUGINS,
    CONFIG_SECTION_PLUGINS,
)
from mmrelay.constants.formats import DEFAULT_TEXT_ENCODING
from mmrelay.constants.plugins import (
    COMMIT_HASH_PATTERN,
    DEFAULT_ALLOWED_COMMUNITY_HOSTS,
    DEFAULT_BRANCHES,
    DEFAULT_PLUGIN_PRIORITY,
    DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
    GIT_BRANCH_CMD,
    GIT_CHECKOUT_CMD,
    GIT_CLONE_CMD,
    GIT_CLONE_FILTER_BLOB_NONE,
    GIT_COMMAND_TIMEOUT_SECONDS,
    GIT_COMMIT_DEREF_SUFFIX,
    GIT_DEFAULT_BRANCH_SENTINEL,
    GIT_FETCH_CMD,
    GIT_FETCH_DEPTH_ONE,
    GIT_PULL_CMD,
    GIT_REF_HEAD,
    GIT_REMOTE_ORIGIN,
    GIT_RETRY_ATTEMPTS,
    GIT_RETRY_DELAY_SECONDS,
    GIT_REV_PARSE_CMD,
    GIT_TAGS_FLAG,
    GIT_TERMINAL_PROMPT_DISABLED,
    GIT_TERMINAL_PROMPT_ENV,
    PIP_INSTALL_MISSING_DEP_TIMEOUT,
    PIP_INSTALL_TIMEOUT_SECONDS,
    PIP_SOURCE_FLAGS,
    PIPX_ENVIRONMENT_KEYS,
    PLUGIN_TYPE_COMMUNITY,
    PLUGIN_TYPE_CUSTOM,
    REF_NAME_PATTERN,
    RISKY_REQUIREMENT_PREFIXES,
    SCHEDULER_LOOP_WAIT_SECONDS,
    SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS,
    SENSITIVE_URL_PARAMS,
)
from mmrelay.log_utils import get_logger

schedule: ModuleType | None = None
try:
    import schedule as _schedule

    schedule = _schedule
except ImportError:
    schedule = None

# Global config variable that will be set from main.py
config = None

logger = get_logger(name="Plugins")

_SENSITIVE_URL_PARAMS_LOWER: Final[frozenset[str]] = frozenset(
    param.lower() for param in SENSITIVE_URL_PARAMS
)

sorted_active_plugins: list[Any] = []
plugins_loaded = False
_last_logged_plugin_roots: tuple[str | None, tuple[str, ...]] | None = None


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
PLUGIN_REQUIREMENTS_FILENAME: Final[str] = "requirements.txt"
PLUGIN_STATE_FILENAME: Final[str] = ".mmrelay-plugin-state.json"
COMMUNITY_PLUGIN_UPDATE_CHECK_INTERVAL: Final[timedelta] = timedelta(hours=24)
_community_dep_install_warning_logged = False


def _is_safe_plugin_name(name: str) -> bool:
    """
    Validate a short plugin name to ensure it contains no path traversal, path separators, or absolute-path references.

    Intended for short plugin identifiers (for example, "my-plugin"); do not use to validate full filesystem paths.

    Parameters:
        name (str): Candidate plugin name or identifier to validate.

    Returns:
        bool: `True` if the name is non-empty, contains no path separators, contains no "`..`" segments, and is not an absolute path; `False` otherwise.
    """
    if not name or name.strip() == "":
        return False

    # Reject path separators
    for sep in ["/", "\\", os.sep] + ([os.altsep] if os.altsep is not None else []):
        if sep in name:
            return False

    # Reject parent directory references
    if ".." in name:
        return False

    # Reject absolute paths
    if os.path.isabs(name):
        return False

    return True


def _is_path_contained(root: str, child: str) -> bool:
    """
    Check whether a path is strictly contained within a root directory.

    Both paths are resolved with os.path.realpath and normalized with os.path.normcase before comparison; symbolic links and case differences are therefore accounted for. The function returns True only when the child path is located inside the root (i.e., child is not equal to root and resides within a subpath).

    Parameters:
        root (str): Root directory path.
        child (str): Path to test for containment.

    Returns:
        bool: `True` if `child` is located inside `root` (strict containment), `False` otherwise.
    """
    # Normalize both paths for comparison
    root_normalized = os.path.normcase(os.path.realpath(root))
    child_normalized = os.path.normcase(os.path.realpath(child))

    # Use os.path.commonpath for platform-independent containment test
    try:
        common = os.path.commonpath([root_normalized, child_normalized])
    except ValueError:
        return False
    return common == root_normalized and child_normalized != root_normalized


def _get_plugin_root_dirs() -> list[str]:
    """
    Compute an ordered list of candidate plugin root directories, preferring the user's HOME/plugins and including existing legacy plugin directories when present.

    Returns:
        list[str]: Ordered list of filesystem paths to plugin root directories.
    """
    roots: list[str] = []
    seen: set[str] = set()

    try:
        home_dir = str(paths_module.get_home_dir())
        if home_dir:
            home_plugins = os.path.join(home_dir, "plugins")
            if home_plugins not in seen:
                roots.append(home_plugins)
                seen.add(home_plugins)
    except (OSError, RuntimeError, ValueError) as e:
        logger.warning("Could not determine primary plugin root: %s", e)

    try:
        legacy_dirs_list = paths_module.get_legacy_dirs()
    except (OSError, RuntimeError, ValueError) as e:
        logger.warning("Could not determine legacy plugin roots: %s", e)
        legacy_dirs_list = []

    for legacy_root in legacy_dirs_list:
        legacy_plugins = os.path.join(str(legacy_root), "plugins")
        if legacy_plugins not in seen and os.path.exists(legacy_plugins):
            roots.append(legacy_plugins)
            seen.add(legacy_plugins)

    primary_root = roots[0] if roots else None
    legacy_roots = roots[1:] if len(roots) > 1 else []
    global _last_logged_plugin_roots
    root_snapshot = (
        str(primary_root) if primary_root else None,
        tuple(legacy_roots),
    )
    if _last_logged_plugin_roots != root_snapshot:
        logger.info(
            "Plugin roots: primary=%s, legacy=%s",
            str(primary_root) if primary_root else "none",
            legacy_roots if legacy_roots else [],
        )
        _last_logged_plugin_roots = root_snapshot

    return roots


def _get_legacy_plugin_roots() -> set[str]:
    """
    Compute the legacy plugin root directories located under known legacy bases.

    If discovery of legacy bases fails, an empty set is returned and a warning is logged.

    Returns:
        A set of path strings for legacy "plugins" directories; empty if discovery fails or none are found.
    """
    try:
        legacy_dirs = paths_module.get_legacy_dirs()
    except (OSError, RuntimeError, ValueError) as e:
        logger.warning("Could not determine legacy plugin roots: %s", e)
        return set()

    legacy_roots: set[str] = set()
    for legacy_root in legacy_dirs:
        legacy_roots.add(os.path.join(str(legacy_root), "plugins"))
    return legacy_roots


try:
    deps_roots = _get_plugin_root_dirs()
except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover
    logger.debug("Unable to resolve base dir for plugin deps at import time: %s", exc)
    _PLUGIN_DEPS_DIR = None
else:
    legacy_roots = _get_legacy_plugin_roots()
    for deps_root in deps_roots:
        if deps_root in legacy_roots:
            logger.debug(
                "Skipping legacy plugin root for deps directory creation: %s",
                deps_root,
            )
            continue
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
        q = parse_qsl(s.query, keep_blank_values=True)
        redacted = [
            (k, "***" if k.lower() in _SENSITIVE_URL_PARAMS_LOWER else v) for k, v in q
        ]
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
    global sorted_active_plugins, plugins_loaded, _community_dep_install_warning_logged
    sorted_active_plugins = []
    plugins_loaded = False
    _community_dep_install_warning_logged = False


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


def _install_requirements_for_repo(
    repo_path: str,
    repo_name: str,
    plugin_type: str = PLUGIN_TYPE_CUSTOM,
) -> None:
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

    requirements_path = os.path.join(repo_path, PLUGIN_REQUIREMENTS_FILENAME)
    if not os.path.isfile(requirements_path):
        return

    if not _check_auto_install_enabled(config, plugin_type=plugin_type):
        logger.warning(
            "Auto-install of requirements for %s disabled by config; skipping.",
            repo_name,
        )
        return

    global _community_dep_install_warning_logged
    if (
        plugin_type == PLUGIN_TYPE_COMMUNITY
        and not _community_dep_install_warning_logged
    ):
        logger.warning(
            "Community plugin dependencies execute arbitrary code and are unsafe"
        )
        _community_dep_install_warning_logged = True

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
                    _run(cmd, timeout=PIP_INSTALL_TIMEOUT_SECONDS)
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
                    _run(cmd, timeout=PIP_INSTALL_TIMEOUT_SECONDS)
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
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
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

    legacy_roots = _get_legacy_plugin_roots()
    for root_dir in _get_plugin_root_dirs():
        if os.path.basename(root_dir) == "plugins":
            user_dir = os.path.join(root_dir, plugin_type)
        else:
            user_dir = root_dir
        if user_dir in dirs:
            continue
        if root_dir in legacy_roots:
            if os.path.isdir(user_dir):
                dirs.append(user_dir)
            else:
                logger.debug(
                    "Skipping legacy plugin directory creation for %s", user_dir
                )
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
    return _get_plugin_dirs(PLUGIN_TYPE_CUSTOM)


def get_community_plugin_dirs() -> list[str]:
    """
    List community plugin directories in priority order.

    Includes the per-user community plugins directory and a legacy local application directory for backward compatibility; directories that cannot be accessed or created are omitted.

    Returns:
        list[str]: Filesystem paths to search for community plugins, ordered from highest to lowest priority.
    """
    return _get_plugin_dirs(PLUGIN_TYPE_COMMUNITY)


def _run(
    cmd: list[str],
    timeout: float = DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
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
    cmd: list[str],
    timeout: float = GIT_COMMAND_TIMEOUT_SECONDS,
    retry_attempts: int | None = None,
    retry_delay: float | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """
    Execute a git command with a non-interactive environment.

    Parameters:
        cmd (list[str]): Command and arguments to run (e.g., ['git', 'clone', '...']).
        timeout (float): Maximum seconds to wait for each attempt.
        retry_attempts (int | None): Optional retry count. When omitted, `_run()`
            default behavior is used (single attempt).
        retry_delay (float | None): Optional delay between retries in seconds.
        **kwargs: Additional subprocess options (for example `env`) that modify execution.

    Returns:
        subprocess.CompletedProcess[str]: Completed process containing `returncode`, `stdout`, and `stderr`.
    """
    if retry_attempts is not None:
        kwargs.setdefault("retry_attempts", retry_attempts)
    if retry_delay is not None:
        kwargs.setdefault("retry_delay", retry_delay)
    # Ensure non-interactive git by default
    env = dict(os.environ)
    if "env" in kwargs:
        env.update(kwargs["env"])
    env[GIT_TERMINAL_PROMPT_ENV] = (
        GIT_TERMINAL_PROMPT_DISABLED  # Enforce non-interactive, cannot be overridden
    )
    kwargs["env"] = env
    return _run(cmd, timeout=timeout, **kwargs)


def _check_auto_install_enabled(
    config: Any, plugin_type: str = PLUGIN_TYPE_CUSTOM
) -> bool:
    """
    Determine whether automatic dependency installation is enabled for the given configuration and plugin type.

    Parameters:
        config (dict|Any): Configuration mapping expected to contain a "security" dict
            with an optional boolean "auto_install_deps" key.
        plugin_type (str): Plugin source category. Community plugin dependency
            auto-install is disabled by default unless explicitly enabled in config.

    Returns:
        True if automatic installation is enabled for the plugin type, False otherwise.
    """
    if not isinstance(config, dict):
        return plugin_type != PLUGIN_TYPE_COMMUNITY

    security_config = config.get("security", {})
    if not isinstance(security_config, dict):
        security_config = {}

    # Keep existing custom plugin behavior, but require explicit opt-in for
    # community plugin dependency installation.
    default_enabled = plugin_type != PLUGIN_TYPE_COMMUNITY
    return bool(security_config.get("auto_install_deps", default_enabled))


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


def _state_file_path(repo_path: str) -> str:
    """
    Build the plugin state file path for a community plugin repository.

    Parameters:
        repo_path (str): Filesystem path to the plugin repository.

    Returns:
        str: Absolute path to the state file inside the repository.
    """
    return os.path.join(repo_path, PLUGIN_STATE_FILENAME)


def _is_full_commit_sha(value: str) -> bool:
    """
    Check whether a string is a full 40-character hexadecimal commit SHA.

    Parameters:
        value (str): Candidate commit SHA string.

    Returns:
        bool: `True` for full 40-char hex SHAs, else `False`.
    """
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}", (value or "").strip()))


def _load_plugin_state(repo_path: str) -> dict[str, Any]:
    """
    Load persisted community plugin state from disk.

    Parameters:
        repo_path (str): Filesystem path to the plugin repository.

    Returns:
        dict[str, Any]: Parsed state object, or an empty dict if unavailable.
    """
    state_path = _state_file_path(repo_path)
    try:
        with open(state_path, encoding=DEFAULT_TEXT_ENCODING) as handle:
            loaded_state = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.debug("Failed to read plugin state file %s: %s", state_path, exc)
        return {}

    if isinstance(loaded_state, dict):
        return cast(dict[str, Any], loaded_state)

    logger.debug("Ignoring invalid plugin state in %s: expected object", state_path)
    return {}


def _save_plugin_state(repo_path: str, state: dict[str, Any]) -> None:
    """
    Persist community plugin state to disk using an atomic replace.

    Parameters:
        repo_path (str): Filesystem path to the plugin repository.
        state (dict[str, Any]): Serializable state mapping to persist.
    """
    state_path = _state_file_path(repo_path)
    tmp_path: str | None = None
    try:
        os.makedirs(repo_path, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=DEFAULT_TEXT_ENCODING,
            delete=False,
            dir=repo_path,
        ) as temp_handle:
            json.dump(state, temp_handle, indent=2, sort_keys=True)
            temp_handle.write("\n")
            temp_handle.flush()
            os.fsync(temp_handle.fileno())
            tmp_path = temp_handle.name
        os.replace(tmp_path, state_path)
    except (OSError, TypeError, ValueError) as exc:
        logger.debug("Failed to write plugin state file %s: %s", state_path, exc)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _parse_state_timestamp(value: Any) -> datetime | None:
    """
    Parse an ISO timestamp from plugin state.

    Parameters:
        value (Any): Raw timestamp value from state.

    Returns:
        datetime | None: Parsed UTC datetime, or None when invalid.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resolve_remote_default_branch(repo_path: str) -> str | None:
    """
    Resolve the remote default branch name for origin.

    Parameters:
        repo_path (str): Filesystem path to the plugin repository.

    Returns:
        str | None: Default branch name (for example "main"), or None.
    """
    result: subprocess.CompletedProcess[str] | None = None
    try:
        result = _run_git(
            [
                "git",
                "-C",
                repo_path,
                "ls-remote",
                "--symref",
                GIT_REMOTE_ORIGIN,
                "HEAD",
            ],
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug(
            "Unable to resolve remote default branch for %s via symref: %s",
            repo_path,
            exc,
        )

    if result is not None:
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line.startswith("ref:"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            ref_value = parts[1].strip()
            prefix = "refs/heads/"
            if ref_value.startswith(prefix):
                return ref_value[len(prefix) :]

    for fallback_branch in DEFAULT_BRANCHES:
        if _resolve_remote_branch_head_commit(repo_path, fallback_branch):
            return fallback_branch

    logger.debug(
        "Could not determine remote default branch for %s; skipping update check",
        repo_path,
    )
    return None


def _resolve_local_head_commit(repo_path: str) -> str | None:
    """
    Resolve the current local HEAD commit SHA for a repository.

    Parameters:
        repo_path (str): Filesystem path to the plugin repository.

    Returns:
        str | None: Full commit SHA, or None when unavailable.
    """
    try:
        result = _run_git(
            ["git", "-C", repo_path, GIT_REV_PARSE_CMD, GIT_REF_HEAD],
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("Unable to resolve local HEAD commit for %s: %s", repo_path, exc)
        return None

    commit_sha = result.stdout.strip()
    return commit_sha if _is_full_commit_sha(commit_sha) else None


def _resolve_remote_branch_head_commit(repo_path: str, branch_name: str) -> str | None:
    """
    Resolve the remote HEAD commit SHA for a specific branch on origin.

    Parameters:
        repo_path (str): Filesystem path to the plugin repository.
        branch_name (str): Branch name to resolve.

    Returns:
        str | None: Commit SHA for origin/<branch_name>, or None.
    """
    try:
        result = _run_git(
            [
                "git",
                "-C",
                repo_path,
                "ls-remote",
                GIT_REMOTE_ORIGIN,
                f"refs/heads/{branch_name}",
            ],
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug(
            "Unable to resolve remote branch head for %s (%s): %s",
            repo_path,
            branch_name,
            exc,
        )
        return None

    first_line = next((line.strip() for line in result.stdout.splitlines() if line), "")
    if not first_line:
        return None
    remote_sha = first_line.split("\t", 1)[0].strip()
    return remote_sha if _is_full_commit_sha(remote_sha) else None


def _get_repo_host_info(repo_url: str) -> tuple[str, str, str] | None:
    """
    Extract host, owner, and repository name from a GitHub-style URL.

    Supports:
    - https://github.com/owner/repo.git
    - git@github.com:owner/repo.git

    Parameters:
        repo_url (str): Repository URL to parse.

    Returns:
        tuple[str, str, str] | None: (host, owner, repo_name), or None if parsing fails.
    """
    trimmed_url = (repo_url or "").strip()
    if not trimmed_url:
        return None

    ssh_match = re.match(r"^git@([^:]+):([^/]+)/([^/]+?)(?:\.git)?/?$", trimmed_url)
    if ssh_match:
        host, owner, repo_name = ssh_match.groups()
        return host.lower(), owner, repo_name

    parsed = urlsplit(trimmed_url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").strip()
    https_match = re.match(r"^/([^/]+)/([^/]+?)(?:\.git)?/?$", path)
    if not host or https_match is None:
        return None
    owner, repo_name = https_match.groups()
    if not owner or not repo_name:
        return None
    return host, owner, repo_name


def _build_compare_url(repo_url: str, base_sha: str, head_sha: str) -> str | None:
    """
    Build a GitHub compare URL between two explicit commits.

    Parameters:
        repo_url (str): Repository URL.
        base_sha (str): Older/base commit SHA.
        head_sha (str): Newer/head commit SHA.

    Returns:
        str | None: Compare URL for GitHub repositories, otherwise None.
    """
    if not base_sha or not head_sha:
        return None

    repo_info = _get_repo_host_info(repo_url)
    if repo_info is None:
        return None
    host, owner, repo_name = repo_info
    if host != "github.com":
        return None

    return f"https://github.com/{owner}/{repo_name}/compare/{base_sha}...{head_sha}"


def _check_commit_pin_for_upstream_updates(
    plugin_name: str,
    repo_url: str,
    repo_path: str,
) -> None:
    """
    Check whether a commit-pinned community plugin is behind the upstream default branch.

    The check is throttled by the state file's `last_checked_at` value and never
    raises. It only logs a new notification when upstream default-branch HEAD
    changes since the last notification.

    Parameters:
        plugin_name (str): Configured plugin name for logging.
        repo_url (str): Repository URL used for optional compare URL generation.
        repo_path (str): Filesystem path to the checked-out repository.
    """
    try:
        state = _load_plugin_state(repo_path)
        now = datetime.now(timezone.utc)
        last_checked_at = _parse_state_timestamp(state.get("last_checked_at"))
        if (
            last_checked_at is not None
            and now - last_checked_at < COMMUNITY_PLUGIN_UPDATE_CHECK_INTERVAL
        ):
            return

        pinned_sha = _resolve_local_head_commit(repo_path)
        default_branch = _resolve_remote_default_branch(repo_path)
        upstream_sha = None
        if default_branch:
            upstream_sha = _resolve_remote_branch_head_commit(repo_path, default_branch)

        if default_branch:
            state["last_seen_default_branch"] = default_branch
        if upstream_sha:
            state["last_seen_default_branch_head"] = upstream_sha

        if pinned_sha and upstream_sha and upstream_sha != pinned_sha:
            last_notified_upstream = state.get("last_notified_upstream_head")
            if not isinstance(last_notified_upstream, str):
                last_notified_upstream = ""
            if upstream_sha != last_notified_upstream:
                logger.warning(
                    "Plugin '%s' is pinned to %s, upstream is now %s",
                    plugin_name,
                    pinned_sha,
                    upstream_sha,
                )
                compare_url = _build_compare_url(repo_url, pinned_sha, upstream_sha)
                if compare_url:
                    logger.warning("Compare: %s", compare_url)
                state["last_notified_upstream_head"] = upstream_sha

        state["last_checked_at"] = now.isoformat()
        _save_plugin_state(repo_path, state)
    except Exception as exc:  # pragma: no cover - defensive safety net
        logger.debug(
            "Failed to check upstream updates for community plugin %s: %s",
            plugin_name,
            exc,
        )


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
            [
                "git",
                "-C",
                repo_path,
                GIT_FETCH_CMD,
                GIT_FETCH_DEPTH_ONE,
                GIT_REMOTE_ORIGIN,
                ref_value,
            ],
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            retry_attempts=GIT_RETRY_ATTEMPTS,
            retry_delay=GIT_RETRY_DELAY_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        logger.warning(
            "Could not fetch commit %s for %s from remote; trying general fetch",
            ref_value,
            repo_name,
        )
        # Fall back to fetching everything
        try:
            _run_git(
                ["git", "-C", repo_path, GIT_FETCH_CMD, GIT_REMOTE_ORIGIN],
                timeout=GIT_COMMAND_TIMEOUT_SECONDS,
                retry_attempts=GIT_RETRY_ATTEMPTS,
                retry_delay=GIT_RETRY_DELAY_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
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
                ["git", "-C", repo_path, GIT_REV_PARSE_CMD, GIT_REF_HEAD],
                capture_output=True,
            ).stdout.strip()
            # Using ^{commit} ensures we're resolving to a commit object.
            target_full = _run_git(
                [
                    "git",
                    "-C",
                    repo_path,
                    GIT_REV_PARSE_CMD,
                    f"{ref_value}{GIT_COMMIT_DEREF_SUFFIX}",
                ],
                capture_output=True,
            ).stdout.strip()

            if current_full == target_full:
                logger.info(
                    "Repository %s is already at commit %s", repo_name, ref_value
                )
                return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            # This can happen if ref_value is not a local commit.
            # We can proceed to the more robust checking and fetching logic below.
            pass

        # Try a direct checkout first (commit may already be available locally)
        try:
            _run_git(
                ["git", "-C", repo_path, GIT_CHECKOUT_CMD, ref_value],
                timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            logger.info("Commit %s not found locally, attempting to fetch", ref_value)
            if not _fetch_commit_with_fallback(repo_path, ref_value, repo_name):
                return False
            _run_git(
                ["git", "-C", repo_path, GIT_CHECKOUT_CMD, ref_value],
                timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            )
        logger.info("Updated repository %s to commit %s", repo_name, ref_value)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
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
            ["git", GIT_CLONE_CMD, GIT_CLONE_FILTER_BLOB_NONE, repo_url, repo_name],
            cwd=plugins_dir,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            # _run_git retries cannot clean a partially created destination between attempts.
            # Use a single clone attempt here so we do not fail retries with
            # "destination path already exists".
            retry_attempts=1,
        )
        logger.info(f"Cloned repository {repo_name} from {_redact_url(repo_url)}")

        # If we're already at the requested commit, skip extra work
        try:
            current_full = _run_git(
                ["git", "-C", repo_path, GIT_REV_PARSE_CMD, GIT_REF_HEAD],
                capture_output=True,
            ).stdout.strip()
            target_full = _run_git(
                [
                    "git",
                    "-C",
                    repo_path,
                    GIT_REV_PARSE_CMD,
                    f"{ref_value}{GIT_COMMIT_DEREF_SUFFIX}",
                ],
                capture_output=True,
            ).stdout.strip()
            if current_full == target_full:
                logger.info(
                    "Repository %s is already at commit %s", repo_name, ref_value
                )
                return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

        # Then checkout the specific commit
        try:
            # Try direct checkout first (commit might be available from clone)
            _run_git(
                ["git", "-C", repo_path, GIT_CHECKOUT_CMD, ref_value],
                timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            # If direct checkout fails, try to fetch the specific commit
            logger.info(f"Commit {ref_value} not available, attempting to fetch")
            if not _fetch_commit_with_fallback(repo_path, ref_value, repo_name):
                return False
            # Try checkout again after fetch
            _run_git(
                ["git", "-C", repo_path, GIT_CHECKOUT_CMD, ref_value],
                timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            )
        logger.info(f"Checked out repository {repo_name} to commit {ref_value}")
        return True
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
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
        _run_git(
            ["git", "-C", repo_path, GIT_CHECKOUT_CMD, ref_value],
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
        _run_git(
            ["git", "-C", repo_path, GIT_PULL_CMD, GIT_REMOTE_ORIGIN, ref_value],
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
        logger.info("Updated repository %s to %s %s", repo_name, ref_type, ref_value)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        if ref_type == "branch":
            logger.debug(
                "Pull/checkout failed for %s branch %s: %s",
                repo_name,
                ref_value,
                exc,
            )
            logger.warning(
                "Pull failed for %s branch %s, attempting force sync to origin/%s",
                repo_name,
                ref_value,
                ref_value,
            )
            try:
                _run_git(
                    [
                        "git",
                        "-C",
                        repo_path,
                        GIT_FETCH_CMD,
                        GIT_REMOTE_ORIGIN,
                        ref_value,
                    ],
                    timeout=GIT_COMMAND_TIMEOUT_SECONDS,
                    retry_attempts=GIT_RETRY_ATTEMPTS,
                    retry_delay=GIT_RETRY_DELAY_SECONDS,
                )
                _run_git(
                    [
                        "git",
                        "-C",
                        repo_path,
                        GIT_CHECKOUT_CMD,
                        "-B",
                        ref_value,
                        f"{GIT_REMOTE_ORIGIN}/{ref_value}",
                    ],
                    timeout=GIT_COMMAND_TIMEOUT_SECONDS,
                )
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                FileNotFoundError,
            ):
                logger.warning(
                    "Force sync failed for %s branch %s",
                    repo_name,
                    ref_value,
                )
                return False
            else:
                logger.info(
                    "Force-synced repository %s to branch %s", repo_name, ref_value
                )
                return True

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


# Guards sys.modules mutations performed by plugin_loader to avoid
# exposing partially-initialized modules during concurrent loads.
_SYS_MODULES_LOCK: threading.RLock = threading.RLock()


def _exec_plugin_module(
    *,
    spec: importlib.machinery.ModuleSpec,  # pyright: ignore[reportAttributeAccessIssue]
    plugin_module: ModuleType,
    module_name: str,
    plugin_dir: str,
) -> None:
    """
    Execute a plugin module while keeping ``sys.modules`` consistent.

    Registering the module before execution ensures ``inspect.getfile()`` works
    for plugin classes during ``BasePlugin`` initialization (tier inference uses
    class file paths).

    A module-level lock serialises the ``sys.modules`` manipulation so
    concurrent plugin loading threads cannot leave the global namespace in an
    inconsistent state.
    """
    if spec.loader is None:
        raise ImportError(f"No loader available for plugin module '{module_name}'")
    # Intentionally hold lock during execution to avoid exposing partially
    # initialized modules to other threads.
    with _SYS_MODULES_LOCK:
        previous_module = sys.modules.get(module_name)
        sys.modules[module_name] = plugin_module
        try:
            with _temp_sys_path(plugin_dir):
                spec.loader.exec_module(plugin_module)
        except BaseException:
            if previous_module is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous_module
            raise


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
                [
                    "git",
                    "-C",
                    repo_path,
                    GIT_FETCH_CMD,
                    GIT_REMOTE_ORIGIN,
                    f"refs/tags/{ref_value}",
                ],
                timeout=GIT_COMMAND_TIMEOUT_SECONDS,
                retry_attempts=GIT_RETRY_ATTEMPTS,
                retry_delay=GIT_RETRY_DELAY_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            try:
                _run_git(
                    ["git", "-C", repo_path, GIT_FETCH_CMD, GIT_TAGS_FLAG],
                    timeout=GIT_COMMAND_TIMEOUT_SECONDS,
                    retry_attempts=GIT_RETRY_ATTEMPTS,
                    retry_delay=GIT_RETRY_DELAY_SECONDS,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                # If that fails, try fetching with an explicit refspec to force updating the local tag
                _run_git(
                    [
                        "git",
                        "-C",
                        repo_path,
                        GIT_FETCH_CMD,
                        GIT_REMOTE_ORIGIN,
                        f"refs/tags/{ref_value}:refs/tags/{ref_value}",
                    ],
                    timeout=GIT_COMMAND_TIMEOUT_SECONDS,
                    retry_attempts=GIT_RETRY_ATTEMPTS,
                    retry_delay=GIT_RETRY_DELAY_SECONDS,
                )

        # Checkout the tag
        _run_git(
            ["git", "-C", repo_path, GIT_CHECKOUT_CMD, ref_value],
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
        logger.info(
            "Successfully fetched and checked out tag %s for %s", ref_value, repo_name
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
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
        _run_git(
            ["git", "-C", repo_path, GIT_FETCH_CMD, GIT_REMOTE_ORIGIN, ref_value],
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            retry_attempts=GIT_RETRY_ATTEMPTS,
            retry_delay=GIT_RETRY_DELAY_SECONDS,
        )
        _run_git(
            ["git", "-C", repo_path, GIT_CHECKOUT_CMD, ref_value],
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
        _run_git(
            ["git", "-C", repo_path, GIT_PULL_CMD, GIT_REMOTE_ORIGIN, ref_value],
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
        logger.info(f"Updated repository {repo_name} to branch {ref_value}")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    except FileNotFoundError:
        logger.exception("Error updating repository %s; git not found.", repo_name)
        return False


def _fallback_to_default_branches(
    repo_path: str, default_branches: Sequence[str], ref_value: str, repo_name: str
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
            _run_git(
                ["git", "-C", repo_path, GIT_CHECKOUT_CMD, default_branch],
                timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            )
            _run_git(
                [
                    "git",
                    "-C",
                    repo_path,
                    GIT_PULL_CMD,
                    GIT_REMOTE_ORIGIN,
                    default_branch,
                ],
                timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            )
            logger.info(
                f"Using {default_branch} instead of {ref_value} for {repo_name}"
            )
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
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
    default_branches: Sequence[str],
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
        _run_git(
            ["git", "-C", repo_path, GIT_FETCH_CMD, GIT_REMOTE_ORIGIN],
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            retry_attempts=GIT_RETRY_ATTEMPTS,
            retry_delay=GIT_RETRY_DELAY_SECONDS,
        )
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ) as e:
        logger.warning(f"Error fetching from remote: {e}")
        if isinstance(e, FileNotFoundError):
            logger.exception("Error updating repository %s; git not found.", repo_name)
            return False

    if is_default_branch:
        default_branch_candidates = [ref_value] + [
            branch_name for branch_name in default_branches if branch_name != ref_value
        ]
        for branch_name in default_branch_candidates:
            if branch_name != ref_value:
                logger.warning("Branch %s not found, trying %s", ref_value, branch_name)
            if _try_checkout_and_pull_ref(repo_path, branch_name, repo_name, "branch"):
                return True
        logger.warning(
            "Could not checkout any default branch, repository update failed"
        )
        return False

    if ref_type == "branch":
        if not _try_checkout_and_pull_ref(repo_path, ref_value, repo_name, "branch"):
            logger.warning(
                "Failed to update %s to branch %s",
                repo_name,
                ref_value,
            )
            return False
        return True

    # Handle tags
    try:
        current_commit = _run_git(
            ["git", "-C", repo_path, GIT_REV_PARSE_CMD, GIT_REF_HEAD],
            capture_output=True,
        ).stdout.strip()
        tag_commit = _run_git(
            [
                "git",
                "-C",
                repo_path,
                GIT_REV_PARSE_CMD,
                f"{ref_value}{GIT_COMMIT_DEREF_SUFFIX}",
            ],
            capture_output=True,
        ).stdout.strip()
        if current_commit == tag_commit:
            logger.info(f"Repository {repo_name} is already at tag {ref_value}")
            return True
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
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
        default_branch_candidates = [ref_value] + [
            branch_name for branch_name in DEFAULT_BRANCHES if branch_name != ref_value
        ]
        for branch_name in default_branch_candidates:
            clone_commands.append(
                (
                    [
                        "git",
                        GIT_CLONE_CMD,
                        GIT_CLONE_FILTER_BLOB_NONE,
                        GIT_BRANCH_CMD,
                        branch_name,
                        repo_url,
                        repo_name,
                    ],
                    branch_name,
                )
            )
        clone_commands.append(
            (
                ["git", GIT_CLONE_CMD, GIT_CLONE_FILTER_BLOB_NONE, repo_url, repo_name],
                GIT_DEFAULT_BRANCH_SENTINEL,
            )
        )
    elif ref_type == "branch":
        clone_commands.append(
            (
                [
                    "git",
                    GIT_CLONE_CMD,
                    GIT_CLONE_FILTER_BLOB_NONE,
                    GIT_BRANCH_CMD,
                    ref_value,
                    repo_url,
                    repo_name,
                ],
                ref_value,
            )
        )
        clone_commands.append(
            (
                ["git", GIT_CLONE_CMD, GIT_CLONE_FILTER_BLOB_NONE, repo_url, repo_name],
                GIT_DEFAULT_BRANCH_SENTINEL,
            )
        )
    else:  # tag
        # For tags, it's simpler to just clone default branch
        # and then handle tag checkout in post-clone step.
        clone_commands.append(
            (
                ["git", GIT_CLONE_CMD, GIT_CLONE_FILTER_BLOB_NONE, repo_url, repo_name],
                GIT_DEFAULT_BRANCH_SENTINEL,
            )
        )

    last_exc: subprocess.CalledProcessError | subprocess.TimeoutExpired | None = None
    for command, branch_name in clone_commands:
        try:
            if os.path.isdir(repo_path):
                shutil.rmtree(repo_path, ignore_errors=True)
            # _run_git retries cannot clean a partially created destination between attempts.
            # Use a single clone attempt per strategy to avoid retries failing with
            # "destination path already exists".
            clone_retry_attempts = 1
            _run_git(
                command,
                cwd=plugins_dir,
                timeout=GIT_COMMAND_TIMEOUT_SECONDS,
                retry_attempts=clone_retry_attempts,
            )
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
                            ["git", "-C", repo_path, GIT_REV_PARSE_CMD, GIT_REF_HEAD],
                            capture_output=True,
                        )
                        current = _cp.stdout.strip()
                        _cp = _run_git(
                            [
                                "git",
                                "-C",
                                repo_path,
                                GIT_REV_PARSE_CMD,
                                f"{ref_value}{GIT_COMMIT_DEREF_SUFFIX}",
                            ],
                            capture_output=True,
                        )
                        tag_commit = _cp.stdout.strip()
                        if current == tag_commit:
                            return True
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                        pass  # Continue to fetch and checkout
                    success = _try_fetch_and_checkout_tag(
                        repo_path, ref_value, repo_name
                    )
                elif ref_type == "branch":
                    success = _try_checkout_as_branch(repo_path, ref_value, repo_name)

            if success:
                return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            last_exc = e
            logger.warning(
                f"Could not clone with {ref_type} {branch_name}, trying next option."
            )
            continue
        except FileNotFoundError:
            logger.exception(f"Error cloning repository {repo_name}; git not found.")
            return False

    if last_exc:
        logger.error(
            "Error cloning repository %s; please manually clone into %s: %s",
            repo_name,
            repo_path,
            last_exc,
        )
    else:
        logger.error(
            "Error cloning repository %s; please manually clone into %s",
            repo_name,
            repo_path,
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

    try:
        if os.path.isdir(repo_path):
            # Repository exists, update it
            # Handle commits differently from branches and tags
            if is_commit:
                return _update_existing_repo_to_commit(repo_path, ref_value, repo_name)
            return _update_existing_repo_to_branch_or_tag(
                repo_path,
                ref_type,
                ref_value,
                repo_name,
                is_default_branch,
                default_branches,
            )

        # Repository doesn't exist, clone it
        # Handle commits differently from branches and tags
        if is_commit:
            return _clone_new_repo_to_commit(
                repo_url, repo_path, ref_value, repo_name, plugins_dir
            )
        return _clone_new_repo_to_branch_or_tag(
            repo_url,
            repo_path,
            ref_type,
            ref_value,
            repo_name,
            plugins_dir,
            is_default_branch,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ):
        logger.warning(
            "Error cloning/updating repository %s at %s %s",
            repo_name,
            ref_type,
            ref_value,
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
    if (
        validation_result.repo_url is None
        or validation_result.ref_type is None
        or validation_result.ref_value is None
        or validation_result.repo_name is None
    ):
        logger.error(
            "Repository validation returned incomplete data for %s",
            _redact_url(repo_url),
        )
        return False
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
                        + hashlib.sha256(
                            plugin_path.encode(DEFAULT_TEXT_ENCODING)
                        ).hexdigest()
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

                    module_imported = False
                    try:
                        _exec_plugin_module(
                            spec=spec,
                            plugin_module=plugin_module,
                            module_name=module_name,
                            plugin_dir=plugin_dir,
                        )
                        module_imported = True
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
                                    timeout=PIP_INSTALL_MISSING_DEP_TIMEOUT,
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
                                _run(cmd, timeout=PIP_INSTALL_MISSING_DEP_TIMEOUT)

                            logger.info(
                                f"Successfully installed {missing_pkg}, retrying plugin load"
                            )
                            try:
                                _refresh_dependency_paths()
                            except (OSError, ImportError, AttributeError) as e:
                                logger.debug(
                                    f"Path refresh after auto-install failed: {e}"
                                )

                            try:
                                if not module_imported:
                                    plugin_module = importlib.util.module_from_spec(
                                        spec
                                    )
                                    _exec_plugin_module(
                                        spec=spec,
                                        plugin_module=plugin_module,
                                        module_name=module_name,
                                        plugin_dir=plugin_dir,
                                    )

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
                            except (Exception, SystemExit):
                                logger.exception(
                                    "Error loading plugin %s after dependency installation",
                                    plugin_path,
                                )

                        except (
                            OSError,
                            subprocess.CalledProcessError,
                            subprocess.TimeoutExpired,
                        ):
                            logger.exception(
                                f"Failed to automatically install {missing_pkg}. "
                                f"Please install manually:\n"
                                f"  pipx inject mmrelay {missing_pkg}  # if using pipx\n"
                                f"  pip install {missing_pkg}        # if using pip\n"
                                f"  pip install --user {missing_pkg}  # if not in a venv"
                            )
                    except (Exception, SystemExit):
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

        Continuously calls `schedule.run_pending()` (if the `schedule` library is available) and waits up to ``SCHEDULER_LOOP_WAIT_SECONDS`` between iterations. The loop exits when the module-level stop_event is set.
        """
        logger.debug("Global scheduler thread started")
        # Capture stop_event locally to avoid races if globals are reset.
        while not stop_event.is_set():
            if schedule is not None:
                schedule.run_pending()
            # Wait until stop is requested or timeout elapses
            stop_event.wait(SCHEDULER_LOOP_WAIT_SECONDS)

    _global_scheduler_thread = threading.Thread(
        target=scheduler_loop, name="global-plugin-scheduler", daemon=True
    )
    _global_scheduler_thread.start()
    logger.info("Global plugin scheduler started")


def stop_global_scheduler() -> None:
    """
    Stop the global scheduler thread.

    Signals the scheduler loop to stop, waits up to ``SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS`` for the thread to terminate, clears all scheduled jobs, and resets the scheduler state.
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
        _global_scheduler_thread.join(timeout=SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS)
        if _global_scheduler_thread.is_alive():
            logger.warning("Global scheduler thread did not stop within timeout")

    # Clear all scheduled jobs
    if schedule is not None:
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
    if not isinstance(config, dict):
        logger.error(
            "Invalid plugin configuration type: expected mapping, got %s",
            type(config).__name__,
        )
        return []
    config_dict = cast(dict[str, Any], config)

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

    def _section_dict(section_name: str) -> dict[str, Any]:
        section = config_dict.get(section_name, {})
        if isinstance(section, dict):
            return section
        logger.warning("Ignoring invalid %s config; expected a mapping.", section_name)
        return {}

    def _active_plugin_names(section_name: str, section: dict[str, Any]) -> list[str]:
        active_plugins: list[str] = []
        for plugin_name, plugin_info in section.items():
            if not isinstance(plugin_name, str):
                logger.warning(
                    "Ignoring invalid %s plugin key %r; expected a string.",
                    section_name,
                    plugin_name,
                )
                continue
            if not isinstance(plugin_info, dict):
                logger.warning(
                    "Ignoring invalid %s plugin entry for '%s'; expected a mapping.",
                    section_name,
                    plugin_name,
                )
                continue
            active = plugin_info.get("active", False)
            if isinstance(active, bool) and active:
                active_plugins.append(plugin_name)
            elif active is not None and not isinstance(active, bool):
                logger.warning(
                    "Ignoring non-boolean 'active' value for %s plugin '%s'; expected true/false.",
                    section_name,
                    plugin_name,
                )
        return active_plugins

    core_plugins_config = _section_dict(CONFIG_SECTION_PLUGINS)

    # Process and load custom plugins
    custom_plugins_config = _section_dict(CONFIG_SECTION_CUSTOM_PLUGINS)
    custom_plugin_dirs = get_custom_plugin_dirs()

    active_custom_plugins = _active_plugin_names(
        CONFIG_SECTION_CUSTOM_PLUGINS, custom_plugins_config
    )

    if active_custom_plugins:
        logger.debug(
            f"Loading active custom plugins: {', '.join(active_custom_plugins)}"
        )

    # Only load custom plugins that are explicitly enabled
    for plugin_name in active_custom_plugins:
        plugin_found = False

        # Validate plugin name to prevent path traversal attacks
        if not _is_safe_plugin_name(plugin_name):
            logger.warning(
                "Custom plugin name '%s' rejected: contains invalid characters or path traversal",
                plugin_name,
            )
            continue

        # Try each directory in order
        for custom_dir in custom_plugin_dirs:
            plugin_path = os.path.join(custom_dir, plugin_name)

            # Validate path containment to prevent symlink escapes
            if not _is_path_contained(custom_dir, plugin_path):
                logger.warning(
                    "Custom plugin path '%s' is not contained within allowed root '%s'",
                    plugin_path,
                    custom_dir,
                )
                continue

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
    community_plugins_config = _section_dict(CONFIG_SECTION_COMMUNITY_PLUGINS)
    community_plugin_dirs = get_community_plugin_dirs()

    if not community_plugin_dirs:
        logger.warning(
            "No writable community plugin directories available; clone/update operations will be skipped."
        )
        community_plugins_dir = None
    else:
        community_plugins_dir = community_plugin_dirs[0]

    # Create community plugins directory if needed
    active_community_plugins = _active_plugin_names(
        CONFIG_SECTION_COMMUNITY_PLUGINS, community_plugins_config
    )
    ready_community_plugins = []
    tag_ref_warning_logged = False

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

    # Only process community plugins explicitly enabled in config.
    for plugin_name, plugin_info in community_plugins_config.items():
        if not isinstance(plugin_name, str):
            logger.warning(
                "Ignoring invalid %s plugin key %r; expected a string.",
                CONFIG_SECTION_COMMUNITY_PLUGINS,
                plugin_name,
            )
            continue
        if not isinstance(plugin_info, dict):
            logger.warning(
                "Ignoring invalid %s plugin entry for '%s'; expected a mapping.",
                CONFIG_SECTION_COMMUNITY_PLUGINS,
                plugin_name,
            )
            continue

        active = plugin_info.get("active", False)
        if not isinstance(active, bool):
            if active is not None:
                logger.warning(
                    "Ignoring non-boolean 'active' value for %s plugin '%s'; expected true/false.",
                    CONFIG_SECTION_COMMUNITY_PLUGINS,
                    plugin_name,
                )
            logger.debug(
                f"Skipping community plugin {plugin_name} - not active in config"
            )
            continue
        if not active:
            logger.debug(
                f"Skipping community plugin {plugin_name} - not active in config"
            )
            continue

        repo_url = plugin_info.get("repository")

        # Support commit, tag, and branch parameters
        commit = plugin_info.get("commit")
        tag = plugin_info.get("tag")
        branch = plugin_info.get("branch")

        # Validate that repo_url and ref fields are strings if provided
        if repo_url is not None and not isinstance(repo_url, str):
            logger.warning(
                "Ignoring community plugin '%s': repository must be a string.",
                plugin_name,
            )
            continue
        invalid_ref_field = next(
            (
                field_name
                for field_name, field_value in (
                    ("commit", commit),
                    ("tag", tag),
                    ("branch", branch),
                )
                if field_value is not None and not isinstance(field_value, str)
            ),
            None,
        )
        if invalid_ref_field is not None:
            logger.warning(
                "Ignoring community plugin '%s': %s must be a string.",
                plugin_name,
                invalid_ref_field,
            )
            continue

        # Determine what to use (commit, tag, branch, or default)
        # Priority: commit > tag > branch
        explicit_branch_ref = False
        allow_moving_ref = plugin_info.get("allow_moving_ref") is True
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
            explicit_branch_ref = True
        else:
            # Default to the first configured default branch if neither is specified.
            logger.warning(
                "No ref specified; defaulting to branch 'main' is deprecated and unsafe"
            )
            ref = {"type": "branch", "value": DEFAULT_BRANCHES[0]}

        if ref["type"] == "tag" and not tag_ref_warning_logged:
            logger.warning("Tags can be retargeted; commit pins are safer")
            tag_ref_warning_logged = True
        elif ref["type"] == "branch" and explicit_branch_ref and not allow_moving_ref:
            logger.warning(
                "Branch refs are moving targets and not recommended in production"
            )

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
            if not _is_safe_plugin_name(repo_name):
                logger.error(
                    "Repository name '%s' rejected: contains invalid characters or path traversal",
                    repo_name,
                )
                continue
            repo_path = os.path.join(community_plugins_dir, repo_name)
            if not _is_path_contained(community_plugins_dir, repo_path):
                logger.error(
                    "Plugin repo path '%s' is not contained within allowed root '%s'",
                    repo_path,
                    community_plugins_dir,
                )
                continue
            if (
                validation_result.repo_url is None
                or validation_result.ref_type is None
                or validation_result.ref_value is None
            ):
                logger.error(
                    "Validation returned no repository URL or ref for community plugin %s",
                    plugin_name,
                )
                continue

            # Call public helper so tests and integrations can patch this seam.
            success = clone_or_update_repo(
                validation_result.repo_url,
                ref,
                community_plugins_dir,
            )
            if not success:
                logger.warning(f"Failed to clone/update plugin {plugin_name}, skipping")
                continue
            if ref["type"] == "commit":
                _check_commit_pin_for_upstream_updates(
                    plugin_name,
                    validation_result.repo_url,
                    repo_path,
                )
            if _check_auto_install_enabled(config, plugin_type=PLUGIN_TYPE_COMMUNITY):
                _install_requirements_for_repo(
                    repo_path, repo_name, plugin_type=PLUGIN_TYPE_COMMUNITY
                )
            ready_community_plugins.append(plugin_name)
        else:
            logger.error("Repository URL not specified for a community plugin")
            logger.error("Please specify the repository URL in config.yaml")
            continue

    # Only load community plugins that were successfully synced
    for plugin_name in ready_community_plugins:
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

            # Validate plugin name is safe before trying directories
            if not _is_safe_plugin_name(repo_name_candidate):
                logger.error(
                    "Plugin name '%s' rejected: contains invalid characters or path traversal",
                    repo_name_candidate,
                )
                continue

            # Try each directory in order
            plugin_found = False
            for dir_path in community_plugin_dirs:
                plugin_path = os.path.join(dir_path, repo_name_candidate)
                # Validate path containment to prevent symlink escapes
                if not _is_path_contained(dir_path, plugin_path):
                    logger.warning(
                        "Plugin path '%s' is not contained within allowed root '%s'",
                        plugin_path,
                        dir_path,
                    )
                    continue
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
            plugin_config = core_plugins_config.get(plugin_name, {})
            if not isinstance(plugin_config, dict):
                logger.warning(
                    "Ignoring invalid %s plugin entry for '%s'; expected a mapping.",
                    CONFIG_SECTION_PLUGINS,
                    plugin_name,
                )
                plugin_config = {}
            raw_active = plugin_config.get("active", False)
            if isinstance(raw_active, bool):
                is_active = raw_active
            else:
                if raw_active is not None:
                    logger.warning(
                        "Ignoring non-boolean 'active' value for plugin '%s'; expected true/false.",
                        plugin_name,
                    )
                is_active = False
        else:
            # Custom and community plugins: default to inactive unless specified
            # Custom plugins take precedence over community plugins.
            if plugin_name in custom_plugins_config:
                plugin_config = custom_plugins_config.get(plugin_name, {})
            elif plugin_name in community_plugins_config:
                plugin_config = community_plugins_config.get(plugin_name, {})
            else:
                plugin_config = {}

            if not isinstance(plugin_config, dict):
                logger.warning(
                    "Ignoring invalid plugin config for '%s'; expected a mapping.",
                    plugin_name,
                )
                plugin_config = {}
            raw_active = plugin_config.get("active", False)
            if isinstance(raw_active, bool):
                is_active = raw_active
            else:
                if raw_active is not None:
                    logger.warning(
                        "Ignoring non-boolean 'active' value for plugin '%s'; expected true/false.",
                        plugin_name,
                    )
                is_active = False

        if is_active:
            default_priority = getattr(plugin, "priority", DEFAULT_PLUGIN_PRIORITY)
            if not isinstance(default_priority, int) or isinstance(
                default_priority, bool
            ):
                default_priority = DEFAULT_PLUGIN_PRIORITY
            raw_priority = plugin_config.get("priority", default_priority)
            if isinstance(raw_priority, int) and not isinstance(raw_priority, bool):
                plugin.priority = raw_priority
            else:
                logger.warning(
                    "Ignoring invalid priority for '%s'; expected an integer, got %s.",
                    plugin_name,
                    type(raw_priority).__name__,
                )
                plugin.priority = default_priority
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
