import os
import threading
from abc import ABC, abstractmethod

import markdown

from mmrelay.config import get_plugin_data_dir
from mmrelay.constants.database import (
    DEFAULT_MAX_DATA_ROWS_PER_NODE_BASE,
    DEFAULT_TEXT_TRUNCATION_LENGTH,
)
from mmrelay.constants.queue import DEFAULT_MESSAGE_DELAY, MINIMUM_MESSAGE_DELAY
from mmrelay.db_utils import (
    delete_plugin_data,
    get_plugin_data,
    get_plugin_data_for_node,
    store_plugin_data,
)
from mmrelay.log_utils import get_logger
from mmrelay.message_queue import queue_message
from mmrelay.plugin_loader import (
    clear_plugin_jobs,
)
from mmrelay.plugin_loader import logger as plugins_logger
from mmrelay.plugin_loader import (
    schedule_job,
)

# Global config variable that will be set from main.py
config = None

# Track if we've already shown the deprecated warning
_deprecated_warning_shown = False

# Track delay values we've already warned about to prevent spam
_warned_delay_values = set()
_plugins_low_delay_warned = False


class BasePlugin(ABC):
    """Abstract base class for all mmrelay plugins.

    Provides common functionality for plugin development including:
    - Configuration management and validation
    - Database storage for plugin-specific data
    - Channel and direct message handling
    - Matrix message sending capabilities
    - Scheduling support for background tasks
    - Command matching and routing

    Attributes:
        plugin_name (str): Unique identifier for the plugin
        max_data_rows_per_node (int): Maximum data rows stored per node (default: 100)
        priority (int): Plugin execution priority (lower = higher priority, default: 10)

    Subclasses must:
    - Set plugin_name as a class attribute
    - Implement handle_meshtastic_message() and handle_room_message()
    - Optionally override other methods for custom behavior
    """

    # Class-level default attributes
    plugin_name = None  # Must be overridden in subclasses
    max_data_rows_per_node = DEFAULT_MAX_DATA_ROWS_PER_NODE_BASE
    priority = 10

    @property
    def description(self):
        """Get the plugin description for help text.

        Returns:
            str: Human-readable description of plugin functionality

        Override this property in subclasses to provide meaningful help text
        that will be displayed by the help plugin.
        """
        return ""

    def __init__(self, plugin_name=None) -> None:
        """
        Initialize plugin state: name, logger, configuration, mapped channels, scheduling controls, and response delay.

        Parameters:
            plugin_name (str, optional): Overrides the class-level `plugin_name` when provided.

        Returns:
            None

        Raises:
            ValueError: If no plugin name is available from the parameter, instance, or class attribute.

        Details:
            - Loads per-plugin configuration from the global `config` by checking "plugins", "community-plugins", then "custom-plugins"; defaults to `{"active": False}` if not found.
            - Builds `self.mapped_channels` from `config["matrix_rooms"]` supporting both dict and list formats.
            - Determines `self.channels` from the plugin config (falls back to `self.mapped_channels`) and ensures it is a list; logs a warning for any configured channels not present in `mapped_channels`.
            - Initializes scheduling controls (`self._stop_event`, `self._schedule_thread`) used by the plugin scheduler.
            - Reads Meshtastic delay settings from `config["meshtastic"]`, preferring `message_delay` with fallback to deprecated `plugin_response_delay`. Emits a one-time deprecation warning when `plugin_response_delay` is used.
            - Enforces a minimum delay of `MINIMUM_MESSAGE_DELAY`; values below the minimum are clamped and emit a one-time warning per unique delay value.
        """
        # Allow plugin_name to be passed as a parameter for simpler initialization
        # This maintains backward compatibility while providing a cleaner API
        super().__init__()

        self._stop_event = threading.Event()
        self._schedule_thread = None

        # If plugin_name is provided as a parameter, use it
        if plugin_name is not None:
            self.plugin_name = plugin_name

        # For backward compatibility: if plugin_name is not provided as a parameter,
        # check if it's set as an instance attribute (old way) or use the class attribute
        if not hasattr(self, "plugin_name") or self.plugin_name is None:
            # Try to get the class-level plugin_name
            class_plugin_name = getattr(self.__class__, "plugin_name", None)
            if class_plugin_name is not None:
                self.plugin_name = class_plugin_name
            else:
                raise ValueError(
                    f"{self.__class__.__name__} is missing plugin_name definition. "
                    f"Either set class.plugin_name, pass plugin_name to __init__, "
                    f"or set self.plugin_name before calling super().__init__()"
                )

        self.logger = get_logger(f"Plugin:{self.plugin_name}")
        self.config = {"active": False}
        global config
        plugin_levels = ["plugins", "community-plugins", "custom-plugins"]

        # Check if config is available
        if config is not None:
            for level in plugin_levels:
                if level in config and self.plugin_name in config[level]:
                    self.config = config[level][self.plugin_name]
                    break

            # Get the list of mapped channels
            # Handle both list format and dict format for matrix_rooms
            matrix_rooms = config.get("matrix_rooms", [])
            if isinstance(matrix_rooms, dict):
                # Dict format: {"room_name": {"id": "...", "meshtastic_channel": 0}}
                self.mapped_channels = [
                    room_config.get("meshtastic_channel")
                    for room_config in matrix_rooms.values()
                    if isinstance(room_config, dict)
                ]
            else:
                # List format: [{"id": "...", "meshtastic_channel": 0}]
                self.mapped_channels = [
                    room.get("meshtastic_channel")
                    for room in matrix_rooms
                    if isinstance(room, dict)
                ]
        else:
            self.mapped_channels = []

        # Get the channels specified for this plugin, or default to all mapped channels
        self.channels = self.config.get("channels", self.mapped_channels)

        # Ensure channels is a list
        if not isinstance(self.channels, list):
            self.channels = [self.channels]

        # Validate the channels
        invalid_channels = [
            ch for ch in self.channels if ch not in self.mapped_channels
        ]
        if invalid_channels:
            self.logger.warning(
                f"Plugin '{self.plugin_name}': Channels {invalid_channels} are not mapped in configuration."
            )

        # Get the response delay from the meshtastic config
        self.response_delay = DEFAULT_MESSAGE_DELAY
        if config is not None:
            meshtastic_config = config.get("meshtastic", {})

            # Check for new message_delay option first, with fallback to deprecated option
            delay = None
            delay_key = None
            if "message_delay" in meshtastic_config:
                delay = meshtastic_config["message_delay"]
                delay_key = "message_delay"
            elif "plugin_response_delay" in meshtastic_config:
                delay = meshtastic_config["plugin_response_delay"]
                delay_key = "plugin_response_delay"
                # Show deprecated warning only once globally
                global _deprecated_warning_shown
                if not _deprecated_warning_shown:
                    plugins_logger.warning(
                        "Configuration option 'plugin_response_delay' is deprecated. "
                        "Please use 'message_delay' instead. Support for 'plugin_response_delay' will be removed in a future version."
                    )
                    _deprecated_warning_shown = True

            if delay is not None:
                self.response_delay = delay
                # Enforce minimum delay above firmware limit to prevent message dropping
                if self.response_delay < MINIMUM_MESSAGE_DELAY:
                    # Only warn once per unique delay value to prevent spam
                    global _warned_delay_values, _plugins_low_delay_warned  # Track warning status across plugin instances
                    warning_message = f"{delay_key} of {self.response_delay}s is below minimum of {MINIMUM_MESSAGE_DELAY}s (above firmware limit). Using {MINIMUM_MESSAGE_DELAY}s."

                    if self.response_delay not in _warned_delay_values:
                        # Show generic plugins warning on first occurrence
                        if not _plugins_low_delay_warned:
                            plugins_logger.warning(
                                f"One or more plugins have message_delay below {MINIMUM_MESSAGE_DELAY}s. "
                                f"This may affect multiple plugins. Check individual plugin logs for details."
                            )
                            _plugins_low_delay_warned = True

                        # Show specific delay warning (global configuration issue)
                        plugins_logger.warning(warning_message)
                        _warned_delay_values.add(self.response_delay)
                    else:
                        # Log additional instances at debug level to avoid spam
                        # This ensures we only warn once per plugin while still providing visibility
                        self.logger.debug(warning_message)
                    self.response_delay = MINIMUM_MESSAGE_DELAY

    def start(self) -> None:
        """
        Starts the plugin and configures scheduled background tasks based on plugin settings.

        If scheduling options are present in plugin configuration, sets up periodic execution of `background_job` method using the global scheduler. If no scheduling is configured, the plugin starts without background tasks.
        """
        schedule_config = self.config.get("schedule") or {}
        if not isinstance(schedule_config, dict):
            schedule_config = {}

        if not schedule_config or (
            "at" not in schedule_config
            and "hours" not in schedule_config
            and "minutes" not in schedule_config
            and "seconds" not in schedule_config
        ):
            self.logger.debug(f"Started with priority={self.priority}")
            return

        # Ensure plugin_name is set
        if not self.plugin_name:
            self.logger.error("Plugin name not set, cannot schedule background jobs")
            return

        # Clear stop event to ensure clean start
        if hasattr(self, "_stop_event") and self._stop_event is not None:
            self._stop_event.clear()

        # Clear any existing jobs for this plugin
        clear_plugin_jobs(self.plugin_name)

        job = None
        # Schedule background job based on configuration
        try:
            if "at" in schedule_config and "hours" in schedule_config:
                job = (
                    schedule_job(self.plugin_name, schedule_config["hours"])
                    .hours.at(schedule_config["at"])
                    .do(self.background_job)
                )
            elif "at" in schedule_config and "minutes" in schedule_config:
                job = (
                    schedule_job(self.plugin_name, schedule_config["minutes"])
                    .minutes.at(schedule_config["at"])
                    .do(self.background_job)
                )
            elif "hours" in schedule_config:
                job = schedule_job(self.plugin_name, schedule_config["hours"]).hours.do(
                    self.background_job
                )
            elif "minutes" in schedule_config:
                job = schedule_job(
                    self.plugin_name, schedule_config["minutes"]
                ).minutes.do(self.background_job)
            elif "seconds" in schedule_config:
                job = schedule_job(
                    self.plugin_name, schedule_config["seconds"]
                ).seconds.do(self.background_job)
        except (ValueError, TypeError) as e:
            self.logger.warning(
                "Invalid schedule configuration for plugin '%s': %s. Starting without background job.",
                self.plugin_name,
                e,
            )
            job = None

        if job is None:
            self.logger.warning(
                "Schedule config present but invalid for plugin '%s'. Starting without background job.",
                self.plugin_name,
            )
            self.logger.debug(f"Started with priority={self.priority}")
            return

        self.logger.debug(f"Scheduled with priority={self.priority}")

    def stop(self) -> None:
        """
        Stop scheduled background work and run the plugin's cleanup hook.

        Clears any scheduled jobs tagged with the plugin name and then invokes on_stop() for plugin-specific cleanup. Exceptions raised by on_stop() are caught and logged.
        """
        # Signal stop event for any threads waiting on it
        if hasattr(self, "_stop_event") and self._stop_event is not None:
            self._stop_event.set()

        if self.plugin_name:
            clear_plugin_jobs(self.plugin_name)
        try:
            self.on_stop()
        except Exception:
            self.logger.exception(
                "Error running on_stop for plugin %s", self.plugin_name or "unknown"
            )
        self.logger.debug(f"Stopped plugin '{self.plugin_name or 'unknown'}'")

    def on_stop(self) -> None:
        """
        Hook for subclasses to clean up resources during shutdown.

        Default implementation does nothing.
        """
        return None

    # trunk-ignore(ruff/B027)
    def background_job(self) -> None:
        """Background task executed on schedule.

        Override this method in subclasses to implement scheduled functionality.
        Called automatically based on schedule configuration in start().

        Default implementation does nothing.
        """
        pass  # Implement in subclass if needed

    def strip_raw(self, data):
        """Recursively remove 'raw' keys from data structures.

        Args:
            data: Data structure (dict, list, or other) to clean

        Returns:
            Cleaned data structure with 'raw' keys removed

        Useful for cleaning packet data before logging or storage to remove
        binary protobuf data that's not human-readable.
        """
        if isinstance(data, dict):
            data.pop("raw", None)
            for k, v in data.items():
                data[k] = self.strip_raw(v)
        elif isinstance(data, list):
            data = [self.strip_raw(item) for item in data]
        return data

    def get_response_delay(self):
        """
        Return the configured delay in seconds before sending a Meshtastic response.

        The delay is determined by the `meshtastic.message_delay` configuration option, defaulting to 2.5 seconds with a minimum of 2.1 seconds. The deprecated `plugin_response_delay` option is also supported for backward compatibility.

        Returns:
            float: The response delay in seconds.
        """
        return self.response_delay

    def get_my_node_id(self):
        """Get the relay's Meshtastic node ID.

        Returns:
            int: The relay's node ID, or None if unavailable

        This method provides access to the relay's own node ID without requiring
        plugins to call connect_meshtastic() directly. Useful for determining
        if messages are direct messages or for other node identification needs.

        The node ID is cached after first successful retrieval to avoid repeated
        connection calls, as the relay's node ID is static during runtime.
        """
        if hasattr(self, "_my_node_id"):
            return self._my_node_id

        from mmrelay.meshtastic_utils import connect_meshtastic

        meshtastic_client = connect_meshtastic()
        if meshtastic_client and meshtastic_client.myInfo:
            self._my_node_id = meshtastic_client.myInfo.my_node_num
            return self._my_node_id
        return None

    def is_direct_message(self, packet):
        """Check if a Meshtastic packet is a direct message to this relay.

        Args:
            packet (dict): Meshtastic packet data

        Returns:
            bool: True if the packet is a direct message to this relay, False otherwise

        This method encapsulates the common pattern of checking if a message
        is addressed directly to the relay node, eliminating the need for plugins
        to call connect_meshtastic() directly for DM detection.
        """
        toId = packet.get("to")
        if toId is None:
            return False

        myId = self.get_my_node_id()
        return toId == myId

    def send_message(self, text: str, channel: int = 0, destination_id=None) -> bool:
        """
        Send a message to the Meshtastic network using the message queue.

        Queues the specified text for broadcast or direct delivery on the given channel. Returns True if the message was successfully queued, or False if the Meshtastic client is unavailable.

        Parameters:
            text (str): The message content to send.
            channel (int, optional): The channel index for sending the message. Defaults to 0.
            destination_id (optional): The destination node ID for direct messages. If None, the message is broadcast.

        Returns:
            bool: True if the message was queued successfully; False otherwise.
        """
        from mmrelay.meshtastic_utils import connect_meshtastic

        meshtastic_client = connect_meshtastic()
        if not meshtastic_client:
            self.logger.error("No Meshtastic client available")
            return False

        description = f"Plugin {self.plugin_name}: {text[:DEFAULT_TEXT_TRUNCATION_LENGTH]}{'...' if len(text) > DEFAULT_TEXT_TRUNCATION_LENGTH else ''}"

        send_kwargs = {
            "text": text,
            "channelIndex": channel,
        }
        if destination_id:
            send_kwargs["destinationId"] = destination_id

        return queue_message(
            meshtastic_client.sendText,
            description=description,
            **send_kwargs,
        )

    def is_channel_enabled(self, channel, is_direct_message=False):
        """
        Determine whether the plugin should respond to a message on the specified channel or direct message.

        Parameters:
            channel: The channel identifier to check.
            is_direct_message (bool): Set to True if the message is a direct message.

        Returns:
            bool: True if the plugin should respond on the given channel or to a direct message; False otherwise.
        """
        if is_direct_message:
            return True  # Always respond to DMs if the plugin is active
        else:
            return channel in self.channels

    def get_matrix_commands(self):
        """Get list of Matrix commands this plugin responds to.

        Returns:
            list: List of command strings (without ! prefix)

        Default implementation returns [plugin_name]. Override to provide
        custom commands or multiple command aliases.
        """
        return [self.plugin_name]

    async def send_matrix_message(self, room_id, message, formatted=True):
        """Send a message to a Matrix room.

        Args:
            room_id (str): Matrix room identifier
            message (str): Message content to send
            formatted (bool): Whether to send as formatted HTML (default: True)

        Returns:
            dict: Response from Matrix API room_send

        Connects to Matrix using matrix_utils and sends a room message
        with optional HTML formatting via markdown.
        """
        from mmrelay.matrix_utils import connect_matrix

        matrix_client = await connect_matrix()

        return await matrix_client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "format": "org.matrix.custom.html" if formatted else None,
                "body": message,
                "formatted_body": markdown.markdown(message) if formatted else None,
            },
        )

    def get_mesh_commands(self):
        """Get list of mesh/radio commands this plugin responds to.

        Returns:
            list: List of command strings (without ! prefix)

        Default implementation returns empty list. Override to handle
        commands sent over the mesh radio network.
        """
        return []

    def store_node_data(self, meshtastic_id, node_data):
        """Store data for a specific node, appending to existing data.

        Args:
            meshtastic_id (str): Node identifier
            node_data: Data to store (single item or list)

        Retrieves existing data, appends new data, trims to max_data_rows_per_node,
        and stores back to database. Use for accumulating time-series data.
        """
        data = self.get_node_data(meshtastic_id=meshtastic_id)
        data = data[-self.max_data_rows_per_node :]
        if isinstance(node_data, list):
            data.extend(node_data)
        else:
            data.append(node_data)
        store_plugin_data(self.plugin_name, meshtastic_id, data)

    def set_node_data(self, meshtastic_id, node_data):
        """Replace all data for a specific node.

        Args:
            meshtastic_id (str): Node identifier
            node_data: Data to store (replaces existing data)

        Completely replaces existing data for the node, trimming to
        max_data_rows_per_node if needed. Use when you want to reset
        or completely replace a node's data.
        """
        node_data = node_data[-self.max_data_rows_per_node :]
        store_plugin_data(self.plugin_name, meshtastic_id, node_data)

    def delete_node_data(self, meshtastic_id):
        """Delete all stored data for a specific node.

        Args:
            meshtastic_id (str): Node identifier

        Returns:
            bool: True if deletion succeeded, False otherwise
        """
        return delete_plugin_data(self.plugin_name, meshtastic_id)

    def get_node_data(self, meshtastic_id):
        """Retrieve stored data for a specific node.

        Args:
            meshtastic_id (str): Node identifier

        Returns:
            list: Stored data for the node (JSON deserialized)
        """
        return get_plugin_data_for_node(self.plugin_name, meshtastic_id)

    def get_data(self):
        """Retrieve all stored data for this plugin across all nodes.

        Returns:
            list: List of tuples containing raw data entries

        Returns raw data without JSON deserialization. Use get_node_data()
        for individual node data that's automatically deserialized.
        """
        return get_plugin_data(self.plugin_name)

    def get_plugin_data_dir(self, subdir=None):
        """
        Returns the directory for storing plugin-specific data files.

        Creates the directory if it doesn't exist.

        Args:
            subdir (str, optional): Optional subdirectory within the plugin's data directory.
                                   If provided, this subdirectory will be created.

        Returns:
            str: Path to the plugin's data directory or subdirectory

        Example:
            self.get_plugin_data_dir() returns ~/.mmrelay/data/plugins/your_plugin_name/
            self.get_plugin_data_dir("data_files") returns ~/.mmrelay/data/plugins/your_plugin_name/data_files/
        """
        # Get the plugin-specific data directory
        plugin_dir = get_plugin_data_dir(self.plugin_name)

        # If a subdirectory is specified, create and return it
        if subdir:
            subdir_path = os.path.join(plugin_dir, subdir)
            os.makedirs(subdir_path, exist_ok=True)
            return subdir_path

        return plugin_dir

    def matches(self, event):
        """Check if a Matrix event matches this plugin's commands.

        Args:
            event: Matrix room event to check

        Returns:
            bool: True if event matches plugin commands, False otherwise

        Uses bot_command() utility to check if the event contains any of
        the plugin's matrix commands with proper bot command syntax.
        """
        from mmrelay.matrix_utils import bot_command

        # Pass the entire event to bot_command
        return bot_command(self.plugin_name, event)

    @abstractmethod
    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
    ):
        pass  # Implement in subclass

    @abstractmethod
    async def handle_room_message(self, room, event, full_message):
        pass  # Implement in subclass
