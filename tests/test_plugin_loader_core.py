"""Tests for plugin loader: Core plugin loading, directory discovery, scheduling."""

# Decomposed from test_plugin_loader.py

import hashlib
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
from unittest.mock import MagicMock, patch

import mmrelay.plugin_loader as pl
from mmrelay.plugin_loader import (
    _temp_sys_path,
    get_community_plugin_dirs,
    get_custom_plugin_dirs,
    load_plugins,
    load_plugins_from_directory,
    shutdown_plugins,
)
from tests._plugin_loader_helpers import BaseGitTest, MockPlugin


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

    def _community_repo_path(self) -> str:
        repo_path = os.path.join(self.community_dir, "repo")
        os.makedirs(repo_path, exist_ok=True)
        return repo_path

    def _write_community_requirements(self, content: str = "requests==2.28.0\n") -> str:
        repo_path = self._community_repo_path()
        with open(
            os.path.join(repo_path, pl.PLUGIN_REQUIREMENTS_FILENAME), "w"
        ) as req_file:
            req_file.write(content)
        return repo_path

    def tearDown(self):
        """
        Remove temporary directories and clean up resources after each test.
        """
        super().tearDown()
        # Clean up temporary directories
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("mmrelay.paths.get_home_dir")
    @patch("mmrelay.paths.get_legacy_dirs")
    @patch("mmrelay.plugin_loader.get_app_path")
    def test_get_custom_plugin_dirs(
        self, mock_get_app_path, mock_get_legacy_dirs, mock_get_home_dir
    ):
        """
        Test that custom plugin directories are discovered and created as expected.

        Verifies that `get_custom_plugin_dirs()` returns correct list of custom plugin directories and that directory creation function is called for each directory.
        """
        import tempfile

        mock_get_home_dir.return_value = self.test_dir
        mock_get_legacy_dirs.return_value = []

        # Use a temporary directory instead of hardcoded path
        with tempfile.TemporaryDirectory() as temp_app_dir:
            mock_get_app_path.return_value = temp_app_dir
            os.makedirs(os.path.join(temp_app_dir, "plugins", "custom"))

            dirs = get_custom_plugin_dirs()

            expected_dirs = [
                os.path.join(self.test_dir, "plugins", "custom"),
                os.path.join(temp_app_dir, "plugins", "custom"),
            ]
            self.assertEqual(dirs, expected_dirs)

    @patch("mmrelay.paths.get_home_dir")
    @patch("mmrelay.paths.get_legacy_dirs")
    @patch("mmrelay.plugin_loader.get_app_path")
    def test_get_community_plugin_dirs(
        self, mock_get_app_path, mock_get_legacy_dirs, mock_get_home_dir
    ):
        """
        Test that community plugin directory discovery returns correct directories and creates them if they do not exist.
        """
        import tempfile

        mock_get_home_dir.return_value = self.test_dir
        mock_get_legacy_dirs.return_value = []

        # Use a temporary directory instead of hardcoded path
        with tempfile.TemporaryDirectory() as temp_app_dir:
            mock_get_app_path.return_value = temp_app_dir
            os.makedirs(os.path.join(temp_app_dir, "plugins", "community"))

            dirs = get_community_plugin_dirs()

            expected_dirs = [
                os.path.join(self.test_dir, "plugins", "community"),
                os.path.join(temp_app_dir, "plugins", "community"),
            ]
            self.assertEqual(dirs, expected_dirs)

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
        self.plugin_name = "sample_plugin"
        self.priority = 10

    def start(self):
        pass
"""
        plugin_file = os.path.join(self.custom_dir, "sample_plugin.py")
        with open(plugin_file, "w") as f:
            f.write(plugin_content)

        plugins = load_plugins_from_directory(self.custom_dir)

        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].plugin_name, "sample_plugin")
        self.assertEqual(plugins[0].priority, 10)

        module_name = (
            "plugin_"
            + hashlib.sha256(plugin_file.encode(pl.DEFAULT_TEXT_ENCODING)).hexdigest()
        )
        self.assertIn(module_name, sys.modules)
        sys.modules.pop(module_name, None)

    def test_load_plugins_from_directory_base_plugin_infers_custom_tier(self):
        """Dynamically loaded BasePlugin should infer custom tier from filesystem."""
        plugin_content = """
