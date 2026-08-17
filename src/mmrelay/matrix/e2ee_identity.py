"""MMRelay policy for Matrix E2EE device identity and cross-signing."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Final, Protocol, cast

from mmrelay.log_utils import get_logger

__all__ = [
    "_ensure_own_device_cross_signed",
]

logger = get_logger(name="Matrix")

_CROSS_SIGNING_UPLOADED_AND_SIGNED: Final = "uploaded_and_signed"
_CROSS_SIGNING_DEVICE_SIGNED: Final = "device_signed"
_CROSS_SIGNING_ALREADY_SIGNED: Final = "already_signed"
_CROSS_SIGNING_OPERATION_TIMEOUT_SECONDS: Final[float] = 120.0


class _MatrixHttpResponse(Protocol):
    """Response surface used by the raw Matrix identity query."""

    status: int

    async def json(self, *, content_type: object = None) -> object: ...

    async def text(self) -> str: ...


class _VerifiableCrossSigningIdentity(Protocol):
    """mindroom-nio identity surface used to verify the exact Matrix chain."""

    master_public_key: str
    self_signing_public_key: str

    def self_signing_key_payload(self) -> dict[str, object]: ...

    def signed_device_payload(
        self, device_keys: dict[str, object]
    ) -> dict[str, object]: ...


_SendRequest = Callable[[str, str, str, dict[str, str]], Awaitable[_MatrixHttpResponse]]
_EnsureCrossSigning = Callable[..., Awaitable[str]]
_UploadOwnDeviceSignature = Callable[[object], Awaitable[None]]


def _summarize_keys_query_failures(failures: dict[object, object]) -> str:
    """Return a bounded diagnostic summary of Matrix keys/query failures."""
    summaries: list[str] = []
    for server, detail in list(failures.items())[:5]:
        server_label = str(server).replace("\r", "\\r").replace("\n", "\\n")[:80]
        errcode = detail.get("errcode") if isinstance(detail, dict) else None
        if isinstance(errcode, str) and errcode:
            errcode_label = errcode.replace("\r", "\\r").replace("\n", "\\n")[:64]
            summaries.append(f"{server_label} ({errcode_label})")
        else:
            summaries.append(server_label)
    if len(failures) > 5:
        summaries.append(f"+{len(failures) - 5} more")
    return ", ".join(summaries)[:300]


def _client_label(client: object, attribute: str) -> str:
    """Return a diagnostic client attribute without allowing getters to escape."""
    try:
        value = getattr(client, attribute, None)
    except Exception:  # noqa: BLE001 - diagnostic getters are provider-owned
        return "<unknown>"
    return str(value) if value else "<unknown>"


async def _query_own_keys(
    client: object,
    *,
    device_ids: list[str],
) -> tuple[str, dict[str, object]]:
    """Query this account's cross-signing keys and selected device keys."""
    user_id = getattr(client, "user_id", None)
    access_token = getattr(client, "access_token", None)
    send = getattr(client, "send", None)
    if not isinstance(user_id, str) or not user_id:
        raise RuntimeError("Matrix user id is unavailable for cross-signing query")
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("Matrix access token is unavailable for cross-signing query")
    if not callable(send):
        raise RuntimeError(
            "Matrix provider does not expose an authenticated send method"
        )

    response = await cast(_SendRequest, send)(
        "POST",
        "/_matrix/client/v3/keys/query",
        json.dumps({"device_keys": {user_id: device_ids}}, separators=(",", ":")),
        {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
    if response.status != 200:
        detail = await response.text()
        raise RuntimeError(
            f"Matrix keys/query failed: {response.status} {detail[:300]}"
        )
    try:
        payload = await response.json(content_type=None)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Matrix keys/query returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Matrix keys/query returned a non-object response")
    failures = payload.get("failures")
    if isinstance(failures, dict) and failures:
        summary = _summarize_keys_query_failures(failures)
        raise RuntimeError(
            f"Matrix keys/query reported homeserver failures: {summary}"
        )
    return user_id, cast(dict[str, object], payload)


async def _server_has_own_cross_signing_identity(client: object) -> bool:
    """Return whether Matrix already stores a master key for this account.

    A missing local cross-signing sidecar must not silently rotate an existing
    server identity. Query the public key state before allowing mindroom-nio to
    generate a new bot identity.
    """
    user_id, payload = await _query_own_keys(client, device_ids=[])
    master_keys = payload.get("master_keys")
    return isinstance(master_keys, dict) and isinstance(master_keys.get(user_id), dict)


def _nested_dict(container: object, *keys: str) -> dict[str, object] | None:
    """Return a nested JSON object, or ``None`` when any level is absent."""
    current = container
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return cast(dict[str, object], current) if isinstance(current, dict) else None


def _signature_value(
    payload: dict[str, object] | None,
    *,
    user_id: str,
    key_id: str,
) -> str | None:
    """Return one Matrix signature string from a signed JSON object."""
    signatures = _nested_dict(payload, "signatures", user_id)
    if signatures is None:
        return None
    signature = signatures.get(key_id)
    return signature if isinstance(signature, str) and signature else None


def _verifiable_cross_signing_identity(
    identity: object,
) -> _VerifiableCrossSigningIdentity | None:
    """Return mindroom-nio's verification surface when it is available."""
    try:
        master = getattr(identity, "master_public_key", None)
        self_signing = getattr(identity, "self_signing_public_key", None)
        self_signing_payload = getattr(identity, "self_signing_key_payload", None)
        signed_device_payload = getattr(identity, "signed_device_payload", None)
    except Exception:  # noqa: BLE001 - provider object boundary
        return None
    if not (
        isinstance(master, str)
        and master
        and isinstance(self_signing, str)
        and self_signing
        and callable(self_signing_payload)
        and callable(signed_device_payload)
    ):
        return None
    return cast(_VerifiableCrossSigningIdentity, identity)


async def _server_own_device_cross_signing_status(
    client: object,
    identity: _VerifiableCrossSigningIdentity,
) -> tuple[bool, bool, str]:
    """Check the server-visible master -> self-signing -> current-device chain.

    Returns ``(valid, repairable_device_signature, reason)``. Signature values
    are reconstructed from MMRelay's persisted mindroom-nio identity and compared
    to the server response, mirroring the master -> self-signing -> device chain
    that Matrix clients evaluate.
    """
    device_id = getattr(client, "device_id", None)
    if not isinstance(device_id, str) or not device_id:
        raise RuntimeError("Matrix device id is unavailable for cross-signing query")
    user_id, payload = await _query_own_keys(client, device_ids=[device_id])

    master_public_key = identity.master_public_key
    self_signing_public_key = identity.self_signing_public_key
    master = _nested_dict(payload, "master_keys", user_id)
    master_key_id = f"ed25519:{master_public_key}"
    if (
        master is None
        or master.get("user_id") != user_id
        or master.get("usage") != ["master"]
        or _nested_dict(master, "keys") != {master_key_id: master_public_key}
    ):
        return False, False, "the server master key does not match MMRelay's identity"

    self_signing = _nested_dict(payload, "self_signing_keys", user_id)
    self_signing_key_id = f"ed25519:{self_signing_public_key}"
    if (
        self_signing is None
        or self_signing.get("user_id") != user_id
        or self_signing.get("usage") != ["self_signing"]
        or _nested_dict(self_signing, "keys")
        != {self_signing_key_id: self_signing_public_key}
    ):
        return (
            False,
            False,
            "the server self-signing key does not match the persisted identity",
        )
    expected_self_signing = identity.self_signing_key_payload()
    expected_master_signature = _signature_value(
        expected_self_signing,
        user_id=user_id,
        key_id=master_key_id,
    )
    observed_master_signature = _signature_value(
        self_signing,
        user_id=user_id,
        key_id=master_key_id,
    )
    if (
        expected_master_signature is None
        or observed_master_signature != expected_master_signature
    ):
        return (
            False,
            False,
            "the server self-signing key has an invalid master signature",
        )

    device = _nested_dict(payload, "device_keys", user_id, device_id)
    if device is None:
        return False, False, "the server does not expose keys for the current device"
    if device.get("user_id") != user_id or device.get("device_id") != device_id:
        return False, False, "the server returned inconsistent current-device keys"
    signable_device = {
        key: value
        for key, value in device.items()
        if key not in ("signatures", "unsigned")
    }
    expected_signed_device = identity.signed_device_payload(signable_device)
    expected_device_signature = _signature_value(
        expected_signed_device,
        user_id=user_id,
        key_id=self_signing_key_id,
    )
    observed_device_signature = _signature_value(
        device,
        user_id=user_id,
        key_id=self_signing_key_id,
    )
    if (
        expected_device_signature is None
        or observed_device_signature != expected_device_signature
    ):
        return (
            False,
            True,
            "the current device is missing a valid owner self-signing signature",
        )
    return True, False, "server-visible cross-signing chain is valid"


async def _republish_own_device_signature(client: object, identity: object) -> bool:
    """Re-publish mindroom-nio's signature over its *local* current-device keys.

    mindroom-nio 0.40 has no public force-refresh operation. This compatibility
    hook deliberately delegates payload construction to the provider's own upload
    helper, and is used only after the server master/self-signing public keys have
    been matched to the persisted local identity. It never rotates identity keys.
    """
    try:
        upload = getattr(client, "_upload_own_device_signature", None)
    except Exception:  # noqa: BLE001 - provider capability boundary
        return False
    if not callable(upload):
        return False
    await cast(_UploadOwnDeviceSignature, upload)(identity)
    return True


async def _confirm_server_visible_cross_signing(
    client: object,
    identity: object,
) -> bool:
    """Verify the server postcondition and repair a stale device signature safely."""
    verifiable_identity = _verifiable_cross_signing_identity(identity)
    if verifiable_identity is None:
        # Keep support for providers that implement ensure_cross_signing() but do
        # not expose mindroom-nio's diagnostic identity surface.
        return True

    valid, repairable, reason = await _server_own_device_cross_signing_status(
        client,
        verifiable_identity,
    )
    if valid:
        logger.info(
            "Confirmed server-visible Matrix self-signing for device %s",
            _client_label(client, "device_id"),
        )
        return True

    if not repairable:
        logger.warning(
            "Matrix device %s is not server-verifiably self-signed: %s. "
            "Refusing to replace cross-signing identity material automatically.",
            _client_label(client, "device_id"),
            reason,
        )
        return False

    try:
        refreshed = await _republish_own_device_signature(client, identity)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - provider upload boundary
        logger.warning(
            "Could not repair the Matrix self-signing signature for device %s: %s",
            _client_label(client, "device_id"),
            exc,
        )
        logger.debug("Matrix cross-signing signature repair failure", exc_info=True)
        return False
    if not refreshed:
        logger.warning(
            "Matrix device %s is missing its owner self-signing signature, and the "
            "active provider does not expose a safe signature-repair path.",
            _client_label(client, "device_id"),
        )
        return False

    valid, _repairable, reason = await _server_own_device_cross_signing_status(
        client,
        verifiable_identity,
    )
    if not valid:
        logger.warning(
            "Matrix device %s signature upload completed, but the server-visible "
            "cross-signing chain is still invalid: %s",
            _client_label(client, "device_id"),
            reason,
        )
        return False

    logger.info(
        "Repaired server-visible Matrix self-signing for device %s",
        _client_label(client, "device_id"),
    )
    return True


def _inspect_cross_signing_provider(
    client: object,
) -> tuple[_EnsureCrossSigning, object | None] | None:
    """Return the provider bootstrap callable and local identity when inspectable."""
    try:
        ensure_method = getattr(client, "ensure_cross_signing", None)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - provider capability boundary
        logger.warning(
            "Could not inspect Matrix cross-signing support for device %s: %s. "
            "MMRelay startup will continue, but clients enforcing cross-signing "
            "may withhold encrypted room keys.",
            _client_label(client, "device_id"),
            exc,
        )
        logger.debug(
            "Matrix cross-signing capability inspection failure", exc_info=True
        )
        return None

    if not callable(ensure_method):
        logger.warning(
            "The active Matrix provider does not support automatic device "
            "self-verification. MMRelay startup will continue, but clients "
            "enforcing cross-signing may withhold encrypted room keys."
        )
        return None

    try:
        local_identity = getattr(client, "cross_signing_identity", None)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - provider property boundary
        logger.warning(
            "Could not inspect the local Matrix cross-signing identity for device %s: "
            "%s. Refusing to generate a replacement identity automatically.",
            _client_label(client, "device_id"),
            exc,
        )
        logger.debug("Matrix cross-signing identity inspection failure", exc_info=True)
        return None

    return cast(_EnsureCrossSigning, ensure_method), local_identity


async def _resolve_cross_signing_reset_policy(
    client: object,
    *,
    local_identity: object | None,
    password: str | None,
    reset_cross_signing: bool,
) -> bool:
    """Decide whether bootstrap may proceed without rotating identity implicitly."""
    if local_identity is not None:
        return True

    try:
        server_has_identity = await _server_has_own_cross_signing_identity(client)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - provider request boundary
        logger.warning(
            "Could not confirm Matrix cross-signing state for device %s: %s. "
            "Refusing to generate a replacement identity automatically.",
            _client_label(client, "device_id"),
            exc,
        )
        logger.debug("Matrix cross-signing state query failure", exc_info=True)
        return False

    if not server_has_identity:
        return True
    if not reset_cross_signing:
        logger.warning(
            "Matrix already has a cross-signing identity for %s, but MMRelay's "
            "local cross-signing sidecar is missing. The existing identity was "
            "preserved; restore the E2EE store/sidecar or run 'mmrelay auth login "
            "--reset-cross-signing' to replace it explicitly.",
            _client_label(client, "user_id"),
        )
        return False
    if not password:
        logger.warning(
            "Refusing to reset the existing Matrix cross-signing identity for %s "
            "without password authentication.",
            _client_label(client, "user_id"),
        )
        return False

    logger.warning(
        "Replacing the existing Matrix cross-signing identity for %s because an "
        "authenticated reset was explicitly requested. Other Matrix clients may "
        "require identity verification again.",
        _client_label(client, "user_id"),
    )
    return True


async def _verify_cross_signing_postcondition(
    client: object,
    result: str,
) -> bool:
    """Validate a provider result and its server-visible self-signing postcondition."""
    device_id = _client_label(client, "device_id")
    if result not in {
        _CROSS_SIGNING_UPLOADED_AND_SIGNED,
        _CROSS_SIGNING_DEVICE_SIGNED,
        _CROSS_SIGNING_ALREADY_SIGNED,
    }:
        logger.warning(
            "Matrix provider returned an unexpected cross-signing result for "
            "device %s: %r",
            device_id,
            result,
        )
        return False

    # First use creates the mindroom-nio sidecar inside ensure_cross_signing(),
    # so reload it before checking the server-visible postcondition. Providers
    # without mindroom-nio's identity surface retain the historical best-effort
    # behavior.
    try:
        current_identity = getattr(client, "cross_signing_identity", None)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - provider property boundary
        logger.warning(
            "Matrix provider reported successful self-signing for device %s, but "
            "MMRelay could not reload its local identity: %s",
            device_id,
            exc,
        )
        logger.debug("Matrix post-bootstrap identity inspection failure", exc_info=True)
        return False

    try:
        return await _confirm_server_visible_cross_signing(client, current_identity)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - provider/server boundary
        logger.warning(
            "Matrix provider reported successful self-signing for device %s, but "
            "MMRelay could not verify the server-visible identity chain: %s",
            device_id,
            exc,
        )
        logger.debug(
            "Matrix cross-signing postcondition verification failure",
            exc_info=True,
        )
        return False


async def _ensure_own_device_cross_signed(
    client: object,
    *,
    password: str | None = None,
    reset_cross_signing: bool = False,
) -> str | None:
    """Attempt to cross-sign the bot's own Matrix device when supported.

    mindroom-nio provides a bot-scoped producer implementation that creates a
    master and self-signing key and signs only the current device. It does not
    verify other users. The operation is idempotent and deliberately non-fatal
    for MMRelay: startup can continue if a provider or homeserver rejects the
    bootstrap, while logs explain that enforcing clients may withhold room keys
    and how to retry with password UIA via ``mmrelay auth login``.

    ``reset_cross_signing`` is an explicit recovery path for a lost sidecar. It
    allows a password-authenticated login to replace server identity material;
    ordinary startup remains fail-closed. ``asyncio.CancelledError`` is always
    allowed to propagate.
    """
    provider = _inspect_cross_signing_provider(client)
    if provider is None:
        return None
    ensure_method, local_identity = provider

    timeout_phase = "server identity check"
    try:
        async with asyncio.timeout(_CROSS_SIGNING_OPERATION_TIMEOUT_SECONDS):
            if not await _resolve_cross_signing_reset_policy(
                client,
                local_identity=local_identity,
                password=password,
                reset_cross_signing=reset_cross_signing,
            ):
                return None

            timeout_phase = "bootstrap"
            try:
                result = await ensure_method(password=password)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - provider bootstrap boundary
                logger.warning(
                    "Could not self-verify Matrix device %s: %s. MMRelay startup will "
                    "continue, but clients enforcing cross-signing may withhold room "
                    "keys; run 'mmrelay auth login' to retry with password "
                    "authentication.",
                    _client_label(client, "device_id"),
                    exc,
                )
                logger.debug("Matrix cross-signing bootstrap failure", exc_info=True)
                return None

            timeout_phase = "postcondition verification"
            if not await _verify_cross_signing_postcondition(client, result):
                return None
    except asyncio.TimeoutError:
        if timeout_phase == "server identity check":
            logger.warning(
                "Timed out after %.0f seconds while confirming Matrix cross-signing "
                "state for device %s. Refusing to generate a replacement identity "
                "automatically.",
                _CROSS_SIGNING_OPERATION_TIMEOUT_SECONDS,
                _client_label(client, "device_id"),
            )
        else:
            logger.warning(
                "Timed out after %.0f seconds while self-verifying Matrix device %s. "
                "MMRelay startup will continue; run 'mmrelay auth login' to retry.",
                _CROSS_SIGNING_OPERATION_TIMEOUT_SECONDS,
                _client_label(client, "device_id"),
            )
        return None

    device_id = _client_label(client, "device_id")
    if result == _CROSS_SIGNING_UPLOADED_AND_SIGNED:
        logger.info(
            "Created Matrix cross-signing identity and self-verified device %s",
            device_id,
        )
    elif result == _CROSS_SIGNING_DEVICE_SIGNED:
        logger.info(
            "Self-verified Matrix device %s with the existing cross-signing identity",
            device_id,
        )
    else:
        logger.debug("Matrix device %s is already self-verified", device_id)
    return result
