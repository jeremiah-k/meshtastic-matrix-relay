# trunk-ignore-all(bandit)
import hashlib
import importlib
import importlib.util
import os
import re
import shutil
import site
import subprocess
import sys
from contextlib import contextmanager

from mmrelay.config import get_app_path, get_base_dir
from mmrelay.log_utils import get_logger

# Global config variable that will be set from main.py
config = None

logger = get_logger(name="Plugins")
sorted_active_plugins = []
plugins_loaded = False


@contextmanager
def _temp_sys_path(path: str):
    """
    Context manager that temporarily prepends a directory to Python's import search path.
    
    Use as: `with _temp_sys_path(path): ...` — the given `path` is inserted at the front of `sys.path` for the duration of the context. On exit the first occurrence of `path` is removed; if the path is already absent, removal is silently ignored.
    """
    sys.path.insert(0, path)
    try:
        yield
    finally:
        try:
            sys.path.remove(path)
        except ValueError:
            pass


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

    for path in dict.fromkeys(candidate_paths):  # dedupe while preserving order
        if not path:
            continue
        if path not in sys.path:
            try:
                site.addsitedir(path)
            except OSError as e:
                logger.warning(
                    f"site.addsitedir failed for '{path}': {e}. Falling back to sys.path.append."
                )
                sys.path.append(path)

    # Ensure import machinery notices new packages
    importlib.invalidate_caches()


def _get_plugin_dirs(plugin_type):
    """
    Return a prioritized list of directories for the specified plugin type, including user and local plugin directories if accessible.

    Parameters:
        plugin_type (str): Either "custom" or "community", specifying the type of plugins.

    Returns:
        list: List of plugin directories to search, with the user directory first if available, followed by the local directory for backward compatibility.
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
    Return the list of directories to search for community plugins, ordered by priority.

    The directories include the user-specific community plugins directory and a local directory for backward compatibility.
    """
    return _get_plugin_dirs("community")


def _run(cmd, timeout=120, **kwargs):
    # Validate command to prevent shell injection
    """
    Run a subprocess command safely with validated arguments and a configurable timeout.
    
    Validates that `cmd` is a non-empty list of non-empty strings (to avoid shell-injection risks),
    ensures text output by default, and executes the command via subprocess.run with check=True.
    
    Parameters:
        cmd (list[str]): Command and arguments to execute; must be a non-empty list of non-empty strings.
        timeout (int|float): Maximum seconds to allow the process to run before raising TimeoutExpired.
        **kwargs: Additional keyword arguments forwarded to subprocess.run (e.g., cwd, env). `text=True`
            is set by default if not provided.
    
    Returns:
        subprocess.CompletedProcess: The completed process object returned by subprocess.run.
    
    Raises:
        TypeError: If `cmd` is not a list or any element of `cmd` is not a string.
        ValueError: If `cmd` is empty or contains empty/whitespace-only arguments.
        subprocess.CalledProcessError: If the subprocess exits with a non-zero status (check=True).
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
    # Add capture_output and text for consistency
    kwargs.setdefault("text", True)
    return subprocess.run(cmd, check=True, timeout=timeout, **kwargs)


def _check_auto_install_enabled(config):
    """
    Return whether automatic dependency installation is enabled.
    
    Reads the value at config["security"]["auto_install_deps"] and returns its truthiness.
    If `config` is None or falsy, or the key is missing, this function returns True (auto-install enabled by default).
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


