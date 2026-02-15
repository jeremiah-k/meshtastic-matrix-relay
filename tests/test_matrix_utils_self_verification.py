from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import mmrelay.matrix_utils as matrix_utils


@pytest.mark.asyncio
async def test_self_verification_request_sends_ready_and_tracks_transaction():
    matrix_utils._self_verify_pending_transactions.clear()
    client = MagicMock()
    client.user_id = "@bot:example.org"
    client.device_id = "BOT_DEVICE"
    client.to_device = AsyncMock()

    event = SimpleNamespace(
        sender="@bot:example.org",
        type="m.key.verification.request",
        source={
            "type": "m.key.verification.request",
            "content": {
                "from_device": "OTHER_DEVICE",
                "transaction_id": "txn-ready",
                "methods": ["m.sas.v1"],
            },
        },
    )

    await matrix_utils._handle_internal_self_verification_to_device_event(client, event)

    client.to_device.assert_awaited_once()
    sent_message = client.to_device.await_args.args[0]
    assert sent_message["type"] == "m.key.verification.ready"
    assert sent_message["recipient"] == "@bot:example.org"
    assert sent_message["recipient_device"] == "OTHER_DEVICE"
    assert sent_message["content"]["transaction_id"] == "txn-ready"
    assert "txn-ready" in matrix_utils._self_verify_pending_transactions


@pytest.mark.asyncio
async def test_self_verification_flow_accepts_confirms_and_sends_done():
    matrix_utils._self_verify_pending_transactions.clear()
    matrix_utils._self_verify_pending_transactions.add("txn-flow")

    client = MagicMock()
    client.user_id = "@bot:example.org"
    client.device_id = "BOT_DEVICE"
    client.accept_key_verification = AsyncMock()
    client.confirm_short_auth_string = AsyncMock()
    client.send_to_device_messages = AsyncMock()
    client.to_device = AsyncMock()

    start_event = SimpleNamespace(
        sender="@bot:example.org",
        transaction_id="txn-flow",
        type="m.key.verification.start",
        source={"type": "m.key.verification.start", "content": {}},
    )
    await matrix_utils._handle_internal_self_verification_to_device_event(
        client, start_event
    )
    client.accept_key_verification.assert_awaited_once_with("txn-flow")

    key_event = SimpleNamespace(
        sender="@bot:example.org",
        transaction_id="txn-flow",
        type="m.key.verification.key",
        source={"type": "m.key.verification.key", "content": {}},
    )
    await matrix_utils._handle_internal_self_verification_to_device_event(
        client, key_event
    )
    client.send_to_device_messages.assert_awaited_once()
    client.confirm_short_auth_string.assert_awaited_once_with("txn-flow")

    client.key_verifications = {
        "txn-flow": SimpleNamespace(
            verified=True,
            other_olm_device=SimpleNamespace(id="OTHER_DEVICE"),
        )
    }
    mac_event = SimpleNamespace(
        sender="@bot:example.org",
        transaction_id="txn-flow",
        type="m.key.verification.mac",
        source={"type": "m.key.verification.mac", "content": {}},
    )
    await matrix_utils._handle_internal_self_verification_to_device_event(
        client, mac_event
    )

    sent_message = client.to_device.await_args.args[0]
    assert sent_message["type"] == "m.key.verification.done"
    assert sent_message["recipient"] == "@bot:example.org"
    assert sent_message["recipient_device"] == "OTHER_DEVICE"
    assert sent_message["content"]["transaction_id"] == "txn-flow"
    assert "txn-flow" not in matrix_utils._self_verify_pending_transactions


@pytest.mark.asyncio
async def test_self_verification_ignores_other_senders():
    matrix_utils._self_verify_pending_transactions.clear()
    client = MagicMock()
    client.user_id = "@bot:example.org"
    client.device_id = "BOT_DEVICE"
    client.to_device = AsyncMock()

    event = SimpleNamespace(
        sender="@attacker:example.org",
        type="m.key.verification.request",
        source={
            "type": "m.key.verification.request",
            "content": {
                "from_device": "OTHER_DEVICE",
                "transaction_id": "txn-ignore",
                "methods": ["m.sas.v1"],
            },
        },
    )

    await matrix_utils._handle_internal_self_verification_to_device_event(client, event)

    client.to_device.assert_not_called()
    assert "txn-ignore" not in matrix_utils._self_verify_pending_transactions


def test_register_self_verification_callback_only_when_e2ee_enabled():
    client = MagicMock()
    client.add_to_device_callback = MagicMock()

    matrix_utils._register_internal_self_verification_callback(
        client, self_verification_enabled=True
    )
    client.add_to_device_callback.assert_called_once()

    client.add_to_device_callback.reset_mock()
    matrix_utils._register_internal_self_verification_callback(
        client, self_verification_enabled=False
    )
    client.add_to_device_callback.assert_not_called()


def test_internal_self_verification_default_enabled_when_e2ee_enabled():
    assert (
        matrix_utils._is_internal_self_verification_enabled(
            matrix_section={"e2ee": {"enabled": True}},
            e2ee_enabled=True,
        )
        is True
    )


def test_internal_self_verification_can_be_disabled_in_e2ee_section():
    assert (
        matrix_utils._is_internal_self_verification_enabled(
            matrix_section={"e2ee": {"self_verify": False}},
            e2ee_enabled=True,
        )
        is False
    )


def test_internal_self_verification_ignores_legacy_section():
    assert (
        matrix_utils._is_internal_self_verification_enabled(
            matrix_section={"encryption": {"self_verify": False}},
            e2ee_enabled=True,
        )
        is True
    )


def test_internal_self_verification_disabled_when_e2ee_disabled():
    assert (
        matrix_utils._is_internal_self_verification_enabled(
            matrix_section={"e2ee": {"self_verify": True}},
            e2ee_enabled=False,
        )
        is False
    )


@pytest.mark.asyncio
async def test_maybe_initiate_internal_self_verification_starts_for_unverified_devices():
    matrix_utils._self_verify_pending_transactions.clear()

    device_unverified = SimpleNamespace(id="OTHER_DEVICE", verified=False)
    client = MagicMock()
    client.user_id = "@bot:example.org"
    client.device_id = "BOT_DEVICE"
    client.whoami = AsyncMock(
        return_value=SimpleNamespace(user_id="@bot:example.org", device_id="BOT_DEVICE")
    )
    client.users_for_key_query = set()
    client.should_query_keys = False
    client.device_store = MagicMock()
    client.device_store.active_user_devices.return_value = [device_unverified]
    client.get_active_sas = MagicMock(return_value=None)
    client.key_verifications = {}

    async def _start(device):
        client.key_verifications["txn-init"] = SimpleNamespace(
            transaction_id="txn-init",
            canceled=False,
        )
        return SimpleNamespace()

    client.start_key_verification = AsyncMock(side_effect=_start)
    client.send_to_device_messages = AsyncMock()

    await matrix_utils._maybe_initiate_internal_self_verification(
        client, self_verification_enabled=True
    )

    client.start_key_verification.assert_awaited_once_with(device_unverified)
    client.send_to_device_messages.assert_awaited_once()
    assert "txn-init" in matrix_utils._self_verify_pending_transactions


@pytest.mark.asyncio
async def test_maybe_initiate_internal_self_verification_skips_when_disabled():
    client = MagicMock()
    client.start_key_verification = AsyncMock()

    await matrix_utils._maybe_initiate_internal_self_verification(
        client, self_verification_enabled=False
    )

    client.start_key_verification.assert_not_called()
