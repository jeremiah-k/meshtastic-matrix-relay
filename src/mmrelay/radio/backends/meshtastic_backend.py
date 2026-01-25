from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable

import mmrelay.meshtastic_utils as meshtastic_utils
from mmrelay.log_utils import get_logger
from mmrelay.radio.base_backend import BaseRadioBackend

logger = get_logger(name="Radio")


class MeshtasticBackend(BaseRadioBackend):
    """Meshtastic backend adapter using existing meshtastic_utils helpers."""

    def __init__(
        self,
        connect_fn: Callable[..., Any] | None = None,
        to_thread: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._connect_fn = connect_fn or meshtastic_utils.connect_meshtastic
        self._to_thread = to_thread or asyncio.to_thread

    @property
    def backend_name(self) -> str:
        return "meshtastic"

    async def connect(self, config: dict[str, Any]) -> bool:
        maybe_client = self._to_thread(self._connect_fn, passed_config=config)
        client = (
            await maybe_client if inspect.isawaitable(maybe_client) else maybe_client
        )
        if client is None:
            meshtastic_utils.meshtastic_client = None
            return False
        if client is not None and meshtastic_utils.meshtastic_client is None:
            meshtastic_utils.meshtastic_client = client
        return True

    async def disconnect(self) -> None:
        client = meshtastic_utils.meshtastic_client
        if client is None:
            return
        try:
            client.close()
        except Exception:
            logger.exception("Error closing Meshtastic client")
        meshtastic_utils.meshtastic_client = None

    def is_connected(self) -> bool:
        if meshtastic_utils.reconnecting:
            return False
        client = meshtastic_utils.meshtastic_client
        if client is None:
            return False
        is_conn = getattr(client, "is_connected", None)
        if is_conn is None:
            return True
        return is_conn() if callable(is_conn) else bool(is_conn)

    def get_client(self) -> Any:
        return meshtastic_utils.meshtastic_client
