import asyncio
import base64
import dataclasses
import math
import re
from datetime import datetime
from typing import Any

# matrix-nio is not marked py.typed; keep import-untyped for strict mypy.
from nio import (
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)

from mmrelay.constants.domain import (
    RELATIVE_TIME_DAYS_THRESHOLD,
    SECONDS_PER_DAY,
    SECONDS_PER_HOUR,
    SECONDS_PER_MINUTE,
    UNKNOWN_NODE_VALUE,
)
from mmrelay.constants.formats import DATE_FORMAT_LONG, SNR_UNIT_SUFFIX
from mmrelay.log_utils import get_logger
from mmrelay.plugins.base_plugin import BasePlugin

logger = get_logger(__name__)

DEFAULT_FIELDS = ["name", "hardware", "power", "snr", "hops", "last_seen", "status"]
FIELD_PATHS = {
    "short_name": "user.shortName",
    "long_name": "user.longName",
    "node_id": "user.id",
    "node_num": "num",
    "hardware": "user.hwModel",
    "role": "user.role",
    "public_key": "user.publicKey",
    "status": "status",
    "battery": "deviceMetrics.batteryLevel",
    "voltage": "deviceMetrics.voltage",
    "channel_utilization": "deviceMetrics.channelUtilization",
    "air_util_tx": "deviceMetrics.airUtilTx",
    "uptime": "deviceMetrics.uptimeSeconds",
    "snr": "snr",
    "hops": "hopsAway",
    "last_seen": "lastHeard",
    "channel": "channel",
    "favorite": "isFavorite",
    "latitude": "position.latitude",
    "longitude": "position.longitude",
    "altitude": "position.altitude",
}
FIELD_LABELS = {
    "short_name": "short",
    "long_name": "long",
    "node_id": "id",
    "node_num": "num",
    "role": "role",
    "public_key": "key",
    "status": "status",
    "battery": "battery",
    "voltage": "voltage",
    "channel_utilization": "channel util",
    "air_util_tx": "air util tx",
    "uptime": "uptime",
    "channel": "channel",
    "favorite": "favorite",
    "latitude": "lat",
    "longitude": "lon",
    "altitude": "alt",
}
AVAILABLE_FIELDS = tuple(["name", "power", *FIELD_PATHS.keys(), "<dotted node path>"])
DEFAULT_MAX_RESULTS = 20
SORT_DIRECTIONS = ("asc", "desc")
# Paths whose values compare numerically even when stored as strings.
# Other fields infer numeric ordering from observed scalar values.
NUMERIC_FIELD_PATHS = frozenset(
    {
        "num",
        "snr",
        "hopsAway",
        "lastHeard",
        "channel",
        "deviceMetrics.batteryLevel",
        "deviceMetrics.voltage",
        "deviceMetrics.channelUtilization",
        "deviceMetrics.airUtilTx",
        "deviceMetrics.uptimeSeconds",
        "position.latitude",
        "position.longitude",
        "position.altitude",
    }
)
USAGE_TEXT = (
    "Usage: !nodes [limit N|all] [sort <field> [asc|desc]] "
    "[<field> <value>]... [fields <field,...>]\n"
    "\n"
    "Examples:\n"
    "  !nodes                        list up to max_results nodes (default 20), newest first\n"
    "  !nodes limit 50               show more; 'limit all' or a bare number (!nodes 50) also work\n"
    "  !nodes role client_mute       filter by field, case-insensitive substring; commas mean any-of\n"
    "  !nodes hardware rak4631       multiple filters combine with AND\n"
    "  !nodes sort snr desc          sort by a field; numeric fields default high-to-low, text A-to-Z\n"
    "  !nodes fields battery,uptime  choose output fields for this listing only\n"
    "\n"
    f"Fields: {', '.join(['name', 'power', *FIELD_PATHS.keys()])}, or any dotted node "
    "path (for example environmentMetrics.temperature). Potentially secret-bearing "
    "paths are always rejected."
)
# Secret-bearing path segments (compared against lowercased alphanumeric
# forms) that are never rendered, even when explicitly configured, so future
# mtjk schema additions cannot leak credentials through raw node paths.
SENSITIVE_FIELD_TOKENS = frozenset(
    {
        "adminkey",
        "fixedpin",
        "passwd",
        "passkey",
        "password",
        "privatekey",
        "psk",
        "secret",
        "sessionkey",
        "wifi",
    }
)


