#!/usr/bin/env python3
"""Targeted tests for node-name refresh interval and refresh-loop edge paths."""

import asyncio
import logging
from typing import Any, cast
from unittest.mock import patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.config import DEFAULT_NODE_NAME_REFRESH_INTERVAL


class _OnePassEvent:
    """Event that starts cleared and sets itself when awaited once."""

    def __init__(self) -> None:
        self._set = False

    def is_set(self) -> bool:
        return self._set

    async def wait(self) -> None:
        self._set = True


class _TimeoutThenSetEvent:
    """Event whose first wait times out and second wait sets the event."""

    def __init__(self) -> None:
        self._set = False
        self._wait_calls = 0
        self.first_wait_cancelled = False

    def is_set(self) -> bool:
        return self._set

    async def wait(self) -> None:
        self._wait_calls += 1
        if self._wait_calls == 1:
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                self.first_wait_cancelled = True
                raise
            return
        self._set = True


class _ClientWithoutNodes:
    """Minimal client shape with no nodes attribute."""


class _ClientWithNodes:
    """Minimal client shape with a dict-backed nodes attribute."""

    def __init__(self, nodes: dict[str, Any]) -> None:
        self.nodes = nodes


def test_get_node_name_refresh_interval_ignores_non_dict_config(
    reset_meshtastic_globals,
) -> None:
    """Non-dict config inputs should fall back to default refresh interval."""
    _ = reset_meshtastic_globals
    interval = mu.get_node_name_refresh_interval_seconds(cast(Any, []))
    assert interval == DEFAULT_NODE_NAME_REFRESH_INTERVAL


def test_refresh_node_name_tables_skips_when_nodes_attribute_unavailable(
    reset_meshtastic_globals,
) -> None:
    """Missing client.nodes should skip sync rather than treat as empty nodedb."""
    _ = reset_meshtastic_globals
    with (
        patch.object(mu, "meshtastic_client", _ClientWithoutNodes()),
        patch.object(mu, "sync_name_tables_if_changed") as mock_sync,
    ):
        asyncio.run(
            mu.refresh_node_name_tables(
                _OnePassEvent(),  # type: ignore[arg-type]
                refresh_interval_seconds=0.01,
            )
        )
    mock_sync.assert_not_called()


def test_refresh_node_name_tables_handles_timeout_then_retries(
    reset_meshtastic_globals,
) -> None:
    """Refresh loop should continue after wait timeout and retry sync."""
    _ = reset_meshtastic_globals
    event = _TimeoutThenSetEvent()
    client = _ClientWithNodes(
        {
            "node_a": {
                "user": {"id": "!1", "longName": "Alpha", "shortName": "A"},
            }
        }
    )
    with (
        patch.object(mu, "meshtastic_client", client),
        patch.object(mu, "sync_name_tables_if_changed", return_value=()) as mock_sync,
    ):
        asyncio.run(
            mu.refresh_node_name_tables(
                event,  # type: ignore[arg-type]
                refresh_interval_seconds=0.01,
            )
        )
    assert event.first_wait_cancelled is True
    assert event._wait_calls == 2
    assert mock_sync.call_count == 2


def test_refresh_node_name_tables_non_positive_interval_exits_after_one_pass(
    reset_meshtastic_globals,
) -> None:
    """Zero interval should perform one immediate pass and return."""
    _ = reset_meshtastic_globals
    with (
        patch.object(
            mu,
            "meshtastic_client",
            _ClientWithNodes(
                {
                    "node_a": {
                        "user": {"id": "!1", "longName": "Alpha", "shortName": "A"},
                    }
                }
            ),
        ),
        patch.object(mu, "sync_name_tables_if_changed", return_value=()) as mock_sync,
    ):
        asyncio.run(
            mu.refresh_node_name_tables(
                _OnePassEvent(),  # type: ignore[arg-type]
                refresh_interval_seconds=0.0,
            )
        )
    mock_sync.assert_called_once()


def test_refresh_node_name_tables_handles_sync_exceptions(
    reset_meshtastic_globals,
    caplog,
) -> None:
    """Sync errors should be logged and re-raised for supervisor handling."""
    _ = reset_meshtastic_globals
    caplog.set_level(logging.ERROR, logger=mu.logger.name)
    original_propagate = mu.logger.propagate
    mu.logger.propagate = True
    client = _ClientWithNodes(
        {
            "node_a": {
                "user": {"id": "!1", "longName": "Alpha", "shortName": "A"},
            }
        }
    )
    try:
        with (
            patch.object(mu, "meshtastic_client", client),
            patch.object(
                mu,
                "sync_name_tables_if_changed",
                side_effect=RuntimeError("sync failure"),
            ) as mock_sync,
        ):
            with pytest.raises(RuntimeError, match="sync failure"):
                asyncio.run(
                    mu.refresh_node_name_tables(
                        _OnePassEvent(),  # type: ignore[arg-type]
                        refresh_interval_seconds=0.0,
                    )
                )
    finally:
        mu.logger.propagate = original_propagate
    mock_sync.assert_called_once()
    assert any(
        "Failed to refresh node-name tables from node snapshot" in record.message
        for record in caplog.records
    )
