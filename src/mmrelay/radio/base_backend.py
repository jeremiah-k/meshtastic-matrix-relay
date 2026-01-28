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
        """
        Unique identifier for this backend (for example, 'meshtastic').

        Returns:
            The backend's unique identifier string (e.g., 'meshtastic').
        """

    @abstractmethod
    async def connect(self, config: dict[str, Any]) -> bool:
        """
        Connect the backend using the provided configuration.

        Parameters:
            config (dict[str, Any]): Backend-specific connection settings.

        Returns:
            bool: `true` if the backend connected successfully, `false` otherwise.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect and clean up backend resources."""

    @abstractmethod
    def is_connected(self) -> bool:
        """
        Report whether the backend is currently connected.

        Returns:
            bool: `True` if the backend is connected, `False` otherwise.
        """

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
        """
        Send a text message through the backend to an optional channel or destination.

        Parameters:
            text (str): Message content to send.
            channel (int | None): Optional channel identifier to send the message on.
            destination_id (int | None): Optional destination identifier for the recipient.
            reply_to_id (int | str | None): Optional existing message identifier to mark this message as a reply.

        Returns:
            Any: Backend-specific result of the send operation.
        """

    def get_message_delay(self, _config: dict[str, Any], default: float) -> float:
        """
        Provide the message send delay configured for this backend.

        Parameters:
            _config (dict[str, Any]): Backend configuration dictionary (ignored by the base implementation).
            default (float): Fallback delay in seconds to use if the backend does not supply a value.

        Returns:
            float: The delay in seconds to wait between sending messages.
        """
        return default

    def get_nodes(self) -> dict[str, Any]:
        """
        Retrieve the list of nodes known to the backend.

        Returns:
            dict: A dictionary of nodes, where keys are node identifiers and values are node objects.
                Node objects should contain at least a "user" key with "id", "longName", and "shortName" fields.
        """
        return {}

    def get_client(self) -> Any:
        """
        Retrieve the backend's underlying client instance.

        Returns:
            The client object if one exists, otherwise None.
        """
        return None
