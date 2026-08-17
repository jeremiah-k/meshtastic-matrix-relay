"""MMRelay integration policy for mindroom-nio cross-signing features."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from aiohttp import ClientConnectionError

import mmrelay.matrix.e2ee_identity as e2ee_identity
import mmrelay.matrix_utils as matrix_utils
from tests.constants import TEST_LOGIN_CREDENTIAL, TEST_MATRIX_SESSION_CREDENTIAL


class _CrossSigningClient:
    cross_signing_identity = object()

    def __init__(self, result: str = "already_signed") -> None:
        self.device_id = "MMRELAYDEVICE"
        self.result = result
        self.passwords: list[str | None] = []

    async def ensure_cross_signing(self, password: str | None = None) -> str:
        self.passwords.append(password)
        return self.result


class _FailingCrossSigningClient:
    cross_signing_identity = object()
    device_id = "MMRELAYDEVICE"

    async def ensure_cross_signing(self, password: str | None = None) -> str:
        del password
        raise RuntimeError("homeserver rejected signing")


class _CancelledCrossSigningClient:
    cross_signing_identity = object()
    device_id = "MMRELAYDEVICE"

    async def ensure_cross_signing(self, password: str | None = None) -> str:
        del password
        raise asyncio.CancelledError


class _DisconnectedCrossSigningClient:
    cross_signing_identity = object()
    device_id = "MMRELAYDEVICE"

    async def ensure_cross_signing(self, password: str | None = None) -> str:
        del password
        raise ClientConnectionError("homeserver disconnected")


class _UnexpectedProviderError(Exception):
    """Provider failure outside the previous hard-coded exception tuple."""


class _UnexpectedFailureClient:
    cross_signing_identity = object()
    device_id = "MMRELAYDEVICE"

    async def ensure_cross_signing(self, password: str | None = None) -> str:
        del password
        raise _UnexpectedProviderError("unexpected provider failure")


class _HangingCrossSigningClient:
    cross_signing_identity = object()
    device_id = "MMRELAYDEVICE"

    async def ensure_cross_signing(self, password: str | None = None) -> str:
        del password
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _BrokenIdentityPropertyClient(_CrossSigningClient):
    device_id = "MMRELAYDEVICE"

    @property
    def cross_signing_identity(self) -> None:
        raise _UnexpectedProviderError("identity getter failed")


class _QueryResponse:
    status = 200

    def __init__(self, *, user_id: str, has_master: bool) -> None:
        self.user_id = user_id
        self.has_master = has_master

    async def json(self, *, content_type: object = None) -> dict[str, object]:
        del content_type
        master_keys: dict[str, object] = (
            {self.user_id: {"keys": {}}} if self.has_master else {}
        )
        return {"master_keys": master_keys}


class _GuardedCrossSigningClient(_CrossSigningClient):
    user_id = "@bot:example.org"
    access_token = TEST_MATRIX_SESSION_CREDENTIAL
    device_id = "MMRELAYDEVICE"

    @property
    def cross_signing_identity(self) -> None:
        return None

    def __init__(self, *, has_master: bool) -> None:
        super().__init__("uploaded_and_signed")
        self.has_master = has_master
        self.query_calls = 0

    async def send(
        self, method: str, path: str, data: str, headers: dict[str, str]
    ) -> _QueryResponse:
        assert method == "POST"
        assert path == "/_matrix/client/v3/keys/query"
        assert self.user_id in data
        assert headers["Authorization"] == f"Bearer {TEST_MATRIX_SESSION_CREDENTIAL}"
        self.query_calls += 1
        return _QueryResponse(user_id=self.user_id, has_master=self.has_master)


class _NoIdentityPropertyClient:
    user_id = "@bot:example.org"
    access_token = TEST_MATRIX_SESSION_CREDENTIAL
    device_id = "MMRELAYDEVICE"

    def __init__(self, *, has_master: bool) -> None:
        self.has_master = has_master
        self.query_calls = 0
        self.passwords: list[str | None] = []

    async def ensure_cross_signing(self, password: str | None = None) -> str:
        self.passwords.append(password)
        return "uploaded_and_signed"

    async def send(
        self, method: str, path: str, data: str, headers: dict[str, str]
    ) -> _QueryResponse:
        del method, path, data, headers
        self.query_calls += 1
        return _QueryResponse(user_id=self.user_id, has_master=self.has_master)


class _BrokenGuardedCrossSigningClient(_GuardedCrossSigningClient):
    async def send(
        self, method: str, path: str, data: str, headers: dict[str, str]
    ) -> _QueryResponse:
        del method, path, data, headers
        self.query_calls += 1
        raise RuntimeError("keys query unavailable")


class _HangingGuardedCrossSigningClient(_GuardedCrossSigningClient):
    async def send(
        self, method: str, path: str, data: str, headers: dict[str, str]
    ) -> _QueryResponse:
        del method, path, data, headers
        self.query_calls += 1
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected_log_fragment"),
    [
        ("uploaded_and_signed", "Created Matrix cross-signing identity"),
        ("device_signed", "Self-verified Matrix device"),
        ("already_signed", "already self-verified"),
    ],
)
async def test_cross_signing_bootstrap_is_idempotent_and_reports_status(
    monkeypatch: pytest.MonkeyPatch,
    result: str,
    expected_log_fragment: str,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)
    client = _CrossSigningClient(result)

    observed = await matrix_utils._ensure_own_device_cross_signed(
        client,
        password=TEST_LOGIN_CREDENTIAL,
    )

    assert observed == result
    assert client.passwords == [TEST_LOGIN_CREDENTIAL]
    log_calls = [*logger.info.call_args_list, *logger.debug.call_args_list]
    assert any(expected_log_fragment in str(call.args[0]) for call in log_calls)


@pytest.mark.asyncio
async def test_cross_signing_failure_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)

    result = await matrix_utils._ensure_own_device_cross_signed(
        _FailingCrossSigningClient(),
        password=None,
    )

    assert result is None
    assert any(
        "Could not self-verify Matrix device" in str(call.args[0])
        for call in logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_unexpected_cross_signing_failure_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)

    result = await matrix_utils._ensure_own_device_cross_signed(
        _UnexpectedFailureClient(),
    )

    assert result is None
    assert any(
        "unexpected provider failure" in str(call.args)
        for call in logger.warning.call_args_list
    )
    logger.debug.assert_called()


@pytest.mark.asyncio
async def test_cross_signing_identity_getter_failure_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)
    client = _BrokenIdentityPropertyClient()

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result is None
    assert client.passwords == []
    assert any(
        "Refusing to generate a replacement identity automatically" in str(call.args[0])
        for call in logger.warning.call_args_list
    )
    logger.debug.assert_called()


@pytest.mark.asyncio
async def test_cross_signing_transport_failure_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)

    result = await matrix_utils._ensure_own_device_cross_signed(
        _DisconnectedCrossSigningClient(),
    )

    assert result is None
    assert any(
        "homeserver disconnected" in str(call.args)
        for call in logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_cross_signing_cancellation_propagates() -> None:
    with pytest.raises(asyncio.CancelledError):
        await matrix_utils._ensure_own_device_cross_signed(
            _CancelledCrossSigningClient(),
        )


@pytest.mark.asyncio
async def test_cross_signing_bootstrap_timeout_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)
    monkeypatch.setattr(
        e2ee_identity,
        "_CROSS_SIGNING_OPERATION_TIMEOUT_SECONDS",
        0.001,
    )

    result = await matrix_utils._ensure_own_device_cross_signed(
        _HangingCrossSigningClient()
    )

    assert result is None
    assert any(
        "Timed out" in str(call.args[0]) for call in logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_server_identity_precheck_timeout_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)
    monkeypatch.setattr(
        e2ee_identity,
        "_CROSS_SIGNING_OPERATION_TIMEOUT_SECONDS",
        0.001,
    )
    client = _HangingGuardedCrossSigningClient(has_master=False)

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result is None
    assert client.query_calls == 1
    assert client.passwords == []
    assert any(
        "confirming Matrix cross-signing state" in str(call.args[0])
        for call in logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_missing_sidecar_does_not_replace_server_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)
    client = _GuardedCrossSigningClient(has_master=True)

    result = await matrix_utils._ensure_own_device_cross_signed(
        client,
        password=TEST_LOGIN_CREDENTIAL,
    )

    assert result is None
    assert client.query_calls == 1
    assert client.passwords == []
    assert any(
        "existing identity was preserved" in str(call.args[0])
        for call in logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_missing_sidecar_can_replace_server_identity_with_explicit_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An authenticated opt-in should recover without logging out the device."""
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)
    client = _GuardedCrossSigningClient(has_master=True)

    result = await matrix_utils._ensure_own_device_cross_signed(
        client,
        password=TEST_LOGIN_CREDENTIAL,
        reset_cross_signing=True,
    )

    assert result == "uploaded_and_signed"
    assert client.query_calls == 1
    assert client.passwords == [TEST_LOGIN_CREDENTIAL]


