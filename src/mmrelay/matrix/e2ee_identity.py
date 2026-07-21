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


class _MatrixHttpResponse(Protocol):
    """Response surface used by the raw Matrix identity query."""

    status: int

    async def json(self, *, content_type: object = None) -> object: ...

    async def text(self) -> str: ...


_SendRequest = Callable[[str, str, str, dict[str, str]], Awaitable[_MatrixHttpResponse]]
_EnsureCrossSigning = Callable[..., Awaitable[str]]


def _client_label(client: object, attribute: str) -> str:
    """Return a diagnostic client attribute without allowing getters to escape."""
    try:
        value = getattr(client, attribute, None)
    except Exception:
        return "<unknown>"
    return str(value) if value else "<unknown>"


async def _server_has_own_cross_signing_identity(client: object) -> bool:
    """Return whether Matrix already stores a master key for this account.

    A missing local cross-signing sidecar must not silently rotate an existing
    server identity. Query the public key state before allowing mindroom-nio to
    generate a new bot identity.
    """
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
        json.dumps({"device_keys": {user_id: []}}, separators=(",", ":")),
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
    master_keys = payload.get("master_keys")
    return isinstance(master_keys, dict) and isinstance(master_keys.get(user_id), dict)


async def _ensure_own_device_cross_signed(
    client: object,
    *,
    password: str | None = None,
) -> str | None:
    """Attempt to cross-sign the bot's own Matrix device when supported.

    mindroom-nio provides a bot-scoped producer implementation that creates a
    master and self-signing key and signs only the current device. It does not
    verify other users. The operation is idempotent and deliberately non-fatal
    for MMRelay: startup can continue if a provider or homeserver rejects the
    bootstrap, while logs explain that enforcing clients may withhold room keys
    and how to retry with password UIA via ``mmrelay auth login``.

    ``asyncio.CancelledError`` is always allowed to propagate.
    """
    try:
        ensure_method = getattr(client, "ensure_cross_signing", None)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
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

    # mindroom-nio owns cross-signing private keys in a local sidecar. When the
    # provider exposes that diagnostic property and the sidecar is absent,
    # refuse to replace an existing server identity automatically.
    try:
        identity_property = getattr(type(client), "cross_signing_identity", None)
        local_identity = (
            getattr(client, "cross_signing_identity", None)
            if identity_property is not None
            else None
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "Could not inspect the local Matrix cross-signing identity for device %s: "
            "%s. Refusing to generate a replacement identity automatically.",
            _client_label(client, "device_id"),
            exc,
        )
        logger.debug("Matrix cross-signing identity inspection failure", exc_info=True)
        return None

    if identity_property is not None and local_identity is None:
        try:
            server_has_identity = await _server_has_own_cross_signing_identity(client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Could not confirm Matrix cross-signing state for device %s: %s. "
                "Refusing to generate a replacement identity automatically.",
                _client_label(client, "device_id"),
                exc,
            )
            logger.debug("Matrix cross-signing state query failure", exc_info=True)
            return None
        if server_has_identity:
            logger.warning(
                "Matrix already has a cross-signing identity for %s, but MMRelay's local "
                "cross-signing sidecar is missing. The existing identity was preserved; "
                "restore the E2EE store/sidecar or use a dedicated bot account.",
                _client_label(client, "user_id"),
            )
            return None

    try:
        result = await cast(_EnsureCrossSigning, ensure_method)(password=password)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "Could not self-verify Matrix device %s: %s. MMRelay startup will "
            "continue, but clients enforcing cross-signing may withhold room keys; "
            "run 'mmrelay auth login' to retry with password authentication.",
            _client_label(client, "device_id"),
            exc,
        )
        logger.debug("Matrix cross-signing bootstrap failure", exc_info=True)
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
    elif result == _CROSS_SIGNING_ALREADY_SIGNED:
        logger.debug("Matrix device %s is already self-verified", device_id)
    else:
        logger.warning(
            "Matrix provider returned an unexpected cross-signing result for device %s: %r",
            device_id,
            result,
        )
        return None
    return result
