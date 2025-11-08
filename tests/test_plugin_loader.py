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
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.plugin_loader import (
    _clean_python_cache,
    _collect_requirements,
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
            Record a directory and ensure it's on the Python import path.

            Adds `path` to the external `added_dirs` list and, if not already present, inserts it at the front of `sys.path` so it takes precedence during imports.

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


def test_clone_or_update_repo_new_repo_tag(tmp_path):
    """Test cloning a new repository with a specific tag."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    repo_url = "https://github.com/user/test-repo.git"
    ref = {"type": "tag", "value": "v1.0.0"}

    with patch("mmrelay.plugin_loader._run") as mock_run:
        result = clone_or_update_repo(repo_url, ref, str(plugins_dir))

        assert result is True
        mock_run.assert_called_with(
            ["git", "clone", "--branch", "v1.0.0", repo_url],
            cwd=str(plugins_dir),
            timeout=120,
        )


def test_clone_or_update_repo_new_repo_branch(tmp_path):
    """Test cloning a new repository with a specified branch."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    repo_url = "https://github.com/user/test-repo.git"
    ref = {"type": "branch", "value": "develop"}

    with patch("mmrelay.plugin_loader._run") as mock_run:
        result = clone_or_update_repo(repo_url, ref, str(plugins_dir))

        assert result is True
        mock_run.assert_called_with(
            ["git", "clone", "--branch", "develop", repo_url],
            cwd=str(plugins_dir),
            timeout=120,
        )


@patch("mmrelay.plugin_loader._run")
def test_clone_or_update_repo_existing_repo_same_branch(mock_run, tmp_path):
    """Test updating an existing repository on the same branch."""
    # Mock the return values for the _run calls
    mock_run.side_effect = [
        MagicMock(),  # for git fetch
        MagicMock(stdout="main\n"),  # for git rev-parse
        MagicMock(),  # for git pull
    ]

    plugins_dir = tmp_path / "plugins"
    repo_dir = plugins_dir / "test-repo"
    repo_dir.mkdir(parents=True)
    repo_url = "https://github.com/user/test-repo.git"
    ref = {"type": "branch", "value": "main"}

    result = clone_or_update_repo(repo_url, ref, str(plugins_dir))

    assert result is True
    # Should fetch, rev-parse and pull
    assert mock_run.call_count == 3
    mock_run.assert_any_call(
        ["git", "-C", str(repo_dir), "fetch", "origin"], timeout=120
    )
    mock_run.assert_any_call(
        ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
    )
    mock_run.assert_any_call(
        ["git", "-C", str(repo_dir), "pull", "origin", "main"], timeout=120
    )


@patch("mmrelay.plugin_loader._run")
def test_clone_or_update_repo_existing_repo_different_branch(mock_run, tmp_path):
    """Test updating an existing repository to a different branch."""
    mock_run.side_effect = [
        MagicMock(),  # git fetch
        MagicMock(),  # git checkout develop
        MagicMock(),  # git pull
    ]
    plugins_dir = tmp_path / "plugins"
    repo_dir = plugins_dir / "test-repo"
    repo_dir.mkdir(parents=True)
    repo_url = "https://github.com/user/test-repo.git"
    ref = {"type": "branch", "value": "develop"}

    result = clone_or_update_repo(repo_url, ref, str(plugins_dir))

    assert result is True
    # Should fetch, checkout and pull
    assert mock_run.call_count == 3
    mock_run.assert_any_call(
        ["git", "-C", str(repo_dir), "fetch", "origin"], timeout=120
    )
    mock_run.assert_any_call(
        ["git", "-C", str(repo_dir), "checkout", "develop"], timeout=120
    )
    mock_run.assert_any_call(
        ["git", "-C", str(repo_dir), "pull", "origin", "develop"], timeout=120
    )


def test_clone_or_update_repo_git_error(tmp_path):
    """Test handling Git command errors."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    repo_url = "https://github.com/user/test-repo.git"
    ref = {"type": "branch", "value": "main"}

    with patch(
        "mmrelay.plugin_loader._run",
        side_effect=subprocess.CalledProcessError(1, "git"),
    ):
        result = clone_or_update_repo(repo_url, ref, str(plugins_dir))

        assert result is False


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
        # The function appears to include both requirements and constraints
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
        """Test that debug messages are logged."""
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
    def test_clean_python_cache_logs_info_message(self, mock_logger):
        """Test that info message is logged when cache directories are removed."""
        # Create multiple __pycache__ directories
        for i in range(3):
            pycache = os.path.join(self.temp_dir, f"subdir{i}", "__pycache__")
            os.makedirs(pycache, exist_ok=True)

        # Clean cache
        _clean_python_cache(self.temp_dir)

        # Verify info message was logged
        mock_logger.info.assert_called_once()
        self.assertIn(
            "Cleaned 3 Python cache directories", mock_logger.info.call_args[0][0]
        )


class TestCacheCleaningIntegration(unittest.TestCase):
    """Test cases for cache cleaning integration in plugin loading workflow."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

        # Reset plugin loader state
        import mmrelay.plugin_loader as pl

        pl.sorted_active_plugins = []
        pl.plugins_loaded = False

    @patch("mmrelay.plugin_loader._clean_python_cache")
    def test_load_plugins_calls_cache_cleaning(self, mock_clean_cache):
        """Test that load_plugins_from_directory calls cache cleaning."""
        # Create a plugin directory
        plugin_dir = os.path.join(self.temp_dir, "plugins")
        os.makedirs(plugin_dir, exist_ok=True)

        # Call load_plugins_from_directory
        load_plugins_from_directory(plugin_dir)

        # Verify cache cleaning was called
        mock_clean_cache.assert_called_once_with(plugin_dir)

    @patch("mmrelay.plugin_loader._run")
    def test_clone_or_update_repo_success(self, mock_run):
        """Test that clone_or_update_repo succeeds with valid git operations."""
        # Set up mock for successful git pull
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "pull"], returncode=0, stdout="", stderr=""
        )

        repo_url = "https://github.com/test/plugin.git"
        ref = {"type": "branch", "value": "main"}
        plugins_dir = self.temp_dir

        # Create a repo directory
        repo_name = "plugin"
        repo_path = os.path.join(plugins_dir, repo_name)
        os.makedirs(repo_path, exist_ok=True)

        # Mock current branch check to return the same branch
        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = [
                # Current branch check
                subprocess.CompletedProcess(
                    args=["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    returncode=0,
                    stdout="main\n",
                    stderr="",
                ),
                # Git pull
                mock_run.return_value,
            ]

            result = clone_or_update_repo(repo_url, ref, plugins_dir)

        # Verify success (cache cleaning now happens in load_plugins_from_directory)
        self.assertTrue(result)

    @patch("mmrelay.plugin_loader._run")
    def test_clone_or_update_repo_checkout_success(self, mock_run):
        """Test that clone_or_update_repo succeeds when checking out different branch."""
        # Set up mock for successful git checkout and pull
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "pull"], returncode=0, stdout="", stderr=""
        )

        repo_url = "https://github.com/test/plugin.git"
        ref = {"type": "branch", "value": "develop"}
        plugins_dir = self.temp_dir

        # Create a repo directory
        repo_name = "plugin"
        repo_path = os.path.join(plugins_dir, repo_name)
        os.makedirs(repo_path, exist_ok=True)

        # Mock current branch check to return different branch
        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = [
                # Current branch check (different branch)
                subprocess.CompletedProcess(
                    args=["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    returncode=0,
                    stdout="main\n",
                    stderr="",
                ),
                # Git checkout
                subprocess.CompletedProcess(
                    args=["git", "checkout", "develop"],
                    returncode=0,
                    stdout="",
                    stderr="",
                ),
                # Git pull
                mock_run.return_value,
            ]

            result = clone_or_update_repo(repo_url, ref, plugins_dir)

        # Verify success (cache cleaning now happens in load_plugins_from_directory)
        self.assertTrue(result)

    @patch("mmrelay.plugin_loader._clean_python_cache")
    @patch("mmrelay.plugin_loader._run")
    def test_clone_or_update_repo_no_cache_cleaning_on_failure(
        self, mock_run, mock_clean_cache
    ):
        """Test that cache cleaning is not called when git operations fail."""
        # Set up mock for failed git operation
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        repo_url = "https://github.com/test/plugin.git"
        ref = {"type": "branch", "value": "main"}
        plugins_dir = self.temp_dir

        # Create a repo directory
        repo_name = "plugin"
        repo_path = os.path.join(plugins_dir, repo_name)
        os.makedirs(repo_path, exist_ok=True)

        result = clone_or_update_repo(repo_url, ref, plugins_dir)

        # Verify cache cleaning was not called
        mock_clean_cache.assert_not_called()
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