@pytest.mark.asyncio
async def test_missing_sidecar_reset_requires_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity replacement must remain unavailable to unattended startup."""
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)
    client = _GuardedCrossSigningClient(has_master=True)

    result = await matrix_utils._ensure_own_device_cross_signed(
        client,
        reset_cross_signing=True,
    )

    assert result is None
    assert client.query_calls == 1
    assert client.passwords == []
    assert any(
        "without password authentication" in str(call.args[0])
        for call in logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_provider_without_identity_property_preserves_server_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)
    client = _NoIdentityPropertyClient(has_master=True)

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result is None
    assert client.query_calls == 1
    assert client.passwords == []
    assert any(
        "existing identity was preserved" in str(call.args[0])
        for call in logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_missing_sidecar_fails_closed_when_server_state_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)
    client = _BrokenGuardedCrossSigningClient(has_master=False)

    result = await matrix_utils._ensure_own_device_cross_signed(
        client,
        password=TEST_LOGIN_CREDENTIAL,
    )

    assert result is None
    assert client.query_calls == 1
    assert client.passwords == []
    assert any(
        "Refusing to generate a replacement identity automatically" in str(call.args[0])
        for call in logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_missing_sidecar_bootstraps_when_server_has_no_identity() -> None:
    client = _GuardedCrossSigningClient(has_master=False)

    result = await matrix_utils._ensure_own_device_cross_signed(
        client,
        password=TEST_LOGIN_CREDENTIAL,
    )

    assert result == "uploaded_and_signed"
    assert client.query_calls == 1
    assert client.passwords == [TEST_LOGIN_CREDENTIAL]


@pytest.mark.asyncio
async def test_provider_without_cross_signing_logs_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)

    result = await matrix_utils._ensure_own_device_cross_signed(object())

    assert result is None
    assert any(
        "does not support automatic device self-verification" in str(call.args[0])
        for call in logger.warning.call_args_list
    )


class _RaisingAttributeClient:
    @property
    def device_id(self) -> str:
        raise RuntimeError("device id unavailable")


class _EnsureGetterFailureClient:
    device_id = "MMRELAYDEVICE"

    @property
    def ensure_cross_signing(self) -> object:
        raise RuntimeError("capability getter failed")


class _EnsureGetterCancelledClient:
    device_id = "MMRELAYDEVICE"

    @property
    def ensure_cross_signing(self) -> object:
        raise asyncio.CancelledError


class _IdentityGetterCancelledClient(_CrossSigningClient):
    @property
    def cross_signing_identity(self) -> None:
        raise asyncio.CancelledError


class _TextResponse:
    def __init__(
        self,
        *,
        status: int,
        payload: object = None,
        error: Exception | None = None,
    ) -> None:
        self.status = status
        self.payload = payload
        self.error = error

    async def text(self) -> str:
        return "homeserver unavailable"

    async def json(self, *, content_type: object = None) -> object:
        del content_type
        if self.error is not None:
            raise self.error
        return self.payload


class _RawQueryClient:
    user_id = "@bot:example.org"
    access_token = TEST_MATRIX_SESSION_CREDENTIAL

    def __init__(self, response: _TextResponse) -> None:
        self.response = response

    async def send(
        self, method: str, path: str, data: str, headers: dict[str, str]
    ) -> _TextResponse:
        del method, path, data, headers
        return self.response


def test_client_label_hides_attribute_getter_failures() -> None:
    assert (
        e2ee_identity._client_label(_RaisingAttributeClient(), "device_id")
        == "<unknown>"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client", "message"),
    [
        (object(), "user id is unavailable"),
        (
            type("NoTokenClient", (), {"user_id": "@bot:example.org"})(),
            "access token is unavailable",
        ),
        (
            type(
                "NoSendClient",
                (),
                {
                    "user_id": "@bot:example.org",
                    "access_token": TEST_MATRIX_SESSION_CREDENTIAL,
                },
            )(),
            "does not expose an authenticated send method",
        ),
    ],
)
async def test_server_identity_query_requires_authenticated_client_surface(
    client: object,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        await e2ee_identity._server_has_own_cross_signing_identity(client)


@pytest.mark.asyncio
async def test_server_identity_query_reports_non_success_response() -> None:
    client = _RawQueryClient(_TextResponse(status=503))

    with pytest.raises(RuntimeError, match="503 homeserver unavailable"):
        await e2ee_identity._server_has_own_cross_signing_identity(client)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("bad json"), TypeError("bad json")])
async def test_server_identity_query_rejects_invalid_json(error: Exception) -> None:
    client = _RawQueryClient(_TextResponse(status=200, error=error))

    with pytest.raises(RuntimeError, match="returned invalid JSON"):
        await e2ee_identity._server_has_own_cross_signing_identity(client)


@pytest.mark.asyncio
async def test_server_identity_query_rejects_non_object_json() -> None:
    client = _RawQueryClient(_TextResponse(status=200, payload=[]))

    with pytest.raises(RuntimeError, match="non-object response"):
        await e2ee_identity._server_has_own_cross_signing_identity(client)


@pytest.mark.asyncio
async def test_cross_signing_capability_getter_failure_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)

    result = await matrix_utils._ensure_own_device_cross_signed(
        _EnsureGetterFailureClient()
    )

    assert result is None
    assert any(
        "Could not inspect Matrix cross-signing support" in str(call.args[0])
        for call in logger.warning.call_args_list
    )
    logger.debug.assert_called_once()


@pytest.mark.asyncio
async def test_cross_signing_capability_getter_cancellation_propagates() -> None:
    with pytest.raises(asyncio.CancelledError):
        await matrix_utils._ensure_own_device_cross_signed(
            _EnsureGetterCancelledClient()
        )


@pytest.mark.asyncio
async def test_cross_signing_identity_getter_cancellation_propagates() -> None:
    with pytest.raises(asyncio.CancelledError):
        await matrix_utils._ensure_own_device_cross_signed(
            _IdentityGetterCancelledClient()
        )


@pytest.mark.asyncio
async def test_server_identity_query_cancellation_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _GuardedCrossSigningClient(has_master=False)

    async def cancelled_query(_client: object) -> bool:
        raise asyncio.CancelledError

    monkeypatch.setattr(
        e2ee_identity,
        "_server_has_own_cross_signing_identity",
        cancelled_query,
    )

    with pytest.raises(asyncio.CancelledError):
        await matrix_utils._ensure_own_device_cross_signed(client)


@pytest.mark.asyncio
async def test_unexpected_cross_signing_result_is_not_treated_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)

    result = await matrix_utils._ensure_own_device_cross_signed(
        _CrossSigningClient("future-provider-result")
    )

    assert result is None
    assert any(
        "unexpected cross-signing result" in str(call.args[0])
        for call in logger.warning.call_args_list
    )


class _ServerVisibleIdentity:
    master_public_key = "MASTERKEY"
    self_signing_public_key = "SELFSIGNINGKEY"

    def self_signing_key_payload(self) -> dict[str, object]:
        return {
            "user_id": "@bot:example.org",
            "usage": ["self_signing"],
            "keys": {"ed25519:SELFSIGNINGKEY": "SELFSIGNINGKEY"},
            "signatures": {"@bot:example.org": {"ed25519:MASTERKEY": "master-sig"}},
        }

    def signed_device_payload(
        self, device_keys: dict[str, object]
    ) -> dict[str, object]:
        signed = dict(device_keys)
        signed["signatures"] = {
            "@bot:example.org": {"ed25519:SELFSIGNINGKEY": "device-sig"}
        }
        return signed


class _ServerVisibleQueryResponse:
    status = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def json(self, *, content_type: object = None) -> dict[str, object]:
        del content_type
        return self.payload

    async def text(self) -> str:
        return ""


class _ServerVisibleCrossSigningClient:
    user_id = "@bot:example.org"
    access_token = TEST_MATRIX_SESSION_CREDENTIAL
    device_id = "MMRELAYDEVICE"

    def __init__(
        self,
        *,
        result: str = "already_signed",
        device_signed: bool = True,
        matching_master: bool = True,
        matching_self_signing: bool = True,
        include_device: bool = True,
        device_user_id: str | None = None,
        device_id_field: str | None = None,
        master_signature: str = "master-sig",
        device_signature: str = "device-sig",
        query_error: BaseException | None = None,
        upload_error: BaseException | None = None,
        repair_changes_signature: bool = True,
    ) -> None:
        self.result = result
        self.device_signed = device_signed
        self.matching_master = matching_master
        self.matching_self_signing = matching_self_signing
        self.include_device = include_device
        self.device_user_id = device_user_id or self.user_id
        self.device_id_field = device_id_field or self.device_id
        self.master_signature = master_signature
        self.device_signature = device_signature
        self.query_error = query_error
        self.upload_error = upload_error
        self.repair_changes_signature = repair_changes_signature
        self.identity = _ServerVisibleIdentity()
        self.query_calls = 0
        self.signature_upload_calls = 0

    @property
    def cross_signing_identity(self) -> _ServerVisibleIdentity:
        return self.identity

    async def ensure_cross_signing(self, password: str | None = None) -> str:
        del password
        return self.result

    async def _upload_own_device_signature(self, identity: object) -> None:
        assert identity is self.identity
        self.signature_upload_calls += 1
        if self.upload_error is not None:
            raise self.upload_error
        if self.repair_changes_signature:
            self.device_signed = True
            self.device_signature = "device-sig"

    async def send(
        self, method: str, path: str, data: str, headers: dict[str, str]
    ) -> _ServerVisibleQueryResponse:
        assert method == "POST"
        assert path == "/_matrix/client/v3/keys/query"
        assert self.device_id in data
        assert headers["Authorization"] == f"Bearer {TEST_MATRIX_SESSION_CREDENTIAL}"
        self.query_calls += 1
        if self.query_error is not None:
            raise self.query_error
        master_key = (
            self.identity.master_public_key if self.matching_master else "OTHERMASTER"
        )
        self_signing_key = (
            self.identity.self_signing_public_key
            if self.matching_self_signing
            else "OTHERSELFSIGNING"
        )
        signatures: dict[str, object] = {}
        if self.device_signed:
            signatures = {
                self.user_id: {
                    f"ed25519:{self.identity.self_signing_public_key}": (
                        self.device_signature
                    )
                }
            }
        return _ServerVisibleQueryResponse(
            {
                "master_keys": {
                    self.user_id: {
                        "user_id": self.user_id,
                        "usage": ["master"],
                        "keys": {f"ed25519:{master_key}": master_key},
                    }
                },
                "self_signing_keys": {
                    self.user_id: {
                        "user_id": self.user_id,
                        "usage": ["self_signing"],
                        "keys": {f"ed25519:{self_signing_key}": self_signing_key},
                        "signatures": {
                            self.user_id: {
                                f"ed25519:{self.identity.master_public_key}": (
                                    self.master_signature
                                )
                            }
                        },
                    }
                },
                "device_keys": {
                    self.user_id: (
                        {
                            self.device_id: {
                                "user_id": self.device_user_id,
                                "device_id": self.device_id_field,
                                "keys": {f"ed25519:{self.device_id}": "device-key"},
                                "signatures": signatures,
                            }
                        }
                        if self.include_device
                        else {}
                    )
                },
            }
        )


@pytest.mark.asyncio
async def test_already_signed_refreshes_server_device_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)
    client = _ServerVisibleCrossSigningClient(device_signed=True)

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result == "already_signed"
    assert client.query_calls == 1
    assert client.signature_upload_calls == 1
    assert any(
        "Confirmed server-visible Matrix self-signing" in str(call.args[0])
        for call in logger.info.call_args_list
    )


@pytest.mark.asyncio
async def test_stale_sidecar_repairs_missing_server_device_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)
    client = _ServerVisibleCrossSigningClient(device_signed=False)

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result == "already_signed"
    assert client.query_calls == 2
    assert client.signature_upload_calls == 1
    assert any(
        "Repaired server-visible Matrix self-signing" in str(call.args[0])
        for call in logger.info.call_args_list
    )


@pytest.mark.asyncio
async def test_new_device_signature_is_verified_without_duplicate_upload() -> None:
    client = _ServerVisibleCrossSigningClient(
        result="device_signed",
        device_signed=True,
    )

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result == "device_signed"
    assert client.query_calls == 1
    assert client.signature_upload_calls == 0


@pytest.mark.asyncio
async def test_mismatched_server_identity_fails_closed_without_signature_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)
    client = _ServerVisibleCrossSigningClient(matching_master=False)

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result is None
    assert client.query_calls == 1
    assert client.signature_upload_calls == 0
    assert any(
        "Refusing to replace cross-signing identity material" in str(call.args[0])
        for call in logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_invalid_device_signature_is_repaired_not_just_key_id_checked() -> None:
    client = _ServerVisibleCrossSigningClient(device_signature="invalid-signature")

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result == "already_signed"
    assert client.query_calls == 2
    assert client.signature_upload_calls == 1
    assert client.device_signature == "device-sig"


@pytest.mark.asyncio
async def test_invalid_master_signature_fails_closed_without_device_repair() -> None:
    client = _ServerVisibleCrossSigningClient(master_signature="invalid-signature")

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result is None
    assert client.query_calls == 1
    assert client.signature_upload_calls == 0


class _BrokenVerificationIdentity:
    @property
    def master_public_key(self) -> str:
        raise RuntimeError("public key unavailable")


class _BrokenUploadGetterClient:
    @property
    def _upload_own_device_signature(self) -> object:
        raise RuntimeError("upload helper unavailable")


class _SecondIdentityReadFailureClient(_ServerVisibleCrossSigningClient):
    def __init__(self) -> None:
        self.identity_reads = 0
        super().__init__()

    @property
    def cross_signing_identity(self) -> _ServerVisibleIdentity:
        self.identity_reads += 1
        if self.identity_reads > 1:
            raise RuntimeError("identity reload failed")
        return self.identity


@pytest.mark.asyncio
async def test_own_key_query_rejects_homeserver_failures() -> None:
    client = _RawQueryClient(
        _TextResponse(
            status=200,
            payload={"failures": {"example.org": {"errcode": "M_UNKNOWN"}}},
        )
    )

    with pytest.raises(RuntimeError, match="reported homeserver failures"):
        await e2ee_identity._query_own_keys(client, device_ids=[])


def test_nested_dict_stops_at_non_mapping_value() -> None:
    assert e2ee_identity._nested_dict({"outer": "not-a-map"}, "outer", "inner") is None


def test_verifiable_identity_hides_provider_property_failures() -> None:
    assert (
        e2ee_identity._verifiable_cross_signing_identity(_BrokenVerificationIdentity())
        is None
    )


@pytest.mark.asyncio
async def test_server_cross_signing_status_requires_current_device_id() -> None:
    client = _RawQueryClient(_TextResponse(status=200, payload={}))
    identity = _ServerVisibleIdentity()

    with pytest.raises(RuntimeError, match="device id is unavailable"):
        await e2ee_identity._server_own_device_cross_signing_status(
            client,
            identity,
        )


@pytest.mark.asyncio
async def test_mismatched_self_signing_key_fails_closed() -> None:
    client = _ServerVisibleCrossSigningClient(matching_self_signing=False)

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result is None
    assert client.query_calls == 1
    assert client.signature_upload_calls == 0


@pytest.mark.asyncio
async def test_missing_current_device_keys_are_not_repaired_blindly() -> None:
    client = _ServerVisibleCrossSigningClient(include_device=False)

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result is None
    assert client.query_calls == 1
    assert client.signature_upload_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("device_user_id", "device_id_field"),
    [
        ("@other:example.org", "MMRELAYDEVICE"),
        ("@bot:example.org", "OTHERDEVICE"),
    ],
)
async def test_inconsistent_current_device_keys_are_not_signed(
    device_user_id: str,
    device_id_field: str,
) -> None:
    client = _ServerVisibleCrossSigningClient(
        device_user_id=device_user_id,
        device_id_field=device_id_field,
    )

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result is None
    assert client.signature_upload_calls == 0


@pytest.mark.asyncio
async def test_signature_republish_handles_missing_or_broken_provider_hook() -> None:
    assert not await e2ee_identity._republish_own_device_signature(object(), object())
    assert not await e2ee_identity._republish_own_device_signature(
        _BrokenUploadGetterClient(),
        object(),
    )


@pytest.mark.asyncio
async def test_cache_refresh_failure_does_not_invalidate_verified_server_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)
    client = _ServerVisibleCrossSigningClient(
        upload_error=RuntimeError("refresh unavailable")
    )

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result == "already_signed"
    assert client.query_calls == 1
    assert client.signature_upload_calls == 1
    assert any(
        "could not refresh its device-signature notification" in str(call.args[0])
        for call in logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_missing_signature_repair_upload_failure_is_nonfatal() -> None:
    client = _ServerVisibleCrossSigningClient(
        device_signed=False,
        upload_error=RuntimeError("signature upload unavailable"),
    )

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result is None
    assert client.query_calls == 1
    assert client.signature_upload_calls == 1


@pytest.mark.asyncio
async def test_missing_signature_repair_requires_verified_postcondition() -> None:
    client = _ServerVisibleCrossSigningClient(
        device_signed=False,
        repair_changes_signature=False,
    )

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result is None
    assert client.query_calls == 2
    assert client.signature_upload_calls == 1


@pytest.mark.asyncio
async def test_successful_provider_result_fails_if_identity_cannot_be_reloaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)
    client = _SecondIdentityReadFailureClient()

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result is None
    assert client.identity_reads == 2
    assert any(
        "could not reload its local identity" in str(call.args[0])
        for call in logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_postcondition_query_failure_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)
    client = _ServerVisibleCrossSigningClient(query_error=RuntimeError("query failed"))

    result = await matrix_utils._ensure_own_device_cross_signed(client)

    assert result is None
    assert any(
        "could not verify the server-visible identity chain" in str(call.args[0])
        for call in logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_postcondition_query_cancellation_propagates() -> None:
    client = _ServerVisibleCrossSigningClient(query_error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await matrix_utils._ensure_own_device_cross_signed(client)


@pytest.mark.asyncio
async def test_verified_signature_refresh_cancellation_propagates() -> None:
    client = _ServerVisibleCrossSigningClient(upload_error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await matrix_utils._ensure_own_device_cross_signed(client)


@pytest.mark.asyncio
async def test_missing_signature_repair_cancellation_propagates() -> None:
    client = _ServerVisibleCrossSigningClient(
        device_signed=False,
        upload_error=asyncio.CancelledError(),
    )

    with pytest.raises(asyncio.CancelledError):
        await matrix_utils._ensure_own_device_cross_signed(client)
