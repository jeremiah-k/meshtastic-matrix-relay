"""Real-provider contracts, isolated from the suite's global nio doubles."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - test helper runs a fixed interpreter argv
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def run_sdk(source: str, path: Path, executable: str = sys.executable) -> str:
    result = subprocess.run(  # nosec B603
        [executable, "-W", "error", "-c", textwrap.dedent(source), str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_real_provider_capabilities_and_client_configuration(tmp_path: Path) -> None:
    run_sdk(
        """
        from importlib import metadata
        from mmrelay.matrix.compat import detect_matrix_capabilities
        from mmrelay.matrix.client_config import build_matrix_client_config

        capabilities = detect_matrix_capabilities()
        assert capabilities.provider_distribution == "mindroom-nio"
        assert capabilities.provider_version == metadata.version("mindroom-nio")
        assert capabilities.crypto_backend == "vodozemac"
        assert capabilities.encryption_available
        assert capabilities.supports_stop_sync_forever
        assert capabilities.supports_authenticated_media
        config = build_matrix_client_config(e2ee_enabled=True, max_limit_exceeded=0, max_timeouts=0)
        assert config.encryption_enabled
        assert config.store_sync_tokens
        assert config.max_timeouts == 0
        assert config.replace_rotated_device_keys
    """,
        tmp_path,
    )


def test_real_provider_sends_restored_session_and_plaintext_message(
    tmp_path: Path,
) -> None:
    run_sdk(
        """
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from nio import AsyncClient, MatrixRoom, RoomSendResponse, WhoamiResponse
        from mmrelay.matrix.client_config import build_matrix_client_config

        async def main():
            client = AsyncClient("https://matrix.example.com", config=build_matrix_client_config(e2ee_enabled=False))
            client.restore_login("@relay:example.com", "SDK-DEVICE", "test-sdk-access")
            responses = [
                {"user_id": client.user_id, "device_id": client.device_id, "is_guest": False},
                {"event_id": "$sdk-message"},
            ]
            async def request(*args, **kwargs):
                return SimpleNamespace(status=200, content_type="application/json", content_disposition=None, json=AsyncMock(return_value=responses.pop(0)))
            http = AsyncMock(side_effect=request)
            client.client_session = SimpleNamespace(request=http, close=AsyncMock())
            try:
                whoami = await client.whoami()
                assert isinstance(whoami, WhoamiResponse)
                assert whoami.device_id == "SDK-DEVICE"
                room_id = "!sdk:example.com"
                client.rooms[room_id] = MatrixRoom(room_id, client.user_id)
                sent = await client.room_send(room_id, "m.room.message", {"msgtype": "m.text", "body": "SDK contract"})
                assert isinstance(sent, RoomSendResponse)
                assert sent.event_id == "$sdk-message"
                assert http.await_count == 2
                for call in http.call_args_list:
                    assert call.kwargs["headers"]["Authorization"] == "Bearer test-sdk-access"
                    assert "access_token=" not in call.args[1]
            finally:
                await client.close()
        asyncio.run(main())
    """,
        tmp_path,
    )


def test_real_provider_delivers_classic_sync_callbacks_in_order(tmp_path: Path) -> None:
    run_sdk(
        """
        import asyncio
        from nio import AsyncClient, RoomMessageText, SyncResponse
        from mmrelay.matrix.client_config import build_matrix_client_config

        async def main():
            client = AsyncClient("https://matrix.example.com", config=build_matrix_client_config(e2ee_enabled=False))
            client.restore_login("@relay:example.com", "SDK-DEVICE", "test-sdk-access")
            seen = []
            async def callback(room, event):
                await asyncio.sleep(0)
                seen.append((room.room_id, event.event_id, event.body))
            client.add_event_callback(callback, RoomMessageText)
            events = [
                {"type": "m.room.message", "event_id": "$sdk-1", "sender": "@peer:example.com", "origin_server_ts": 1, "content": {"msgtype": "m.text", "body": "one"}},
                {"type": "m.room.message", "event_id": "$sdk-2", "sender": "@peer:example.com", "origin_server_ts": 2, "content": {"msgtype": "m.text", "body": "two"}},
            ]
            response = SyncResponse.from_dict({"next_batch": "sdk-cursor", "rooms": {"join": {"!sdk:example.com": {"state": {"events": []}, "timeline": {"events": events, "limited": False, "prev_batch": "previous"}, "ephemeral": {"events": []}, "account_data": {"events": []}}}}})
            assert isinstance(response, SyncResponse)
            try:
                await client.receive_response(response)
                assert seen == [("!sdk:example.com", "$sdk-1", "one"), ("!sdk:example.com", "$sdk-2", "two")]
                assert client.next_batch == "sdk-cursor"
                await client.receive_response(response)
                assert len(seen) == 2
            finally:
                await client.close()
        asyncio.run(main())
    """,
        tmp_path,
    )


_STORE_WRITER = """
    import asyncio
    import base64
    import json
    import sys
    from pathlib import Path
    from nio import AsyncClient, AsyncClientConfig
    from nio.crypto import OlmAccount, OlmDevice
    from nio.crypto.sessions import InboundGroupSession, InboundSession, OutboundGroupSession, OutboundSession
    from nio.crypto.cross_signing import CrossSigningIdentity, cross_signing_sidecar_path

    async def main():
        root = Path(sys.argv[1])
        root.mkdir(parents=True, exist_ok=True)
        client = AsyncClient("https://matrix.example.com", store_path=str(root), config=AsyncClientConfig(encryption_enabled=True, store_sync_tokens=True))
        client.restore_login("@relay:example.com", "SDK-DEVICE", "test-sdk-access")
        peer = OlmAccount()
        device = OlmDevice("@peer:example.com", "SDK-PEER", peer.identity_keys)
        client.device_store.add(device)
        client.store.save_device_keys(client.device_store)
        assert client.verify_device(device)
        # Persist populated sessions, then decrypt their pending messages after restart.
        account = client.olm.account
        account.generate_one_time_keys(1)
        one_time_key = next(iter(account.one_time_keys["curve25519"].values()))
        remote = OutboundSession(peer, account.identity_keys["curve25519"], one_time_key)
        first_message = remote.encrypt("olm handshake")
        inbound = InboundSession(account, first_message, device.curve25519)
        assert inbound.decrypt(first_message) == "olm handshake"
        client.store.save_session(device.curve25519, inbound)
        message_type, ciphertext = remote.encrypt("persisted olm message").to_parts()

        group = OutboundGroupSession()
        group.mark_as_shared()
        room_id = "!sdk:example.com"
        inbound_group = InboundGroupSession(group.session_key, device.ed25519, device.curve25519, room_id)
        client.store.save_inbound_group_session(inbound_group)
        (root / "encrypted-messages.json").write_text(json.dumps({
            "sender_key": device.curve25519,
            "olm_session_id": inbound.id,
            "olm_message_type": message_type,
            "olm_ciphertext": base64.b64encode(ciphertext).decode(),
            "room_id": room_id,
            "megolm_session_id": group.id,
            "megolm_ciphertext": group.encrypt("persisted megolm message"),
        }), encoding="utf-8")
        client.store.save_sync_token("sdk-persisted-cursor")
        identity = CrossSigningIdentity.generate(client.user_id)
        identity.uploaded = True
        identity.signed_devices = [client.device_id]
        identity.save(cross_signing_sidecar_path(str(root), client.user_id))
        print(json.dumps({"account_keys": client.olm.account.identity_keys, "master_public_key": identity.master_public_key, "self_signing_public_key": identity.self_signing_public_key}))
        await client.close()
        client.store.database.close()
    asyncio.run(main())
