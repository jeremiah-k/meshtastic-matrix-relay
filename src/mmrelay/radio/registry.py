from __future__ import annotations

from typing import Any

from mmrelay.log_utils import get_logger
from mmrelay.radio.backends.meshtastic_backend import MeshtasticBackend
from mmrelay.radio.base_backend import BaseRadioBackend

logger = get_logger(name="Radio")


class RadioRegistry:
    """Registry for a single active radio backend."""

    def __init__(self) -> None:
        self._backends: dict[str, BaseRadioBackend] = {}
        self._active_backend: str | None = None

    def register_backend(
        self, backend: BaseRadioBackend, *, replace: bool = False
    ) -> None:
        name = backend.backend_name
        key = name.lower()
        if key in self._backends and not replace:
            logger.debug("Backend '%s' already registered, skipping", name)
            return
        self._backends[key] = backend
        logger.debug("Registered backend '%s'", name)
        if self._active_backend is None:
            self._active_backend = key
            logger.debug("Auto-activated backend '%s'", name)

    def set_active_backend(self, name: str | None) -> bool:
        if name is None:
            self._active_backend = None
            return True
        key = name.lower()
        if key not in self._backends:
            return False
        self._active_backend = key
        return True

    def get_backend(self, name: str) -> BaseRadioBackend | None:
        return self._backends.get(name.lower())

    def get_backend_names(self) -> list[str]:
        return list(self._backends.keys())

    def get_active_backend(self) -> BaseRadioBackend | None:
        if self._active_backend is None:
            return None
        return self._backends.get(self._active_backend)

    def get_active_backend_name(self) -> str | None:
        backend = self.get_active_backend()
        return backend.backend_name if backend else None

    def is_ready(self) -> bool:
        backend = self.get_active_backend()
        if backend is None:
            return False
        return backend.is_connected()

    async def connect_active_backend(self, config: dict[str, Any]) -> bool:
        backend = self.get_active_backend()
        if backend is None:
            return False
        return await backend.connect(config)

    async def disconnect_active_backend(self) -> None:
        backend = self.get_active_backend()
        if backend is None:
            return
        await backend.disconnect()


_registry: RadioRegistry | None = None


def get_radio_registry() -> RadioRegistry:
    global _registry
    if _registry is None:
        _registry = RadioRegistry()
        _registry.register_backend(MeshtasticBackend())
    return _registry