def _is_sensitive_field_path(field_path: str) -> bool:
    """
    Check whether a dotted node-data path may carry secret material.

    Parameters:
        field_path (str): Dotted field path (e.g. "user.publicKey" or a raw
            configured path such as "config.network.wifiPsk").

    Returns:
        bool: True when any dot-separated segment, normalized to lowercase
        alphanumerics, contains a known secret-bearing token. Public keys and
        other intentional aliases remain allowed.
    """
    for segment in field_path.split("."):
        normalized = re.sub(r"[^a-z0-9]", "", segment.lower())
        if any(token in normalized for token in SENSITIVE_FIELD_TOKENS):
            return True
    return False


class NodesUsageError(ValueError):
    """Raised when !nodes arguments cannot be parsed."""


@dataclasses.dataclass(frozen=True)
class NodesQuery:
    """Parsed !nodes arguments; every member defaults to "not requested"."""

    filters: tuple[tuple[str, tuple[str, ...]], ...] = ()
    sort_field: str | None = None
    sort_direction: str | None = None
    limit: int | None = None
    display_fields: tuple[str, ...] | None = None


def _normalize_field_token(token: str) -> str:
    # Only aliases are case-insensitive; stored paths, including top-level
    # protobuf keys such as lastHeard, must keep their spelling.
    alias = token.lower()
    return alias if alias in FIELD_PATHS or alias in ("name", "power") else token


def _is_known_field(token: str) -> bool:
    field = _normalize_field_token(token)
    return (
        field in FIELD_PATHS
        or field in FIELD_PATHS.values()
        or field in ("name", "power")
        or "." in field
    )


def _reject_sensitive_field(token: str) -> None:
    field = _normalize_field_token(token)
    if _is_sensitive_field_path(FIELD_PATHS.get(field, field)):
        raise NodesUsageError(
            f"Field '{token}' may carry secret material and cannot be used.\n\n"
            f"{USAGE_TEXT}"
        )


def _parse_limit(value: str) -> int:
    """Convert numeric limits without leaking conversion failures to the handler."""
    if value.lower() == "all":
        return 0
    if value.isdigit():
        try:
            return int(value)
        except ValueError:
            pass
    raise NodesUsageError(
        f"'limit' expects a non-negative integer or 'all'.\n\n{USAGE_TEXT}"
    )