"""

_STORE_READER = """
    import asyncio
    import base64
    import json
    import sys
    from pathlib import Path
    from nio import AsyncClient
    from vodozemac import AnyOlmMessage
    from mmrelay.matrix.client_config import build_matrix_client_config
    from mmrelay.matrix.e2ee_identity import _inspect_cross_signing_provider

    async def main():
        client = AsyncClient("https://matrix.example.com", store_path=sys.argv[1], config=build_matrix_client_config(e2ee_enabled=True))
        client.restore_login("@relay:example.com", "SDK-DEVICE", "test-sdk-access")
        assert client.loaded_sync_token == "sdk-persisted-cursor"
        device = client.device_store["@peer:example.com"]["SDK-PEER"]
        assert device.verified
        pending = json.loads((Path(sys.argv[1]) / "encrypted-messages.json").read_text(encoding="utf-8"))
        olm_session = client.olm.session_store.get(pending["sender_key"])
        assert olm_session is not None
        assert olm_session.id == pending["olm_session_id"]
        olm_message = AnyOlmMessage.from_parts(pending["olm_message_type"], base64.b64decode(pending["olm_ciphertext"]))
        assert olm_session.decrypt(olm_message) == "persisted olm message"
        megolm_session = client.olm.inbound_group_store.get(pending["room_id"], pending["sender_key"], pending["megolm_session_id"])
        assert megolm_session is not None
        assert megolm_session.decrypt(pending["megolm_ciphertext"]) == ("persisted megolm message", 0)
        provider = _inspect_cross_signing_provider(client)
        assert provider is not None
        identity = client.cross_signing_identity
        assert identity is not None
        assert identity.uploaded
        assert identity.signed_devices == [client.device_id]
        assert await client.ensure_cross_signing() == "already_signed"
        print(json.dumps({"account_keys": client.olm.account.identity_keys, "master_public_key": identity.master_public_key, "self_signing_public_key": identity.self_signing_public_key}))
        await client.close()
        client.store.database.close()
    asyncio.run(main())
"""


def test_real_provider_retains_crypto_trust_cursor_and_identity_on_restart(
    tmp_path: Path,
) -> None:
    before = json.loads(run_sdk(_STORE_WRITER, tmp_path))
    after = json.loads(run_sdk(_STORE_READER, tmp_path))
    assert after == before


@pytest.mark.skipif(
    not os.environ.get("MMRELAY_PREVIOUS_SDK_PYTHON"),
    reason="Requires an isolated previous-SDK interpreter",
)
def test_previous_provider_store_reopens_without_identity_reset(tmp_path: Path) -> None:
    reader_version = run_sdk(
        "from importlib.metadata import version; print(version('mindroom-nio'))",
        tmp_path,
    ).strip()
    if reader_version == "0.40.0":
        pytest.skip("Requires mindroom-nio 1.1.2 in the current reader interpreter")
    assert reader_version == "1.1.2"
    # Preserve the venv entry point; resolving its symlink selects base Python.
    previous = str(Path(os.environ["MMRELAY_PREVIOUS_SDK_PYTHON"]).absolute())
    run_sdk(
        "from importlib.metadata import version; assert version('mindroom-nio') == '0.40.0'",
        tmp_path,
        previous,
    )
    before = json.loads(run_sdk(_STORE_WRITER, tmp_path, previous))
    after = json.loads(run_sdk(_STORE_READER, tmp_path))
    assert after == before
