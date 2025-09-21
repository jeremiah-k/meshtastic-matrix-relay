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
import subprocess  # nosec B404 - Used for controlled test environment operations
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.plugin_loader import (
    clone_or_update_repo,
    get_community_plugin_dirs,
    get_custom_plugin_dirs,
    load_plugins,
    load_plugins_from_directory,
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

    async def handle_meshtastic_message(
        self, packet, interface, longname, shortname, meshnet_name
    ):
        """
        Asynchronously handles a Meshtastic message; implemented as a mock to suppress warnings during testing.
        """
        pass

    async def handle_room_message(self, room, event, full_message):
        """
        Asynchronously handles a room message event for testing purposes.

        This mock method is implemented to satisfy interface requirements and prevent warnings during tests.
        """
        pass


class TestPluginLoader(unittest.TestCase):
    """Test cases for plugin loading functionality."""

    def setUp(self):
        """
        Prepares a temporary test environment with isolated plugin directories and resets plugin loader state before each test.
        """
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
        # Clean up temporary directories
        import shutil

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
        """Ensure dependency installs on user site become importable for plugins."""

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
            with open(
                os.path.join(user_site, "mockdep.py"), "w", encoding="utf-8"
            ) as dep:
                dep.write("VALUE = 1\n")
            return 0

        added_dirs = []

        def fake_addsitedir(path):
            added_dirs.append(path)
            if path not in sys.path:
                sys.path.insert(0, path)

        with patch(
            "mmrelay.plugin_loader.subprocess.check_call", side_effect=fake_check_call
        ), patch(
            "mmrelay.plugin_loader.site.getusersitepackages", return_value=[user_site]
        ), patch(
            "mmrelay.plugin_loader.site.getsitepackages", return_value=[]
        ), patch(
            "mmrelay.plugin_loader.site.addsitedir", side_effect=fake_addsitedir
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

    @patch("mmrelay.plugins.health_plugin.Plugin")
    def test_load_plugins_start_error(self, mock_health_plugin):
        """
        Test that plugins raising exceptions in their start() method are still loaded.

        Ensures that if a plugin's start() method raises an exception during loading, the error is handled gracefully and the plugin remains in the loaded plugin list.
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

        # Plugin should still be in the list even if start() failed
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].plugin_name, "error_plugin")


class TestGitRepositoryHandling(unittest.TestCase):
    """Test cases for Git repository handling functions."""

    def setUp(self):
        """
        Create a temporary directory and a subdirectory for plugins for use in test setup.
        """
        self.test_dir = tempfile.mkdtemp()
        self.plugins_dir = os.path.join(self.test_dir, "plugins")

    def tearDown(self):
        """
        Remove the temporary directory used for test fixtures after each test.
        """
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("os.makedirs")
    @patch("subprocess.check_call")
    @patch("subprocess.check_output")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_new_repo_tag(
        self, mock_isdir, mock_check_output, mock_check_call, mock_makedirs
    ):
        """
        Verify that cloning a new Git repository with a specific tag invokes the correct git commands and returns success.
        """
        mock_isdir.return_value = False  # Repository doesn't exist

        repo_url = "https://github.com/user/test-repo.git"
        ref = {"type": "tag", "value": "v1.0.0"}

        result = clone_or_update_repo(repo_url, ref, self.plugins_dir)

        self.assertTrue(result)
        # Should call git clone with the tag
        mock_check_call.assert_called()
        clone_call = mock_check_call.call_args_list[0]
        self.assertIn("clone", clone_call[0][0])
        self.assertIn("--branch", clone_call[0][0])
        self.assertIn("v1.0.0", clone_call[0][0])

    @patch("os.makedirs")
    @patch("subprocess.check_call")
    @patch("subprocess.check_output")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_new_repo_branch(
        self, mock_isdir, mock_check_output, mock_check_call, mock_makedirs
    ):
        """
        Test that cloning a new Git repository with a specified branch triggers the correct git commands.

        Verifies that when the repository does not exist locally, `clone_or_update_repo` clones the repository using the provided branch name and returns True.
        """
        mock_isdir.return_value = False  # Repository doesn't exist

        repo_url = "https://github.com/user/test-repo.git"
        ref = {"type": "branch", "value": "develop"}

        result = clone_or_update_repo(repo_url, ref, self.plugins_dir)

        self.assertTrue(result)
        # Should call git clone with the branch
        mock_check_call.assert_called()
        clone_call = mock_check_call.call_args_list[0]
        self.assertIn("clone", clone_call[0][0])
        self.assertIn("--branch", clone_call[0][0])
        self.assertIn("develop", clone_call[0][0])

    @patch("os.makedirs")
    @patch("subprocess.check_call")
    @patch("subprocess.check_output")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_existing_repo_same_branch(
        self, mock_isdir, mock_check_output, mock_check_call, mock_makedirs
    ):
        """
        Test that updating an existing Git repository on the same branch triggers fetch and pull operations.

        Verifies that when the repository directory exists and the current branch matches the requested branch, the `clone_or_update_repo` function performs a fetch and pull, and returns True.
        """
        mock_isdir.return_value = True  # Repository exists
        mock_check_output.return_value = "main\n"  # Current branch is main

        repo_url = "https://github.com/user/test-repo.git"
        ref = {"type": "branch", "value": "main"}

        result = clone_or_update_repo(repo_url, ref, self.plugins_dir)

        self.assertTrue(result)
        # Should fetch and pull
        fetch_called = any(
            "fetch" in str(call) for call in mock_check_call.call_args_list
        )
        pull_called = any(
            "pull" in str(call) for call in mock_check_call.call_args_list
        )
        self.assertTrue(fetch_called)
        self.assertTrue(pull_called)

    @patch("os.makedirs")
    @patch("subprocess.check_call")
    @patch("subprocess.check_output")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_existing_repo_different_branch(
        self, mock_isdir, mock_check_output, mock_check_call, mock_makedirs
    ):
        """
        Test that updating an existing Git repository to a different branch triggers the appropriate Git commands.

        Verifies that when the current branch differs from the target branch, the repository is updated by switching branches and pulling the latest changes.
        """
        mock_isdir.return_value = True  # Repository exists
        mock_check_output.return_value = "main\n"  # Current branch is main

        repo_url = "https://github.com/user/test-repo.git"
        ref = {"type": "branch", "value": "develop"}

        result = clone_or_update_repo(repo_url, ref, self.plugins_dir)

        self.assertTrue(result)
        # Should call git commands (fetch, checkout, etc.)
        self.assertTrue(mock_check_call.called)

    @patch("os.makedirs")
    @patch("subprocess.check_call")
    @patch("subprocess.check_output")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_git_error(
        self, mock_isdir, mock_check_output, mock_check_call, mock_makedirs
    ):
        """Test handling Git command errors."""
        mock_isdir.return_value = False  # Repository doesn't exist
        mock_check_call.side_effect = subprocess.CalledProcessError(1, "git")

        repo_url = "https://github.com/user/test-repo.git"
        ref = {"type": "branch", "value": "main"}

        result = clone_or_update_repo(repo_url, ref, self.plugins_dir)

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
