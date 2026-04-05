import asyncio
import math
from typing import Any

import mmrelay.meshtastic_utils as facade
from mmrelay.constants.config import (
    CONFIG_KEY_NODEDB_REFRESH_INTERVAL,
    DEFAULT_NODEDB_REFRESH_INTERVAL,
)
from mmrelay.constants.database import PROTO_NODE_NAME_LONG, PROTO_NODE_NAME_SHORT
from mmrelay.db_utils import NodeNameState

__all__ = [
    "_parse_refresh_interval_seconds",
    "get_nodedb_refresh_interval_seconds",
    "_snapshot_node_name_rows",
    "refresh_node_name_tables",
]


def _parse_refresh_interval_seconds(raw_interval: Any) -> float | None:
    """
    Parse and validate a refresh interval value.

    Returns the parsed float if valid, or None if invalid (wrong type, non-finite, etc.).
    """
    try:
        if isinstance(raw_interval, bool):
            raise TypeError("boolean interval")
        interval = float(raw_interval)
        if not math.isfinite(interval):
            raise ValueError("non-finite interval")
        if interval < 0:
            raise ValueError("negative interval")
        return interval
    except (TypeError, ValueError, OverflowError):
        return None


def get_nodedb_refresh_interval_seconds(
    passed_config: dict[str, Any] | None = None,
) -> float:
    """
    Return the configured nodedb refresh interval (seconds).

    Reads `meshtastic.nodedb_refresh_interval` and falls back to
    `DEFAULT_NODEDB_REFRESH_INTERVAL` when missing or invalid.

    Current scope: this interval controls periodic refresh of cached long/short
    node-name tables derived from the Meshtastic NodeDB. The key name is
    future-oriented because later releases may expand persistence beyond names.

    Parameters:
        passed_config (dict[str, Any] | None): Optional config to read from.
            When omitted, uses this module's global `config`.
    """
    config_source = passed_config if passed_config is not None else facade.config
    if not isinstance(config_source, dict):
        config_source = {}
    raw_interval = facade.get_meshtastic_config_value(
        config_source,
        CONFIG_KEY_NODEDB_REFRESH_INTERVAL,
        DEFAULT_NODEDB_REFRESH_INTERVAL,
    )
    interval = _parse_refresh_interval_seconds(raw_interval)
    if interval is not None:
        return interval

    facade.logger.warning(
        "Invalid meshtastic.nodedb_refresh_interval=%r; defaulting to %.1f",
        raw_interval,
        DEFAULT_NODEDB_REFRESH_INTERVAL,
    )
    return DEFAULT_NODEDB_REFRESH_INTERVAL


def _snapshot_node_name_rows() -> tuple[dict[str, Any] | None, bool]:
    """
    Build a minimal node-name snapshot under meshtastic_lock.

    Returns:
        tuple[dict[str, Any] | None, bool]:
            - Snapshot suitable for sync_name_tables_if_changed(), or None when unavailable.
            - True when the Meshtastic client is unavailable.
    """
    with facade.meshtastic_lock:
        client = facade.meshtastic_client
        if client is None:
            return None, True

        raw_nodes = getattr(client, "nodes", None)
        if not isinstance(raw_nodes, dict):
            return None, False

        nodes_snapshot: dict[str, Any] = {}
        for node_id, raw_node in raw_nodes.items():
            node_key = str(node_id)
            if not isinstance(raw_node, dict):
                nodes_snapshot[node_key] = {"user": None}
                continue

            raw_user = raw_node.get("user")
            if not isinstance(raw_user, dict):
                nodes_snapshot[node_key] = {"user": {"id": None}}
                continue

            user_snapshot: dict[str, Any] = {
                "id": raw_user.get("id"),
                PROTO_NODE_NAME_LONG: raw_user.get(PROTO_NODE_NAME_LONG),
                PROTO_NODE_NAME_SHORT: raw_user.get(PROTO_NODE_NAME_SHORT),
            }
            nodes_snapshot[node_key] = {"user": user_snapshot}

        return nodes_snapshot, False


async def refresh_node_name_tables(
    shutdown_event: asyncio.Event,
    *,
    refresh_interval_seconds: float | None = None,
) -> None:
    """
    Periodically sync longname/shortname tables from the current Meshtastic node DB.

    The first refresh attempt runs immediately. When `refresh_interval_seconds`
    is zero, one immediate refresh is attempted and periodic refresh
    is disabled afterward.

    Current scope: this task updates only long/short name cache tables from the
    NodeDB snapshot. Future releases may extend persistence to broader NodeDB
    fields while keeping this interval setting.

    Note: Exceptions are intentionally propagated to the caller (the supervisor in
    main.py) which catches them and restarts this task with exponential backoff.
    This prevents silent infinite retry loops on persistent errors while still
    allowing recovery from transient failures.
    """
    if refresh_interval_seconds is None:
        interval = facade.get_nodedb_refresh_interval_seconds()
    else:
        parsed = facade._parse_refresh_interval_seconds(refresh_interval_seconds)
        if parsed is None:
            configured_interval = facade.get_nodedb_refresh_interval_seconds()
            facade.logger.warning(
                "Invalid NodeDB name-cache refresh interval override %r; defaulting to configured interval %.1f",
                refresh_interval_seconds,
                configured_interval,
            )
            interval = configured_interval
        else:
            interval = parsed

    previous_state: NodeNameState | None = None
    client_unavailable_reason: str | None = None
    while not shutdown_event.is_set():
        try:
            nodes_snapshot, client_missing = await asyncio.to_thread(
                facade._snapshot_node_name_rows
            )

            if nodes_snapshot is None:
                if client_missing:
                    if facade.reconnecting:
                        next_reason = "reconnecting"
                        if client_unavailable_reason != next_reason:
                            facade.logger.debug(
                                "Skipping name-cache refresh from NodeDB while reconnection is in progress"
                            )
                        client_unavailable_reason = next_reason
                    else:
                        next_reason = "unavailable"
                        if client_unavailable_reason != next_reason:
                            facade.logger.debug(
                                "Skipping name-cache refresh from NodeDB because Meshtastic client is unavailable"
                            )
                        client_unavailable_reason = next_reason
                else:
                    client_unavailable_reason = None
                    facade.logger.debug(
                        "Skipping name-cache refresh from NodeDB because client.nodes is unavailable"
                    )
            else:
                client_unavailable_reason = None
                previous_state = await asyncio.to_thread(
                    facade.sync_name_tables_if_changed,
                    nodes_snapshot,
                    previous_state,
                )
        except Exception:
            facade.logger.exception(
                "Failed to refresh name-cache tables from NodeDB snapshot"
            )
            raise

        if interval <= 0:
            facade.logger.debug(
                "NodeDB name-cache periodic refresh disabled (interval=%.3f)",
                float(interval),
            )
            return

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=float(interval))
        except asyncio.TimeoutError:
            continue
