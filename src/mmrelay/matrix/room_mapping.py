from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Generator,
    Optional,
    Tuple,
)

import mmrelay.matrix_utils as facade

__all__ = [
    "_is_room_alias",
    "_get_valid_device_id",
    "_extract_localpart_from_mxid",
    "_is_room_mapped",
    "_iter_room_alias_entries",
    "_resolve_aliases_in_mapping",
    "_update_room_id_in_mapping",
    "_display_room_channel_mappings",
    "_create_mapping_info",
]


def _is_room_alias(value: Any) -> bool:
    """
    Determine whether a value is a Matrix room alias.

    Returns:
        `True` if `value` is a string that begins with '#', `False` otherwise.
    """

    return isinstance(value, str) and value.startswith("#")


def _get_valid_device_id(device_id_value: Any) -> Optional[str]:
    """
    Normalize and validate a device ID value.

    Parameters:
        device_id_value (Any): Value to validate; expected to be a string or other type.

    Returns:
        Optional[str]: The input string with surrounding whitespace removed if it is non-empty, `None` otherwise.
    """
    if isinstance(device_id_value, str):
        value = device_id_value.strip()
        return value or None
    return None


def _extract_localpart_from_mxid(mxid: str | None) -> str | None:
    """
    Extract the localpart from a Matrix MXID.

    Parameters:
        mxid (str | None): A Matrix user ID (e.g., "@user:server") or localpart.

    Returns:
        str | None: The localpart portion of the MXID (without @ and server),
        or the original value if it's already a localpart, or None if input is None.
    """
    if not mxid:
        return mxid
    if mxid.startswith("@"):
        return mxid[1:].split(":", 1)[0]
    return mxid


def _is_room_mapped(mapping: Any, room_id_or_alias: str) -> bool:
    """
    Determine whether a room ID or alias exists in a matrix_rooms configuration.

    Parameters:
        mapping (list|dict): The matrix_rooms configuration (accepted as a list or dict form).
        room_id_or_alias (str): Room ID (e.g., "!abc:server") or room alias (e.g., "#room:server").

    Returns:
        bool: `True` if the room ID or alias is present in the mapping, `False` otherwise.
    """
    if not isinstance(mapping, (list, dict)):
        return False

    return any(
        alias_or_id == room_id_or_alias
        for alias_or_id, _ in _iter_room_alias_entries(mapping)
    )


def _iter_room_alias_entries(
    mapping: Any,
) -> Generator[Tuple[str, Callable[[str], None]], None, None]:
    """
    Yield (alias_or_id, setter) pairs for entries in a Matrix room mapping.

    Each yielded tuple contains:
    - alias_or_id (str): the room alias or room ID found in the entry (may be an alias starting with '#' or a canonical room ID). If a dict entry has no "id" key, an empty string is yielded.
    - setter (callable): a single-argument function new_id -> None that updates the original mapping in-place to replace the entry with the resolved room ID.

    Parameters:
        mapping (list|dict): A collection of room entries in one of two shapes:
            - list: items may be strings (alias or ID) or dicts with an "id" key.
            - dict: values may be strings (alias or ID) or dicts with an "id" key.

    Yields:
        tuple[str, Callable[[str], None]]: (alias_or_id, setter) for each entry in the mapping.
    """

    def _make_entry_setter(entry: dict[str, Any]) -> Callable[[str], None]:
        # Capture the current entry via default args to avoid loop-variable reuse.
        """
        Create and return a setter function that updates the given entry's "id" field in place.

        Parameters:
            entry (dict[str, Any]): The dictionary whose "id" key will be updated by the returned setter.

        Returns:
            Callable[[str], None]: A function that sets entry["id"] to the provided string.
        """

        def _set_entry_id(new_id: str, target: dict[str, Any] = entry) -> None:
            target["id"] = new_id

        return _set_entry_id

    def _make_list_setter(index: int, collection: list[Any]) -> Callable[[str], None]:
        """
        Create a callable that replaces the element at a fixed index in a given list.

        Parameters:
            index (int): The index in the list whose value the returned callable will replace.
            collection (list[Any]): The list to be modified by the returned callable.

        Returns:
            Callable[[str], None]: A function that accepts a single `new_id` string and sets `collection[index]` to `new_id`.
        """

        def _set_list_entry_value(
            new_id: str, idx: int = index, target: list[Any] = collection
        ) -> None:
            target[idx] = new_id

        return _set_list_entry_value

    def _make_dict_setter(
        key: Any, collection: dict[Any, Any]
    ) -> Callable[[str], None]:
        """
        Create a setter function that assigns a new string ID to a specific key in a dictionary.

        Parameters:
            key (Any): The dictionary key whose value the returned setter will replace.
            collection (dict[Any, Any]): The dictionary to be modified by the returned setter.

        Returns:
            setter (Callable[[str], None]): A function that sets collection[key] to the provided `new_id`.
        """

        def _set_dict_entry_value(
            new_id: str,
            target_key: Any = key,
            target: dict[Any, Any] = collection,
        ) -> None:
            target[target_key] = new_id

        return _set_dict_entry_value

    if isinstance(mapping, list):
        for index, entry in enumerate(mapping):
            if isinstance(entry, dict):
                yield (entry.get("id", ""), _make_entry_setter(entry))
            else:
                yield (entry, _make_list_setter(index, mapping))
    elif isinstance(mapping, dict):
        for key, entry in list(mapping.items()):
            if isinstance(entry, dict):
                yield (entry.get("id", ""), _make_entry_setter(entry))
            else:
                yield (entry, _make_dict_setter(key, mapping))


