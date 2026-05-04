"""Shared test helpers for plugin loader decomposition."""

import os
import shutil
import tempfile
import unittest
from typing import Any, Optional

# Re-exported for decomposed test modules (isort would otherwise drop this)
from tests.constants import TEST_GIT_TIMEOUT  # noqa: F401


class MockPlugin:
    """Mock plugin class for testing."""

    plugin_name: str
    priority: int
    started: bool

    def __init__(self, name: str = "test_plugin", priority: int = 10) -> None:
        """
        Initialize a mock plugin with a specified name and priority.

        Parameters:
            name (str): The name of the plugin.
            priority (int): The plugin's priority for loading and activation.
        """
        self.plugin_name = name
        self.priority = priority
        self.started = False

    def start(self) -> None:
        """
        Marks the mock plugin as started by setting the `started` flag to True.
        """
        self.started = True

    def stop(self) -> None:
        """
        Marks the mock plugin as stopped by setting the `started` flag to False.
        """
        self.started = False

    async def handle_meshtastic_message(
        self,
        packet: Any,
        interface: Any,
        longname: str,
        shortname: str,
        meshnet_name: Optional[str],
    ) -> None:
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

    async def handle_room_message(
        self, room: Any, event: Any, full_message: str
    ) -> None:
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

    temp_plugins_dir: str
    temp_repo_path: str

    def setUp(self) -> None:
        """
        Prepare temporary directories for plugin tests.

        Creates a temporary directory and assigns its path to `self.temp_plugins_dir`,
        then sets `self.temp_repo_path` to a `repo` subdirectory path inside it.
        """
        super().setUp()
        self.temp_plugins_dir = tempfile.mkdtemp()
        self.temp_repo_path = os.path.join(self.temp_plugins_dir, "repo")

    def tearDown(self) -> None:
        """
        Cleans up test resources created in setUp.

        Removes the temporary plugins directory used by the test and delegates further teardown to the superclass.
        """
        shutil.rmtree(self.temp_plugins_dir, ignore_errors=True)
        super().tearDown()