def parse_nodes_args(args: str) -> NodesQuery:
    """
    Parse !nodes arguments into a NodesQuery.

    Token pairs are order-free and combinable: ``limit N|all``, ``sort <field>
    [by] [asc|desc]``, ``fields <field,...>``, ``<field> <value>`` filters, and
    a bare number as limit shorthand. Aliases, keywords, and filter values
    are case-insensitive; raw node paths keep their stored spelling.

    Raises:
        NodesUsageError: When a token cannot be parsed or names a
            secret-bearing field.
    """
    tokens = args.split()
    filters: list[tuple[str, tuple[str, ...]]] = []
    sort_field: str | None = None
    sort_direction: str | None = None
    limit: int | None = None
    display_fields: tuple[str, ...] | None = None

    i = 0
    while i < len(tokens):
        token = tokens[i]
        keyword = token.lower()
        if keyword == "limit":
            if i + 1 >= len(tokens):
                raise NodesUsageError(f"'limit' requires a value.\n\n{USAGE_TEXT}")
            limit = _parse_limit(tokens[i + 1])
            i += 2
        elif keyword == "sort":
            if i + 1 >= len(tokens):
                raise NodesUsageError(f"'sort' requires a field.\n\n{USAGE_TEXT}")
            j = i + 1
            if tokens[j].lower() == "by":
                if j + 1 >= len(tokens):
                    raise NodesUsageError(f"'sort' requires a field.\n\n{USAGE_TEXT}")
                j += 1
            if not _is_known_field(tokens[j]):
                raise NodesUsageError(
                    f"Unknown sort field '{tokens[j]}'.\n\n{USAGE_TEXT}"
                )
            _reject_sensitive_field(tokens[j])
            sort_field = _normalize_field_token(tokens[j])
            sort_direction = None
            j += 1
            if j < len(tokens) and tokens[j].lower() in SORT_DIRECTIONS:
                sort_direction = tokens[j].lower()
                j += 1
            i = j
        elif keyword == "fields":
            if i + 1 >= len(tokens):
                raise NodesUsageError(f"'fields' requires a value.\n\n{USAGE_TEXT}")
            parts = [
                normalized
                for part in tokens[i + 1].split(",")
                if (normalized := _normalize_field_token(part.strip()))
            ]
            if not parts:
                raise NodesUsageError(
                    f"'fields' requires at least one field.\n\n{USAGE_TEXT}"
                )
            for part in parts:
                _reject_sensitive_field(part)
            display_fields = tuple(parts)
            i += 2
        elif token.isdigit():
            limit = _parse_limit(token)
            i += 1
        elif _is_known_field(token):
            _reject_sensitive_field(token)
            if i + 1 >= len(tokens):
                raise NodesUsageError(
                    f"Filter '{token}' requires a value.\n\n{USAGE_TEXT}"
                )
            values = tuple(
                stripped
                for value in tokens[i + 1].split(",")
                if (stripped := value.strip())
            )
            if not values:
                raise NodesUsageError(
                    f"Filter '{token}' requires a value.\n\n{USAGE_TEXT}"
                )
            filters.append((_normalize_field_token(token), values))
            i += 2
        else:
            raise NodesUsageError(f"Unknown option or field '{token}'.\n\n{USAGE_TEXT}")

    return NodesQuery(
        filters=tuple(filters),
        sort_field=sort_field,
        sort_direction=sort_direction,
        limit=limit,
        display_fields=display_fields,
    )


def _field_filter_texts(field: str, info: dict[str, Any]) -> list[str]:
    """Collect the stored value(s) a filter compares against, as display text."""
    if field == "name":
        values: list[Any] = [
            _get_field_value(info, "user.shortName"),
            _get_field_value(info, "user.longName"),
        ]
    elif field == "power":
        values = [
            _get_field_value(info, "deviceMetrics.batteryLevel"),
            _get_field_value(info, "deviceMetrics.voltage"),
        ]
    else:
        values = [_get_field_value(info, FIELD_PATHS.get(field, field))]

    texts: list[str] = []
    for value in values:
        if value is None or isinstance(value, (dict, list, tuple, set)):
            continue
        if isinstance(value, bool):
            # Match both spellings users naturally type for boolean fields.
            texts.extend(("true", "yes") if value else ("false", "no"))
        elif isinstance(value, bytes):
            texts.append(base64.b64encode(value).decode("ascii"))
        elif isinstance(value, bytearray):
            texts.append(base64.b64encode(bytes(value)).decode("ascii"))
        else:
            text = str(value).strip()
            if text:
                texts.append(text)
    return texts


def _node_matches_filters(
    info: dict[str, Any], filters: tuple[tuple[str, tuple[str, ...]], ...]
) -> bool:
    """Every filtered field must match at least one of its comma values."""
    for field, needles in filters:
        haystacks = [text.casefold() for text in _field_filter_texts(field, info)]
        if not any(
            needle.casefold() in haystack
            for needle in needles
            for haystack in haystacks
        ):
            return False
    return True