async def _resolve_aliases_in_mapping(
    mapping: Any,
    resolver: Callable[[str], Awaitable[str | None]],
) -> None:
    """
    Resolve Matrix room aliases found in a list or dict by replacing them in-place with resolved room IDs.

    Parameters:
        mapping (list|dict): A list or dict containing room identifiers or alias entries; entries that look like room aliases (strings starting with '#') will be replaced in-place when resolved.
        resolver (Callable[[str], Awaitable[str | None]]): Async callable that accepts a room alias and returns a resolved room ID (truthy) or None on failure.

    Returns:
        None

    Notes:
        If `mapping` is not a list or dict, the function logs a warning and makes no changes.
    """

    if not isinstance(mapping, (list, dict)):
        facade.logger.warning(
            "matrix_rooms is expected to be a list or dict, got %s",
            type(mapping).__name__,
        )
        return

    for alias, setter in _iter_room_alias_entries(mapping):
        if _is_room_alias(alias):
            resolved_id = await resolver(alias)
            if resolved_id:
                setter(resolved_id)


def _update_room_id_in_mapping(
    mapping: Any,
    alias: str,
    resolved_id: str,
) -> bool:
    """
    Replace a room alias with its resolved room ID in a mapping.

    Parameters:
        mapping (list|dict): A matrix_rooms mapping represented as a list of aliases or a dict of entries; only list and dict types are supported.
        alias (str): The room alias to replace (e.g., "#room:server").
        resolved_id (str): The canonical room ID to substitute for the alias (e.g., "!abcdef:server").

    Returns:
        bool: True if the alias was found and replaced with resolved_id; False if the mapping type is unsupported or the alias was not present.
    """

    if not isinstance(mapping, (list, dict)):
        return False

    for existing_alias, setter in _iter_room_alias_entries(mapping):
        if existing_alias == alias:
            setter(resolved_id)
            return True
    return False