from mmrelay.plugins.base_plugin import BasePlugin

class Plugin(BasePlugin):
    plugin_name = "test_plugin"

    async def handle_meshtastic_message(self, packet, formatted_message, longname, meshnet_name):
        return False

    async def handle_room_message(self, room, event, full_message):
        return False
"""
        plugin_file = os.path.join(self.custom_dir, "tier_plugin.py")
        with open(plugin_file, "w", encoding="utf-8") as handle:
            handle.write(plugin_content)

        module_name = (
            "plugin_"
            + hashlib.sha256(plugin_file.encode(pl.DEFAULT_TEXT_ENCODING)).hexdigest()
        )
        sys.modules.pop(module_name, None)

        legacy_config = {"plugins": {"test_plugin": {"active": True}}}
        with (
            patch("mmrelay.plugins.base_plugin.config", legacy_config),
            patch(
                "mmrelay.plugin_loader.get_custom_plugin_dirs",
                return_value=[self.custom_dir],
            ),
            patch("mmrelay.plugin_loader.get_community_plugin_dirs", return_value=[]),
        ):
            plugins = load_plugins_from_directory(self.custom_dir)

        self.assertEqual(len(plugins), 1)
        self.assertEqual(getattr(plugins[0], "plugin_type", None), "custom")
        sys.modules.pop(module_name, None)

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

    def test_load_plugins_from_directory_auto_installs_missing_dependency(self):
        """Auto-install missing dependencies and retry plugin load."""
        for var in ("PIPX_HOME", "PIPX_LOCAL_VENVS"):
            os.environ.pop(var, None)

        plugin_content = """
import missingdep


class Plugin:
    def __init__(self):
        self.plugin_name = "auto_plugin"
        self.priority = 1

    def start(self):
        pass
"""
        plugin_file = os.path.join(self.custom_dir, "auto_plugin.py")
        with open(plugin_file, "w", encoding="utf-8") as handle:
            handle.write(plugin_content)

        def fake_run(_cmd, *_args, **_kwargs):  # nosec B603
            """
            Simulate a successful subprocess call and inject a dummy module named "missingdep" into sys.modules.

            This test helper inserts a ModuleType("missingdep") into sys.modules as a side effect and returns a subprocess.CompletedProcess indicating success.

            Returns:
                subprocess.CompletedProcess: CompletedProcess with `args` set to the provided command and `returncode` 0.
            """
            sys.modules["missingdep"] = ModuleType("missingdep")
            return subprocess.CompletedProcess(args=_cmd, returncode=0)

        try:
            with patch("mmrelay.plugin_loader._run", side_effect=fake_run):
                plugins = load_plugins_from_directory(self.custom_dir)
        finally:
            sys.modules.pop("missingdep", None)

        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].plugin_name, "auto_plugin")

    def test_load_plugins_from_directory_community_missing_dependency_blocked(self):
        """Community plugin dependency retry should be blocked by policy."""
        plugin_content = """
import missingdep_for_community

class Plugin:
    pass
