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
