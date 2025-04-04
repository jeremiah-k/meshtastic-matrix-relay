# trunk-ignore-all(bandit)
import hashlib
import importlib.util
import os
import subprocess
import sys
import pathlib # Use pathlib

# Import the new path utilities
from mmrelay.path_utils import get_custom_plugins_dir, get_community_plugins_dir
from mmrelay.config import relay_config # Keep this for reading plugin config sections
from mmrelay.log_utils import get_logger

logger = get_logger(name="Plugins")
sorted_active_plugins = []
plugins_loaded = False


def clone_or_update_repo(repo_url, tag, community_plugins_dir: pathlib.Path): # Use Path type hint
    # Extract the repository name from the URL
    repo_name = os.path.splitext(os.path.basename(repo_url.rstrip("/")))[0]
    # Use pathlib for path joining
    repo_path = community_plugins_dir / repo_name
    # Convert pathlib.Path to string for subprocess calls if needed, although often works directly
    repo_path_str = str(repo_path)
    community_plugins_dir_str = str(community_plugins_dir)

    if repo_path.is_dir():
        try:
            subprocess.check_call(["git", "-C", repo_path_str, "fetch"])
            # Use rev-parse to check if tag exists locally before checkout
            subprocess.check_call(["git", "-C", repo_path_str, "rev-parse", f"{tag}^{{commit}}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", repo_path_str, "checkout", tag])
            # Pull only if not detached HEAD (i.e., tracking a branch that matches tag)
            current_branch = subprocess.check_output(["git", "-C", repo_path_str, "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
            if current_branch == tag:
                 subprocess.check_call(["git", "-C", repo_path_str, "pull", "origin", tag])
            logger.info(f"Updated repository {repo_name} to {tag}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Could not checkout/update tag '{tag}' for repository {repo_name}. It might be a commit hash or non-existent tag. Error: {e}")
            # Allow proceeding if checkout failed but repo exists, might be manual setup
    else:
        try:
            # Ensure parent dir exists (handled by path_utils, but belt-and-suspenders)
            community_plugins_dir.mkdir(parents=True, exist_ok=True)
            # Clone directly into the correct path
            subprocess.check_call(
                ["git", "clone", "--branch", tag, repo_url, repo_path_str] # Clone into specific path
            )
            logger.info(f"Cloned repository {repo_name} from {repo_url} at {tag}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Error cloning repository {repo_name}: {e}")
            logger.error(
                f"Please manually git clone the repository {repo_url} into {repo_path_str}"
            )
            sys.exit(1) # Exit if initial clone fails

    # Install requirements if requirements.txt exists
    requirements_path = repo_path / "requirements.txt"
    if requirements_path.is_file():
        try:
            # Use pip to install the requirements.txt
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "-r", str(requirements_path)]
            )
            logger.info(f"Installed requirements for plugin {repo_name}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Error installing requirements for plugin {repo_name}: {e}")
            logger.error(
                f"Please manually install the requirements from {str(requirements_path)}"
            )
            # Decide if this should be fatal, maybe not?
            # sys.exit(1)


