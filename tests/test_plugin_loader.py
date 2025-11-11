#!/usr/bin/env python3
"""
Test suite for plugin loading system in MMRelay.

Tests the plugin discovery, loading, and management functionality including:
- Plugin directory discovery and prioritization
- Core plugin loading and initialization
- Custom plugin loading from filesystem
- Community plugin repository handling
- Plugin configuration and activation
- Plugin priority sorting and startup
"""

import os
import shutil
import subprocess  # nosec B404 - Used for controlled test environment operations
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import mmrelay.plugin_loader as pl
from mmrelay.plugin_loader import (
    _clean_python_cache,
    _clone_new_repo_to_branch_or_tag,
    _collect_requirements,
    _filter_risky_requirements,
    _install_requirements_for_repo,
    _is_repo_url_allowed,
    _run,
    _update_existing_repo_to_branch_or_tag,
    _validate_clone_inputs,
    clear_plugin_jobs,
    clone_or_update_repo,
    get_community_plugin_dirs,
    get_custom_plugin_dirs,
    load_plugins,
    load_plugins_from_directory,
    schedule_job,
    shutdown_plugins,
    start_global_scheduler,
    stop_global_scheduler,
)


class MockPlugin:
    """Mock plugin class for testing."""

    def __init__(self, name="test_plugin", priority=10):
        """
        Initialize a mock plugin with a specified name and priority.

        Parameters:
            name (str): The name of the plugin.
            priority (int): The plugin's priority for loading and activation.
        """
        self.plugin_name = name
        self.priority = priority
        self.started = False

    def start(self):
        """
        Marks the mock plugin as started by setting the `started` flag to True.
        """
        self.started = True

    def stop(self):
        """
        Marks the mock plugin as stopped by setting the `started` flag to False.
        """
        self.started = False

    async def handle_meshtastic_message(
        self, packet, interface, longname, shortname, meshnet_name
    ):
        """
        Mock handler for Meshtastic messages used in tests; performs no action to suppress warnings.

        Parameters:
            packet: The raw Meshtastic packet object received.
            interface: The interface name or object the packet arrived on.
            longname (str): Sender's long display name.
            shortname (str): Sender's short/abbreviated name.
            meshnet_name (str): The mesh network identifier.
        """
        pass

    async def handle_room_message(self, room, event, full_message):
        """
        Handle an incoming room message event for the mock plugin used in tests.

        This method is a no-op stub that satisfies the plugin interface during testing and intentionally performs no action.

        Parameters:
            room: Identifier or object representing the destination room for the message.
            event: Payload object or mapping describing the message event (metadata, sender, etc.).
            full_message: The full message text content received.
        """
        pass


class BaseGitTest(unittest.TestCase):
    """Base class for tests that need temporary Git repository directories."""

    def setUp(self):
        """
        Prepare temporary directories for plugin tests.

        Creates a temporary directory and assigns its path to `self.temp_plugins_dir`,
        then sets `self.temp_repo_path` to a `repo` subdirectory path inside it.
        """
        super().setUp()
        self.temp_plugins_dir = tempfile.mkdtemp()
        self.temp_repo_path = os.path.join(self.temp_plugins_dir, "repo")

    def tearDown(self):
        """
        Cleans up test resources created in setUp.

        Removes the temporary plugins directory used by the test and delegates further teardown to the superclass.
        """
        super().tearDown()
        shutil.rmtree(self.temp_plugins_dir, ignore_errors=True)


