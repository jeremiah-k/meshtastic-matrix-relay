from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseRadioBackend(ABC):
    """Minimal contract for a single active radio backend."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Return the unique backend identifier."""

    @abstractmethod
    async def connect(self, config: dict[str, Any]) -> bool:
        """Connect the backend using the provided configuration."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect and clean up backend resources."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if the backend is currently connected."""

    def get_client(self) -> Any:
        """Return the underlying client, if one exists."""
        return None