def load_plugins_from_directory(directory: pathlib.Path, recursive=False): # Use Path type hint
    plugins = []
    # Use pathlib methods
    if directory.is_dir():
        logger.debug(f"Scanning for plugins in: {directory}")
        # Use glob for finding python files directly
        pattern = "**/*.py" if recursive else "*.py"
        for plugin_path in directory.glob(pattern):
            if plugin_path.is_file():
                # Generate a unique module name based on path hash
                # Use relative path from the base plugin dir for stability if possible
                try:
                    relative_path = plugin_path.relative_to(directory.parent) # Use parent (plugins/)
                    module_name_suffix = str(relative_path).replace(os.sep, '_').removesuffix(".py")
                except ValueError: # If not relative (e.g. scanning root directly)
                     module_name_suffix = hashlib.sha256(str(plugin_path).encode("utf-8")).hexdigest()[:16]

                module_name = f"mmrelay.external_plugins.{module_name_suffix}"

                spec = importlib.util.spec_from_file_location(
                    module_name, str(plugin_path) # Use string path for spec
                )
                if spec and spec.loader: # Check if spec and loader are valid
                    plugin_module = importlib.util.module_from_spec(spec)
                    # Add to sys.modules BEFORE execution to handle relative imports within plugin
                    sys.modules[module_name] = plugin_module
                    try:
                        spec.loader.exec_module(plugin_module)
                        if hasattr(plugin_module, "Plugin"):
                            # Instantiate the plugin
                            plugin_instance = plugin_module.Plugin()
                            # Store the module path with the instance if needed later
                            plugin_instance._module_path = str(plugin_path)
                            plugins.append(plugin_instance)
                            logger.debug(f"Successfully loaded plugin from: {plugin_path}")
                        else:
                            logger.warning(
                                f"{plugin_path} does not define a 'Plugin' class."
                            )
                    except Exception as e:
                        logger.error(f"Error executing plugin module {plugin_path}: {e}", exc_info=True)
                        # Remove from sys.modules if loading failed
                        if module_name in sys.modules:
                             del sys.modules[module_name]
                else:
                     logger.warning(f"Could not create import spec for: {plugin_path}")

    else:
        if not plugins_loaded:  # Only log the missing directory once
            logger.debug(f"Plugin directory does not exist or is not a directory: {directory}")
    return plugins


