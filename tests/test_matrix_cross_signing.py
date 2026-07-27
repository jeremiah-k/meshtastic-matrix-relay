"""MMRelay integration policy for mindroom-nio cross-signing features."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from aiohttp import ClientConnectionError

import mmrelay.matrix.e2ee_identity as e2ee_identity
import mmrelay.matrix_utils as matrix_utils


class _CrossSigningClient:
    def __init__(self, result: str = "already_signed") -> None:
        self.device_id = "MMRELAYDEVICE"
        self.result = result
        self.passwords: list[str | None] = []

    async def ensure_cross_signing(self, password: str | None = None) -> str:
        self.passwords.append(password)
        return self.result


class _FailingCrossSigningClient:
    device_id = "MMRELAYDEVICE"

    async def ensure_cross_signing(self, password: str | None = None) -> str:
        del password
        raise RuntimeError("homeserver rejected signing")


class _CancelledCrossSigningClient:
    device_id = "MMRELAYDEVICE"

    async def ensure_cross_signing(self, password: str | None = None) -> str:
        del password
        raise asyncio.CancelledError


class _DisconnectedCrossSigningClient:
    device_id = "MMRELAYDEVICE"

    async def ensure_cross_signing(self, password: str | None = None) -> str:
        del password
        raise ClientConnectionError("homeserver disconnected")


class _UnexpectedProviderError(Exception):
    """Provider failure outside the previous hard-coded exception tuple."""


class _UnexpectedFailureClient:
    device_id = "MMRELAYDEVICE"

    async def ensure_cross_signing(self, password: str | None = None) -> str:
        del password
        raise _UnexpectedProviderError("unexpected provider failure")


class _HangingCrossSigningClient:
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
    access_token = "token"
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
        assert headers["Authorization"] == "Bearer token"
        self.query_calls += 1
        return _QueryResponse(user_id=self.user_id, has_master=self.has_master)


class _BrokenGuardedCrossSigningClient(_GuardedCrossSigningClient):
    async def send(
        self, method: str, path: str, data: str, headers: dict[str, str]
    ) -> _QueryResponse:
        del method, path, data, headers
        self.query_calls += 1
        raise RuntimeError("keys query unavailable")


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
        password="secret",
    )

    assert observed == result
    assert client.passwords == ["secret"]
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
        "_CROSS_SIGNING_BOOTSTRAP_TIMEOUT_SECONDS",
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
async def test_missing_sidecar_does_not_replace_server_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(e2ee_identity, "logger", logger)
    client = _GuardedCrossSigningClient(has_master=True)

    result = await matrix_utils._ensure_own_device_cross_signed(
        client,
        password="secret",
    )

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
        password="secret",
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
        password="secret",
    )

    assert result == "uploaded_and_signed"
    assert client.query_calls == 1
    assert client.passwords == ["secret"]


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
    access_token = "token"

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
                {"user_id": "@bot:example.org", "access_token": "token"},
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
