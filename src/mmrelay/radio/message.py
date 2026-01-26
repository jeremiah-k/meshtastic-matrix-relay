"""
Radio message abstraction for backend-agnostic message handling.

Provides a standardized message format that all radio backends must conform to,
enabling clean separation between radio-specific implementations and relay logic.
"""

from dataclasses import dataclass, field
from typing import Any


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
        Validate message fields after initialization.

        Ensures critical fields are present and valid. Raises ValueError
        for invalid messages.
        """
        if not self.text:
            raise ValueError("RadioMessage.text cannot be empty")

        if not self.sender_id:
            raise ValueError("RadioMessage.sender_id is required")

        if not self.sender_name:
            # Use a fallback if sender_name is not provided
            # Use full sender ID if 8 or fewer chars, otherwise truncate
            id_fallback = (
                self.sender_id if len(self.sender_id) < 8 else self.sender_id[:8]
            )
            object.__setattr__(self, "sender_name", f"Node {id_fallback}")

        if not self.backend:
            raise ValueError("RadioMessage.backend is required")

        if not self.meshnet_name:
            # Default meshnet name if not provided
            object.__setattr__(self, "meshnet_name", "default")