def load_plugins():
    global sorted_active_plugins
    global plugins_loaded

    if plugins_loaded:
        return sorted_active_plugins

    logger.info("Loading plugins...")

    config = relay_config # Use relay_config loaded in config.py

    # Import core plugins (these are always part of the package)
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

    # Initial list of core plugin instances
    core_plugins_instances = [
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
    # Map core plugin class name to instance for easier lookup
    core_plugin_map = {p.__class__.__name__: p for p in core_plugins_instances}

    # Start with core plugins
    loaded_plugins = core_plugins_instances.copy()

    # --- Load Custom Plugins ---
    custom_plugins_config = config.get("custom-plugins", {})
    # Get the correct custom plugins directory using path_utils
    custom_plugins_dir: pathlib.Path = get_custom_plugins_dir()
    logger.info(f"Looking for custom plugins in: {custom_plugins_dir}")

    active_custom_plugin_names = [
        name for name, info in custom_plugins_config.items() if info.get("active", False)
    ]

    if active_custom_plugin_names:
        logger.debug(f"Attempting to load active custom plugins: {', '.join(active_custom_plugin_names)}")
        # Load all found plugins first, then filter by active config
        found_custom_plugins = load_plugins_from_directory(custom_plugins_dir, recursive=True) # Allow subdirs
        # Add only the ones marked active in config
        for plugin in found_custom_plugins:
             plugin_name = getattr(plugin, "plugin_name", plugin.__class__.__name__)
             if plugin_name in active_custom_plugin_names:
                  loaded_plugins.append(plugin)
             else:
                  logger.debug(f"Found custom plugin '{plugin_name}' but it's not active in config.")
    else:
         logger.debug("No custom plugins marked active in configuration.")


    # --- Load Community Plugins ---
    community_plugins_config = config.get("community-plugins", {})
    # Get the correct community plugins directory using path_utils
    community_plugins_dir: pathlib.Path = get_community_plugins_dir()
    logger.info(f"Looking for community plugins in: {community_plugins_dir}")

    active_community_plugin_names = [
        name for name, info in community_plugins_config.items() if info.get("active", False)
    ]

    # Download/update repositories for active community plugins FIRST
    if isinstance(community_plugins_config, dict):
        for plugin_name, plugin_info in community_plugins_config.items():
            if plugin_info.get("active", False):
                repo_url = plugin_info.get("repository")
                tag = plugin_info.get("tag", "main") # Default to 'main' branch
                if repo_url:
                    logger.debug(f"Checking/updating community plugin '{plugin_name}' from {repo_url} @ {tag}")
                    # Pass the specific community plugins directory
                    clone_or_update_repo(repo_url, tag, community_plugins_dir)
                else:
                    logger.error(f"Repository URL not specified for active community plugin '{plugin_name}'. Skipping.")


    # NOW, load the actual plugin code from the directories
    if active_community_plugin_names:
         logger.debug(f"Attempting to load active community plugins code: {', '.join(active_community_plugin_names)}")
         # Load from the base community dir, assuming each repo is a subdir
         found_community_plugins = load_plugins_from_directory(community_plugins_dir, recursive=True) # Search subdirs
         # Add only the ones marked active in config
         for plugin in found_community_plugins:
              plugin_name = getattr(plugin, "plugin_name", plugin.__class__.__name__)
              if plugin_name in active_community_plugin_names:
                   loaded_plugins.append(plugin)
              else:
                  # This might log plugins from inactive repos if they were downloaded previously
                  logger.debug(f"Found community plugin code '{plugin_name}' but it's not active in config.")
    else:
         logger.debug("No community plugins marked active in configuration.")


    # --- Filter, Sort, and Start Active Plugins ---
    active_plugins_final = []
    plugin_priorities = {} # Store priorities read from config

    # Read priorities from config first
    all_plugin_configs = config.get("plugins", {}) # Core plugins section
    all_plugin_configs.update(custom_plugins_config) # Add custom
    all_plugin_configs.update(community_plugins_config) # Add community

    for name, conf in all_plugin_configs.items():
        if isinstance(conf, dict) and "priority" in conf:
             plugin_priorities[name] = conf.get("priority")

    # Determine final active set based on config and apply priorities
    for plugin in loaded_plugins:
        plugin_name = getattr(plugin, "plugin_name", plugin.__class__.__name__)
        is_core = plugin_name in core_plugin_map

        # Get specific config section for this plugin
        if is_core:
            plugin_config = config.get("plugins", {}).get(plugin_name, {})
            is_active = plugin_config.get("active", False) # Core default inactive
        elif plugin_name in custom_plugins_config:
            plugin_config = custom_plugins_config.get(plugin_name, {})
            is_active = plugin_config.get("active", False) # Custom default inactive
        elif plugin_name in community_plugins_config:
            plugin_config = community_plugins_config.get(plugin_name, {})
            is_active = plugin_config.get("active", False) # Community default inactive
        else:
             # Should not happen if loading logic is correct, but good fallback
             logger.warning(f"Plugin '{plugin_name}' loaded but not found in any config section. Treating as inactive.")
             is_active = False
             plugin_config = {}


        if is_active:
            # Apply priority: Config overrides class attribute, default is 100
            plugin.priority = plugin_priorities.get(plugin_name, getattr(plugin, "priority", 100))
            active_plugins_final.append(plugin)
            logger.debug(f"Activating plugin '{plugin_name}' with priority {plugin.priority}")
            try:
                # Call start method if it exists
                start_method = getattr(plugin, "start", None)
                if callable(start_method):
                     start_method()
            except Exception as e:
                logger.error(f"Error during start() method for plugin {plugin_name}: {e}", exc_info=True)
        else:
            # Ensure inactive plugins don't run stop methods later if they weren't started
             logger.debug(f"Plugin '{plugin_name}' is inactive.")


    # Sort the final active list by priority
    sorted_active_plugins = sorted(active_plugins_final, key=lambda p: p.priority)

    # Log the final list of active plugins
    if sorted_active_plugins:
        final_plugin_names = [getattr(p, "plugin_name", p.__class__.__name__) for p in sorted_active_plugins]
        logger.info(f"Active plugins loaded and sorted by priority: {', '.join(final_plugin_names)}")
    else:
        logger.info("No active plugins were loaded.")

    plugins_loaded = True
    return sorted_active_plugins
