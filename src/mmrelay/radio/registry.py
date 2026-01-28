from __future__ import annotations

import threading
from typing import Any

from mmrelay.log_utils import get_logger
from mmrelay.radio.backends.meshtastic_backend import MeshtasticBackend
from mmrelay.radio.base_backend import BaseRadioBackend

logger = get_logger(name="Radio")


class RadioRegistry:
    """Registry for a single active radio backend."""

    def __init__(self) -> None:
        """
        Initialize a RadioRegistry instance.

        Sets up internal state with an empty mapping of backend names to their BaseRadioBackend instances and no active backend selected.
        """
        self._backends: dict[str, BaseRadioBackend] = {}
        self._active_backend: str | None = None

    def register_backend(
        self, backend: BaseRadioBackend, *, replace: bool = False
    ) -> None:
        """
        Register a radio backend with the registry.

        Stores the backend under the lowercase form of its `backend_name`. If a backend with the same name is already registered, the registration is skipped unless `replace` is True. If no active backend is set, the newly registered backend becomes the active backend.

        Parameters:
            backend (BaseRadioBackend): The backend instance to register.
            replace (bool): If True, replace any existing backend registered under the same name.
        """
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
        """
        Set the currently active radio backend or clear the active backend.

        Parameters:
            name (str | None): The backend name to activate (case-insensitive). If `None`, clears any active backend.

        Returns:
            bool: `true` if the active backend was set or cleared successfully, `false` if the given backend name is not registered.
        """
        if name is None:
            self._active_backend = None
            return True
        key = name.lower()
        if key not in self._backends:
            return False
        self._active_backend = key
        return True

    def get_backend(self, name: str) -> BaseRadioBackend | None:
        """
        Retrieve a registered backend by name (case-insensitive).

        Parameters:
            name (str): The backend name to look up; matching is case-insensitive.

        Returns:
            BaseRadioBackend | None: The backend instance with the given name, or `None` if no such backend is registered.
        """
        return self._backends.get(name.lower())

    def get_backend_names(self) -> list[str]:
        """
        Get the names of all registered backends.

        Returns:
            list[str]: Registered backend keys (lowercase) in insertion order.
        """
        return list(self._backends.keys())

    def get_active_backend(self) -> BaseRadioBackend | None:
        """
        Retrieve the currently active radio backend instance.

        Returns:
            BaseRadioBackend | None: The active backend instance, or `None` if no backend is active.
        """
        if self._active_backend is None:
            return None
        return self._backends.get(self._active_backend)

    def get_active_backend_name(self) -> str | None:
        """
        Get the human-readable name of the currently active backend.

        Returns:
            str | None: The active backend's `backend_name`, or `None` if no backend is active.
        """
        backend = self.get_active_backend()
        return backend.backend_name if backend else None

    def is_ready(self) -> bool:
        """
        Determine whether the registry's active backend is connected.

        Returns:
            True if there is an active backend and it is connected, False otherwise.
        """
        backend = self.get_active_backend()
        if backend is None:
            return False
        return backend.is_connected()

    async def connect_active_backend(self, config: dict[str, Any]) -> bool:
        """
        Connects the currently active radio backend using the provided configuration.

        Parameters:
            config (dict[str, Any]): Connection parameters to pass to the backend's connect method.

        Returns:
            `true` if the active backend was present and connected successfully, `false` otherwise.
        """
        backend = self.get_active_backend()
        if backend is None:
            return False
        return await backend.connect(config)

    async def disconnect_active_backend(self) -> None:
        """
        Disconnects the currently active radio backend if one is set.

        If there is no active backend, this method does nothing. Otherwise it invokes the backend's `disconnect()` coroutine.
        """
        backend = self.get_active_backend()
        if backend is None:
            return
        await backend.disconnect()


_registry: RadioRegistry | None = None
_registry_lock = threading.Lock()


def get_radio_registry() -> RadioRegistry:
    """
    Return the singleton RadioRegistry instance, creating and initializing it on first access.

    If the registry does not yet exist, a new RadioRegistry is created and a MeshtasticBackend is registered as the initial backend before the instance is returned.

    Returns:
        RadioRegistry: The module-level singleton RadioRegistry.
    """
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = RadioRegistry()
                _registry.register_backend(MeshtasticBackend())
    return _registry
