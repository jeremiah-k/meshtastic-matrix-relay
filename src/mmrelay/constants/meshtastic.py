"""
Meshtastic radio constants.

This module contains constants shared between the Meshtastic connection
layer and the plugins that react to radio lifecycle events.
"""

from typing import Final

# Published on pypubsub after radio setup completes outside the connection
# lock, so configuration plugins see populated settings and channel caches.
MESHTASTIC_READY_TOPIC: Final[str] = "mmrelay.meshtastic.ready"
