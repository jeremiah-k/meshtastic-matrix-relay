import asyncio
from unittest.mock import MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestParseRefreshIntervalSeconds:
    def test_bool_rejected(self):
        from mmrelay.meshtastic.node_refresh import _parse_refresh_interval_seconds

        assert _parse_refresh_interval_seconds(True) is None

    def test_infinite_rejected(self):
        from mmrelay.meshtastic.node_refresh import _parse_refresh_interval_seconds

        assert _parse_refresh_interval_seconds(float("inf")) is None

    def test_negative_rejected(self):
        from mmrelay.meshtastic.node_refresh import _parse_refresh_interval_seconds

        assert _parse_refresh_interval_seconds(-1.0) is None

    def test_zero_valid(self):
        from mmrelay.meshtastic.node_refresh import _parse_refresh_interval_seconds

        assert _parse_refresh_interval_seconds(0) == 0.0


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestGetNodeDBRefreshIntervalSeconds:
    def test_invalid_interval_uses_default(self):
        from mmrelay.constants.config import DEFAULT_NODEDB_REFRESH_INTERVAL
        from mmrelay.meshtastic.node_refresh import get_nodedb_refresh_interval_seconds

        cfg = {"meshtastic": {"nodedb_refresh_interval": True}}
        result = get_nodedb_refresh_interval_seconds(cfg)
        assert result == DEFAULT_NODEDB_REFRESH_INTERVAL


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestSnapshotNodeNameRows:
    def test_client_none(self):
        from mmrelay.meshtastic.node_refresh import _snapshot_node_name_rows

        mu.meshtastic_client = None
        snapshot, missing = _snapshot_node_name_rows()
        assert snapshot is None
        assert missing is True

    def test_nodes_not_dict(self):
        from mmrelay.meshtastic.node_refresh import _snapshot_node_name_rows

        client = MagicMock()
        client.nodes = "not_a_dict"
        mu.meshtastic_client = client
        snapshot, missing = _snapshot_node_name_rows()
        assert snapshot is None
        assert missing is False

    def test_non_dict_node_entry(self):
        from mmrelay.meshtastic.node_refresh import _snapshot_node_name_rows

        client = MagicMock()
        client.nodes = {"!abc": "not_a_dict"}
        mu.meshtastic_client = client
        snapshot, missing = _snapshot_node_name_rows()
        assert snapshot is not None
        assert snapshot["!abc"]["user"] is None

    def test_non_dict_user_entry(self):
        from mmrelay.meshtastic.node_refresh import _snapshot_node_name_rows

        client = MagicMock()
        client.nodes = {"!abc": {"user": "not_a_dict"}}
        mu.meshtastic_client = client
        snapshot, missing = _snapshot_node_name_rows()
        assert snapshot is not None
        assert snapshot["!abc"]["user"]["id"] is None

    def test_valid_node_snapshot(self):
        from mmrelay.meshtastic.node_refresh import _snapshot_node_name_rows

        client = MagicMock()
        client.nodes = {
            "!abc": {
                "user": {
                    "id": "!abc",
                    "longName": "Long",
                    "shortName": "Srt",
                }
            }
        }
        mu.meshtastic_client = client
        snapshot, missing = _snapshot_node_name_rows()
        assert missing is False
        assert snapshot["!abc"]["user"]["longName"] == "Long"


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestRefreshNodeNameTables:
    @pytest.mark.asyncio
    async def test_zero_interval_runs_once_then_returns(self):
        from mmrelay.meshtastic.node_refresh import refresh_node_name_tables

        shutdown_event = asyncio.Event()

        with patch(
            "mmrelay.meshtastic.node_refresh._snapshot_node_name_rows",
            return_value=(None, True),
        ):
            task = asyncio.create_task(
                refresh_node_name_tables(shutdown_event, refresh_interval_seconds=0)
            )
            await asyncio.sleep(0.1)
            assert task.done()

    @pytest.mark.asyncio
    async def test_shutdown_event_stops_loop(self):
        from mmrelay.meshtastic.node_refresh import refresh_node_name_tables

        shutdown_event = asyncio.Event()
        shutdown_event.set()

        with patch(
            "mmrelay.meshtastic.node_refresh._snapshot_node_name_rows",
            return_value=(None, True),
        ):
            await refresh_node_name_tables(shutdown_event, refresh_interval_seconds=10)

    @pytest.mark.asyncio
    async def test_snapshot_exception_propagates(self):
        from mmrelay.meshtastic.node_refresh import refresh_node_name_tables

        shutdown_event = asyncio.Event()

        with patch(
            "mmrelay.meshtastic.node_refresh._snapshot_node_name_rows",
            side_effect=RuntimeError("db error"),
        ):
            with patch(
                "mmrelay.meshtastic.node_refresh.get_nodedb_refresh_interval_seconds",
                return_value=10,
            ):
                with pytest.raises(RuntimeError, match="db error"):
                    await refresh_node_name_tables(
                        shutdown_event, refresh_interval_seconds=None
                    )

    @pytest.mark.asyncio
    async def test_invalid_override_uses_configured(self):
        from mmrelay.meshtastic.node_refresh import refresh_node_name_tables

        shutdown_event = asyncio.Event()
        shutdown_event.set()

        with patch(
            "mmrelay.meshtastic.node_refresh._snapshot_node_name_rows",
            return_value=(None, True),
        ):
            with patch(
                "mmrelay.meshtastic.node_refresh.get_nodedb_refresh_interval_seconds",
                return_value=30,
            ):
                await refresh_node_name_tables(
                    shutdown_event, refresh_interval_seconds=True
                )