"""
        plugin_file = os.path.join(self.custom_dir, "community_missing_dep.py")
        with open(plugin_file, "w", encoding="utf-8") as handle:
            handle.write(plugin_content)

        with (
            patch(
                "mmrelay.plugin_loader._raise_install_error",
                side_effect=subprocess.CalledProcessError(1, "pip/pipx"),
            ) as mock_raise_install_error,
            patch("mmrelay.plugin_loader._run") as mock_run,
        ):
            plugins = load_plugins_from_directory(
                self.custom_dir,
                plugin_type=pl.PLUGIN_TYPE_COMMUNITY,
            )

        self.assertEqual(plugins, [])
        mock_raise_install_error.assert_called_once_with("missingdep_for_community")
        mock_run.assert_not_called()

    def test_load_plugins_from_directory_auto_install_retry_no_plugin_class(self):
        orig_pipx_home = os.environ.get("PIPX_HOME")
        orig_pipx_local_venvs = os.environ.get("PIPX_LOCAL_VENVS")
        for var in ("PIPX_HOME", "PIPX_LOCAL_VENVS"):
            os.environ.pop(var, None)
        plugin_content = "import missingdep_noclass\n"
        plugin_file = os.path.join(self.custom_dir, "auto_noclass.py")
        with open(plugin_file, "w", encoding="utf-8") as handle:
            handle.write(plugin_content)

        def fake_run(_cmd, *_args, **_kwargs):
            sys.modules["missingdep_noclass"] = ModuleType("missingdep_noclass")
            return subprocess.CompletedProcess(args=_cmd, returncode=0)

        try:
            with (
                patch("mmrelay.plugin_loader._run", side_effect=fake_run),
                patch("mmrelay.plugin_loader.logger") as mock_logger,
            ):
                plugins = load_plugins_from_directory(self.custom_dir)
            self.assertEqual(len(plugins), 0)
            mock_logger.warning.assert_any_call(
                f"{plugin_file} does not define a Plugin class."
            )
        finally:
            sys.modules.pop("missingdep_noclass", None)
            if orig_pipx_home is not None:
                os.environ["PIPX_HOME"] = orig_pipx_home
            else:
                os.environ.pop("PIPX_HOME", None)
            if orig_pipx_local_venvs is not None:
                os.environ["PIPX_LOCAL_VENVS"] = orig_pipx_local_venvs
            else:
                os.environ.pop("PIPX_LOCAL_VENVS", None)

    def test_load_plugins_from_directory_auto_install_retry_module_not_found(self):
        orig_pipx_home = os.environ.get("PIPX_HOME")
        orig_pipx_local_venvs = os.environ.get("PIPX_LOCAL_VENVS")
        try:
            for var in ("PIPX_HOME", "PIPX_LOCAL_VENVS"):
                os.environ.pop(var, None)
            plugin_content = "import missingdep_stillgone\n"
            plugin_file = os.path.join(self.custom_dir, "auto_stillgone.py")
            with open(plugin_file, "w", encoding="utf-8") as handle:
                handle.write(plugin_content)

            def fake_run(_cmd, *_args, **_kwargs):
                return subprocess.CompletedProcess(args=_cmd, returncode=0)

            with (
                patch("mmrelay.plugin_loader._run", side_effect=fake_run),
                patch("mmrelay.plugin_loader.logger") as mock_logger,
            ):
                plugins = load_plugins_from_directory(self.custom_dir)
            self.assertEqual(len(plugins), 0)
            self.assertTrue(
                any(
                    "still not available after installation" in str(c)
                    for c in mock_logger.exception.call_args_list
                )
            )
        finally:
            if orig_pipx_home is not None:
                os.environ["PIPX_HOME"] = orig_pipx_home
            else:
                os.environ.pop("PIPX_HOME", None)
            if orig_pipx_local_venvs is not None:
                os.environ["PIPX_LOCAL_VENVS"] = orig_pipx_local_venvs
            else:
                os.environ.pop("PIPX_LOCAL_VENVS", None)

    def test_load_plugins_from_directory_auto_install_retry_generic_exception(self):
        orig_pipx_home = os.environ.get("PIPX_HOME")
        orig_pipx_local_venvs = os.environ.get("PIPX_LOCAL_VENVS")
        for var in ("PIPX_HOME", "PIPX_LOCAL_VENVS"):
            os.environ.pop(var, None)
        plugin_content = "import missingdep_generic\nraise ValueError('test error')\n"
        plugin_file = os.path.join(self.custom_dir, "auto_generic.py")
        with open(plugin_file, "w", encoding="utf-8") as handle:
            handle.write(plugin_content)

        def fake_run(_cmd, *_args, **_kwargs):
            sys.modules["missingdep_generic"] = ModuleType("missingdep_generic")
            return subprocess.CompletedProcess(args=_cmd, returncode=0)

        try:
            with (
                patch("mmrelay.plugin_loader._run", side_effect=fake_run),
                patch("mmrelay.plugin_loader.logger") as mock_logger,
            ):
                plugins = load_plugins_from_directory(self.custom_dir)
            self.assertEqual(len(plugins), 0)
            self.assertTrue(
                any(
                    "Error loading plugin" in str(c)
                    for c in mock_logger.exception.call_args_list
                )
            )
        finally:
            sys.modules.pop("missingdep_generic", None)
            if orig_pipx_home is not None:
                os.environ["PIPX_HOME"] = orig_pipx_home
            else:
                os.environ.pop("PIPX_HOME", None)
            if orig_pipx_local_venvs is not None:
                os.environ["PIPX_LOCAL_VENVS"] = orig_pipx_local_venvs
            else:
                os.environ.pop("PIPX_LOCAL_VENVS", None)

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

        module_name = (
            "plugin_"
            + hashlib.sha256(plugin_file.encode(pl.DEFAULT_TEXT_ENCODING)).hexdigest()
        )
        sys.modules.pop(module_name, None)
        plugins = load_plugins_from_directory(self.custom_dir)
        self.assertEqual(plugins, [])
        self.assertNotIn(module_name, sys.modules)

    def test_load_plugins_community_missing_repository_logs_errors(self):
        """Missing repository URL should log errors in community plugin processing."""
        config = {
            "plugins": {},
            "community-plugins": {"no_repo": {"active": True}},
        }

        with (
            patch("mmrelay.plugin_loader.get_custom_plugin_dirs", return_value=[]),
            patch("mmrelay.plugin_loader.get_community_plugin_dirs", return_value=[]),
            patch("mmrelay.plugin_loader.start_global_scheduler"),
            patch("mmrelay.plugin_loader.logger") as mock_logger,
        ):
            pl.plugins_loaded = False
            pl.sorted_active_plugins = []
            load_plugins(config)

        mock_logger.error.assert_any_call(
            "Repository URL not specified for a community plugin"
        )
        mock_logger.error.assert_any_call(
            "Please specify the repository URL in config.yaml"
        )

    def test_load_plugins_community_invalid_repo_url(self):
        """Invalid repository URLs should be rejected in community loading."""
        config = {
            "plugins": {},
            "community-plugins": {
                "bad_repo": {"active": True, "repository": "bad-url"}
            },
        }

        with (
            patch("mmrelay.plugin_loader.get_custom_plugin_dirs", return_value=[]),
            patch(
                "mmrelay.plugin_loader.get_community_plugin_dirs",
                return_value=[self.community_dir],
            ),
            patch("mmrelay.plugin_loader._get_repo_name_from_url", return_value=None),
            patch("mmrelay.plugin_loader.clone_or_update_repo", return_value=True),
            patch("mmrelay.plugin_loader.start_global_scheduler"),
            patch("mmrelay.plugin_loader.logger") as mock_logger,
        ):
            pl.plugins_loaded = False
            pl.sorted_active_plugins = []
            load_plugins(config)

        mock_logger.error.assert_any_call(
            "Invalid repository URL for community plugin %s: %s",
            "bad_repo",
            pl._redact_url("bad-url"),
        )

    def test_load_plugins_community_found_and_missing(self):
        """Load community plugins when present and warn when missing."""
        config = {
            "plugins": {},
            "community-plugins": {
                "found_plugin": {
                    "active": True,
                    "repository": "https://example.com/found_repo.git",
                },
                "missing_plugin": {
                    "active": True,
                    "repository": "https://example.com/missing_repo.git",
                },
            },
        }
        found_path = os.path.join(self.community_dir, "found_repo")

        def fake_validate(repo_url, ref):
            """
            Create a ValidationResult for the given repository URL and ref, marking it as found and attaching an inferred repository name.

            Parameters:
                repo_url (str): The repository URL being validated.
                ref (dict): A mapping containing ref information; expected keys are `"type"` and `"value"`.

            Returns:
                pl.ValidationResult: A ValidationResult with `success=True`, the original `repo_url`, the ref `type` and `value` extracted from `ref`, and a `repo_name` set to `"found_repo"` if `"found_repo"` is a substring of `repo_url`, otherwise `"missing_repo"`.
            """
            repo_name = "found_repo" if "found_repo" in repo_url else "missing_repo"
            return pl.ValidationResult(
                True,
                repo_url,
                ref.get("type"),
                ref.get("value"),
                repo_name,
            )

        def fake_repo_name(repo_url):
            """
            Derives a simplified repository identifier from a repository URL.

            Parameters:
                repo_url (str): The repository URL or path to evaluate.

            Returns:
                repo_name (str): `"found_repo"` if the substring `"found_repo"` appears in `repo_url`, otherwise `"missing_repo"`.
            """
            return "found_repo" if "found_repo" in repo_url else "missing_repo"

        def fake_exists(path):
            """
            Determine whether the provided path equals the preconfigured `found_path` value.

            Parameters:
                path (str): Filesystem path to check.

            Returns:
                bool: `True` if `path` is equal to the outer-scope `found_path`, `False` otherwise.
            """
            return path == found_path

        with (
            patch("mmrelay.plugin_loader.get_custom_plugin_dirs", return_value=[]),
            patch(
                "mmrelay.plugin_loader.get_community_plugin_dirs",
                return_value=[self.community_dir],
            ),
            patch(
                "mmrelay.plugin_loader._validate_clone_inputs",
                side_effect=fake_validate,
            ),
            patch("mmrelay.plugin_loader.clone_or_update_repo", return_value=True),
            patch("mmrelay.plugin_loader._install_requirements_for_repo"),
            patch(
                "mmrelay.plugin_loader._get_repo_name_from_url",
                side_effect=fake_repo_name,
            ),
            patch("mmrelay.plugin_loader.os.path.exists", side_effect=fake_exists),
            patch(
                "mmrelay.plugin_loader.load_plugins_from_directory",
                return_value=[MockPlugin("community_plugin", priority=1)],
            ) as mock_load,
            patch("mmrelay.plugin_loader.start_global_scheduler"),
            patch("mmrelay.plugin_loader.logger") as mock_logger,
        ):
            pl.plugins_loaded = False
            pl.sorted_active_plugins = []
            load_plugins(config)

        mock_load.assert_called_once_with(
            found_path,
            recursive=True,
            plugin_type=pl.PLUGIN_TYPE_COMMUNITY,
        )
        mock_logger.warning.assert_any_call(
            "Community plugin 'missing_plugin' not found in any of the plugin directories"
        )

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
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    @patch("mmrelay.plugin_loader._check_commit_pin_for_upstream_updates")
    def test_load_plugins_community_loader_passes_plugin_type(
        self,
        mock_update_check,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_clone_repo,
    ):
        """Community plugin load should pass plugin_type to directory loader."""
        pl.plugins_loaded = False
        pl.sorted_active_plugins = []

        config = {
            "community-plugins": {
                "commit-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                }
            },
            "plugins": {},
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []
        os.makedirs(os.path.join(self.community_dir, "repo"), exist_ok=True)

        load_plugins(config)

        mock_load_from_dir.assert_any_call(
            os.path.join(self.community_dir, "repo"),
            recursive=True,
            plugin_type=pl.PLUGIN_TYPE_COMMUNITY,
        )
        mock_update_check.assert_called_once()
        mock_start_scheduler.assert_called_once()


def test_plugin_loader_schedule_import_error():
    """Reload plugin_loader with schedule unavailable to exercise import fallback."""
    import mmrelay.plugin_loader as pl_module

    original_schedule = sys.modules.get("schedule")
    original_import = __import__

    def raising_import(name, globals=None, locals=None, fromlist=(), level=0):
        """
        Simulate a missing 'schedule' module by raising ImportError for that name, otherwise delegate to the original import.

        Returns:
                The result of importing `name` using the original import function.

        Raises:
                ImportError: If `name` is "schedule".
        """
        if name == "schedule":
            raise ImportError("missing")
        return original_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=raising_import):
        sys.modules.pop("schedule", None)
        importlib.reload(pl_module)
        assert pl_module.schedule is None

    if original_schedule is not None:
        sys.modules["schedule"] = original_schedule
    else:
        sys.modules.pop("schedule", None)
    importlib.reload(pl_module)


def test_temp_sys_path_handles_missing_remove():
    """_temp_sys_path should swallow ValueError when path removal fails."""
    original_path = sys.path

    class PathList(list):
        def remove(self, _value):
            """
            Always raises a ValueError indicating the requested item is missing.

            Parameters:
                _value: The item attempted to be removed; this value is ignored.

            Raises:
                ValueError: Always raised with the message "missing".
            """
            raise ValueError("missing")

    sys.path = PathList(original_path)
    try:
        with _temp_sys_path("fake-path"):
            pass
    finally:
        sys.path = original_path