def _display_room_channel_mappings(
    rooms: Dict[str, Any], config: Dict[str, Any], e2ee_status: Dict[str, Any]
) -> None:
    """
    Log Matrix rooms grouped by Meshtastic channel and show encryption/E2EE status indicators.

    Reads the "matrix_rooms" entry from config (accepting dict or list form), builds a mapping from room ID to its configured "meshtastic_channel", groups the provided rooms by channel, and logs each room with an icon indicating whether the room is encrypted and the supplied E2EE overall status.

    Parameters:
        rooms (dict): Mapping of room_id -> room object. Room objects should expose `display_name` and `encrypted` attributes; falls back to the room_id when `display_name` is missing.
        config (dict): Configuration containing a "matrix_rooms" section with entries that include "id" and "meshtastic_channel".
        e2ee_status (dict): E2EE status information; expects an "overall_status" key used to determine status messages (common values: "ready", "unavailable", "disabled").
    """
    if not rooms:
        facade.logger.info("Bot is not in any Matrix rooms")
        return

    # Get matrix_rooms configuration
    matrix_rooms_config = config.get("matrix_rooms", [])
    if not matrix_rooms_config:
        facade.logger.info("No matrix_rooms configuration found")
        return

    # Normalize matrix_rooms configuration to list format
    if isinstance(matrix_rooms_config, dict):
        # Convert dict format to list format
        matrix_rooms_list = list(matrix_rooms_config.values())
    else:
        # Already in list format
        matrix_rooms_list = matrix_rooms_config

    # Create mapping of room_id -> channel number
    room_to_channel = {}
    for room_config in matrix_rooms_list:
        if isinstance(room_config, dict):
            room_id = room_config.get("id")
            channel = room_config.get("meshtastic_channel")
            if room_id and channel is not None:
                room_to_channel[room_id] = channel

    # Group rooms by channel
    channels: dict[int, list[tuple[str, Any]]] = {}

    for room_id, room in rooms.items():
        if room_id in room_to_channel:
            channel = room_to_channel[room_id]
            if channel not in channels:
                channels[channel] = []
            channels[channel].append((room_id, room))

    # Display header
    mapped_rooms = sum(len(room_list) for room_list in channels.values())
    facade.logger.info(
        f"Meshtastic Channels ↔ Matrix Rooms ({mapped_rooms} configured):"
    )

    # Display rooms organized by channel (sorted by channel number)
    for channel in sorted(channels.keys()):
        room_list = channels[channel]
        facade.logger.info(f"  Channel {channel}:")

        for room_id, room in room_list:
            room_name = getattr(room, "display_name", room_id)
            encrypted = getattr(room, "encrypted", False)

            # Format with encryption status
            if e2ee_status["overall_status"] == "ready":
                if encrypted:
                    facade.logger.info(f"    🔒 {room_name}")
                else:
                    facade.logger.info(f"    ✅ {room_name}")
            else:
                if encrypted:
                    if e2ee_status["overall_status"] == "unavailable":
                        facade.logger.info(
                            f"    ⚠️ {room_name} (E2EE not supported - messages blocked)"
                        )
                    elif e2ee_status["overall_status"] == "disabled":
                        facade.logger.info(
                            f"    ⚠️ {room_name} (E2EE disabled - messages blocked)"
                        )
                    else:
                        facade.logger.info(
                            f"    ⚠️ {room_name} (E2EE incomplete - messages may be blocked)"
                        )
                else:
                    facade.logger.info(f"    ✅ {room_name}")


def _create_mapping_info(
    matrix_event_id: str,
    room_id: str,
    text: str,
    meshnet: str | None = None,
    msgs_to_keep: int | None = None,
) -> dict[str, Any] | None:
    """
    Create a mapping dictionary that links a Matrix event to a Meshtastic message.

    If `msgs_to_keep` is None, the value is obtained from _get_msgs_to_keep_config(). The `text` value in the mapping has quoted lines removed. Returns None when `matrix_event_id`, `room_id`, or `text` is missing or empty.

    Parameters:
        matrix_event_id: The Matrix event ID to map from.
        room_id: The Matrix room ID where the event was posted.
        text: The message text to store (quoted lines will be stripped).
        meshnet: Optional meshnet name to record for the mapping.
        msgs_to_keep: Optional override for how many message mappings to retain; if omitted, the configured default is used.

    Returns:
        A dict with keys `matrix_event_id`, `room_id`, `text`, `meshnet`, and `msgs_to_keep`, or `None` if required inputs are missing.
    """
    if not matrix_event_id or not room_id or not text:
        return None

    if msgs_to_keep is None:
        msgs_to_keep = facade._get_msgs_to_keep_config()

    return {
        "matrix_event_id": matrix_event_id,
        "room_id": room_id,
        "text": facade.strip_quoted_lines(text),
        "meshnet": meshnet,
        "msgs_to_keep": msgs_to_keep,
    }
