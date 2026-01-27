from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from mmrelay.radio.message import RadioMessage


class BaseRadioBackend(ABC):
    """
    Minimal contract for a single active radio backend.

    Provides core connection and messaging functionality needed for
    MMRelay to interact with radio backends in an agnostic way.
    """

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Return the unique backend identifier (e.g., 'meshtastic')."""

    @abstractmethod
    async def connect(self, config: dict[str, Any]) -> bool:
        """Connect the backend using the provided configuration."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect and clean up backend resources."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if the backend is currently connected."""

    @abstractmethod
    def register_message_callback(
        self,
        callback: Callable[[RadioMessage], None],
    ) -> None:
        """
        Register a callback to be invoked when messages are received.

        The callback will be called with a RadioMessage object for each
        incoming message from the radio backend.

        Parameters:
            callback: Function to call with RadioMessage when messages arrive.
        """

    @abstractmethod
    async def send_message(
        self,
        text: str,
        channel: int | None = None,
        destination_id: int | None = None,
        reply_to_id: int | str | None = None,
    ) -> Any:
        """Send a message via the radio backend."""

    def get_message_delay(self, _config: dict[str, Any], default: float) -> float:
        """Return the configured message delay for this backend."""
        return default

    def get_client(self) -> Any:
        """Return the underlying client, if one exists."""
        return None