def _filters_echo(filters: tuple[tuple[str, tuple[str, ...]], ...]) -> str:
    return ", ".join(f"{field} ~ {','.join(values)}" for field, values in filters)


def _field_sort_key(
    field: str, info: dict[str, Any], *, numeric: bool = False
) -> tuple[int, int, float | str] | None:
    """
    Sort key for one node; None places the node after every valued node.

    Keys compare within numeric (0, 0, number) and text (0, 1, casefolded)
    buckets so a stray string value in a numeric field cannot crash the sort.
    """
    if field == "name":
        short_name = _get_field_value(info, "user.shortName")
        long_name = _get_field_value(info, "user.longName")
        if not (short_name or long_name):
            return None
        return (0, 1, f"{short_name or ''} {long_name or ''}".strip().casefold())
    if field == "power":
        value = _get_field_value(info, "deviceMetrics.batteryLevel")
    else:
        value = _get_field_value(info, FIELD_PATHS.get(field, field))
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    path = (
        "deviceMetrics.batteryLevel"
        if field == "power"
        else FIELD_PATHS.get(field, field)
    )
    if numeric or path in NUMERIC_FIELD_PATHS:
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return (0, 0, number) if math.isfinite(number) else None
    if not isinstance(value, bool) and isinstance(value, (int, float)):
        number = float(value)
        return (0, 0, number) if math.isfinite(number) else None
    return (0, 1, str(value).casefold())


def _field_sorts_ascending(field: str) -> bool:
    """Numeric fields default high-to-low (newest/best first); text A-to-Z."""
    if field == "name":
        return True
    if field == "power":
        return False
    return FIELD_PATHS.get(field, field) not in NUMERIC_FIELD_PATHS


def _apply_sort(
    node_entries: list[tuple[Any, dict[str, Any]]],
    field: str,
    direction: str | None,
) -> None:
    """Sort entries in place by field; missing values always sort last."""
    path = FIELD_PATHS.get(field, field)
    numeric = not _field_sorts_ascending(field) or any(
        isinstance(value := _get_field_value(info, path), (int, float))
        and not isinstance(value, bool)
        for _node_key, info in node_entries
    )
    keyed: list[tuple[tuple[int, int, float | str], tuple[Any, dict[str, Any]]]] = []
    missing: list[tuple[Any, dict[str, Any]]] = []
    for entry in node_entries:
        key = _field_sort_key(field, entry[1], numeric=numeric)
        if key is None:
            missing.append(entry)
        else:
            keyed.append((key, entry))
    if direction is None:
        direction = "desc" if numeric else "asc"
    keyed.sort(key=lambda pair: pair[0], reverse=direction == "desc")
    node_entries[:] = [entry for _key, entry in keyed] + missing


def get_relative_time(timestamp: float) -> str:
    """
    Convert a POSIX timestamp into a concise, human-readable relative time string.

    Parameters:
        timestamp (float): POSIX timestamp (seconds since the epoch) to compare with the current time.

    Returns:
        str: A relative time description:
                - "Just now" for times less than 60 seconds ago
                - "<N> minutes ago" for times between 60 seconds and 1 hour
                - "<N> hours ago" for times between 1 hour and 24 hours
                - "<N> days ago" for times between 1 day and RELATIVE_TIME_DAYS_THRESHOLD days
                - a timestamp formatted with DATE_FORMAT_LONG when
                  delta > RELATIVE_TIME_DAYS_THRESHOLD * SECONDS_PER_DAY
    """
    now = datetime.now()
    dt = datetime.fromtimestamp(timestamp)

    # Calculate the time difference between the current time and the given timestamp
    delta = now - dt

    # Compute signed total seconds and guard against future timestamps
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "Just now"

    # Convert the time difference into a relative timeframe
    if total_seconds > RELATIVE_TIME_DAYS_THRESHOLD * SECONDS_PER_DAY:
        return dt.strftime(
            DATE_FORMAT_LONG
        )  # Return formatted date if older than RELATIVE_TIME_DAYS_THRESHOLD days

    days = total_seconds // SECONDS_PER_DAY
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''} ago"

    hours = total_seconds // SECONDS_PER_HOUR
    if hours >= 1:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"

    minutes = total_seconds // SECONDS_PER_MINUTE
    if minutes >= 1:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"

    return "Just now"


