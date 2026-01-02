import inspect
import os
import re
import threading
from abc import ABC, abstractmethod
from typing import Any, Dict, Union

import markdown
from nio import (  # type: ignore[import-untyped]
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
    RoomSendError,
    RoomSendResponse,
)

from mmrelay.config import get_plugin_data_dir
from mmrelay.constants.config import (
    CONFIG_KEY_REQUIRE_BOT_MENTION,
    DEFAULT_REQUIRE_BOT_MENTION,
)
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
_warned_delay_values: set[float] = set()
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
    plugin_name: str | None = None  # Must be overridden in subclasses
    is_core_plugin: bool | None = None
    max_data_rows_per_node = DEFAULT_MAX_DATA_ROWS_PER_NODE_BASE
    priority = 10

    @property
    def description(self) -> str:
        """Get the plugin description for help text.

        Returns:
            str: Human-readable description of plugin functionality

        Override this property in subclasses to provide meaningful help text
        that will be displayed by help plugin.
        """
        return ""

    def __init__(self, plugin_name: str | None = None) -> None:
        """
        Initialize plugin state and load per-plugin configuration and runtime defaults.

        Loads this plugin's configuration (searching "plugins", "community-plugins", then "custom-plugins"), builds mapped Matrix-to-meshtastic channels from the global `matrix_rooms` config (supporting dict or list formats), and establishes runtime attributes used by the plugin scheduler and messaging code (including `mapped_channels`, `channels`, and `response_delay`).

        Parameters:
            plugin_name (str, optional): Override the class-level `plugin_name` for this instance.

        Raises:
            ValueError: If no plugin name is available from the parameter, the instance, or the class attribute.
        """
        # Allow plugin_name to be passed as a parameter for simpler initialization
        # This maintains backward compatibility while providing a cleaner API
        super().__init__()

        self._stop_event = threading.Event()
        self._my_node_id: int | None = None

        # If plugin_name is provided as a parameter, use it
        if plugin_name is not None:
            self.plugin_name = plugin_name

        # Allow plugin to declare core status; fall back to module location
        self.is_core_plugin = getattr(self, "is_core_plugin", None)
        if self.is_core_plugin is None:
            try:
                class_file = inspect.getfile(self.__class__)
            except TypeError:
                class_file = ""
            core_plugins_dir = os.path.dirname(__file__)
            self.is_core_plugin = class_file.startswith(core_plugins_dir)

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
        self.config: Dict[str, Any] = {"active": False}
        self.mapped_channels: list[int | None] = []
        self._global_require_bot_mention: bool | None = None
        global config
        plugin_levels = ["plugins", "community-plugins", "custom-plugins"]

        # Check if config is available
        if config is not None:
            for level in plugin_levels:
                if level in config and self.plugin_name in config[level]:
                    self.config = config[level][self.plugin_name]
                    break

            # Cache global plugin-level settings (for options like require_bot_mention)
            for section_name in ("plugins", "community-plugins", "custom-plugins"):
                section_config = config.get(section_name, {})
                if (
                    isinstance(section_config, dict)
                    and CONFIG_KEY_REQUIRE_BOT_MENTION in section_config
                ):
                    self._global_require_bot_mention = bool(
                        section_config[CONFIG_KEY_REQUIRE_BOT_MENTION]
                    )
                    break

            # Get the list of mapped channels
            # Handle both list format and dict format for matrix_rooms
            matrix_rooms: Union[Dict[str, Any], list[Any]] = config.get(
                "matrix_rooms", []
            )
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
        schedule_config: Dict[str, Any] = self.config.get("schedule") or {}
        if not isinstance(schedule_config, dict):
            schedule_config = {}

        # Always reset stop state on startup to ensure clean restart
        if hasattr(self, "_stop_event") and self._stop_event is not None:
            self._stop_event.clear()

        # Clear any existing jobs for this plugin if we have a name
        if self.plugin_name:
            clear_plugin_jobs(self.plugin_name)

        # Check if scheduling is configured
        has_schedule = any(
            key in schedule_config for key in ("at", "hours", "minutes", "seconds")
        )

        if not has_schedule:
            self.logger.debug(f"Started with priority={self.priority}")
            return

        # Ensure plugin_name is set for scheduling operations
        if not self.plugin_name:
            self.logger.error("Plugin name not set, cannot schedule background jobs")
            return

        # Schedule background job based on configuration
        job = None
        try:
            if "at" in schedule_config and "hours" in schedule_config:
                job_obj = schedule_job(self.plugin_name, schedule_config["hours"])
                if job_obj is not None:
                    job = job_obj.hours.at(schedule_config["at"]).do(
                        self.background_job
                    )
            elif "at" in schedule_config and "minutes" in schedule_config:
                job_obj = schedule_job(self.plugin_name, schedule_config["minutes"])
                if job_obj is not None:
                    job = job_obj.minutes.at(schedule_config["at"]).do(
                        self.background_job
                    )
            elif "hours" in schedule_config:
                job_obj = schedule_job(self.plugin_name, schedule_config["hours"])
                if job_obj is not None:
                    job = job_obj.hours.do(self.background_job)
            elif "minutes" in schedule_config:
                job_obj = schedule_job(self.plugin_name, schedule_config["minutes"])
                if job_obj is not None:
                    job = job_obj.minutes.do(self.background_job)
            elif "seconds" in schedule_config:
                job_obj = schedule_job(self.plugin_name, schedule_config["seconds"])
                if job_obj is not None:
                    job = job_obj.seconds.do(self.background_job)
        except (ValueError, TypeError) as e:
            self.logger.warning(
                "Invalid schedule configuration for plugin '%s': %s. Starting without background job.",
                self.plugin_name,
                e,
            )
            job = None

        if job is None:
            self.logger.warning(
                "Could not set up scheduled job for plugin '%s'. This may be due to an invalid configuration or a missing 'schedule' library. Starting without background job.",
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

    def strip_raw(self, data: Any) -> Any:
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

    def get_response_delay(self) -> float:
        """
        Return the configured delay in seconds before sending a Meshtastic response.

        The delay is determined by the `meshtastic.message_delay` configuration option, defaulting to 2.5 seconds with a minimum of 2.1 seconds. The deprecated `plugin_response_delay` option is also supported for backward compatibility.

        Returns:
            float: The response delay in seconds.
        """
        return self.response_delay

    def get_my_node_id(self) -> int | None:
        """Get the relay's Meshtastic node ID.

        Returns:
            int: The relay's node ID, or None if unavailable

        This method provides access to the relay's own node ID without requiring
        plugins to call connect_meshtastic() directly. Useful for determining
        if messages are direct messages or for other node identification needs.

        The node ID is cached after first successful retrieval to avoid repeated
        connection calls, as the relay's node ID is static during runtime.
        """
        if hasattr(self, "_my_node_id") and self._my_node_id is not None:
            return self._my_node_id

        from mmrelay.meshtastic_utils import connect_meshtastic

        meshtastic_client = connect_meshtastic()
        if meshtastic_client and meshtastic_client.myInfo:
            self._my_node_id = meshtastic_client.myInfo.my_node_num
            return self._my_node_id
        return None

    def is_direct_message(self, packet: dict[str, Any]) -> bool:
        """Check if a Meshtastic packet is a direct message to this relay.

        Args:
            packet (dict): Meshtastic packet data

        Returns:
            bool: True if the packet is a direct message to this relay, False otherwise

        This method encapsulates the common pattern of checking if a message
        is addressed directly to the relay node, eliminating the need for plugins
        to call connect_meshtastic() directly for DM detection.
        """
        toId: int | None = packet.get("to")
        if toId is None:
            return False

        myId = self.get_my_node_id()
        if myId is None:
            return False
        return toId == myId

    def send_message(
        self, text: str, channel: int = 0, destination_id: int | None = None
    ) -> bool:
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

        send_kwargs: Dict[str, Any] = {
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

    def is_channel_enabled(
        self, channel: int | None, is_direct_message: bool = False
    ) -> bool:
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

    def get_matrix_commands(self) -> list[str]:
        """
        Return the Matrix command names this plugin responds to.

        By default returns a single-item list containing the plugin's name; override to provide custom commands or aliases.

        Returns:
            list[str]: Command names (without a leading '!' prefix)
        """
        if self.plugin_name is None:
            return []
        return [self.plugin_name]

    def get_matching_matrix_command(
        self,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
    ) -> str | None:
        """
        Identify the first Matrix command that matches the given event.

        Checks each command returned by get_matrix_commands() against the event using this plugin's bot-mention requirement.

        Returns:
                The matching command string, or None if no command matches.
        """
        from mmrelay.matrix_utils import bot_command

        require_mention = self.get_require_bot_mention()
        for command in self.get_matrix_commands():
            if bot_command(command, event, require_mention=require_mention):
                return command
        return None

    async def send_matrix_message(
        self, room_id: str, message: str, formatted: bool = True
    ) -> RoomSendResponse | RoomSendError | None:
        """
        Send a message to a Matrix room, optionally formatted as HTML.

        Parameters:
            room_id (str): Matrix room identifier.
            message (str): Message content to send.
            formatted (bool): If True, send an HTML-formatted message (message is converted from Markdown); otherwise send plain text.

        Returns:
            dict | None: The Matrix API response from the room send call, or `None` if the Matrix client could not be obtained.
        """
        from mmrelay.matrix_utils import connect_matrix

        matrix_client = await connect_matrix()  # type: ignore[no-untyped-call]

        if matrix_client is None:
            self.logger.error("Failed to connect to Matrix client")
            return None

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

    def get_mesh_commands(self) -> list[str]:
        """Get list of mesh/radio commands this plugin responds to.

        Returns:
            list: List of command strings (without ! prefix)

        Default implementation returns empty list. Override to handle
        commands sent over the mesh radio network.
        """
        return []

    def store_node_data(self, meshtastic_id: str, node_data: Any) -> None:
        """Store data for a specific node, appending to existing data.

        Args:
            meshtastic_id (str): Node identifier
            node_data: Data to store (single item or list)

        Retrieves existing data, appends new data, trims to max_data_rows_per_node,
        and stores back to database. Use for accumulating time-series data.
        """
        assert self.plugin_name is not None
        data = self.get_node_data(meshtastic_id=meshtastic_id)
        if isinstance(node_data, list):
            data.extend(node_data)
        else:
            data.append(node_data)
        data = data[-self.max_data_rows_per_node :]
        store_plugin_data(self.plugin_name, meshtastic_id, data)

    def set_node_data(self, meshtastic_id: str, node_data: Any) -> None:
        """Replace all data for a specific node.

        Args:
            meshtastic_id (str): Node identifier
            node_data: Data to store (replaces existing data)

        Completely replaces existing data for the node, trimming to
        max_data_rows_per_node if needed. Use when you want to reset
        or completely replace a node's data.
        """
        assert self.plugin_name is not None
        node_data = node_data[-self.max_data_rows_per_node :]
        store_plugin_data(self.plugin_name, meshtastic_id, node_data)

    def delete_node_data(self, meshtastic_id: str) -> None:
        """Delete all stored data for a specific node.

        Args:
            meshtastic_id (str): Node identifier

        Returns:
            None
        """
        assert self.plugin_name is not None
        delete_plugin_data(self.plugin_name, meshtastic_id)

    def get_node_data(self, meshtastic_id: str) -> list[Any]:
        """Retrieve stored data for a specific node.

        Args:
            meshtastic_id (str): Node identifier

        Returns:
            list: Stored data for the node (JSON deserialized)
        """
        assert self.plugin_name is not None
        return get_plugin_data_for_node(self.plugin_name, meshtastic_id)

    def get_data(self) -> list[Any]:
        """Retrieve all stored data for this plugin across all nodes.

        Returns:
            list: List of tuples containing raw data entries

        Returns raw data without JSON deserialization. Use get_node_data()
        for individual node data that's automatically deserialized.
        """
        assert self.plugin_name is not None
        return get_plugin_data(self.plugin_name)

    def get_plugin_data_dir(self, subdir: str | None = None) -> str:
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
        assert self.plugin_name is not None
        plugin_dir: str = get_plugin_data_dir(self.plugin_name)  # type: ignore[no-untyped-call]

        # If a subdirectory is specified, create and return it
        if subdir:
            subdir_path = os.path.join(plugin_dir, subdir)
            os.makedirs(subdir_path, exist_ok=True)
            return subdir_path

        return plugin_dir

    def matches(
        self,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
    ) -> bool:
        """
        Determine whether a Matrix event invokes this plugin's Matrix commands.

        Checks whether the event contains any of the plugin's configured Matrix commands, taking into account the plugin's configured bot-mention requirement.

        Parameters:
            event: The Matrix room event to evaluate.

        Returns:
            True if the event matches one of the plugin's Matrix commands, False otherwise.
        """
        from mmrelay.matrix_utils import bot_command

        # Determine if bot mentions are required
        require_mention = self.get_require_bot_mention()

        return any(
            bot_command(command, event, require_mention=require_mention)
            for command in self.get_matrix_commands()
        )

    def extract_command_args(self, command: str, text: str) -> str | None:
        """
        Extract arguments that follow a bot command in a message, tolerating an optional leading mention prefix and matching the command case-insensitively.

        If the message contains the command (e.g. "!cmd arg1 arg2" or "@bot: !cmd arg1"), returns the trailing argument string stripped of surrounding whitespace; if the command is present with no arguments returns an empty string; if the input does not match the command pattern or is not a string returns None.

        Returns:
            str: Arguments after the command, stripped of surrounding whitespace, or an empty string if no arguments are present; `None` if the command pattern does not match or input is not a string.
        """
        if not isinstance(text, str):
            return None
        pattern = rf"^(?:.+?:\s*)?!{re.escape(command)}(?:\s+(.*))?$"
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if not match:
            return None
        args = match.group(1)
        if args is None:
            return ""
        return args.strip()

    def get_require_bot_mention(self) -> bool:
        """
        Determine whether this plugin requires the bot to be mentioned.

        Checks plugin-specific configuration first, then a cached global setting, and falls back
        to core/non-core defaults.

        Returns:
            `true` if bot mentions are required for this plugin, `false` otherwise.
        """
        # Check plugin-specific configuration first
        if CONFIG_KEY_REQUIRE_BOT_MENTION in self.config:
            return bool(self.config[CONFIG_KEY_REQUIRE_BOT_MENTION])

        if getattr(self, "_global_require_bot_mention", None) is not None:
            return bool(self._global_require_bot_mention)

        # Default behavior: core plugins require mentions by default
        if self.is_core_plugin:
            return DEFAULT_REQUIRE_BOT_MENTION

        # Non-core plugins default to False (backward compatibility)
        return False

    @abstractmethod
    async def handle_meshtastic_message(
        self,
        packet: dict[str, Any],
        formatted_message: str,
        longname: str,
        meshnet_name: str,
    ) -> bool:
        """
        Handle an incoming Meshtastic packet and perform plugin-specific processing.

        Parameters:
            packet: Original Meshtastic packet (protobuf-derived dict or message).
            formatted_message (str): Clean, human-readable text payload.
            longname (str): Sender display name or node label.
            meshnet_name (str): Identifier of the originating mesh network.

        Returns:
            `true` if the packet was handled, `false` otherwise.
        """
        pass  # Implement in subclass

    @abstractmethod
    async def handle_room_message(
        self, room: Any, event: dict[str, Any], full_message: str
    ) -> bool:
        """
        Handle an incoming Matrix room message and perform plugin-specific processing.

        Parameters:
            room: Matrix room object where the message was received.
            event: Matrix event object containing message metadata and sender.
            full_message: The full text content of the received message.

        Returns:
            True if the message was handled by the plugin, False otherwise.
        """
        pass  # Implement in subclass
