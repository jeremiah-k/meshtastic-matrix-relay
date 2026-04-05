from pubsub.core.topicexc import TopicNameError

import mmrelay.meshtastic_utils as facade

__all__ = [
    "ensure_meshtastic_callbacks_subscribed",
    "unsubscribe_meshtastic_callbacks",
]


def ensure_meshtastic_callbacks_subscribed() -> None:
    """Ensure Meshtastic pubsub callbacks are subscribed exactly once."""
    with facade.meshtastic_sub_lock:
        facade._callbacks_tearing_down = False
        if not facade.subscribed_to_messages:
            facade.pub.subscribe(facade.on_meshtastic_message, "meshtastic.receive")
            facade.subscribed_to_messages = True
            facade.logger.debug("Subscribed to meshtastic.receive")

        if not facade.subscribed_to_connection_lost:
            facade.pub.subscribe(
                facade.on_lost_meshtastic_connection, "meshtastic.connection.lost"
            )
            facade.subscribed_to_connection_lost = True
            facade.logger.debug("Subscribed to meshtastic.connection.lost")


def unsubscribe_meshtastic_callbacks() -> None:
    """Best-effort unsubscribe for Meshtastic pubsub callbacks."""
    with facade.meshtastic_sub_lock:
        facade._callbacks_tearing_down = True
        if facade.subscribed_to_messages:
            try:
                facade.pub.unsubscribe(
                    facade.on_meshtastic_message, "meshtastic.receive"
                )
            except TopicNameError:
                facade.subscribed_to_messages = False
                facade.logger.debug(
                    "meshtastic.receive topic missing during unsubscribe; treated as unsubscribed"
                )
            except Exception:
                facade.logger.exception(
                    "Failed to unsubscribe from meshtastic.receive; keeping subscribed_to_messages=True"
                )
            else:
                facade.subscribed_to_messages = False
                facade.logger.debug("Unsubscribed from meshtastic.receive")

        if facade.subscribed_to_connection_lost:
            try:
                facade.pub.unsubscribe(
                    facade.on_lost_meshtastic_connection,
                    "meshtastic.connection.lost",
                )
            except TopicNameError:
                facade.subscribed_to_connection_lost = False
                facade.logger.debug(
                    "meshtastic.connection.lost topic missing during unsubscribe; treated as unsubscribed"
                )
            except Exception:
                facade.logger.exception(
                    "Failed to unsubscribe from meshtastic.connection.lost; keeping subscribed_to_connection_lost=True"
                )
            else:
                facade.subscribed_to_connection_lost = False
                facade.logger.debug("Unsubscribed from meshtastic.connection.lost")