def _get_field_value(node: dict[str, Any], field_path: str) -> Any:
    value: Any = node
    for key in field_path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    # Only leaf values belong in summaries, including the combined name/power fields.
    return None if isinstance(value, (dict, list, tuple, set)) else value


def _format_public_key(value: Any) -> str | None:
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii") if value else None
    if isinstance(value, bytearray):
        return base64.b64encode(bytes(value)).decode("ascii") if value else None
    if isinstance(value, str):
        return value or None
    return str(value) if value is not None else None


def _format_last_seen(value: Any) -> str:
    if value is None:
        return "?"
    try:
        timestamp = float(value)
        if timestamp <= 0 or not math.isfinite(timestamp):
            return "?"
        return get_relative_time(timestamp)
    except (TypeError, ValueError, OverflowError, OSError):
        logger.debug("Failed to parse lastHeard timestamp: %s", value)
        return "?"


def _last_heard_sort_value(info: dict[str, Any]) -> float:
    try:
        timestamp = float(info.get("lastHeard") or 0)
        return timestamp if timestamp > 0 and math.isfinite(timestamp) else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def _format_hops(value: Any) -> str:
    if value is None:
        return "? hops away"
    if value == 0:
        return "direct"
    if value == 1:
        return "1 hop away"
    return f"{value} hops away"


def _format_field_value(field: str, value: Any) -> str | None:
    if isinstance(value, (dict, list, tuple, set)):
        # Never stringify whole containers; select leaf paths explicitly so
        # secret-bearing keys added upstream cannot be dumped wholesale.
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if field == "public_key":
        return _format_public_key(value)
    if field == "last_seen":
        return _format_last_seen(value)
    if field == "hops":
        return _format_hops(value)
    if field == "snr":
        return f"{value}{SNR_UNIT_SUFFIX}" if value is not None else None
    if field == "battery":
        return f"{value}%" if value is not None else None
    if field == "voltage":
        return f"{value}V" if value is not None else None
    if field in ("channel_utilization", "air_util_tx"):
        return f"{value}%" if value is not None else None
    if field == "uptime" and isinstance(value, (int, float)):
        return f"{value}s"
    if field == "altitude":
        return f"{value}m" if value is not None else None
    if field in ("latitude", "longitude"):
        return f"{value}°" if value is not None else None
    if field == "favorite":
        return "yes" if value else "no" if value is not None else None
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, bytearray):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value) if value is not None else None


