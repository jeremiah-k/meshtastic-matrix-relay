"""
Radio message abstraction for backend-agnostic message handling.

Provides a standardized message format that all radio backends must conform to,
enabling clean separation between radio-specific implementations and relay logic.
"""

from dataclasses import dataclass, field
from typing import Any

RADIO_MSG_TEXT_REQUIRED = "RadioMessage.text cannot be empty"
RADIO_MSG_SENDER_ID_REQUIRED = "RadioMessage.sender_id is required"
RADIO_MSG_BACKEND_REQUIRED = "RadioMessage.backend is required"


@dataclass
class RadioMessage:
    """
    Standardized message format for all radio backends.

    Designed to be minimal yet complete enough for MMRelay's needs without
    overengineering. Focuses on fields used across the relay flow.
    """

    # Core required fields
    text: str
    sender_id: str
    sender_name: str
    timestamp: float

    # Backend identification
    backend: str  # e.g., 'meshtastic'
    meshnet_name: str

    # Channel/routing
    channel: int | None = None
    is_direct_message: bool = False
    destination_id: int | None = None

    # Backend-specific metadata (flexible for different radio types)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Message routing (for replies/reactions)
    message_id: int | str | None = None
    reply_to_id: int | str | None = None

    # Optional: location and telemetry (common in mesh networks)
    location: dict[str, float] | None = None
    telemetry: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """
        Validate and normalize RadioMessage fields after initialization.

        Performs post-construction checks and enforces required values. Raises ValueError if `text`, `sender_id`, or `backend` are empty. If `sender_name` is empty, sets a fallback of the form "Node {id_fallback}" where `id_fallback` is the full `sender_id` when shorter than 8 characters or the first 8 characters otherwise. If `meshnet_name` is empty, sets it to "default".

        Raises:
            ValueError: with RADIO_MSG_TEXT_REQUIRED if `text` is empty.
            ValueError: with RADIO_MSG_SENDER_ID_REQUIRED if `sender_id` is empty.
            ValueError: with RADIO_MSG_BACKEND_REQUIRED if `backend` is empty.
        """
        if not self.text:
            raise ValueError(RADIO_MSG_TEXT_REQUIRED)

        if not self.sender_id:
            raise ValueError(RADIO_MSG_SENDER_ID_REQUIRED)

        if not self.sender_name:
            # Use a fallback if sender_name is not provided
            # Use full sender ID if 8 or fewer chars, otherwise truncate
            id_fallback = (
                self.sender_id if len(self.sender_id) < 8 else self.sender_id[:8]
            )
            object.__setattr__(self, "sender_name", f"Node {id_fallback}")

        if not self.backend:
            raise ValueError(RADIO_MSG_BACKEND_REQUIRED)

        if not self.meshnet_name:
            # Default meshnet name if not provided
            object.__setattr__(self, "meshnet_name", "default")
