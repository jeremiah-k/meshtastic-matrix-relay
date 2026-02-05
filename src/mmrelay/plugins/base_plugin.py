import inspect
import os
import re
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any

# markdown has stubs in our env; avoid import-untyped so mypy --strict stays clean.
import markdown  # mypy: ignore[import-untyped]
from nio import (
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
    RoomSendError,
    RoomSendResponse,
)

from mmrelay.config import get_plugin_data_dir as resolve_plugin_data_dir
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
config: dict[str, Any] | None = None

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
        """
        Human-readable description of the plugin for help text.

        Override in subclasses to provide plugin-specific help text displayed by the help system.

        Returns:
            str: Description text for the plugin.
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
        self.config: dict[str, Any] = {"active": False}
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
            matrix_rooms: dict[str, Any] | list[Any] = config.get("matrix_rooms", [])
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

        self.plugin_type: str = "core"
        if config is not None:
            if self.plugin_name in config.get("community-plugins", {}):
                self.plugin_type = "community"
            elif self.plugin_name in config.get("custom-plugins", {}):
                self.plugin_type = "custom"

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
        schedule_config: dict[str, Any] = self.config.get("schedule") or {}
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
        """
        Run periodic work for the plugin when scheduled.

        Subclasses should implement this to perform the plugin's scheduled task; the default implementation does nothing.
        """
        pass  # Implement in subclass if needed

    def strip_raw(self, data: Any) -> Any:
        """
        Recursively remove any "raw" keys from dictionaries within a nested data structure.

        This function walks dictionaries and lists and removes entries with the key `"raw"`. Both dictionaries and lists are mutated in place.

        Parameters:
            data (Any): The nested data structure (e.g., dicts and lists) to clean.

        Returns:
            Any: The cleaned data structure with all `"raw"` keys removed.
        """
        if isinstance(data, dict):
            data.pop("raw", None)
            for k, v in data.items():
                data[k] = self.strip_raw(v)
        elif isinstance(data, list):
            for idx, item in enumerate(data):
                data[idx] = self.strip_raw(item)
        return data

    def get_response_delay(self) -> float:
        """
        Get the configured Meshtastic response delay in seconds.

        The value reflects plugin configuration and is clamped to the minimum allowed delay.

        Returns:
            float: The response delay in seconds.
        """
        return self.response_delay

    def get_my_node_id(self) -> int | None:
        """
        Return the relay's Meshtastic node ID.

        Caches the ID after the first successful retrieval to avoid repeated connections.

        Returns:
            int: The relay's node ID if available, `None` otherwise.
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
        """
        Determine whether a Meshtastic packet is addressed to this relay.

        Parameters:
            packet (dict): Meshtastic packet data; may include a "to" field with the destination node ID.

        Returns:
            True if the packet's "to" field equals this relay's node ID, False otherwise.
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
        Queue a text message for broadcast or direct delivery on the Meshtastic network.

        Parameters:
            text: Message content to send.
            channel: Channel index to send the message on (defaults to 0).
            destination_id: Destination node ID for a direct message; if omitted the message is broadcast.

        Returns:
            `true` if the message was queued successfully, `false` otherwise.
        """
        from mmrelay.meshtastic_utils import connect_meshtastic

        meshtastic_client = connect_meshtastic()
        if not meshtastic_client:
            self.logger.error("No Meshtastic client available")
            return False

        description = f"Plugin {self.plugin_name}: {text[:DEFAULT_TEXT_TRUNCATION_LENGTH]}{'...' if len(text) > DEFAULT_TEXT_TRUNCATION_LENGTH else ''}"

        send_kwargs: dict[str, Any] = {
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
        Return the first Matrix command name that matches the given event.

        Uses the plugin's require-mention setting when testing commands.

        Returns:
            The matching command string if a command matches the event, `None` otherwise.
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
        Send a message to a Matrix room, optionally converting Markdown to HTML.

        Parameters:
            room_id: Matrix room identifier.
            message: Message content to send.
            formatted: If True, convert `message` from Markdown to HTML and include it as formatted content; otherwise send plain text only.

        Returns:
            The Matrix client's `room_send` response (`RoomSendResponse` or `RoomSendError`), or `None` if the Matrix client could not be obtained.
        """
        from mmrelay.matrix_utils import connect_matrix

        matrix_client = await connect_matrix()

        if matrix_client is None:
            self.logger.error("Failed to connect to Matrix client")
            return None

        content = {
            "msgtype": "m.text",
            "body": message,
        }
        if formatted:
            content["format"] = "org.matrix.custom.html"
            content["formatted_body"] = markdown.markdown(message)
        return await matrix_client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content=content,
        )

    def get_mesh_commands(self) -> list[str]:
        """
        List mesh/radio command names this plugin handles.

        Subclasses should override to expose commands. Command names must not include a leading '!'.

        Returns:
            list[str]: Command names without a leading '!' (empty list by default).
        """
        return []

    def _require_plugin_name(self) -> str:
        """
        Return the initialized plugin name.

        Returns:
            plugin_name (str): The plugin's name.

        Raises:
            ValueError: If the plugin name has not been initialized.
        """
        if self.plugin_name is None:
            raise ValueError("Plugin name not initialized")
        return self.plugin_name

    def store_node_data(self, meshtastic_id: str, node_data: Any) -> None:
        """
        Append data for a Meshtastic node to this plugin's persistent per-node store.

        The existing stored value (if any) is normalized to a list, the provided item or items are appended, the list is trimmed to the plugin's max_data_rows_per_node, and the updated list is persisted.

        Parameters:
            meshtastic_id (str): Identifier of the Meshtastic node.
            node_data (Any): A single data item or a list/iterable of items to append; the stored value will be a list after this call.
        """
        plugin_name = self._require_plugin_name()
        data = get_plugin_data_for_node(plugin_name, meshtastic_id)
        if not isinstance(data, list):
            data = [data]
        if isinstance(node_data, list):
            data.extend(node_data)
        else:
            data.append(node_data)
        data = data[-self.max_data_rows_per_node :]
        store_plugin_data(plugin_name, meshtastic_id, data)

    def set_node_data(self, meshtastic_id: str, node_data: Any) -> None:
        """
        Replace all stored data for a Meshtastic node with the provided data.

        Parameters:
            meshtastic_id (str): Node identifier for which data will be replaced.
            node_data (Any): New data to store; if a sequence, only the most recent
                entries up to `self.max_data_rows_per_node` are kept. Scalars and
                non-sequence iterables are normalized to a list.
        """
        plugin_name = self._require_plugin_name()
        # Normalize to a list so scalars, dicts, and generators are stored safely.
        if isinstance(node_data, list):
            normalized = node_data
        elif isinstance(node_data, dict):
            normalized = [node_data]
        elif isinstance(node_data, Iterable) and not isinstance(
            node_data, (str, bytes, bytearray)
        ):
            normalized = list(node_data)
        else:
            normalized = [node_data]

        trimmed = normalized[-self.max_data_rows_per_node :]
        store_plugin_data(plugin_name, meshtastic_id, trimmed)

    def delete_node_data(self, meshtastic_id: str) -> None:
        """
        Remove all persisted data associated with the given Meshtastic node for this plugin.

        Parameters:
            meshtastic_id (str): Identifier of the Meshtastic node whose stored data will be deleted.
        """
        plugin_name = self._require_plugin_name()
        delete_plugin_data(plugin_name, meshtastic_id)

    def get_node_data(self, meshtastic_id: str) -> Any:
        """
        Retrieve the plugin-specific data stored for a Meshtastic node.

        Parameters:
            meshtastic_id (str): Identifier of the Meshtastic node.

        Returns:
            Any: The stored data value for the given node (may be any JSON-serializable value), or an empty list [] if no data exists or on error.
        """
        plugin_name = self._require_plugin_name()
        return get_plugin_data_for_node(plugin_name, meshtastic_id)

    def get_data(self) -> list[Any]:
        """
        Retrieve all stored plugin data across all Meshtastic nodes.

        Returns:
            list[Any]: A list of raw stored entries for this plugin across all nodes. Data is returned without JSON deserialization.
        """
        plugin_name = self._require_plugin_name()
        return get_plugin_data(plugin_name)

    def get_plugin_data_dir(self, subdir: str | None = None) -> str:
        """
        Get the filesystem path for this plugin's data directory, creating it if missing.

        Parameters:
                subdir (str | None): Optional subdirectory name inside the plugin data directory to create and return. If None, the top-level plugin data directory path is returned.

        Returns:
                plugin_path (str): Absolute path to the plugin's data directory or the requested subdirectory.
        """
        # Get the plugin-specific data directory
        plugin_name = self._require_plugin_name()
        if subdir:
            return resolve_plugin_data_dir(
                plugin_name, subdir=subdir, plugin_type=self.plugin_type
            )

        return resolve_plugin_data_dir(plugin_name, plugin_type=self.plugin_type)

    def matches(
        self,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
    ) -> bool:
        """
        Check whether a Matrix event invokes this plugin's Matrix commands.

        Parameters:
            event (RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote): The Matrix room event to evaluate.

        Returns:
            `True` if the event invokes one of the plugin's Matrix commands, `False` otherwise.
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
            packet (dict[str, Any]): Original Meshtastic packet (protobuf-derived dict or message).
            formatted_message (str): Clean, human-readable text payload extracted from the packet.
            longname (str): Sender display name or node label.
            meshnet_name (str): Identifier of the originating mesh network.

        Returns:
            bool: True if the packet was handled, False otherwise.
        """
        pass  # Implement in subclass

    @abstractmethod
    async def handle_room_message(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        full_message: str,
    ) -> bool:
        """
        Process an incoming Matrix room message and perform plugin-specific handling.

        Parameters:
            room (MatrixRoom): Matrix room object where the message was received.
            event (RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote):
                Matrix event payload containing metadata and sender information.
            full_message (str): The full text content of the received message.

        Returns:
            bool: `True` if the plugin handled the message, `False` otherwise.
        """
        pass  # Implement in subclass
