"""Shared test helpers for plugin loader decomposition."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import ModuleType
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import mmrelay.plugin_loader as pl
from mmrelay.constants.plugins import (
    DEFAULT_ALLOWED_COMMUNITY_HOSTS,
    DEFAULT_BRANCHES,
    GIT_CHECKOUT_CMD,
)
from mmrelay.plugin_loader import (
    _SYS_MODULES_LOCK,
    _clean_python_cache,
    _clone_new_repo_to_branch_or_tag,
    _collect_requirements,
    _exec_plugin_module,
    _filter_risky_requirements,
    _install_requirements_for_repo,
    _is_repo_url_allowed,
    _run,
    _temp_sys_path,
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
from tests.constants import TEST_GIT_TIMEOUT


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
            room (Any): Identifier or object representing the destination room for the message.
            event (Any): Payload object or mapping describing the message event (metadata, sender, etc.).
            full_message (str): The full message text content received.
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