class Plugin(BasePlugin):
    plugin_name = "nodes"
    is_core_plugin = True

    @property
    def description(self) -> str:
        """
        Provide the plugin description and the configurable node-list line format.

        Returns:
            A multiline string describing the node output and configuration key.
        """
        return (
            "Show mesh radios and node data. Output fields can be selected with "
            "plugins.nodes.fields, and the command accepts limit, sort, and "
            "case-insensitive field filters (try '!nodes help').\n\n"
            "Default: name / hardware / power / snr / hops / last_seen / status\n\n"
            "Potentially secret-bearing paths (private keys, PSKs, passwords, "
            "wifi settings, ...) are never rendered, even when configured."
        )

    def _configured_fields(self) -> list[str]:
        fields = self.config.get("fields", DEFAULT_FIELDS)
        if not isinstance(fields, list) or not fields:
            self.logger.warning(
                "Plugin 'nodes': fields must be a non-empty list; using defaults."
            )
            return DEFAULT_FIELDS.copy()
        configured = [field.strip() for field in fields if isinstance(field, str)]
        configured = [field for field in configured if field]
        if not configured:
            self.logger.warning(
                "Plugin 'nodes': fields contains no valid field names; using defaults."
            )
            return DEFAULT_FIELDS.copy()
        withheld = [
            field
            for field in configured
            if _is_sensitive_field_path(FIELD_PATHS.get(field, field))
        ]
        if withheld:
            self.logger.warning(
                "Plugin 'nodes': ignoring potentially secret-bearing fields: %s",
                ", ".join(withheld),
            )
            configured = [field for field in configured if field not in withheld]
        if not configured:
            self.logger.warning(
                "Plugin 'nodes': all configured fields are potentially "
                "secret-bearing; using defaults."
            )
            return DEFAULT_FIELDS.copy()
        return configured

    def _configured_max_results(self) -> int:
        max_results = self.config.get("max_results", DEFAULT_MAX_RESULTS)
        if (
            isinstance(max_results, bool)
            or not isinstance(max_results, int)
            or max_results < 0
        ):
            self.logger.warning(
                "Plugin 'nodes': max_results must be a non-negative integer; "
                "using default %d.",
                DEFAULT_MAX_RESULTS,
            )
            return DEFAULT_MAX_RESULTS
        return max_results

    def _render_field(
        self, field: str, node_key: Any, info: dict[str, Any]
    ) -> str | None:
        if field == "name":
            short_name = _get_field_value(info, "user.shortName") or UNKNOWN_NODE_VALUE
            long_name = _get_field_value(info, "user.longName") or UNKNOWN_NODE_VALUE
            return f"{short_name} {long_name}"
        if field == "power":
            battery = _get_field_value(info, "deviceMetrics.batteryLevel")
            voltage = _get_field_value(info, "deviceMetrics.voltage")
            battery_text = f"{battery}%" if battery is not None else "?%"
            voltage_text = f"{voltage}V" if voltage is not None else "?V"
            return f"{battery_text} {voltage_text}"

        field_path = FIELD_PATHS.get(field, field)
        value = _get_field_value(info, field_path)
        if field == "node_id" and value is None and isinstance(node_key, str):
            value = node_key
        if field == "hardware" and (value is None or value == ""):
            value = UNKNOWN_NODE_VALUE

        rendered = _format_field_value(field, value)
        if rendered is None:
            return None
        if field in ("hardware", "snr", "hops", "last_seen"):
            return rendered

        label = FIELD_LABELS.get(field)
        if label is not None:
            return f"{label}: {rendered}"
        return f"{field}: {rendered}"

    def generate_response(self, query: NodesQuery | None = None) -> str:
        """
        Build a textual summary of known Meshtastic nodes for a parsed query.

        The response begins with a "Nodes: ..." header reflecting any filters
        and truncation, followed by one line per listed node and, when nodes
        are left unlisted, a trailing "... and N more not shown" line. Nodes
        are newest-first unless the query sorts by another field, and capped
        at ``plugins.nodes.max_results`` (default 20; 0 lists every node)
        unless the query overrides the limit. Fields come from the query's
        display-fields override or ``plugins.nodes.fields`` and may be aliases
        or raw dotted node-data paths. Potentially secret-bearing paths are
        withheld and container-valued paths render nothing rather than dumping
        raw data. If the Meshtastic device cannot be contacted, returns the
        error message "Unable to connect to Meshtastic device."

        Returns:
            response (str): The multi-line nodes summary or connection error.
        """
        from mmrelay.meshtastic_utils import connect_meshtastic

        meshtastic_client = connect_meshtastic()
        if meshtastic_client is None:
            return "Unable to connect to Meshtastic device."

        query = query if query is not None else NodesQuery()
        fields = (
            list(query.display_fields)
            if query.display_fields is not None
            else self._configured_fields()
        )
        node_entries = [
            (node_key, info)
            for node_key, info in meshtastic_client.nodes.items()
            if isinstance(info, dict)
        ]
        total = len(node_entries)
        matched_entries = [
            entry
            for entry in node_entries
            if _node_matches_filters(entry[1], query.filters)
        ]
        if query.sort_field is not None:
            _apply_sort(matched_entries, query.sort_field, query.sort_direction)
        else:
            matched_entries.sort(
                key=lambda item: _last_heard_sort_value(item[1]),
                reverse=True,
            )

        if query.filters and not matched_entries:
            return (
                f"No nodes matched {_filters_echo(query.filters)} "
                f"(of {total} known)."
            )

        limit = self._configured_max_results() if query.limit is None else query.limit
        shown_entries = matched_entries if limit <= 0 else matched_entries[:limit]

        node_lines: list[str] = []
        for node_key, info in shown_entries:
            rendered_fields = [
                rendered
                for field in fields
                if (rendered := self._render_field(field, node_key, info)) is not None
            ]
            node_text = (
                " / ".join(rendered_fields)
                if rendered_fields
                else "No fields available"
            )
            node_lines.append(node_text + "\n")

        suffixes: list[str] = []
        if query.filters:
            suffixes.append(_filters_echo(query.filters))
        if query.sort_field is not None:
            sort_echo = query.sort_field.replace("_", " ")
            if query.sort_direction is not None:
                sort_echo += f" {query.sort_direction}"
            suffixes.append(f"sorted by {sort_echo}")

        if query.filters:
            header = f"Nodes: {len(matched_entries)} matching"
            if len(shown_entries) < len(matched_entries):
                header = (
                    f"Nodes: {len(shown_entries)} of {len(matched_entries)} matching"
                )
            if len(matched_entries) < total:
                header += f" (of {total} known)"
        elif len(shown_entries) < total:
            header = f"Nodes: {len(shown_entries)} of {total}"
        else:
            header = f"Nodes: {total}"
        if suffixes:
            header += " · " + " · ".join(suffixes)

        response = header + "\n" + "".join(node_lines)
        hidden = len(matched_entries) - len(shown_entries)
        if hidden > 0:
            response += f"… and {hidden} more not shown\n"
        return response

    async def handle_meshtastic_message(
        self, packet: Any, formatted_message: str, longname: str, meshnet_name: str
    ) -> bool:
        """
        Handle an incoming Meshtastic packet without processing it.

        Returns:
            bool: `False` indicating the plugin did not handle the message.
        """
        _ = packet, formatted_message, longname, meshnet_name
        return False

    async def handle_room_message(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        full_message: str,
    ) -> bool:
        """
        Handle a Matrix room event and send the nodes summary for its arguments.

        Arguments are parsed with ``parse_nodes_args``; unparseable arguments
        reply with usage text, and a bare "help" request replies with the full
        usage text without querying the node DB.

        Returns:
            bool: `True` if the command was handled, `False` otherwise.
        """
        _ = full_message

        parsed = self.get_matching_matrix_command_with_args(event)
        if not parsed:
            return False
        _parsed_command, args = parsed

        if args.strip().lower() in ("help", "--help"):
            await self.send_matrix_message(
                room_id=room.room_id,
                message=USAGE_TEXT,
                formatted=False,
            )
            await self.send_matrix_reaction(room.room_id, event.event_id, "✅")
            return True

        try:
            query = parse_nodes_args(args)
        except NodesUsageError as error:
            await self.send_matrix_message(
                room_id=room.room_id,
                message=str(error),
                formatted=False,
            )
            await self.send_matrix_reaction(room.room_id, event.event_id, "❌")
            return True

        try:
            response = await asyncio.to_thread(self.generate_response, query)
            await self.send_matrix_message(
                room_id=room.room_id,
                message=response,
                formatted=False,
            )
        except Exception:
            self.logger.exception("Error handling nodes command")
            await self.send_matrix_reaction(room.room_id, event.event_id, "❌")
            return True
        await self.send_matrix_reaction(room.room_id, event.event_id, "✅")
        return True