def clone_or_update_repo(repo_url, ref, plugins_dir):
    """
    Clone or update a community plugin Git repository and ensure its Python dependencies are installed.

    Performs a best-effort clone or update of the repository at repo_url into plugins_dir/repo_name using the provided ref (a dict with keys "type" ("tag" or "branch") and "value" (name)). If the repository already exists, the function attempts to fetch and switch to the requested branch or tag, with fallbacks to common default branches ("main", "master") when appropriate. After a successful clone/update, if a requirements.txt file exists in the repo root the function installs its requirements using pip or pipx (when a pipx environment is detected).

    Parameters:
        ref (dict): Reference spec with keys:
            - type: either "tag" or "branch".
            - value: the tag or branch name to check out.

    Returns:
        bool: True if the repository was successfully cloned/updated and dependency handling completed (or non-fatal errors occurred); False if a fatal git or filesystem error prevented cloning or updating.
    """
    # Extract the repository name from the URL
    repo_name = os.path.splitext(os.path.basename(repo_url.rstrip("/")))[0]
    repo_path = os.path.join(plugins_dir, repo_name)

    # Default branch names to try if ref is not specified
    default_branches = ["main", "master"]

    # Get the ref type and value
    ref_type = ref["type"]  # "tag" or "branch"
    ref_value = ref["value"]

    # Log what we're trying to do
    logger.info(f"Using {ref_type} '{ref_value}' for repository {repo_name}")

    # If it's a branch and one of the default branches, we'll handle it specially
    is_default_branch = ref_type == "branch" and ref_value in default_branches

    if os.path.isdir(repo_path):
        try:
            # Fetch all branches but don't fetch tags to avoid conflicts
            try:
                _run(["git", "-C", repo_path, "fetch", "origin"], timeout=120)
            except subprocess.CalledProcessError as e:
                logger.warning(f"Error fetching from remote: {e}")
                # Continue anyway, we'll try to use what we have

            # If it's a default branch, handle it differently
            if is_default_branch:
                try:
                    # Check if we're already on the right branch
                    current_branch = _run(
                        ["git", "-C", repo_path, "rev-parse", "--abbrev-ref", "HEAD"],
                        capture_output=True,
                    ).stdout.strip()

                    if current_branch == ref_value:
                        # We're on the right branch, just pull
                        try:
                            _run(["git", "-C", repo_path, "pull", "origin", ref_value])
                            logger.info(
                                f"Updated repository {repo_name} branch {ref_value}"
                            )
                            return True
                        except subprocess.CalledProcessError as e:
                            logger.warning(f"Error pulling branch {ref_value}: {e}")
                            # Continue anyway, we'll use what we have
                            return True
                    else:
                        # Switch to the right branch
                        _run(["git", "-C", repo_path, "checkout", ref_value])
                        _run(["git", "-C", repo_path, "pull", "origin", ref_value])
                        if ref_type == "branch":
                            logger.info(f"Switched to and updated branch {ref_value}")
                        else:
                            logger.info(f"Switched to and updated tag {ref_value}")
                        return True
                except subprocess.CalledProcessError:
                    # If we can't checkout the specified branch, try the other default branch
                    other_default = "main" if ref_value == "master" else "master"
                    try:
                        logger.warning(
                            f"Branch {ref_value} not found, trying {other_default}"
                        )
                        _run(["git", "-C", repo_path, "checkout", other_default])
                        _run(["git", "-C", repo_path, "pull", "origin", other_default])
                        logger.info(
                            f"Using {other_default} branch instead of {ref_value}"
                        )
                        return True
                    except subprocess.CalledProcessError:
                        # If that fails too, just use whatever branch we're on
                        logger.warning(
                            "Could not checkout any default branch, using current branch"
                        )
                        return True
            else:
                if ref_type == "branch":
                    try:
                        _run(["git", "-C", repo_path, "checkout", ref_value])
                        _run(["git", "-C", repo_path, "pull", "origin", ref_value])
                        logger.info(
                            f"Updated repository {repo_name} to branch {ref_value}"
                        )
                        return True
                    except subprocess.CalledProcessError as exc:
                        logger.warning(
                            "Failed to update branch %s for %s: %s",
                            ref_value,
                            repo_name,
                            exc,
                        )
                        return False

                # Handle tag checkout
                # Check if we're already on the correct tag/commit
                try:
                    # Get the current commit hash
                    current_commit = _run(
                        ["git", "-C", repo_path, "rev-parse", "HEAD"],
                        capture_output=True,
                    ).stdout.strip()

                    # Get the commit hash for the tag
                    tag_commit = None
                    try:
                        tag_commit = _run(
                            ["git", "-C", repo_path, "rev-parse", ref_value],
                            capture_output=True,
                        ).stdout.strip()
                    except subprocess.CalledProcessError:
                        # Tag doesn't exist locally, we'll need to fetch it
                        pass

                    # If we're already at the tag's commit, we're done
                    if tag_commit and current_commit == tag_commit:
                        logger.info(
                            f"Repository {repo_name} is already at tag {ref_value}"
                        )
                        return True

                    # Otherwise, try to checkout the tag or branch
                    _run(["git", "-C", repo_path, "checkout", ref_value])
                    logger.info(f"Updated repository {repo_name} to tag {ref_value}")
                    return True
                except subprocess.CalledProcessError:
                    # If tag checkout fails, try to fetch it specifically
                    logger.warning(
                        f"Tag {ref_value} not found locally, trying to fetch it specifically"
                    )
                    try:
                        # Try to fetch the specific tag, but first remove any existing tag with the same name
                        try:
                            # Delete the local tag if it exists to avoid conflicts
                            _run(["git", "-C", repo_path, "tag", "-d", ref_value])
                        except subprocess.CalledProcessError:
                            # Tag doesn't exist locally, which is fine
                            pass

                        # Now fetch the tag from remote
                        try:
                            # Try to fetch the tag
                            _run(
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
                            _run(
                                [
                                    "git",
                                    "-C",
                                    repo_path,
                                    "fetch",
                                    "origin",
                                    f"refs/tags/{ref_value}:refs/tags/{ref_value}",
                                ]
                            )

                        _run(["git", "-C", repo_path, "checkout", ref_value])
                        logger.info(
                            f"Successfully fetched and checked out tag {ref_value}"
                        )
                        return True
                    except subprocess.CalledProcessError:
                        # If that fails too, try as a branch
                        logger.warning(
                            f"Could not fetch tag {ref_value}, trying as a branch"
                        )
                        try:
                            _run(["git", "-C", repo_path, "fetch", "origin", ref_value])
                            _run(["git", "-C", repo_path, "checkout", ref_value])
                            _run(["git", "-C", repo_path, "pull", "origin", ref_value])
                            logger.info(
                                f"Updated repository {repo_name} to branch {ref_value}"
                            )
                            return True
                        except subprocess.CalledProcessError:
                            # If all else fails, just use a default branch
                            logger.warning(
                                f"Could not checkout {ref_value} as tag or branch, trying default branches"
                            )
                            for default_branch in default_branches:
                                try:
                                    _run(
                                        [
                                            "git",
                                            "-C",
                                            repo_path,
                                            "checkout",
                                            default_branch,
                                        ]
                                    )
                                    _run(
                                        [
                                            "git",
                                            "-C",
                                            repo_path,
                                            "pull",
                                            "origin",
                                            default_branch,
                                        ]
                                    )
                                    logger.info(
                                        f"Using {default_branch} instead of {ref_value}"
                                    )
                                    return True
                                except subprocess.CalledProcessError:
                                    continue

                            # If we get here, we couldn't checkout any branch
                            logger.warning(
                                "Could not checkout any branch, using current state"
                            )
                            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Error updating repository {repo_name}: {e}")
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
            # If it's a default branch, just clone it directly
            if is_default_branch:
                try:
                    # Try to clone with the specified branch
                    _run(
                        ["git", "clone", "--branch", ref_value, repo_url],
                        cwd=plugins_dir,
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
                        _run(
                            ["git", "clone", "--branch", other_default, repo_url],
                            cwd=plugins_dir,
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
                        _run(["git", "clone", repo_url], cwd=plugins_dir)
                        logger.info(
                            f"Cloned repository {repo_name} from {repo_url} (default branch)"
                        )
                        return True
            else:
                # It's a tag, try to clone with the tag
                try:
                    # Try to clone with the specified tag
                    _run(
                        ["git", "clone", "--branch", ref_value, repo_url],
                        cwd=plugins_dir,
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
                    _run(["git", "clone", repo_url], cwd=plugins_dir)

                    # Then try to fetch and checkout the tag
                    try:
                        # Try to fetch the tag
                        try:
                            _run(
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
                            _run(
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
                        _run(["git", "-C", repo_path, "checkout", ref_value])
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
                            _run(["git", "-C", repo_path, "fetch", "origin", ref_value])
                            _run(["git", "-C", repo_path, "checkout", ref_value])
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
        except subprocess.CalledProcessError as e:
            logger.error(f"Error cloning repository {repo_name}: {e}")
            logger.error(
                f"Please manually git clone the repository {repo_url} into {repo_path}"
            )
            return False
    # Install requirements if requirements.txt exists
    requirements_path = os.path.join(repo_path, "requirements.txt")
    if os.path.isfile(requirements_path):
        auto_install = _check_auto_install_enabled(config)
        if not auto_install:
            logger.warning(
                f"Auto-install of requirements for {repo_name} disabled by config; skipping."
            )
            return True
        try:
            # Check if we're running in a pipx environment
            in_pipx = "PIPX_HOME" in os.environ or "PIPX_LOCAL_VENVS" in os.environ

            if in_pipx:
                # Use pipx to install the requirements.txt
                logger.info(f"Installing requirements for plugin {repo_name} with pipx")
                pipx_path = shutil.which("pipx")
                if not pipx_path:
                    raise FileNotFoundError("pipx executable not found on PATH")
                _run(
                    [pipx_path, "inject", "mmrelay", "-r", requirements_path],
                    timeout=600,
                )
            else:
                in_venv = (sys.prefix != getattr(sys, "base_prefix", sys.prefix)) or (
                    "VIRTUAL_ENV" in os.environ
                )
                # Use pip to install the requirements.txt
                logger.info(f"Installing requirements for plugin {repo_name} with pip")
                cmd = [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    requirements_path,
                    "--disable-pip-version-check",
                    "--no-input",
                ]
                if not in_venv:
                    cmd += ["--user"]
                _run(cmd, timeout=600)

            logger.info(f"Successfully installed requirements for plugin {repo_name}")
            # Make newly installed packages visible immediately
            _refresh_dependency_paths()
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.error(f"Error installing requirements for plugin {repo_name}: {e}")
            logger.error(
                f"Please manually install the requirements from {requirements_path}"
            )
            # Don't exit, just continue with a warning
            logger.warning(
                f"Plugin {repo_name} may not work correctly without its dependencies"
            )


def load_plugins_from_directory(directory, recursive=False):
    """
    Load and instantiate Plugin classes from Python files in a directory.
    
    Searches `directory` (optionally recursively) for .py files, imports each module in an isolated module name and, if the module defines a `Plugin` class, instantiates and collects it. If an import fails with ModuleNotFoundError, the function will (when auto-install is enabled in the global `config`) attempt to install the missing distribution with pip or pipx, refresh import paths, and retry importing the module. Files that do not define `Plugin` are skipped; unresolved import errors or other exceptions are logged and do not abort the whole scan.
    
    Parameters:
        directory (str): Path to the directory containing plugin Python files.
        recursive (bool): If True, scan subdirectories recursively; otherwise only the top-level directory.
    
    Returns:
        list: Instances of found plugin classes (may be empty).
    
    Notes:
    - The function mutates interpreter import state (may add entries to sys.modules) and can invoke external installers (pip/pipx) when auto-install is enabled.
    - Only modules that define a top-level `Plugin` attribute are instantiated and returned.
    """
    plugins = []
    if os.path.isdir(directory):
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
    else:
        if not plugins_loaded:  # Only log the missing directory once
            logger.debug(f"Directory {directory} does not exist.")
    return plugins


def load_plugins(passed_config=None):
    """
    Discovers, loads, and initializes all active plugins based on the provided or global configuration.

    This function orchestrates the full plugin lifecycle, including:
    - Loading core, custom, and community plugins as specified in the configuration.
    - Cloning or updating community plugin repositories and installing their dependencies.
    - Dynamically loading plugin classes from discovered directories.
    - Filtering and sorting plugins by their configured priority.
    - Starting each active plugin.

    If plugins have already been loaded, returns the cached sorted list.

    Parameters:
        passed_config (dict, optional): Configuration dictionary to use instead of the global configuration.

    Returns:
        list: Active plugin instances, sorted by priority.
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

    # Get the first directory for cloning (prefer user directory)
    community_plugins_dir = community_plugin_dirs[
        0
    ]  # Use the user directory for new clones

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

            # Support both tag and branch parameters
            tag = plugin_info.get("tag")
            branch = plugin_info.get("branch")

            # Determine what to use (tag, branch, or default)
            if tag and branch:
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
                # Clone to the user directory by default
                success = clone_or_update_repo(repo_url, ref, community_plugins_dir)
                if not success:
                    logger.warning(
                        f"Failed to clone/update plugin {plugin_name}, skipping"
                    )
                    continue
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
            active_plugins.append(plugin)
            try:
                plugin.start()
            except Exception:
                logger.exception(f"Error starting plugin {plugin_name}")

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