class TestPluginLoader(BaseGitTest):
    """Test cases for plugin loading functionality."""

    def setUp(self):
        """
        Prepares a temporary test environment with isolated plugin directories and resets plugin loader state before each test.
        """
        super().setUp()
        # Create temporary directories for testing
        self.test_dir = tempfile.mkdtemp()
        self.custom_dir = os.path.join(self.test_dir, "plugins", "custom")
        self.community_dir = os.path.join(self.test_dir, "plugins", "community")

        os.makedirs(self.custom_dir, exist_ok=True)
        os.makedirs(self.community_dir, exist_ok=True)

        # Reset plugin loader state
        import mmrelay.plugin_loader

        mmrelay.plugin_loader.plugins_loaded = False
        mmrelay.plugin_loader.sorted_active_plugins = []

    def tearDown(self):
        """
        Remove temporary directories and clean up resources after each test.
        """
        super().tearDown()
        # Clean up temporary directories
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("mmrelay.plugin_loader.get_base_dir")
    @patch("mmrelay.plugin_loader.get_app_path")
    @patch("os.makedirs")
    def test_get_custom_plugin_dirs(
        self, mock_makedirs, mock_get_app_path, mock_get_base_dir
    ):
        """
        Test that custom plugin directories are discovered and created as expected.

        Verifies that `get_custom_plugin_dirs()` returns the correct list of custom plugin directories and that the directory creation function is called for each directory.
        """
        import tempfile

        mock_get_base_dir.return_value = self.test_dir

        # Use a temporary directory instead of hardcoded path
        with tempfile.TemporaryDirectory() as temp_app_dir:
            mock_get_app_path.return_value = temp_app_dir

            dirs = get_custom_plugin_dirs()

            expected_dirs = [
                os.path.join(self.test_dir, "plugins", "custom"),
                os.path.join(temp_app_dir, "plugins", "custom"),
            ]
            self.assertEqual(dirs, expected_dirs)
        # Should be called twice: once for user dir, once for local dir
        self.assertEqual(mock_makedirs.call_count, 2)

    @patch("mmrelay.plugin_loader.get_base_dir")
    @patch("mmrelay.plugin_loader.get_app_path")
    @patch("os.makedirs")
    def test_get_community_plugin_dirs(
        self, mock_makedirs, mock_get_app_path, mock_get_base_dir
    ):
        """
        Test that the community plugin directory discovery returns the correct directories and creates them if they do not exist.
        """
        import tempfile

        mock_get_base_dir.return_value = self.test_dir

        # Use a temporary directory instead of hardcoded path
        with tempfile.TemporaryDirectory() as temp_app_dir:
            mock_get_app_path.return_value = temp_app_dir

            dirs = get_community_plugin_dirs()

            expected_dirs = [
                os.path.join(self.test_dir, "plugins", "community"),
                os.path.join(temp_app_dir, "plugins", "community"),
            ]
            self.assertEqual(dirs, expected_dirs)
        # Should be called twice: once for user dir, once for local dir
        self.assertEqual(mock_makedirs.call_count, 2)

    def test_load_plugins_from_directory_empty(self):
        """
        Test that loading plugins from an empty directory returns an empty list.

        Verifies that no plugins are loaded when the specified directory contains no plugin files.
        """
        plugins = load_plugins_from_directory(self.custom_dir)
        self.assertEqual(plugins, [])

    def test_load_plugins_from_directory_nonexistent(self):
        """
        Test that loading plugins from a non-existent directory returns an empty list.
        """
        nonexistent_dir = os.path.join(self.test_dir, "nonexistent")
        plugins = load_plugins_from_directory(nonexistent_dir)
        self.assertEqual(plugins, [])

    def test_load_plugins_from_directory_with_plugin(self):
        """
        Verifies that loading plugins from a directory containing a valid plugin file returns the plugin with correct attributes.
        """
        # Create a test plugin file
        plugin_content = """
class Plugin:
    def __init__(self):
        self.plugin_name = "test_plugin"
        self.priority = 10
        
    def start(self):
        pass
"""
        plugin_file = os.path.join(self.custom_dir, "test_plugin.py")
        with open(plugin_file, "w") as f:
            f.write(plugin_content)

        plugins = load_plugins_from_directory(self.custom_dir)

        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].plugin_name, "test_plugin")
        self.assertEqual(plugins[0].priority, 10)

    def test_load_plugins_from_directory_no_plugin_class(self):
        """
        Verify that loading plugins from a directory containing a Python file without a Plugin class returns an empty list.
        """
        # Create a Python file without Plugin class
        plugin_content = """
def some_function():
    pass
"""
        plugin_file = os.path.join(self.custom_dir, "not_a_plugin.py")
        with open(plugin_file, "w") as f:
            f.write(plugin_content)

        plugins = load_plugins_from_directory(self.custom_dir)
        self.assertEqual(plugins, [])

    def test_load_plugins_dependency_install_refreshes_path(self):
        """
        Verify that when a plugin requires a package, a dependency installation into the user's site-packages is made importable during plugin loading.

        This test creates a plugin that imports a fake dependency, simulates installing that dependency into a test user site directory (via a patched subprocess call), and patches site package discovery and addsitedir behavior. It then calls the plugin loader and asserts:
        - the plugin is discovered and loaded,
        - the test user site directory was added to the interpreter import path,
        - the plugin source directory itself was not added to sys.path.
        """

        for var in ("PIPX_HOME", "PIPX_LOCAL_VENVS"):
            os.environ.pop(var, None)

        user_site = os.path.join(self.test_dir, "user_site")
        os.makedirs(user_site, exist_ok=True)

        plugin_content = """
import mockdep


class Plugin:
    def __init__(self):
        self.plugin_name = "dep_plugin"
        self.priority = 1

    def start(self):
        pass
"""
        plugin_file = os.path.join(self.custom_dir, "dep_plugin.py")
        with open(plugin_file, "w", encoding="utf-8") as handle:
            handle.write(plugin_content)

        def fake_check_call(_cmd, *_args, **_kwargs):  # nosec B603
            """
            Simulate subprocess.check_call and install a minimal importable dependency into the test user site directory.

            Writes a file named "mockdep.py" containing `VALUE = 1` into the test `user_site` directory so the module can be imported. All additional positional and keyword arguments are ignored.

            Returns:
                subprocess.CompletedProcess: A CompletedProcess with `args` set to the provided `_cmd` and `returncode` 0.
            """
            with open(
                os.path.join(user_site, "mockdep.py"), "w", encoding="utf-8"
            ) as dep:
                dep.write("VALUE = 1\n")
            return subprocess.CompletedProcess(args=_cmd, returncode=0)

        added_dirs = []

        def fake_addsitedir(path):
            """
            Register a directory for testing and ensure it is available to the Python import system.

            Adds the given path to the external `added_dirs` list and places it at the front of `sys.path` if it is not already present so imports prefer that directory.

            Parameters:
                path (str): Filesystem path to register on the import search path.
            """
            added_dirs.append(path)
            if path not in sys.path:
                sys.path.insert(0, path)

        with (
            patch("mmrelay.plugin_loader.subprocess.run", side_effect=fake_check_call),
            patch(
                "mmrelay.plugin_loader.site.getusersitepackages",
                return_value=[user_site],
            ),
            patch("mmrelay.plugin_loader.site.getsitepackages", return_value=[]),
            patch("mmrelay.plugin_loader.site.addsitedir", side_effect=fake_addsitedir),
        ):
            try:
                plugins = load_plugins_from_directory(self.custom_dir)
            finally:
                sys.modules.pop("mockdep", None)
                if user_site in sys.path:
                    sys.path.remove(user_site)

        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].plugin_name, "dep_plugin")
        self.assertIn(user_site, added_dirs)
        self.assertNotIn(self.custom_dir, sys.path)

    def test_load_plugins_from_directory_syntax_error(self):
        """
        Verify that loading plugins from a directory containing a Python file with a syntax error returns an empty list without raising exceptions.
        """
        # Create a Python file with syntax error
        plugin_content = """
class Plugin:
    def __init__(self):
        self.plugin_name = "broken_plugin"
        # Syntax error below
        if True
            pass
"""
        plugin_file = os.path.join(self.custom_dir, "broken_plugin.py")
        with open(plugin_file, "w") as f:
            f.write(plugin_content)

        plugins = load_plugins_from_directory(self.custom_dir)
        self.assertEqual(plugins, [])

    @patch("mmrelay.plugins.health_plugin.Plugin")
    @patch("mmrelay.plugins.map_plugin.Plugin")
    @patch("mmrelay.plugins.help_plugin.Plugin")
    @patch("mmrelay.plugins.nodes_plugin.Plugin")
    @patch("mmrelay.plugins.drop_plugin.Plugin")
    @patch("mmrelay.plugins.debug_plugin.Plugin")
    def test_load_plugins_core_only(self, *mock_plugins):
        """
        Test that only core plugins are loaded, sorted by priority, and started when activated in the configuration.

        Verifies that all core plugins specified as active in the configuration are instantiated, sorted by their priority attribute, and their start methods are called.
        """
        # Mock all core plugins
        for i, mock_plugin_class in enumerate(mock_plugins):
            mock_plugin = MockPlugin(f"core_plugin_{i}", priority=i)
            mock_plugin_class.return_value = mock_plugin

        # Set up minimal config with no custom plugins
        config = {
            "plugins": {
                f"core_plugin_{i}": {"active": True} for i in range(len(mock_plugins))
            }
        }

        import mmrelay.plugin_loader

        mmrelay.plugin_loader.config = config

        plugins = load_plugins(config)

        # Should have loaded all core plugins
        self.assertEqual(len(plugins), len(mock_plugins))

        # Verify plugins are sorted by priority
        for i in range(len(plugins) - 1):
            self.assertLessEqual(plugins[i].priority, plugins[i + 1].priority)

        # Verify all plugins were started
        for plugin in plugins:
            self.assertTrue(plugin.started)

    @patch("mmrelay.plugins.health_plugin.Plugin")
    @patch("mmrelay.plugins.map_plugin.Plugin")
    @patch("mmrelay.plugins.help_plugin.Plugin")
    @patch("mmrelay.plugins.nodes_plugin.Plugin")
    @patch("mmrelay.plugins.drop_plugin.Plugin")
    @patch("mmrelay.plugins.debug_plugin.Plugin")
    def test_load_plugins_inactive_plugins(self, *mock_plugins):
        """
        Verify that only active plugins specified in the configuration are loaded, and inactive plugins are excluded.
        """
        # Mock core plugins
        for i, mock_plugin_class in enumerate(mock_plugins):
            mock_plugin = MockPlugin(f"core_plugin_{i}", priority=i)
            mock_plugin_class.return_value = mock_plugin

        # Set up config with some plugins inactive
        config = {
            "plugins": {
                "core_plugin_0": {"active": True},
                "core_plugin_1": {"active": False},  # Inactive
                "core_plugin_2": {"active": True},
            }
        }

        import mmrelay.plugin_loader

        mmrelay.plugin_loader.config = config

        plugins = load_plugins(config)

        # Should only load active plugins
        active_plugin_names = [p.plugin_name for p in plugins]
        self.assertIn("core_plugin_0", active_plugin_names)
        self.assertNotIn("core_plugin_1", active_plugin_names)
        self.assertIn("core_plugin_2", active_plugin_names)

    @patch("mmrelay.plugins.debug_plugin.Plugin")
    @patch("mmrelay.plugins.drop_plugin.Plugin")
    @patch("mmrelay.plugins.nodes_plugin.Plugin")
    @patch("mmrelay.plugins.help_plugin.Plugin")
    @patch("mmrelay.plugins.map_plugin.Plugin")
    @patch("mmrelay.plugins.health_plugin.Plugin")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    def test_load_plugins_with_custom(self, mock_get_custom_plugin_dirs, *mock_plugins):
        """
        Tests that both core and custom plugins are loaded and activated when specified as active in the configuration.

        Ensures the plugin loader discovers, instantiates, and includes both a mocked core plugin and a custom plugin from a temporary directory in the loaded plugin list when both are marked active in the config.
        """
        # Mock core plugins
        for i, mock_plugin_class in enumerate(mock_plugins):
            mock_plugin = MockPlugin(f"core_plugin_{i}", priority=i)
            mock_plugin_class.return_value = mock_plugin

        # Set up custom plugin directory
        mock_get_custom_plugin_dirs.return_value = [self.custom_dir]

        # Create a custom plugin
        custom_plugin_dir = os.path.join(self.custom_dir, "my_custom_plugin")
        os.makedirs(custom_plugin_dir, exist_ok=True)

        plugin_content = """
class Plugin:
    def __init__(self):
        self.plugin_name = "my_custom_plugin"
        self.priority = 5

    def start(self):
        pass
"""
        plugin_file = os.path.join(custom_plugin_dir, "plugin.py")
        with open(plugin_file, "w") as f:
            f.write(plugin_content)

        # Set up config with custom plugin active
        config = {
            "plugins": {
                "core_plugin_0": {"active": True},
            },
            "custom-plugins": {"my_custom_plugin": {"active": True}},
        }

        import mmrelay.plugin_loader

        mmrelay.plugin_loader.config = config

        plugins = load_plugins(config)

        # Should have loaded both core and custom plugins
        plugin_names = [p.plugin_name for p in plugins]
        self.assertIn("core_plugin_0", plugin_names)
        self.assertIn("my_custom_plugin", plugin_names)

    @patch("mmrelay.plugin_loader.logger")
    def test_load_plugins_caching(self, mock_logger):
        """
        Test that the plugin loader caches loaded plugins and returns the cached list on subsequent calls with the same configuration.
        """
        config = {"plugins": {}}

        import mmrelay.plugin_loader

        mmrelay.plugin_loader.config = config

        # First load
        plugins1 = load_plugins(config)

        # Second load should return cached result
        plugins2 = load_plugins(config)

        # Both should be lists (even if empty)
        self.assertIsInstance(plugins1, list)
        self.assertIsInstance(plugins2, list)
        self.assertEqual(plugins1, plugins2)

    def test_shutdown_plugins_clears_state(self):
        """Shutdown helper should call stop() on plugins and reset loader state."""
        mock_plugin = MockPlugin("cleanup_plugin")
        mock_plugin.stop = MagicMock()

        pl.sorted_active_plugins = [mock_plugin]
        pl.plugins_loaded = True

        shutdown_plugins()

        mock_plugin.stop.assert_called_once()
        self.assertEqual(pl.sorted_active_plugins, [])
        self.assertFalse(pl.plugins_loaded)

    @patch("mmrelay.plugins.health_plugin.Plugin")
    def test_load_plugins_start_error(self, mock_health_plugin):
        """
        Test that plugins raising exceptions in their start() method are skipped.

        Ensures that if a plugin's start() method raises an exception during loading,
        the error is handled gracefully and the plugin is not kept in the loaded list.
        """
        # Create a plugin that raises an error on start
        mock_plugin = MockPlugin("error_plugin")
        mock_plugin.start = MagicMock(side_effect=Exception("Start failed"))
        mock_health_plugin.return_value = mock_plugin

        config = {"plugins": {"error_plugin": {"active": True}}}

        import mmrelay.plugin_loader

        mmrelay.plugin_loader.config = config

        # Should not raise exception, just log error
        plugins = load_plugins(config)

        # Plugin should be skipped after a start failure
        self.assertEqual(len(plugins), 0)

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo")
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    def test_load_plugins_commit_priority_over_tag_and_branch(
        self,
        _,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        __,
        mock_clone_repo,
    ):
        """Test that commit ref takes priority over tag and branch in plugin config."""
        # Reset global state
        pl.plugins_loaded = False
        pl.sorted_active_plugins = []

        config = {
            "community-plugins": {
                "test-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "commit": "deadbeef",
                    "tag": "v1.0.0",
                    "branch": "main",
                    "priority": 10,
                }
            },
            "plugins": {},  # No core plugins active
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        # Verify that clone was called with commit ref (highest priority)
        mock_clone_repo.assert_called_once_with(
            "https://github.com/user/repo.git",
            {"type": "commit", "value": "deadbeef"},
            self.community_dir,
        )

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo")
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    def test_load_plugins_tag_priority_over_branch(
        self,
        _mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        _mock_install_reqs,
        mock_clone_repo,
    ):
        """Test that tag ref takes priority over branch in plugin config."""
        # Reset global state
        pl.plugins_loaded = False
        pl.sorted_active_plugins = []

        config = {
            "community-plugins": {
                "test-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "tag": "v1.0.0",
                    "branch": "main",
                    "priority": 10,
                }
            },
            "plugins": {},  # No core plugins active
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        # Verify that clone was called with tag ref (higher priority than branch)
        mock_clone_repo.assert_called_once_with(
            "https://github.com/user/repo.git",
            {"type": "tag", "value": "v1.0.0"},
            self.community_dir,
        )

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo")
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    @patch("mmrelay.plugin_loader.logger")
    def test_load_plugins_commit_with_tag_and_branch_warning(
        self,
        mock_logger,
        _mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        _mock_install_reqs,
        mock_clone_repo,
    ):
        """Test that warning is logged when commit is specified with tag/branch."""
        # Reset global state
        pl.plugins_loaded = False
        pl.sorted_active_plugins = []

        config = {
            "community-plugins": {
                "test-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "commit": "deadbeef",
                    "tag": "v1.0.0",
                    "branch": "main",
                    "priority": 10,
                }
            },
            "plugins": {},  # No core plugins active
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        # Verify warning was logged about commit taking priority
        mock_logger.warning.assert_any_call(
            "Commit specified along with tag/branch for plugin test-plugin, using commit"
        )

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo")
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    def test_load_plugins_default_to_main_branch(
        self,
        _mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        _mock_install_reqs,
        mock_clone_repo,
    ):
        """Test that plugin defaults to main branch when no ref is specified."""
        # Reset global state
        pl.plugins_loaded = False
        pl.sorted_active_plugins = []

        config = {
            "community-plugins": {
                "test-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "priority": 10,
                }
            },
            "plugins": {},  # No core plugins active
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        # Verify that clone was called with default main branch
        mock_clone_repo.assert_called_once_with(
            "https://github.com/user/repo.git",
            {"type": "branch", "value": "main"},
            self.community_dir,
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_or_update_repo_valid_short_commit_hash(
        self, mock_logger, mock_is_allowed, mock_run_git
    ):
        """Test clone with valid short commit hash (7 characters)."""

        mock_is_allowed.return_value = True
        # Mock git operations to fail by raising exception on first call
        mock_run_git.side_effect = subprocess.CalledProcessError(1, "git")
        ref = {"type": "commit", "value": "a1b2c3d"}

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        # The function is designed to be resilient and may return True even when git operations fail
        # The important part is that validation passes (no "Invalid commit hash" error)
        self.assertIsInstance(result, bool)  # Just verify it returns a boolean
        # Check that no validation error was logged for the valid commit hash
        validation_errors = [
            log_call
            for log_call in mock_logger.error.call_args_list
            if "Invalid commit hash" in str(log_call)
        ]
        self.assertEqual(len(validation_errors), 0)

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    @patch("os.path.isdir")
    @patch("os.makedirs")
    def test_clone_or_update_repo_new_repo_commit(
        self, _mock_makedirs, mock_isdir, _mock_logger, mock_is_allowed, mock_run_git
    ):
        """Test cloning a new repository with commit ref."""

        mock_is_allowed.return_value = True
        mock_isdir.return_value = False  # Repo doesn't exist
        ref = {"type": "commit", "value": "a1b2c3d4"}

        import subprocess

        def mock_git_func(*args, **kwargs):
            if "rev-parse" in args[0]:
                if "HEAD" in args[0]:
                    return subprocess.CompletedProcess(
                        args[0], 0, stdout="different_commit\n", stderr=""
                    )
                elif "a1b2c3d4^{commit}" in args[0]:
                    return subprocess.CompletedProcess(
                        args[0], 0, stdout="a1b2c3d4\n", stderr=""
                    )
                else:
                    return subprocess.CompletedProcess(
                        args[0], 0, stdout="some_commit\n", stderr=""
                    )
            else:
                return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

        mock_run_git.side_effect = mock_git_func

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertTrue(result)

        # Verify sequence of git operations (optimized: direct checkout succeeds)
        expected_calls = [
            # Clone repository
            (
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "https://github.com/user/repo.git",
                    "repo",
                ],
                {"cwd": self.temp_plugins_dir, "timeout": 120},
            ),
            # Check if already at the commit
            (
                ["git", "-C", self.temp_repo_path, "rev-parse", "HEAD"],
                {"capture_output": True},
            ),
            # Check target commit hash
            (
                ["git", "-C", self.temp_repo_path, "rev-parse", "a1b2c3d4^{commit}"],
                {"capture_output": True},
            ),
            # Direct checkout succeeds (no fetch needed)
            (
                ["git", "-C", self.temp_repo_path, "checkout", "a1b2c3d4"],
                {"timeout": 120},
            ),
        ]

        mock_run_git.assert_has_calls(
            [call(args, **kwargs) for args, kwargs in expected_calls],
            any_order=False,
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_existing_repo_commit(
        self, mock_isdir, _mock_logger, mock_is_allowed, mock_run_git
    ):
        """Test updating an existing repository to a specific commit."""

        mock_is_allowed.return_value = True
        mock_isdir.return_value = True  # Repo exists
        ref = {"type": "commit", "value": "deadbeef"}

        # Configure mock to fail on rev-parse (commit not found locally) but succeed on fetch and checkout
        checkout_call_count = 0

        def side_effect(*args, **_kwargs):
            """
            Create a fake git subprocess side effect used in tests.

            Parameters:
                *args: Positional arguments forwarded from subprocess.run or similar; the first element is expected to be the git command sequence (list or str).
                **kwargs: Ignored.

            Returns:
                subprocess.CompletedProcess: A successful result with returncode 0 and empty stdout/stderr.

            Raises:
                subprocess.CalledProcessError: If the git command contains "rev-parse" for the target commit, to simulate a missing commit object.
            """
            nonlocal checkout_call_count
            # Fail on rev-parse for the target commit (not found locally), but succeed on HEAD rev-parse
            if "rev-parse" in args[0] and "deadbeef^{commit}" in args[0]:
                raise subprocess.CalledProcessError(
                    1, "git"
                )  # Commit not found locally
            # Fail on first checkout to force fetch, but succeed on second checkout
            if "checkout" in args[0] and "deadbeef" in args[0]:
                checkout_call_count += 1
                if checkout_call_count == 1:
                    raise subprocess.CalledProcessError(
                        1, "git"
                    )  # First checkout fails, need to fetch
            return subprocess.CompletedProcess(args[0], 0, "", "")

        mock_run_git.side_effect = side_effect

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertTrue(result)

        # Verify sequence of git operations (optimized behavior)
        expected_calls = [
            # Check current commit (fails)
            (
                ["git", "-C", self.temp_repo_path, "rev-parse", "HEAD"],
                {"capture_output": True},
            ),
            # Check if commit exists locally (fails with new rev-parse logic)
            (
                [
                    "git",
                    "-C",
                    self.temp_repo_path,
                    "rev-parse",
                    "deadbeef^{commit}",
                ],
                {"capture_output": True},
            ),
            # Try direct checkout (fails to trigger fetch)
            (
                ["git", "-C", self.temp_repo_path, "checkout", "deadbeef"],
                {"timeout": 120},
            ),
            # Fetch specific commit
            (
                [
                    "git",
                    "-C",
                    self.temp_repo_path,
                    "fetch",
                    "--depth=1",
                    "origin",
                    "deadbeef",
                ],
                {"timeout": 120},
            ),
            # Checkout specific commit (succeeds after fetch)
            (
                ["git", "-C", self.temp_repo_path, "checkout", "deadbeef"],
                {"timeout": 120},
            ),
        ]

        mock_run_git.assert_has_calls(
            [call(args, **kwargs) for args, kwargs in expected_calls],
            any_order=False,
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_commit_fetch_specific_fails_fallback(
        self, mock_isdir, _mock_logger, mock_is_allowed, mock_run_git
    ):
        """Test that when specific commit fetch fails, it falls back to fetching all."""

        mock_is_allowed.return_value = True
        mock_isdir.return_value = True  # Repo exists
        ref = {"type": "commit", "value": "cafebabe"}

        # Configure mock to fail on specific commit fetch and cat-file but succeed on general fetch
        checkout_attempts = []

        def side_effect(*args, **_kwargs):
            """
            Simulate subprocess behavior for git commands used in tests.

            Returns:
                subprocess.CompletedProcess: A successful completed process with exit code 0 for commands that do not trigger error conditions.

            Raises:
                subprocess.CalledProcessError: If the command is a fetch for commit "cafebabe" in the test repository path (self.temp_repo_path) or if the command contains "cat-file".
            """
            if args[0] == [
                "git",
                "-C",
                self.temp_repo_path,
                "fetch",
                "--depth=1",
                "origin",
                "cafebabe",
            ]:
                raise subprocess.CalledProcessError(1, "git")
            if "rev-parse" in args[0] and "cafebabe^{commit}" in args[0]:
                raise subprocess.CalledProcessError(1, "git")
            # Fail first checkout to trigger fetch, succeed second checkout
            if args[0] == ["git", "-C", self.temp_repo_path, "checkout", "cafebabe"]:
                checkout_attempts.append(1)
                if len(checkout_attempts) == 1:
                    raise subprocess.CalledProcessError(1, "git")
            return subprocess.CompletedProcess(args[0], 0, "", "")

        mock_run_git.side_effect = side_effect

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertTrue(result)

        # Verify that both fetch attempts were made
        fetch_calls = [
            call for call in mock_run_git.call_args_list if "fetch" in call[0][0]
        ]

        self.assertEqual(
            len(fetch_calls), 2
        )  # Specific commit fetch fails, fallback fetch
        self.assertEqual(
            fetch_calls[0][0][0],
            [
                "git",
                "-C",
                self.temp_repo_path,
                "fetch",
                "--depth=1",
                "origin",
                "cafebabe",
            ],
        )
        self.assertEqual(
            fetch_calls[1][0][0],
            ["git", "-C", self.temp_repo_path, "fetch", "origin"],
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_commit_fetch_success_no_fallback(
        self, mock_isdir, _mock_logger, mock_is_allowed, mock_run_git
    ):
        """Test successful commit fetch without fallback."""

        mock_is_allowed.return_value = True
        mock_isdir.return_value = True  # Repo exists
        ref = {"type": "commit", "value": "abcd1234"}

        # Configure mock to succeed on all git operations (no fallback needed)
        def mock_run_git_side_effect(*args, **kwargs):
            """
            Simulate git subprocess calls for tests with deterministic successful outcomes.

            Returns:
                subprocess.CompletedProcess: A successful CompletedProcess for the invoked git command.
            """
            cmd = args[0]
            # For rev-parse calls, return same commit hash to simulate "already at target"
            if "rev-parse" in cmd and "capture_output" in kwargs:
                return subprocess.CompletedProcess(args[0], 0, "abcd1234fullhash", "")
            # All other operations succeed
            return subprocess.CompletedProcess(args[0], 0, "", "")

        mock_run_git.side_effect = mock_run_git_side_effect

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertTrue(result)

        # Verify rev-parse calls only (no fetch/checkout needed - already at target)
        expected_calls = [
            # Check current commit (succeeds)
            (
                ["git", "-C", self.temp_repo_path, "rev-parse", "HEAD"],
                {"capture_output": True},
            ),
            # Check if target commit exists locally (succeeds with new rev-parse logic)
            (
                [
                    "git",
                    "-C",
                    self.temp_repo_path,
                    "rev-parse",
                    "abcd1234^{commit}",
                ],
                {"capture_output": True},
            ),
        ]

        mock_run_git.assert_has_calls(
            [call(args, **kwargs) for args, kwargs in expected_calls],
            any_order=False,
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_commit_fetch_fallback_success(
        self, mock_isdir, _mock_logger, mock_is_allowed, mock_run_git
    ):
        """Test commit fetch that fails specific but succeeds with fallback."""
        mock_is_allowed.return_value = True
        mock_isdir.return_value = True  # Repo exists
        ref = {"type": "commit", "value": "cdef5678"}

        # Configure mock to fail on specific commit fetch but succeed on fallback
        checkout_attempts = []

        def side_effect(*args, **_kwargs):
            """
            Test helper that simulates subprocess responses for git commands in tests.

            Simulates a failing `git fetch` for exact command ["git", "-C", self.temp_repo_path, "fetch", "origin", "cdef5678"] and a failing git "rev-parse" for target commit; for all other calls it returns a successful CompletedProcess with empty stdout/stderr.

            Returns:
                subprocess.CompletedProcess: Successful result for non-matching commands.

            Raises:
                subprocess.CalledProcessError: When the command matches the specific fetch case or rev-parse for target commit.
            """
            if args[0] == [
                "git",
                "-C",
                self.temp_repo_path,
                "fetch",
                "--depth=1",
                "origin",
                "cdef5678",
            ]:
                raise subprocess.CalledProcessError(1, "git")
            # Fail on rev-parse for target commit to trigger fetch
            if "rev-parse" in args[0] and "cdef5678^{commit}" in args[0]:
                raise subprocess.CalledProcessError(1, "git")
            # Fail first checkout to trigger fetch, succeed second checkout
            if args[0] == ["git", "-C", self.temp_repo_path, "checkout", "cdef5678"]:
                checkout_attempts.append(1)
                if len(checkout_attempts) == 1:
                    raise subprocess.CalledProcessError(1, "git")
            return subprocess.CompletedProcess(args[0], 0, "", "")

        mock_run_git.side_effect = side_effect

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertTrue(result)

        # Verify cat-file check, failed specific fetch, successful fallback, and checkout
        fetch_calls = [
            call for call in mock_run_git.call_args_list if "fetch" in call[0][0]
        ]

        self.assertEqual(len(fetch_calls), 2)
        self.assertEqual(
            fetch_calls[0][0][0],
            [
                "git",
                "-C",
                self.temp_repo_path,
                "fetch",
                "--depth=1",
                "origin",
                "cdef5678",
            ],
        )
        self.assertEqual(
            fetch_calls[1][0][0],
            ["git", "-C", self.temp_repo_path, "fetch", "origin"],
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_commit_fetch_fallback_failure(
        self, mock_isdir, mock_logger, mock_is_allowed, mock_run_git
    ):
        """Test commit fetch where both specific and fallback fetch fail."""
        mock_is_allowed.return_value = True
        mock_isdir.return_value = True  # Repo exists
        ref = {"type": "commit", "value": "abcd1234"}

        # Configure mock to fail on both specific and fallback fetch
        def side_effect(*args, **_kwargs):
            """
            Simulate git subprocess behavior for tests by returning a successful CompletedProcess for most commands and raising CalledProcessError for specific failing invocations.

            Raises:
                subprocess.CalledProcessError: For these git invocations:
                  - ["git", "-C", <temp_repo_path>, "fetch", "origin", "abcd1234"]
                  - ["git", "-C", <temp_repo_path>, "fetch", "origin"]
                  - any invocation whose argument list contains "cat-file"

            Returns:
                subprocess.CompletedProcess: A CompletedProcess with returncode 0 and empty stdout/stderr for commands that do not match the failing cases.
            """
            if args[0] == [
                "git",
                "-C",
                self.temp_repo_path,
                "fetch",
                "--depth=1",
                "origin",
                "abcd1234",
            ]:
                raise subprocess.CalledProcessError(1, "git")
            if args[0] == ["git", "-C", self.temp_repo_path, "fetch", "origin"]:
                # Fail fallback fetch too
                raise subprocess.CalledProcessError(1, "git")
            if "rev-parse" in args[0] and "abcd1234^{commit}" in args[0]:
                raise subprocess.CalledProcessError(1, "git")
            # Fail checkout to trigger fetch
            if args[0] == ["git", "-C", self.temp_repo_path, "checkout", "abcd1234"]:
                raise subprocess.CalledProcessError(1, "git")
            return subprocess.CompletedProcess(args[0], 0, "", "")

        mock_run_git.side_effect = side_effect

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertFalse(result)  # Should return False when fallback fails

        # Verify warning messages were logged
        mock_logger.warning.assert_any_call(
            "Could not fetch commit %s for %s from remote; trying general fetch",
            "abcd1234",
            "repo",
        )
        # Verify fallback failure was logged
        self.assertTrue(mock_logger.warning.called)
        warning_calls = [
            warn_call[0][0]
            for warn_call in mock_logger.warning.call_args_list
            if "Fallback fetch also failed" in warn_call[0][0]
        ]
        self.assertTrue(len(warning_calls) > 0)

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    @patch("os.path.isdir")
    @patch("os.makedirs")
    def test_clone_or_update_repo_logger_exception_on_error(
        self, mock_makedirs, mock_isdir, mock_logger, mock_is_allowed, mock_run_git
    ):
        """Test that logger.exception is called for repository update errors."""
        mock_is_allowed.return_value = True
        mock_isdir.return_value = False  # Repo doesn't exist, will try to clone
        mock_makedirs.return_value = None
        ref = {"type": "commit", "value": "1234abcd"}

        # Configure mock to fail on git clone
        mock_run_git.side_effect = subprocess.CalledProcessError(1, "git")

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertFalse(result)

        # Verify logger.exception was called (not just logger.error)
        mock_logger.exception.assert_called_once()
        exception_call = mock_logger.exception.call_args[0][0]
        self.assertIn("Error cloning repository", exception_call)

        # Verify manual clone instruction was logged
        mock_logger.error.assert_called_once_with(
            f"Please manually clone the repository at {self.temp_repo_path}"
        )


class TestPluginSecurityGuards(BaseGitTest):
    """Tests for plugin security helper utilities."""

    def setUp(self):
        """
        Prepare test fixture by invoking the superclass setup, attaching the module-level plugin loader to `self.pl`, and saving its current `config` attribute (or `None`) to `self.original_config` for later restoration.
        """
        super().setUp()
        self.pl = pl
        self.original_config = getattr(pl, "config", None)

    def tearDown(self):
        """
        Cleans up temporary test directories and resources created for Git-related tests.
        """
        super().tearDown()

    def test_repo_url_allowed_https_known_host(self):
        self.pl.config = {}
        self.assertTrue(_is_repo_url_allowed("https://github.com/example/project.git"))

    def test_repo_url_rejected_for_unknown_host(self):
        self.pl.config = {}
        self.assertFalse(
            _is_repo_url_allowed("https://malicious.example.invalid/repo.git")
        )

    def test_repo_url_rejected_for_http_scheme(self):
        self.pl.config = {}
        self.assertFalse(_is_repo_url_allowed("http://github.com/example/project.git"))

    def test_repo_url_allows_custom_host_from_config(self):
        self.pl.config = {"security": {"community_repo_hosts": ["example.org"]}}
        self.assertTrue(_is_repo_url_allowed("https://code.example.org/test.git"))

    def test_local_repo_requires_opt_in(self):
        temp_path = os.path.abspath("some/local/path")
        self.pl.config = {}
        self.assertFalse(_is_repo_url_allowed(temp_path))
        self.pl.config = {"security": {"allow_local_plugin_paths": True}}
        with patch("os.path.exists", return_value=True):
            self.assertTrue(_is_repo_url_allowed(temp_path))

    def test_filter_risky_requirements_blocks_vcs_by_default(self):
        self.pl.config = {}
        requirements = [
            "safe-package==1.0.0",
            "git+https://github.com/example/risky.git",
            "--extra-index-url https://mirror.example",
            "another-safe",
        ]
        safe, flagged, _allow = _filter_risky_requirements(requirements)
        self.assertFalse(_allow)
        self.assertEqual(
            flagged,
            [
                "git+https://github.com/example/risky.git",
                "--extra-index-url https://mirror.example",
            ],
        )
        self.assertIn("safe-package==1.0.0", safe)
        self.assertIn("another-safe", safe)

    def test_filter_risky_requirements_can_allow_via_config(self):
        self.pl.config = {"security": {"allow_untrusted_dependencies": True}}
        requirements = ["pkg @ git+ssh://github.com/example/pkg.git"]
        safe, flagged, _allow = _filter_risky_requirements(requirements)
        self.assertTrue(_allow)
        # With new behavior, flagged requirements are still classified as flagged
        # Configuration decision happens in caller
        self.assertEqual(safe, [])
        self.assertEqual(flagged, requirements)

    def test_get_allowed_repo_hosts_empty_list_override(self):
        """Explicit empty list should override default hosts."""
        self.pl.config = {"security": {"community_repo_hosts": []}}
        from mmrelay.plugin_loader import _get_allowed_repo_hosts

        result = _get_allowed_repo_hosts()
        self.assertEqual(result, [])

    def test_get_allowed_repo_hosts_none_uses_default(self):
        """None config should use default hosts."""
        self.pl.config = {"security": {"community_repo_hosts": None}}
        from mmrelay.plugin_loader import _get_allowed_repo_hosts

        result = _get_allowed_repo_hosts()
        expected = ["github.com", "gitlab.com", "codeberg.org", "bitbucket.org"]
        self.assertEqual(result, expected)

    def test_get_allowed_repo_hosts_string_is_accepted(self):
        """String value coerces to a single host entry."""
        self.pl.config = {"security": {"community_repo_hosts": "invalid"}}
        from mmrelay.plugin_loader import _get_allowed_repo_hosts

        result = _get_allowed_repo_hosts()
        # String gets converted to list, then filtered
        expected = ["invalid"]
        self.assertEqual(result, expected)

    def test_get_allowed_repo_filters_empty_strings(self):
        """Empty strings should be filtered out."""
        self.pl.config = {
            "security": {
                "community_repo_hosts": ["github.com", "", "gitlab.com", "   "]
            }
        }
        from mmrelay.plugin_loader import _get_allowed_repo_hosts

        result = _get_allowed_repo_hosts()
        expected = ["github.com", "gitlab.com"]
        self.assertEqual(result, expected)

    def test_get_allowed_repo_hosts_integer_type_uses_default(self):
        """Integer type should use default hosts."""
        self.pl.config = {"security": {"community_repo_hosts": 123}}
        from mmrelay.plugin_loader import _get_allowed_repo_hosts

        result = _get_allowed_repo_hosts()
        expected = ["github.com", "gitlab.com", "codeberg.org", "bitbucket.org"]
        self.assertEqual(result, expected)


class TestURLValidation(unittest.TestCase):
    """Test cases for URL validation and security functions."""

    def setUp(self):
        """
        Set up the test by attaching the plugin loader and saving its original configuration.

        Stores the supplied plugin loader instance on self.pl and records its current
        `config` attribute (or `None` if absent) in `self.original_config` for later restoration.
        """
        self.pl = pl
        self.original_config = getattr(pl, "config", None)

    def tearDown(self):
        """
        Restore the original plugin loader configuration after a test.

        Reassigns the saved original configuration back to the plugin loader's `config` attribute to restore global state modified during the test.
        """
        self.pl.config = self.original_config

    def test_normalize_repo_target_ssh_git_at(self):
        """Test SSH URL normalization with git@ prefix."""
        from mmrelay.plugin_loader import _normalize_repo_target

        scheme, host = _normalize_repo_target("git@github.com:user/repo.git")
        self.assertEqual(scheme, "ssh")
        self.assertEqual(host, "github.com")

    def test_normalize_repo_target_ssh_git_at_with_port(self):
        """Test SSH URL normalization with port."""
        from mmrelay.plugin_loader import _normalize_repo_target

        scheme, host = _normalize_repo_target("git@github.com:2222:user/repo.git")
        self.assertEqual(scheme, "ssh")
        self.assertEqual(host, "github.com")

    def test_normalize_repo_target_https_url(self):
        """Test HTTPS URL normalization."""
        from mmrelay.plugin_loader import _normalize_repo_target

        scheme, host = _normalize_repo_target("https://github.com/user/repo.git")
        self.assertEqual(scheme, "https")
        self.assertEqual(host, "github.com")

    def test_normalize_repo_target_git_ssh_scheme(self):
        """Test git+ssh scheme normalization."""
        from mmrelay.plugin_loader import _normalize_repo_target

        scheme, host = _normalize_repo_target("git+ssh://github.com/user/repo.git")
        self.assertEqual(scheme, "ssh")
        self.assertEqual(host, "github.com")

    def test_normalize_repo_target_ssh_git_scheme(self):
        """Test ssh+git scheme normalization."""
        from mmrelay.plugin_loader import _normalize_repo_target

        scheme, host = _normalize_repo_target("ssh+git://github.com/user/repo.git")
        self.assertEqual(scheme, "ssh")
        self.assertEqual(host, "github.com")

    def test_normalize_repo_target_empty_string(self):
        """Test empty URL normalization."""
        from mmrelay.plugin_loader import _normalize_repo_target

        scheme, host = _normalize_repo_target("")
        self.assertEqual(scheme, "")
        self.assertEqual(host, "")

    def test_normalize_repo_target_none(self):
        """Test None URL normalization."""
        from mmrelay.plugin_loader import _normalize_repo_target

        scheme, host = _normalize_repo_target(None)  # type: ignore[arg-type]
        self.assertEqual(scheme, "")
        self.assertEqual(host, "")

    def test_host_in_allowlist_exact_match(self):
        """Test exact host match in allowlist."""
        from mmrelay.plugin_loader import _host_in_allowlist

        result = _host_in_allowlist("github.com", ["github.com", "gitlab.com"])
        self.assertTrue(result)

    def test_host_in_allowlist_subdomain_match(self):
        """Test subdomain match in allowlist."""
        from mmrelay.plugin_loader import _host_in_allowlist

        result = _host_in_allowlist("api.github.com", ["github.com", "gitlab.com"])
        self.assertTrue(result)

    def test_host_in_allowlist_case_insensitive(self):
        """Test case insensitive matching."""
        from mmrelay.plugin_loader import _host_in_allowlist

        result = _host_in_allowlist("GitHub.com", ["github.com"])
        self.assertTrue(result)

    def test_host_in_allowlist_empty_host(self):
        """Test empty host handling."""
        from mmrelay.plugin_loader import _host_in_allowlist

        result = _host_in_allowlist("", ["github.com"])
        self.assertFalse(result)

    def test_host_in_allowlist_none_host(self):
        """Test None host handling."""
        from mmrelay.plugin_loader import _host_in_allowlist

        result = _host_in_allowlist(None, ["github.com"])  # type: ignore[arg-type]
        self.assertFalse(result)

    @patch("mmrelay.plugin_loader.logger")
    def test_repo_url_rejected_for_dash_prefix(self, _mock_logger):
        """Test that URLs starting with dash are rejected."""
        self.pl.config = {}
        result = _is_repo_url_allowed("-evil-option")
        self.assertFalse(result)

    @patch("mmrelay.plugin_loader.logger")
    def test_repo_url_rejected_for_file_scheme(self, mock_logger):
        """Test that file:// URLs are rejected by default."""
        self.pl.config = {}
        result = _is_repo_url_allowed("file:///local/path")
        self.assertFalse(result)
        mock_logger.error.assert_called_with(
            "file:// repositories are disabled for security reasons."
        )

    @patch("mmrelay.plugin_loader.logger")
    def test_repo_url_allows_file_scheme_with_opt_in(self, _mock_logger):
        """Test that file:// URLs are allowed when local paths are enabled."""
        self.pl.config = {"security": {"allow_local_plugin_paths": True}}
        result = _is_repo_url_allowed("file:///local/path")
        self.assertTrue(result)

    @patch("mmrelay.plugin_loader.logger")
    def test_repo_url_rejected_for_unsupported_scheme(self, _mock_logger):
        """Test that unsupported schemes are rejected."""
        self.pl.config = {}
        result = _is_repo_url_allowed("ftp://github.com/user/repo.git")
        self.assertFalse(result)
        _mock_logger.error.assert_called_with(
            "Unsupported repository scheme '%s' for %s",
            "ftp",
            "ftp://github.com/user/repo.git",
        )

    @patch("mmrelay.plugin_loader.logger")
    def test_repo_url_local_path_nonexistent(self, _mock_logger):
        """Test local path validation when path doesn't exist."""
        self.pl.config = {"security": {"allow_local_plugin_paths": True}}
        with patch("os.path.exists", return_value=False):
            result = _is_repo_url_allowed("/nonexistent/path")
            self.assertFalse(result)
        _mock_logger.error.assert_called_with(
            "Local repository path does not exist: %s", "/nonexistent/path"
        )

    @patch("mmrelay.plugin_loader.logger")
    def test_repo_url_local_path_disabled(self, _mock_logger):
        """Test local path validation when local paths are disabled."""
        self.pl.config = {}
        result = _is_repo_url_allowed("/local/path")
        self.assertFalse(result)
        _mock_logger.error.assert_called_with(
            "Invalid repository '%s'. Local paths are disabled, and remote URLs must include a scheme (e.g., 'https://').",
            "/local/path",
        )

    @patch("mmrelay.plugin_loader.logger")
    def test_repo_url_empty_string(self, mock_logger):
        """Test empty URL handling."""
        self.pl.config = {}
        result = _is_repo_url_allowed("")
        self.assertFalse(result)

    @patch("mmrelay.plugin_loader.logger")
    def test_repo_url_whitespace_only(self, mock_logger):
        """Test whitespace-only URL handling."""
        self.pl.config = {}
        result = _is_repo_url_allowed("   ")
        self.assertFalse(result)


class TestRequirementFiltering(unittest.TestCase):
    """Test cases for requirement filtering security functions."""

    def setUp(self):
        """
        Set up the test by attaching the plugin loader and saving its original configuration.

        Stores the supplied plugin loader instance on self.pl and records its current
        `config` attribute (or `None` if absent) in `self.original_config` for later restoration.
        """
        self.pl = pl
        self.original_config = getattr(pl, "config", None)

    def tearDown(self):
        """
        Restore the original plugin loader configuration after a test.

        Reassigns the saved original configuration back to the plugin loader's `config` attribute to restore global state modified during the test.
        """
        self.pl.config = self.original_config

    def test_is_requirement_risky_vcs_prefixes(self):
        """Test VCS prefix detection."""
        from mmrelay.plugin_loader import _is_requirement_risky

        risky_requirements = [
            "git+https://github.com/user/repo.git",
            "hg+https://bitbucket.org/user/repo",
            "bzr+https://launchpad.net/project",
            "svn+https://svn.example.com/project",
        ]

        for req in risky_requirements:
            with self.subTest(req=req):
                self.assertTrue(_is_requirement_risky(req))

    def test_is_requirement_risky_url_with_at(self):
        """Test URL with @ symbol detection."""
        from mmrelay.plugin_loader import _is_requirement_risky

        risky_requirements = [
            "package@https://example.com/package.tar.gz",
            "pkg@file:///local/path",
        ]

        for req in risky_requirements:
            with self.subTest(req=req):
                self.assertTrue(_is_requirement_risky(req))

    def test_is_requirement_risky_safe_requirements(self):
        """Test safe requirement detection."""
        from mmrelay.plugin_loader import _is_requirement_risky

        safe_requirements = [
            "requests==2.28.0",
            "numpy>=1.20.0",
            "django~=4.0.0",
            "flask",
            "pytest>=6.0.0,<7.0.0",
        ]

        for req in safe_requirements:
            with self.subTest(req=req):
                self.assertFalse(_is_requirement_risky(req))

    def test_filter_risky_requirements_editable_with_url(self):
        """Test filtering editable requirements with URLs."""
        from mmrelay.plugin_loader import _filter_risky_requirements

        requirements = [
            "--editable=git+https://github.com/user/repo.git",
            "requests==2.28.0",
        ]

        safe, flagged, _allow = _filter_risky_requirements(requirements)

        self.assertIn("requests==2.28.0", safe)
        self.assertIn("--editable=git+https://github.com/user/repo.git", flagged)
        self.assertFalse(_allow)

    def test_filter_risky_requirements_editable_safe(self):
        """Test filtering safe editable requirements."""
        from mmrelay.plugin_loader import _filter_risky_requirements

        requirements = [
            "--editable=.",
            "--editable=/local/path",
            "requests==2.28.0",
        ]

        safe, flagged, _allow = _filter_risky_requirements(requirements)

        self.assertIn("requests==2.28.0", safe)
        self.assertIn("--editable=.", safe)
        self.assertIn("--editable=/local/path", safe)
        self.assertEqual(flagged, [])

    def test_filter_risky_requirements_source_flag_removal(self):
        """Test that source flags are removed with risky requirements."""
        from mmrelay.plugin_loader import _filter_risky_requirements

        requirements = [
            "--extra-index-url https://pypi.org/simple",
            "git+https://github.com/user/repo.git",
            "requests==2.28.0",
        ]

        safe, flagged, _allow = _filter_risky_requirements(requirements)

        self.assertIn("requests==2.28.0", safe)
        self.assertIn("--extra-index-url https://pypi.org/simple", flagged)
        self.assertIn("git+https://github.com/user/repo.git", flagged)
        self.assertFalse(_allow)

    def test_filter_risky_requirements_comments_and_empty(self):
        """Test filtering comments and empty strings."""
        from mmrelay.plugin_loader import _filter_risky_requirements

        requirements = [
            "# This is a comment",
            "",
            "   ",
            "requests==2.28.0",
        ]

        safe, flagged, _allow = _filter_risky_requirements(requirements)

        self.assertIn("requests==2.28.0", safe)
        self.assertEqual(flagged, [])

    def test_filter_risky_requirements_allow_untrusted(self):
        """Test that allow_untrusted=True allows risky requirements."""
        from mmrelay.plugin_loader import _filter_risky_requirements

        # Set up config to allow untrusted dependencies
        self.pl.config = {"security": {"allow_untrusted_dependencies": True}}

        requirements = [
            "git+https://github.com/user/repo.git",
            "http://example.com/package.tar.gz",
        ]

        safe, flagged, _allow = _filter_risky_requirements(requirements)

        # With new behavior, classification is independent of config
        self.assertEqual(len(safe), 0)
        self.assertEqual(len(flagged), 2)
        self.assertTrue(_allow)
        self.assertEqual(flagged, requirements)

    def test_filter_risky_requirements_short_form_flags_with_attached_values(self):
        """Test that short-form flags with attached values are properly filtered."""
        from mmrelay.plugin_loader import _filter_risky_requirements

        requirements = [
            "-ihttps://malicious.example.com/simple",  # Should be flagged
            "-fsafe-local-path",  # Should be safe (find-links with local path)
            "-egit+https://github.com/user/repo.git",  # Should be flagged (editable with VCS)
            "requests==2.28.0",  # Should be safe
        ]

        safe, flagged, _allow = _filter_risky_requirements(requirements)

        self.assertIn("requests==2.28.0", safe)
        self.assertIn("-fsafe-local-path", safe)
        self.assertIn("-ihttps://malicious.example.com/simple", flagged)
        self.assertIn("-egit+https://github.com/user/repo.git", flagged)
        self.assertFalse(_allow)


class TestGitOperations(BaseGitTest):
    """Test cases for Git operations and repository management."""

    def setUp(self):
        super().setUp()

        self.pl = pl
        self.original_config = getattr(pl, "config", None)

    def tearDown(self):
        """
        Restore the plugin loader's configuration saved before the test.

        Reassigns the original configuration back to the plugin loader instance and invokes the base class tearDown to complete cleanup.
        """
        self.pl.config = self.original_config
        super().tearDown()

    @patch("mmrelay.plugin_loader._run")
    def test_run_git_with_defaults(self, mock_run):
        """Test _run_git uses default retry settings."""
        from mmrelay.plugin_loader import _run_git

        _run_git(["git", "status"])

        # Check that _run was called with the right parameters, including env
        call_args = mock_run.call_args
        self.assertEqual(call_args[0][0], ["git", "status"])
        self.assertEqual(call_args[1]["timeout"], 120)
        self.assertEqual(call_args[1]["retry_attempts"], 3)
        self.assertEqual(call_args[1]["retry_delay"], 2)
        self.assertIn("env", call_args[1])
        self.assertEqual(call_args[1]["env"]["GIT_TERMINAL_PROMPT"], "0")

    @patch("mmrelay.plugin_loader._run_git")
    def test_run_git_with_custom_settings(self, mock_run):
        """Test _run_git accepts custom settings."""
        from mmrelay.plugin_loader import _run_git

        _run_git(["git", "clone"], timeout=300, retry_attempts=5)

        mock_run.assert_called_once_with(
            ["git", "clone"], timeout=300, retry_attempts=5
        )

    def test_check_auto_install_enabled_default(self):
        """Test auto-install enabled by default."""
        from mmrelay.plugin_loader import _check_auto_install_enabled

        result = _check_auto_install_enabled(None)
        self.assertTrue(result)

    def test_check_auto_install_enabled_explicit_true(self):
        """Test auto-install explicitly enabled."""
        from mmrelay.plugin_loader import _check_auto_install_enabled

        config = {"security": {"auto_install_deps": True}}
        result = _check_auto_install_enabled(config)
        self.assertTrue(result)

    def test_check_auto_install_enabled_explicit_false(self):
        """Test auto-install explicitly disabled."""
        from mmrelay.plugin_loader import _check_auto_install_enabled

        config = {"security": {"auto_install_deps": False}}
        result = _check_auto_install_enabled(config)
        self.assertFalse(result)

    def test_check_auto_install_enabled_missing_security(self):
        """Test auto-install when security section missing."""
        from mmrelay.plugin_loader import _check_auto_install_enabled

        config = {"other": "value"}
        result = _check_auto_install_enabled(config)
        self.assertTrue(result)

    @patch("mmrelay.plugin_loader.logger")
    def test_raise_install_error(self, mock_logger):
        """Test _raise_install_error logs and raises exception."""
        from mmrelay.plugin_loader import _raise_install_error

        with self.assertRaises(subprocess.CalledProcessError):
            _raise_install_error("test-package")

        mock_logger.warning.assert_called_once_with(
            "Auto-install disabled; cannot install test-package. See docs for enabling."
        )

    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_or_update_repo_invalid_ref_type(self, mock_logger, mock_is_allowed):
        """Test clone with invalid ref type."""

        mock_is_allowed.return_value = True
        ref = {"type": "invalid", "value": "main"}

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertFalse(result)
        mock_logger.error.assert_called_with(
            "Invalid ref type %r (expected 'tag', 'branch', or 'commit') for %r",
            "invalid",
            "https://github.com/user/repo.git",
        )

    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_or_update_repo_missing_ref_value(self, mock_logger, mock_is_allowed):
        """
        Verify clone_or_update_repo returns False and logs an error when a ref specifies a type but has an empty value.

        Asserts that the function rejects a ref with an empty 'value' field, returns False, and logs an error mentioning the ref type and repository URL.
        """

        mock_is_allowed.return_value = True
        ref = {"type": "branch", "value": ""}

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertFalse(result)
        mock_logger.error.assert_called_with(
            "Missing ref value for %s on %r",
            "branch",
            "https://github.com/user/repo.git",
        )

    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_or_update_repo_ref_starts_with_dash(
        self, mock_logger, mock_is_allowed
    ):
        """Test clone with ref value starting with dash."""

        mock_is_allowed.return_value = True
        ref = {"type": "branch", "value": "-evil"}

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertFalse(result)
        mock_logger.error.assert_called_with(
            "Ref value looks invalid (starts with '-'): %r", "-evil"
        )

    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_or_update_repo_invalid_ref_chars(self, mock_logger, mock_is_allowed):
        """Test clone with invalid characters in ref value."""

        mock_is_allowed.return_value = True
        ref = {"type": "branch", "value": "invalid@branch"}

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertFalse(result)
        mock_logger.error.assert_called_with(
            "Invalid %s name supplied: %r", "branch", "invalid@branch"
        )

    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_or_update_repo_invalid_url_empty(
        self, _mock_logger, mock_is_allowed
    ):
        """Test clone with empty URL."""
        mock_is_allowed.return_value = False
        ref = {"type": "branch", "value": "main"}
        with tempfile.TemporaryDirectory() as tmpdir:
            result = clone_or_update_repo("", ref, tmpdir)
        self.assertFalse(result)

    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_or_update_repo_invalid_url_whitespace(
        self, _mock_logger, mock_is_allowed
    ):
        """Test clone with whitespace-only URL."""
        mock_is_allowed.return_value = False
        ref = {"type": "branch", "value": "main"}
        with tempfile.TemporaryDirectory() as tmpdir:
            result = clone_or_update_repo("   ", ref, tmpdir)
        self.assertFalse(result)

    @patch("mmrelay.plugin_loader._is_repo_url_allowed", return_value=True)
    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_or_update_repo_pull_current_branch_fails(
        self, mock_run_git, _mock_is_allowed
    ):
        """Test that clone_or_update_repo handles checkout and pull failure on current branch."""
        mock_run_git.side_effect = [
            None,  # fetch
            subprocess.CalledProcessError(1, "git checkout"),  # checkout main fails
            subprocess.CalledProcessError(1, "git checkout"),  # checkout master fails
        ]
        repo_url = "https://github.com/test/plugin.git"
        ref = {"type": "branch", "value": "main"}
        with tempfile.TemporaryDirectory() as plugins_dir:
            repo_path = os.path.join(plugins_dir, "plugin")
            os.makedirs(repo_path)
            result = clone_or_update_repo(repo_url, ref, plugins_dir)
            self.assertFalse(result)

    @patch("mmrelay.plugin_loader._is_repo_url_allowed", return_value=True)
    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_or_update_repo_checkout_and_pull_branch(
        self, mock_run_git, _mock_is_allowed
    ):
        """Test that clone_or_update_repo handles checkout and pull for a different branch."""

        # Mock successful fetch, checkout and pull
        mock_run_git.side_effect = [
            None,  # fetch succeeds
            None,  # checkout succeeds
            None,  # pull succeeds
        ]

        repo_url = "https://github.com/test/plugin.git"
        ref = {"type": "branch", "value": "main"}

        with tempfile.TemporaryDirectory() as plugins_dir:
            repo_path = os.path.join(plugins_dir, "plugin")
            os.makedirs(repo_path)  # It's an existing repo

            result = clone_or_update_repo(repo_url, ref, plugins_dir)
            self.assertTrue(result)

    @patch("mmrelay.plugin_loader._is_repo_url_allowed", return_value=True)
    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_or_update_repo_checkout_and_pull_tag(
        self, mock_run_git, _mock_is_allowed
    ):
        """Test that clone_or_update_repo handles checkout and pull for a tag."""

        def mock_run_git_side_effect(*args, **kwargs):
            """
            Simulate git subprocess responses for tests, returning success for common commands and a commit-containing result for `rev-parse`.

            Parameters:
                *args: Positional arguments forwarded from the mocked runner; the first positional argument is expected to be the git command (string or sequence) inspected by this helper.
                **kwargs: Ignored keyword arguments forwarded by the mock.

            Returns:
                None for successful commands such as `fetch`, `checkout`, and `pull`; otherwise an object whose `stdout` is a string commit hash for `rev-parse` invocations.
            """
            cmd = args[0]
            if "fetch" in cmd:
                return None  # fetch succeeds
            elif "rev-parse" in cmd and "HEAD" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="abc123commit\n", stderr=""
                )  # current commit
            elif "rev-parse" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="def456commit\n", stderr=""
                )  # tag commit (different)
            elif "checkout" in cmd:
                return None  # checkout succeeds
            elif "pull" in cmd:
                return None  # pull succeeds
            return None

        mock_run_git.side_effect = mock_run_git_side_effect

        repo_url = "https://github.com/test/plugin.git"
        ref = {"type": "tag", "value": "v1.0.0"}

        with tempfile.TemporaryDirectory() as plugins_dir:
            repo_path = os.path.join(plugins_dir, "plugin")
            os.makedirs(repo_path)  # It's an existing repo
            result = clone_or_update_repo(repo_url, ref, plugins_dir)
            self.assertTrue(result)

    @patch("mmrelay.plugin_loader._is_repo_url_allowed", return_value=True)
    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_or_update_repo_checkout_fails_fallback(
        self, mock_run_git, _mock_is_allowed
    ):
        """Test that clone_or_update_repo handles checkout failure and tries fallback."""
        mock_run_git.side_effect = [
            None,  # fetch
            subprocess.CalledProcessError(1, "git checkout"),  # checkout main
            subprocess.CalledProcessError(1, "git checkout"),  # checkout master
        ]
        repo_url = "https://github.com/test/plugin.git"
        ref = {"type": "branch", "value": "main"}
        with tempfile.TemporaryDirectory() as plugins_dir:
            repo_path = os.path.join(plugins_dir, "plugin")
            os.makedirs(repo_path)
            result = clone_or_update_repo(repo_url, ref, plugins_dir)
            self.assertFalse(result)


class TestCommandRunner(unittest.TestCase):
    """Verify helper command execution behavior."""

    def test_run_retries_on_failure(self):
        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = [
                subprocess.CalledProcessError(1, ["git", "status"]),
                subprocess.CompletedProcess(args=["git", "status"], returncode=0),
            ]
            result = _run(["git", "status"], retry_attempts=2, retry_delay=0)
            self.assertIsInstance(result, subprocess.CompletedProcess)
            self.assertEqual(mock_subprocess.call_count, 2)

    def test_run_raises_after_max_attempts(self):
        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = subprocess.CalledProcessError(1, ["git"])
            with self.assertRaises(subprocess.CalledProcessError):
                _run(["git"], retry_attempts=2, retry_delay=0)
            self.assertEqual(mock_subprocess.call_count, 2)

    def test_run_type_error_not_list(self):
        """Test _run raises TypeError for non-list command."""
        with self.assertRaises(TypeError) as cm:
            _run("git status")  # type: ignore[arg-type]
        self.assertIn("cmd must be a list of str", str(cm.exception))

    def test_run_value_error_empty_list(self):
        """Test _run raises ValueError for empty command list."""
        with self.assertRaises(ValueError) as cm:
            _run([])
        self.assertIn("Command list cannot be empty", str(cm.exception))

    def test_run_type_error_non_string_args(self):
        """Test _run raises TypeError for non-string arguments."""
        with self.assertRaises(TypeError) as cm:
            _run(["git", 123])  # type: ignore[list-item]
        self.assertIn("all command arguments must be strings", str(cm.exception))

    def test_run_value_error_shell_true(self):
        """Test _run raises ValueError for shell=True."""
        with self.assertRaises(ValueError) as cm:
            _run(["git", "status"], shell=True)  # noqa: S604 - intentional for test
        self.assertIn("shell=True is not allowed in _run", str(cm.exception))

    @patch("subprocess.run")
    def test_run_value_error_empty_args(self, mock_subprocess):
        """Test _run raises ValueError for empty/whitespace arguments."""
        with self.assertRaises(ValueError) as cm:
            _run(["git", ""])
        self.assertIn("command arguments cannot be empty/whitespace", str(cm.exception))

        with self.assertRaises(ValueError) as cm:
            _run(["git", "   "])
        self.assertIn("command arguments cannot be empty/whitespace", str(cm.exception))

    def test_run_sets_text_default(self):
        """Test _run sets text=True by default."""
        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.return_value = subprocess.CompletedProcess(
                args=["echo", "test"], returncode=0, stdout="test"
            )
            _run(["echo", "test"])
            # Check that text=True was set in the call
            call_kwargs = mock_subprocess.call_args[1]
            self.assertTrue(call_kwargs.get("text", False))

    def test_run_preserves_text_setting(self):
        """Test _run preserves existing text setting."""
        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.return_value = subprocess.CompletedProcess(
                args=["echo", "test"], returncode=0, stdout=b"test"
            )
            _run(["echo", "test"], text=False)
            # Check that text=False was preserved
            call_kwargs = mock_subprocess.call_args[1]
            self.assertFalse(call_kwargs.get("text", True))


class TestCollectRequirements(unittest.TestCase):
    """Test cases for _collect_requirements function."""

    def setUp(self):
        """
        Create a temporary directory for the test and register its removal as cleanup.

        The directory path is stored on self.temp_dir and will be removed after the test
        via shutil.rmtree(self.temp_dir, ignore_errors=True).
        """
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

    def test_collect_requirements_basic(self):
        """Test collecting basic requirements from a simple file."""
        req_file = os.path.join(self.temp_dir, "requirements.txt")
        with open(req_file, "w") as f:
            f.write("requests==2.28.0\n")
            f.write("numpy>=1.20.0\n")
            f.write("# This is a comment\n")
            f.write("\n")  # Blank line

        result = _collect_requirements(req_file)
        expected = ["requests==2.28.0", "numpy>=1.20.0"]
        self.assertEqual(result, expected)

    def test_collect_requirements_with_inline_comments(self):
        """Test handling inline comments."""
        req_file = os.path.join(self.temp_dir, "requirements.txt")
        with open(req_file, "w") as f:
            f.write("requests==2.28.0  # HTTP library\n")
            f.write("numpy>=1.20.0    # Numerical computing\n")

        result = _collect_requirements(req_file)
        expected = ["requests==2.28.0", "numpy>=1.20.0"]
        self.assertEqual(result, expected)

    def test_collect_requirements_with_include(self):
        """Test handling -r include directive."""
        # Create main requirements file
        main_req = os.path.join(self.temp_dir, "requirements.txt")
        included_req = os.path.join(self.temp_dir, "base.txt")

        with open(included_req, "w") as f:
            f.write("requests==2.28.0\n")
            f.write("numpy>=1.20.0\n")

        with open(main_req, "w") as f:
            f.write("-r base.txt\n")
            f.write("scipy>=1.7.0\n")

        result = _collect_requirements(main_req)
        expected = ["requests==2.28.0", "numpy>=1.20.0", "scipy>=1.7.0"]
        self.assertEqual(result, expected)

    def test_collect_requirements_with_constraint(self):
        """Test handling -c constraint directive."""
        req_file = os.path.join(self.temp_dir, "requirements.txt")
        constraint_file = os.path.join(self.temp_dir, "constraints.txt")

        with open(constraint_file, "w") as f:
            f.write("requests<=2.30.0\n")

        with open(req_file, "w") as f:
            f.write("-c constraints.txt\n")
            f.write("requests>=2.25.0\n")

        result = _collect_requirements(req_file)
        # The function appears to include constraints in the output
        expected = ["requests<=2.30.0", "requests>=2.25.0"]
        self.assertEqual(result, expected)

    def test_collect_requirements_with_complex_flags(self):
        """Test handling complex requirement flags."""
        req_file = os.path.join(self.temp_dir, "requirements.txt")
        with open(req_file, "w") as f:
            f.write("--requirement=requirements-dev.txt\n")
            f.write("--constraint=constraints.txt\n")
            f.write("package>=1.0.0 --extra-index-url https://pypi.org/simple\n")

        # Create the referenced files
        dev_req = os.path.join(self.temp_dir, "requirements-dev.txt")
        constraint_file = os.path.join(self.temp_dir, "constraints.txt")

        with open(dev_req, "w") as f:
            f.write("pytest>=6.0.0\n")

        with open(constraint_file, "w") as f:
            f.write("pytest<=7.0.0\n")

        result = _collect_requirements(req_file)
        # Requirements are kept as full lines to preserve PEP 508 syntax
        expected = [
            "pytest>=6.0.0",
            "pytest<=7.0.0",
            "package>=1.0.0 --extra-index-url https://pypi.org/simple",
        ]
        self.assertEqual(result, expected)

    def test_collect_requirements_nonexistent_file(self):
        """Test handling nonexistent requirements file."""
        nonexistent_file = os.path.join(self.temp_dir, "nonexistent.txt")

        result = _collect_requirements(nonexistent_file)
        self.assertEqual(result, [])

    def test_collect_requirements_recursive_include_detection(self):
        """Test detection of recursive includes."""
        req1 = os.path.join(self.temp_dir, "req1.txt")
        req2 = os.path.join(self.temp_dir, "req2.txt")

        with open(req1, "w") as f:
            f.write("-r req2.txt\n")
            f.write("package1>=1.0.0\n")

        with open(req2, "w") as f:
            f.write("-r req1.txt\n")  # Recursive include
            f.write("package2>=1.0.0\n")

        result = _collect_requirements(req1)
        # Should handle recursion gracefully and not crash
        self.assertIsInstance(result, list)

    @patch("mmrelay.plugin_loader.logger")
    def test_collect_requirements_malformed_requirement_directive(self, mock_logger):
        """Test handling of malformed requirement directives."""
        req_file = os.path.join(self.temp_dir, "requirements.txt")
        with open(req_file, "w") as f:
            f.write("-r \n")  # Malformed - missing file
            f.write("requests==2.28.0\n")

        result = _collect_requirements(req_file)

        # Should log warning for malformed directive
        mock_logger.warning.assert_called()
        # Should still include the valid requirement
        self.assertIn("requests==2.28.0", result)

    @patch("mmrelay.plugin_loader.logger")
    def test_collect_requirements_io_error(self, mock_logger):
        """Test handling of IO errors during file reading."""
        req_file = os.path.join(self.temp_dir, "requirements.txt")

        # Create file and then mock open to raise IOError
        with open(req_file, "w") as f:
            f.write("requests==2.28.0\n")

        with patch("builtins.open", side_effect=IOError("Permission denied")):
            result = _collect_requirements(req_file)

        # Should handle IOError gracefully
        self.assertEqual(result, [])

    def test_collect_requirements_empty_file(self):
        """Test handling empty requirements file."""
        req_file = os.path.join(self.temp_dir, "empty.txt")
        with open(req_file, "w"):
            pass  # Create empty file

        result = _collect_requirements(req_file)
        self.assertEqual(result, [])


class TestCleanPythonCache(unittest.TestCase):
    """Test cases for _clean_python_cache function."""

    def setUp(self):
        """Create a temporary directory for cache cleaning tests."""
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

    def test_clean_python_cache_removes_pycache_directories(self):
        """Test that __pycache__ directories are removed."""
        # Create __pycache__ directories
        pycache1 = os.path.join(self.temp_dir, "subdir1", "__pycache__")
        pycache2 = os.path.join(self.temp_dir, "subdir2", "__pycache__")
        os.makedirs(pycache1, exist_ok=True)
        os.makedirs(pycache2, exist_ok=True)

        # Create some files in cache directories
        with open(os.path.join(pycache1, "test1.pyc"), "w"):
            pass
        with open(os.path.join(pycache2, "test2.pyc"), "w"):
            pass

        # Verify directories exist
        self.assertTrue(os.path.exists(pycache1))
        self.assertTrue(os.path.exists(pycache2))

        # Clean cache
        _clean_python_cache(self.temp_dir)

        # Verify directories are removed
        self.assertFalse(os.path.exists(pycache1))
        self.assertFalse(os.path.exists(pycache2))

    def test_clean_python_cache_removes_pyc_files(self):
        """Test that .pyc files are removed."""
        # Create .pyc files
        pyc1 = os.path.join(self.temp_dir, "test1.pyc")
        pyc2 = os.path.join(self.temp_dir, "subdir", "test2.pyc")
        os.makedirs(os.path.dirname(pyc2), exist_ok=True)
        with open(pyc1, "w"):
            pass
        with open(pyc2, "w"):
            pass

        # Verify files exist
        self.assertTrue(os.path.exists(pyc1))
        self.assertTrue(os.path.exists(pyc2))

        # Clean cache
        _clean_python_cache(self.temp_dir)

        # Verify files are removed
        self.assertFalse(os.path.exists(pyc1))
        self.assertFalse(os.path.exists(pyc2))

    def test_clean_python_cache_preserves_source_files(self):
        """Test that .py files are preserved."""
        # Create source files
        py1 = os.path.join(self.temp_dir, "test1.py")
        py2 = os.path.join(self.temp_dir, "subdir", "test2.py")
        os.makedirs(os.path.dirname(py2), exist_ok=True)
        with open(py1, "w"):
            pass
        with open(py2, "w"):
            pass

        # Clean cache
        _clean_python_cache(self.temp_dir)

        # Verify source files are preserved
        self.assertTrue(os.path.exists(py1))
        self.assertTrue(os.path.exists(py2))

    def test_clean_python_cache_handles_nonexistent_directory(self):
        """Test that function handles nonexistent directories gracefully."""
        nonexistent_dir = os.path.join(self.temp_dir, "nonexistent")

        # Should not raise exception
        _clean_python_cache(nonexistent_dir)

    def test_clean_python_cache_handles_permission_errors(self):
        """Test that function handles permission errors gracefully."""
        # Create a __pycache__ directory
        pycache = os.path.join(self.temp_dir, "__pycache__")
        os.makedirs(pycache, exist_ok=True)

        # Mock shutil.rmtree to raise PermissionError
        with patch("shutil.rmtree", side_effect=PermissionError("Permission denied")):
            # Should not raise exception
            _clean_python_cache(self.temp_dir)

    def test_clean_python_cache_handles_permission_errors_os_remove(self):
        """Test that function handles permission errors from os.remove gracefully."""
        # Create a .pyc file
        pyc_file = os.path.join(self.temp_dir, "test.pyc")
        with open(pyc_file, "w") as f:
            f.write("dummy")

        # Mock os.remove to raise PermissionError
        with patch("os.remove", side_effect=PermissionError("Permission denied")):
            # Should not raise exception
            _clean_python_cache(self.temp_dir)

    @patch("mmrelay.plugin_loader.logger")
    def test_clean_python_cache_logs_debug_messages(self, mock_logger):
        """
        Verify that cleaning Python cache logs debug messages and includes a removal message for __pycache__ directories.

        The test creates a __pycache__ directory, invokes _clean_python_cache on the containing directory, and asserts that logger.debug was called and one of the debug messages contains "Removed Python cache directory".
        """
        # Create a __pycache__ directory
        pycache = os.path.join(self.temp_dir, "__pycache__")
        os.makedirs(pycache, exist_ok=True)

        # Clean cache
        _clean_python_cache(self.temp_dir)

        # Verify debug messages were logged
        mock_logger.debug.assert_called()

        # Check for cache directory removal message
        debug_calls = [call[0][0] for call in mock_logger.debug.call_args_list]
        self.assertTrue(
            any("Removed Python cache directory" in msg for msg in debug_calls)
        )

    @patch("mmrelay.plugin_loader.logger")
    def test_clean_python_cache_logs_summary_message(self, mock_logger):
        """Test that summary info message is logged when cache directories are removed."""
        # Create multiple __pycache__ directories
        for i in range(3):
            pycache = os.path.join(self.temp_dir, f"subdir{i}", "__pycache__")
            os.makedirs(pycache, exist_ok=True)

        # Clean cache
        _clean_python_cache(self.temp_dir)

        # Verify info message was logged for the summary
        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args[0][0]
        self.assertTrue(call_args.startswith("Cleaned"))
        self.assertIn("3 Python cache directories", call_args)

    @patch("mmrelay.plugin_loader.logger")
    def test_clean_python_cache_logs_combined_info_message(self, mock_logger):
        """Test that combined info message is logged when both cache directories and .pyc files are removed."""
        # Create __pycache__ directories
        pycache1 = os.path.join(self.temp_dir, "subdir1", "__pycache__")
        pycache2 = os.path.join(self.temp_dir, "subdir2", "__pycache__")
        os.makedirs(pycache1, exist_ok=True)
        os.makedirs(pycache2, exist_ok=True)

        # Create .pyc files
        pyc_file1 = os.path.join(self.temp_dir, "test1.pyc")
        pyc_file2 = os.path.join(self.temp_dir, "subdir3", "test2.pyc")
        os.makedirs(os.path.dirname(pyc_file2), exist_ok=True)
        with open(pyc_file1, "w") as f:
            f.write("dummy")
        with open(pyc_file2, "w") as f:
            f.write("dummy")

        # Clean cache
        _clean_python_cache(self.temp_dir)

        # Verify info message was logged for the combined summary
        mock_logger.info.assert_called_once()
        combined_message = mock_logger.info.call_args[0][0]
        self.assertTrue(combined_message.startswith("Cleaned"))
        self.assertIn("Python cache director", combined_message)
        self.assertIn(".pyc file", combined_message)
        self.assertIn(" and ", combined_message)  # Indicates both types were cleaned


class TestPluginDirectories(unittest.TestCase):
    """Test cases for plugin directory discovery and creation."""

    @patch("os.makedirs")
    @patch("mmrelay.plugin_loader.get_base_dir")
    @patch("mmrelay.plugin_loader.get_app_path")
    @patch("mmrelay.plugin_loader.logger")
    def test_get_plugin_dirs_user_dir_success(
        self, mock_logger, mock_get_app_path, mock_get_base_dir, mock_makedirs
    ):
        """Test successful user directory creation."""
        from mmrelay.plugin_loader import _get_plugin_dirs

        mock_get_base_dir.return_value = "/user/base"
        mock_get_app_path.return_value = "/app/path"

        dirs = _get_plugin_dirs("custom")

        self.assertIn("/user/base/plugins/custom", dirs)
        self.assertIn("/app/path/plugins/custom", dirs)

    @patch("mmrelay.plugin_loader.get_base_dir")
    @patch("mmrelay.plugin_loader.get_app_path")
    @patch("mmrelay.plugin_loader.logger")
    @patch("os.makedirs")
    def test_get_plugin_dirs_user_dir_permission_error(
        self, mock_makedirs, mock_logger, mock_get_app_path, mock_get_base_dir
    ):
        """Test handling of permission error in user directory."""
        from mmrelay.plugin_loader import _get_plugin_dirs

        mock_get_base_dir.return_value = "/user/base"
        mock_get_app_path.return_value = "/app/path"
        mock_makedirs.side_effect = [
            PermissionError("Permission denied"),
            None,  # Second call succeeds
        ]

        dirs = _get_plugin_dirs("custom")

        # Should only include local directory since user dir failed
        self.assertEqual(len(dirs), 1)
        self.assertIn("/app/path/plugins/custom", dirs)
        mock_logger.warning.assert_called()

    @patch("mmrelay.plugin_loader.get_base_dir")
    @patch("mmrelay.plugin_loader.get_app_path")
    @patch("mmrelay.plugin_loader.logger")
    @patch("os.makedirs")
    def test_get_plugin_dirs_local_dir_os_error(
        self, mock_makedirs, mock_logger, mock_get_app_path, mock_get_base_dir
    ):
        """Test handling of OS error in local directory."""
        from mmrelay.plugin_loader import _get_plugin_dirs

        mock_get_base_dir.return_value = "/user/base"
        mock_get_app_path.return_value = "/app/path"
        mock_makedirs.side_effect = [
            None,  # User dir succeeds
            OSError("Disk full"),
        ]

        dirs = _get_plugin_dirs("custom")

        # Should only include user directory since local dir failed
        self.assertEqual(len(dirs), 1)
        self.assertIn("/user/base/plugins/custom", dirs)
        mock_logger.debug.assert_called()


class TestDependencyInstallation(BaseGitTest):
    """Test cases for dependency installation functionality."""

    def setUp(self):
        """Set up mocks and temporary directory."""
        super().setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = os.path.join(self.temp_dir, "test-plugin")
        self.requirements_path = os.path.join(self.repo_path, "requirements.txt")
        os.makedirs(self.repo_path, exist_ok=True)
        with open(self.requirements_path, "w") as f:
            f.write("requests==2.28.0\n")

        # Prevent tests from interfering with each other
        self.pl_patcher = patch("mmrelay.plugin_loader.config", new=None)
        self.pl_patcher.start()
        self.addCleanup(self.pl_patcher.stop)

    def tearDown(self):
        """Clean up temporary directory."""
        shutil.rmtree(self.temp_dir)
        super().tearDown()

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._check_auto_install_enabled")
    def test_install_plugin_requirements_disabled(
        self, mock_check_enabled, mock_logger
    ):
        """Test dependency installation when disabled."""
        mock_check_enabled.return_value = False
        _install_requirements_for_repo(self.repo_path, "test-plugin")
        mock_logger.warning.assert_called_with(
            "Auto-install of requirements for %s disabled by config; skipping.",
            "test-plugin",
        )

    @patch("mmrelay.plugin_loader._collect_requirements")
    @patch("mmrelay.plugin_loader._check_auto_install_enabled")
    def test_install_plugin_requirements_no_file(
        self, mock_check_enabled, mock_collect
    ):
        """Test dependency installation when requirements file doesn't exist."""
        mock_check_enabled.return_value = True
        os.remove(self.requirements_path)
        _install_requirements_for_repo(self.repo_path, "test-plugin")
        mock_collect.assert_not_called()

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run")
    @patch("mmrelay.plugin_loader._check_auto_install_enabled")
    @patch("mmrelay.plugin_loader._filter_risky_requirements")
    @patch("mmrelay.plugin_loader._collect_requirements")
    @patch("shutil.which", return_value=None)
    @patch("mmrelay.plugin_loader.tempfile.NamedTemporaryFile")
    @patch("mmrelay.plugin_loader.os.unlink")
    def test_install_plugin_requirements_pip_in_venv(
        self,
        mock_unlink,
        mock_temp_file,
        mock_which,
        mock_collect,
        mock_filter,
        mock_check_enabled,
        mock_run,
        mock_logger,
    ):
        """Test dependency installation with pip in virtual environment."""
        mock_check_enabled.return_value = True
        mock_collect.return_value = ["requests==2.28.0"]
        mock_filter.return_value = (["requests==2.28.0"], [], False)

        # Mock the temporary file
        mock_file = mock_temp_file.return_value.__enter__.return_value
        temp_req = os.path.join(self.temp_dir, "test_requirements.txt")
        mock_file.name = temp_req

        with patch.dict(os.environ, {"VIRTUAL_ENV": "/venv"}, clear=True):
            _install_requirements_for_repo(self.repo_path, "test-plugin")

        # Check that the command uses -r with the temporary file
        called_cmd = mock_run.call_args[0][0]
        expected_base = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
        ]
        assert called_cmd[:6] == expected_base
        assert "-r" in called_cmd
        assert called_cmd[called_cmd.index("-r") + 1] == temp_req
        mock_run.assert_called_once_with(called_cmd, timeout=600)

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run")
    @patch("mmrelay.plugin_loader._check_auto_install_enabled")
    @patch("mmrelay.plugin_loader._filter_risky_requirements")
    @patch("mmrelay.plugin_loader._collect_requirements")
    @patch("shutil.which", return_value="/usr/bin/pipx")
    def test_install_plugin_requirements_pipx_injection(
        self,
        mock_which,
        mock_collect,
        mock_filter,
        mock_check_enabled,
        mock_run,
        mock_logger,
    ):
        """Test dependency installation with pipx."""
        mock_check_enabled.return_value = True
        mock_collect.return_value = [
            "requests==2.28.0",
            "--extra-index-url https://pypi.org/simple",
        ]
        # The function tokenizes lines from _collect_requirements, so filter receives tokenized input
        mock_filter.return_value = (
            ["requests==2.28.0", "--extra-index-url", "https://pypi.org/simple"],
            [],
            False,
        )
        with patch.dict(os.environ, {"PIPX_HOME": "/pipx/home"}):
            _install_requirements_for_repo(self.repo_path, "test-plugin")

        # Verify the call uses temporary file approach
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        cmd = call_args[0][0]

        # Should be pipx inject with --requirement containing requirements file
        assert cmd[0] == "/usr/bin/pipx"
        assert cmd[1] == "inject"
        assert cmd[2] == "mmrelay"
        assert cmd[3] == "--requirement"

        # The --requirement should point to a temporary file path
        req_file = cmd[4]
        assert req_file.endswith(".txt")

        # Verify timeout
        assert call_args[1]["timeout"] == 600

    def test_install_plugin_requirements_pip_install(self):
        """Test dependency installation with pip."""
        with (
            patch("mmrelay.plugin_loader.logger"),
            patch("mmrelay.plugin_loader._run") as mock_run,
            patch(
                "mmrelay.plugin_loader._check_auto_install_enabled"
            ) as mock_check_enabled,
            patch("mmrelay.plugin_loader._filter_risky_requirements") as mock_filter,
            patch("mmrelay.plugin_loader._collect_requirements") as mock_collect,
            patch("sys.prefix", "/fake/prefix"),
            patch("sys.base_prefix", "/fake/prefix"),
            patch("shutil.which", return_value=None),
            patch(
                "mmrelay.plugin_loader.tempfile.NamedTemporaryFile"
            ) as mock_temp_file,
            patch("mmrelay.plugin_loader.os.unlink"),
        ):
            mock_check_enabled.return_value = True
            mock_collect.return_value = ["requests==2.28.0"]
            mock_filter.return_value = (["requests==2.28.0"], [], False)

            # Mock temporary file
            mock_file = mock_temp_file.return_value.__enter__.return_value
            mock_file.name = "/tmp/test_requirements.txt"

            with patch.dict(os.environ, {}, clear=True):
                _install_requirements_for_repo(self.repo_path, "test-plugin")

            # Check that command uses -r with temporary file and --user flag
            called_cmd = mock_run.call_args[0][0]
            expected_base = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--user",
            ]
            assert called_cmd[:7] == expected_base
            assert "-r" in called_cmd
            assert (
                called_cmd[called_cmd.index("-r") + 1] == "/tmp/test_requirements.txt"
            )
            mock_run.assert_called_once_with(called_cmd, timeout=600)

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run")
    @patch("mmrelay.plugin_loader._check_auto_install_enabled")
    @patch("mmrelay.plugin_loader._filter_risky_requirements")
    @patch("mmrelay.plugin_loader._collect_requirements")
    def test_install_plugin_requirements_with_flagged_deps(
        self,
        mock_collect,
        mock_filter,
        mock_check_enabled,
        mock_run,
        mock_logger,
    ):
        """Test dependency installation with flagged dependencies."""
        mock_check_enabled.return_value = True
        mock_collect.return_value = [
            "requests==2.28.0",
            "git+https://github.com/user/repo.git",
        ]
        mock_filter.return_value = (
            ["requests==2.28.0"],
            ["git+https://github.com/user/repo.git"],
            False,
        )
        _install_requirements_for_repo(self.repo_path, "test-plugin")
        mock_logger.warning.assert_called_with(
            "Skipping %d flagged dependency entries for %s. Set security.allow_untrusted_dependencies=True to override.",
            1,
            "test-plugin",
        )

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run")
    @patch("mmrelay.plugin_loader._check_auto_install_enabled")
    @patch("mmrelay.plugin_loader._filter_risky_requirements")
    @patch("mmrelay.plugin_loader._collect_requirements")
    def test_install_plugin_requirements_installation_error(
        self,
        mock_collect,
        mock_filter,
        mock_check_enabled,
        mock_run,
        mock_logger,
    ):
        """Test handling of installation errors."""
        mock_check_enabled.return_value = True
        mock_collect.return_value = ["requests==2.28.0"]
        mock_filter.return_value = (["requests==2.28.0"], [], False)
        mock_run.side_effect = subprocess.CalledProcessError(1, "pip")
        _install_requirements_for_repo(self.repo_path, "test-plugin")
        mock_logger.exception.assert_called()

    def test_install_plugin_requirements_pipx_not_found(self):
        """Test fallback to pip when pipx is not found."""
        with (
            patch("mmrelay.plugin_loader.logger"),
            patch("mmrelay.plugin_loader._run") as mock_run,
            patch(
                "mmrelay.plugin_loader._check_auto_install_enabled"
            ) as mock_check_enabled,
            patch("mmrelay.plugin_loader._filter_risky_requirements") as mock_filter,
            patch("mmrelay.plugin_loader._collect_requirements") as mock_collect,
            patch("sys.prefix", "/fake/prefix"),
            patch("sys.base_prefix", "/fake/prefix"),
            patch("shutil.which", return_value=None),
            patch(
                "mmrelay.plugin_loader.tempfile.NamedTemporaryFile"
            ) as mock_temp_file,
            patch("mmrelay.plugin_loader.os.unlink"),
        ):
            mock_check_enabled.return_value = True
            mock_collect.return_value = ["requests==2.28.0"]
            mock_filter.return_value = (["requests==2.28.0"], [], False)

            # Mock temporary file
            mock_file = mock_temp_file.return_value.__enter__.return_value
            mock_file.name = "/tmp/test_requirements.txt"

            with patch.dict(os.environ, {}, clear=True):
                _install_requirements_for_repo(self.repo_path, "test-plugin")

            # Should fall back to pip with -r and temporary file
            called_cmd = mock_run.call_args[0][0]
            expected_base = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--user",
            ]
            assert called_cmd[:7] == expected_base
            assert "-r" in called_cmd
            assert (
                called_cmd[called_cmd.index("-r") + 1] == "/tmp/test_requirements.txt"
            )
            mock_run.assert_called_once_with(called_cmd, timeout=600)

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run")
    @patch("mmrelay.plugin_loader._check_auto_install_enabled")
    @patch("mmrelay.plugin_loader._filter_risky_requirements")
    @patch("mmrelay.plugin_loader._collect_requirements")
    @patch("shutil.which", return_value="/usr/bin/pipx")
    def test_install_plugin_requirements_pipx_inject_fails(
        self,
        mock_which,
        mock_collect,
        mock_filter,
        mock_check_enabled,
        mock_run,
        mock_logger,
    ):
        """Test handling of pipx inject failure."""
        mock_check_enabled.return_value = True
        mock_collect.return_value = ["requests==2.28.0"]
        mock_filter.return_value = (["requests==2.28.0"], [], False)
        mock_run.side_effect = subprocess.CalledProcessError(1, "pipx")

        with patch.dict(os.environ, {"PIPX_HOME": "/pipx/home"}):
            _install_requirements_for_repo(self.repo_path, "test-plugin")

        # Should log error and warning
        mock_logger.exception.assert_called()
        mock_logger.warning.assert_called_with(
            "Plugin %s may not work correctly without its dependencies",
            "test-plugin",
        )

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run")
    @patch("mmrelay.plugin_loader._check_auto_install_enabled")
    @patch("mmrelay.plugin_loader._filter_risky_requirements")
    @patch("mmrelay.plugin_loader._collect_requirements")
    def test_install_plugin_requirements_pipx_no_packages(
        self, mock_collect, mock_filter, mock_check_enabled, mock_run, mock_logger
    ):
        """Test pipx injection when no packages to install."""
        mock_check_enabled.return_value = True
        mock_collect.return_value = ["--extra-index-url https://pypi.org/simple"]
        mock_filter.return_value = (
            ["--extra-index-url https://pypi.org/simple"],
            [],
            False,
        )

        with open(self.requirements_path, "w") as f:
            f.write("--extra-index-url https://pypi.org/simple\n")

        with patch.dict(os.environ, {"PIPX_HOME": "/pipx/home"}):
            with patch("shutil.which", return_value="/usr/bin/pipx"):
                _install_requirements_for_repo(self.repo_path, "test-plugin")

        # Should not call pipx inject when no packages
        mock_run.assert_not_called()
        mock_logger.info.assert_called_with(
            "No dependency installation run for plugin %s",
            "test-plugin",
        )

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run")
    @patch("mmrelay.plugin_loader._check_auto_install_enabled")
    @patch("mmrelay.plugin_loader._filter_risky_requirements")
    @patch("mmrelay.plugin_loader._collect_requirements")
    def test_install_plugin_requirements_allow_untrusted(
        self, mock_collect, mock_filter, mock_check_enabled, mock_run, mock_logger
    ):
        """Test dependency installation with untrusted dependencies allowed."""
        mock_check_enabled.return_value = True
        mock_collect.return_value = [
            "requests==2.28.0",
            "git+https://github.com/user/repo.git",
        ]
        mock_filter.return_value = (
            ["requests==2.28.0"],
            ["git+https://github.com/user/repo.git"],
            True,
        )

        with open(self.requirements_path, "w") as f:
            f.write("requests==2.28.0\ngit+https://github.com/user/repo.git\n")

        # Mock the config to allow untrusted dependencies
        with patch(
            "mmrelay.plugin_loader.config",
            {"security": {"allow_untrusted_dependencies": True}},
        ):
            _install_requirements_for_repo(self.repo_path, "test-plugin")

        # Should log warning but still install
        mock_logger.warning.assert_called_with(
            "Allowing %d flagged dependency entries for %s due to security.allow_untrusted_dependencies=True",
            1,
            "test-plugin",
        )
        mock_run.assert_called()

    def test_schedule_job_creates_job_with_tag(self):
        """Test that schedule_job creates a job with the correct tag."""
        with patch("mmrelay.plugin_loader.schedule") as mock_schedule:
            mock_job = MagicMock()
            mock_schedule.every.return_value = mock_job

            result = schedule_job("test_plugin", 5)

            mock_schedule.every.assert_called_once_with(5)
            mock_job.tag.assert_called_once_with("test_plugin")
            self.assertEqual(result, mock_job)

    def test_schedule_job_returns_none_when_schedule_unavailable(self):
        """Test that schedule_job returns None when schedule library is not available."""
        with patch("mmrelay.plugin_loader.schedule", None):
            result = schedule_job("test_plugin", 5)
            self.assertIsNone(result)

    def test_clear_plugin_jobs_calls_schedule_clear(self):
        """Test that clear_plugin_jobs calls schedule.clear with plugin name."""
        with patch("mmrelay.plugin_loader.schedule") as mock_schedule:
            clear_plugin_jobs("test_plugin")
            mock_schedule.clear.assert_called_once_with("test_plugin")

    def test_clear_plugin_jobs_handles_none_schedule(self):
        """Test that clear_plugin_jobs handles None schedule gracefully."""
        with patch("mmrelay.plugin_loader.schedule", None):
            # Should not raise an exception
            clear_plugin_jobs("test_plugin")

    @patch("mmrelay.plugin_loader.threading")
    @patch("mmrelay.plugin_loader.schedule")
    def test_start_global_scheduler_starts_thread(self, mock_schedule, mock_threading):
        """Test that start_global_scheduler creates and starts a daemon thread."""

        # Reset global state before test
        pl._global_scheduler_thread = None
        pl._global_scheduler_stop_event = None

        # Ensure mock_schedule is truthy so the function doesn't return early
        mock_schedule.__bool__ = lambda: True

        mock_event = MagicMock()
        mock_threading.Event.return_value = mock_event
        mock_thread = MagicMock()
        mock_threading.Thread.return_value = mock_thread

        start_global_scheduler()

        # Event should be called since schedule is available (mocked)
        mock_threading.Event.assert_called_once()
        mock_threading.Thread.assert_called_once()
        mock_thread.start.assert_called_once()

    @patch("mmrelay.plugin_loader.threading")
    @patch("mmrelay.plugin_loader.schedule", None)
    def test_start_global_scheduler_no_schedule_library(self, mock_threading):
        """Test that start_global_scheduler exits early when schedule is None."""
        start_global_scheduler()

        # Should not create thread when schedule is None
        mock_threading.Thread.assert_not_called()

    @patch("mmrelay.plugin_loader.threading")
    @patch("mmrelay.plugin_loader.schedule")
    def test_start_global_scheduler_already_running(
        self, mock_schedule, mock_threading
    ):
        """Test that start_global_scheduler exits early when already running."""

        # Simulate already running thread
        pl._global_scheduler_thread = MagicMock()
        pl._global_scheduler_thread.is_alive.return_value = True

        start_global_scheduler()

        # Should not create new thread
        mock_threading.Thread.assert_not_called()

    @patch("mmrelay.plugin_loader.threading")
    @patch("mmrelay.plugin_loader.schedule")
    def test_stop_global_scheduler_stops_thread(self, mock_schedule, mock_threading):
        """Test that stop_global_scheduler stops the scheduler thread."""

        # Setup running thread
        mock_event = MagicMock()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        pl._global_scheduler_thread = mock_thread
        pl._global_scheduler_stop_event = mock_event

        stop_global_scheduler()

        mock_event.set.assert_called_once()
        mock_thread.join.assert_called_once_with(timeout=5)
        mock_schedule.clear.assert_called_once()
        self.assertIsNone(pl._global_scheduler_thread)

    @patch("mmrelay.plugin_loader.threading")
    def test_stop_global_scheduler_no_thread(self, mock_threading):
        """Test that stop_global_scheduler exits early when no thread exists."""

        # Ensure no thread is running
        pl._global_scheduler_thread = None

        stop_global_scheduler()

        # Should not call any threading methods

        mock_threading.Event.assert_not_called()

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    @patch("os.makedirs")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_commit_ref_type_validation(
        self, mock_isdir, _mock_makedirs, mock_logger, mock_is_allowed, mock_run_git
    ):
        """Test that 'commit' is accepted as a valid ref type."""
        import subprocess

        mock_is_allowed.return_value = True
        mock_isdir.return_value = False  # Repo doesn't exist
        mock_run_git.side_effect = subprocess.CalledProcessError(
            1, "git"
        )  # Git operations fail
        ref = {"type": "commit", "value": "deadbeef"}

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertFalse(
            result
        )  # Function should return False on git operation failures
        # Verify no "Invalid ref type" error was logged (commit ref type should be accepted)
        for call_args in mock_logger.error.call_args_list:
            self.assertNotIn("Invalid ref type", str(call_args))

    def test_validate_clone_inputs_valid_branch(self):
        """Test _validate_clone_inputs with valid branch ref."""
        ref = {"type": "branch", "value": "main"}
        result = _validate_clone_inputs("https://github.com/user/repo.git", ref)

        is_valid, repo_url, ref_type, ref_value, repo_name = result
        self.assertTrue(is_valid)
        self.assertEqual(repo_url, "https://github.com/user/repo.git")
        self.assertEqual(ref_type, "branch")
        self.assertEqual(ref_value, "main")
        self.assertEqual(repo_name, "repo")

    def test_validate_clone_inputs_valid_tag(self):
        """Test _validate_clone_inputs with valid tag ref."""
        ref = {"type": "tag", "value": "v1.0.0"}
        result = _validate_clone_inputs("https://github.com/user/repo.git", ref)

        is_valid, repo_url, ref_type, ref_value, repo_name = result
        self.assertTrue(is_valid)
        self.assertEqual(repo_url, "https://github.com/user/repo.git")
        self.assertEqual(ref_type, "tag")
        self.assertEqual(ref_value, "v1.0.0")
        self.assertEqual(repo_name, "repo")

    def test_validate_clone_inputs_valid_commit(self):
        """Test _validate_clone_inputs with valid commit ref."""
        ref = {"type": "commit", "value": "a1b2c3d4"}
        result = _validate_clone_inputs("https://github.com/user/repo.git", ref)

        is_valid, repo_url, ref_type, ref_value, repo_name = result
        self.assertTrue(is_valid)
        self.assertEqual(repo_url, "https://github.com/user/repo.git")
        self.assertEqual(ref_type, "commit")
        self.assertEqual(ref_value, "a1b2c3d4")
        self.assertEqual(repo_name, "repo")

    def test_validate_clone_inputs_invalid_ref_type(self):
        """Test _validate_clone_inputs with invalid ref type."""
        ref = {"type": "invalid", "value": "main"}
        result = _validate_clone_inputs("https://github.com/user/repo.git", ref)

        self.assertEqual(result, (False, None, None, None, None))

    def test_validate_clone_inputs_missing_ref_value(self):
        """Test _validate_clone_inputs with missing ref value."""
        ref = {"type": "branch"}
        result = _validate_clone_inputs("https://github.com/user/repo.git", ref)

        self.assertEqual(result, (False, None, None, None, None))

    def test_validate_clone_inputs_empty_url(self):
        """Test _validate_clone_inputs with empty URL."""
        ref = {"type": "branch", "value": "main"}
        result = _validate_clone_inputs("", ref)

        self.assertEqual(result, (False, None, None, None, None))

    def test_validate_clone_inputs_none_url(self):
        """Test _validate_clone_inputs with None URL."""
        ref = {"type": "branch", "value": "main"}
        # The function handles None by converting to empty string internally
        result = _validate_clone_inputs(None, ref)  # type: ignore[arg-type]

        self.assertEqual(result, (False, None, None, None, None))

    def test_validate_clone_inputs_invalid_commit_too_short(self):
        """Test _validate_clone_inputs with commit hash too short (< 7 chars)."""
        ref = {"type": "commit", "value": "abc123"}  # 6 chars
        result = _validate_clone_inputs("https://github.com/user/repo.git", ref)

        self.assertEqual(result, (False, None, None, None, None))

    def test_validate_clone_inputs_invalid_commit_too_long(self):
        """Test _validate_clone_inputs with commit hash too long (> 40 chars)."""
        ref = {
            "type": "commit",
            "value": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4",
        }  # 41 chars
        result = _validate_clone_inputs("https://github.com/user/repo.git", ref)

        self.assertEqual(result, (False, None, None, None, None))

    def test_validate_clone_inputs_invalid_commit_non_hex(self):
        """Test _validate_clone_inputs with commit hash containing non-hex characters."""
        ref = {"type": "commit", "value": "g1b2c3d"}  # contains 'g'
        result = _validate_clone_inputs("https://github.com/user/repo.git", ref)

        self.assertEqual(result, (False, None, None, None, None))

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_new_repo_to_branch_or_tag_default_branch_success(
        self, mock_logger, mock_run_git
    ):
        """Test _clone_new_repo_to_branch_or_tag with default branch success."""
        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "branch",
            "main",
            "repo",
            self.temp_plugins_dir,
            True,  # is_default_branch
        )

        self.assertTrue(result)
        # Should clone with --branch main and --filter=blob:none
        mock_run_git.assert_called_with(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--branch",
                "main",
                "https://github.com/user/repo.git",
                "repo",
            ],
            cwd=self.temp_plugins_dir,
            timeout=120,
        )
        mock_logger.info.assert_called_with(
            "Cloned repository %s from %s at %s %s",
            "repo",
            "https://github.com/user/repo.git",
            "branch",
            "main",
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_new_repo_to_branch_or_tag_default_branch_fallback(
        self, mock_logger, mock_run_git
    ):
        """Test _clone_new_repo_to_branch_or_tag with default branch fallback."""
        # First call fails, second succeeds
        mock_run_git.side_effect = [
            subprocess.CalledProcessError(1, "git"),  # main fails
            None,  # master succeeds
        ]

        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "branch",
            "main",
            "repo",
            self.temp_plugins_dir,
            True,  # is_default_branch
        )

        self.assertTrue(result)
        # Should try main first, then master
        calls = mock_run_git.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[0][0][0],
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--branch",
                "main",
                "https://github.com/user/repo.git",
                "repo",
            ],
        )
        self.assertEqual(
            calls[1][0][0],
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--branch",
                "master",
                "https://github.com/user/repo.git",
                "repo",
            ],
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_new_repo_to_branch_or_tag_default_branch_final_fallback(
        self, mock_logger, mock_run_git
    ):
        """Test _clone_new_repo_to_branch_or_tag with final fallback to default branch."""
        # Both main and master fail, fallback to clone without branch
        mock_run_git.side_effect = [
            subprocess.CalledProcessError(1, "git"),  # main fails
            subprocess.CalledProcessError(1, "git"),  # master fails
            None,  # clone succeeds
        ]

        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "branch",
            "main",
            "repo",
            self.temp_plugins_dir,
            True,  # is_default_branch
        )

        self.assertTrue(result)
        # Should try main, master, then clone without branch
        calls = mock_run_git.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(
            calls[2][0][0],
            [
                "git",
                "clone",
                "--filter=blob:none",
                "https://github.com/user/repo.git",
                "repo",
            ],
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_new_repo_to_branch_or_tag_tag_success(
        self, mock_logger, mock_run_git
    ):
        """Test _clone_new_repo_to_branch_or_tag with tag success."""
        import subprocess

        mock_run_git.side_effect = lambda *args, **kwargs: (
            subprocess.CompletedProcess(args[0], 0, stdout="some_commit\n", stderr="")
            if "rev-parse" in args[0] and "HEAD" in args[0]
            else (
                subprocess.CompletedProcess(
                    args[0], 0, stdout="tag_commit\n", stderr=""
                )
                if "rev-parse" in args[0]
                else subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")
            )
        )
        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "tag",
            "v1.0.0",
            "repo",
            self.temp_plugins_dir,
            False,  # not default branch
        )

        self.assertTrue(result)
        # Should clone default branch, then fetch and checkout tag
        expected_calls = [
            call(
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "https://github.com/user/repo.git",
                    "repo",
                ],
                cwd=self.temp_plugins_dir,
                timeout=120,
            ),
            # Check if already at the tag's commit
            call(
                ["git", "-C", f"{self.temp_plugins_dir}/repo", "rev-parse", "HEAD"],
                capture_output=True,
            ),
            call(
                [
                    "git",
                    "-C",
                    f"{self.temp_plugins_dir}/repo",
                    "rev-parse",
                    "v1.0.0^{commit}",
                ],
                capture_output=True,
            ),
            call(
                [
                    "git",
                    "-C",
                    f"{self.temp_plugins_dir}/repo",
                    "fetch",
                    "origin",
                    "refs/tags/v1.0.0",
                ],
                timeout=120,
            ),
            call(
                ["git", "-C", f"{self.temp_plugins_dir}/repo", "checkout", "v1.0.0"],
                timeout=120,
            ),
        ]
        mock_run_git.assert_has_calls(expected_calls)
        mock_logger.info.assert_any_call(
            "Cloned repository %s from %s at %s %s",
            "repo",
            "https://github.com/user/repo.git",
            "tag",
            "default branch",
        )
        mock_logger.info.assert_any_call(
            "Successfully fetched and checked out tag %s for %s", "v1.0.0", "repo"
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_new_repo_to_branch_or_tag_tag_fetch_fallback(
        self, mock_logger, mock_run_git
    ):
        """Test _clone_new_repo_to_branch_or_tag with tag fetch fallback."""
        # Clone succeeds, rev-parse succeed but don't match, then fetch and checkout succeed
        mock_run_git.side_effect = [
            None,  # clone succeeds
            MagicMock(
                stdout=MagicMock(strip=MagicMock(return_value="different_commit"))
            ),  # rev-parse HEAD
            MagicMock(
                stdout=MagicMock(strip=MagicMock(return_value="tag_commit"))
            ),  # rev-parse tag
            None,  # fetch succeeds
            None,  # checkout succeeds
        ]

        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "tag",
            "v1.0.0",
            "repo",
            self.temp_plugins_dir,
            False,  # not default branch
        )

        self.assertTrue(result)
        # Should fetch and checkout after clone
        calls = mock_run_git.call_args_list
        self.assertEqual(len(calls), 5)
        self.assertEqual(
            calls[3][0][0],
            ["git", "-C", self.temp_repo_path, "fetch", "origin", "refs/tags/v1.0.0"],
        )
        self.assertEqual(
            calls[4][0][0], ["git", "-C", self.temp_repo_path, "checkout", "v1.0.0"]
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_new_repo_to_branch_or_tag_tag_fetch_fallback_alt(
        self, mock_logger, mock_run_git
    ):
        """Test _clone_new_repo_to_branch_or_tag with alternative tag fetch."""
        # Clone succeeds, rev-parse succeed but don't match, first fetch fails, alternative fetch succeeds, checkout succeeds
        mock_run_git.side_effect = [
            None,  # clone succeeds
            MagicMock(
                stdout=MagicMock(strip=MagicMock(return_value="different_commit"))
            ),  # rev-parse HEAD
            MagicMock(
                stdout=MagicMock(strip=MagicMock(return_value="tag_commit"))
            ),  # rev-parse tag
            subprocess.CalledProcessError(1, "git"),  # first fetch fails
            None,  # alternative fetch succeeds
            None,  # checkout succeeds
        ]

        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "tag",
            "v1.0.0",
            "repo",
            self.temp_plugins_dir,
            False,  # not default branch
        )

        self.assertTrue(result)
        # Should try alternative fetch format
        calls = mock_run_git.call_args_list
        self.assertEqual(
            calls[4][0][0],
            [
                "git",
                "-C",
                self.temp_repo_path,
                "fetch",
                "--tags",
            ],
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_new_repo_to_branch_or_tag_tag_as_branch_fallback(
        self, mock_logger, mock_run_git
    ):
        """Test _clone_new_repo_to_branch_or_tag with tag as branch fallback."""
        mock_run_git.side_effect = [
            subprocess.CalledProcessError(1, "git"),  # clone --branch fails
            None,  # clone without branch succeeds
            subprocess.CalledProcessError(1, "git"),  # fetch tag fails
            subprocess.CalledProcessError(1, "git"),  # alt fetch fails
            subprocess.CalledProcessError(1, "git"),  # fetch as branch fails
        ]
        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "tag",
            "v1.0.0",
            "repo",
            self.temp_plugins_dir,
            False,  # not default branch
        )
        self.assertFalse(
            result
        )  # Tag checkout should fail, so overall operation should fail

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_new_repo_to_branch_or_tag_clone_failure(
        self, mock_logger, mock_run_git
    ):
        """Test _clone_new_repo_to_branch_or_tag with clone failure."""
        # All clone attempts fail
        mock_run_git.side_effect = subprocess.CalledProcessError(1, "git")

        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "branch",
            "main",
            "repo",
            self.temp_plugins_dir,
            True,  # is_default_branch
        )

        self.assertFalse(result)
        mock_logger.exception.assert_called_with(
            f"Error cloning repository repo; please manually clone into {self.temp_repo_path}"
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_new_repo_to_branch_or_tag_file_not_found(
        self, mock_logger, mock_run_git
    ):
        """Test _clone_new_repo_to_branch_or_tag with FileNotFoundError."""
        mock_run_git.side_effect = FileNotFoundError("git not found")
        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "branch",
            "main",
            "repo",
            self.temp_plugins_dir,
            True,  # is_default_branch
        )
        self.assertFalse(result)
        mock_logger.exception.assert_called_with(
            "Error cloning repository repo; git not found."
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_update_existing_repo_to_branch_or_tag_default_branch_already_on_branch(
        self, mock_logger, mock_run_git
    ):
        """Test updating when already on the correct default branch."""
        # Mock current branch check
        mock_run_git.side_effect = [
            None,  # fetch succeeds
            MagicMock(stdout="main\n"),  # current branch is main
            None,  # pull succeeds
        ]

        result = _update_existing_repo_to_branch_or_tag(
            self.temp_repo_path,
            "branch",
            "main",
            "repo",
            True,  # is_default_branch
            ["main", "master"],
        )

        self.assertTrue(result)
        mock_logger.info.assert_called_with(
            "Updated repository %s to %s %s", "repo", "branch", "main"
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_update_existing_repo_to_branch_or_tag_default_branch_switch(
        self, mock_logger, mock_run_git
    ):
        """Test switching to a different default branch."""
        # Mock current branch check and checkout
        mock_run_git.side_effect = [
            None,  # fetch succeeds
            MagicMock(stdout="master\n"),  # current branch is master
            None,  # checkout main succeeds
            None,  # pull succeeds
        ]

        result = _update_existing_repo_to_branch_or_tag(
            self.temp_repo_path,
            "branch",
            "main",
            "repo",
            True,  # is_default_branch
            ["main", "master"],
        )

        self.assertTrue(result)
        mock_logger.info.assert_called_with(
            "Updated repository %s to %s %s", "repo", "branch", "main"
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_update_existing_repo_to_branch_or_tag_non_default_branch(
        self, mock_logger, mock_run_git
    ):
        """Test updating a non-default branch."""
        mock_run_git.side_effect = [
            subprocess.CompletedProcess([], 0),  # fetch succeeds
            subprocess.CompletedProcess([], 0),  # checkout succeeds
            subprocess.CompletedProcess([], 0),  # pull succeeds
        ]

        result = _update_existing_repo_to_branch_or_tag(
            self.temp_repo_path,
            "branch",
            "feature-branch",
            "repo",
            False,  # is_default_branch
            ["main", "master"],
        )

        self.assertTrue(result)
        mock_logger.info.assert_called_with(
            "Updated repository %s to %s %s", "repo", "branch", "feature-branch"
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_update_existing_repo_to_branch_or_tag_tag_update(
        self, mock_logger, mock_run_git
    ):
        """Test updating to a tag."""
        mock_run_git.side_effect = [
            subprocess.CompletedProcess([], 0),  # fetch succeeds
            MagicMock(stdout="abc123\n"),  # current commit
            subprocess.CalledProcessError(
                1, "git"
            ),  # rev-parse tag fails (tag not local)
            subprocess.CompletedProcess([], 0),  # fetch tag succeeds
            subprocess.CompletedProcess([], 0),  # checkout succeeds
        ]

        result = _update_existing_repo_to_branch_or_tag(
            self.temp_repo_path,
            "tag",
            "v1.0.0",
            "repo",
            False,  # is_default_branch (tags are not default branches)
            ["main", "master"],
        )

        self.assertTrue(result)
        mock_logger.info.assert_called_with(
            "Successfully fetched and checked out tag %s for %s", "v1.0.0", "repo"
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_update_existing_repo_to_branch_or_tag_fetch_failure(
        self, mock_logger, mock_run_git
    ):
        """Test handling of fetch failure."""
        mock_run_git.side_effect = subprocess.CalledProcessError(
            1, "git"
        )  # all git operations fail

        result = _update_existing_repo_to_branch_or_tag(
            self.temp_repo_path,
            "branch",
            "main",
            "repo",
            True,  # is_default_branch
            ["main", "master"],
        )

        # Should return False when all operations fail
        self.assertFalse(result)
        mock_logger.warning.assert_called()


if __name__ == "__main__":
    unittest.main()
