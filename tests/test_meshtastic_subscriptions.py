from unittest.mock import MagicMock, patch

import pytest
from pubsub.core.topicexc import TopicNameError

import mmrelay.meshtastic_utils as mu


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestUnsubscribeMeshtasticCallbacks:
    def test_unsubscribe_messages_topic_name_error(self):
        from mmrelay.meshtastic.subscriptions import unsubscribe_meshtastic_callbacks

        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = False
        with patch.object(
            mu.pub,
            "unsubscribe",
            side_effect=TopicNameError("meshtastic.receive", "missing"),
        ):
            unsubscribe_meshtastic_callbacks()
        assert mu.subscribed_to_messages is False

    def test_unsubscribe_connection_lost_topic_name_error(self):
        from mmrelay.meshtastic.subscriptions import unsubscribe_meshtastic_callbacks

        mu.subscribed_to_messages = False
        mu.subscribed_to_connection_lost = True
        call_count = 0

        def _unsub(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TopicNameError("meshtastic.connection.lost", "missing")

        with patch.object(mu.pub, "unsubscribe", side_effect=_unsub):
            unsubscribe_meshtastic_callbacks()
        assert mu.subscribed_to_connection_lost is False

    def test_unsubscribe_messages_generic_exception_keeps_flag(self):
        from mmrelay.meshtastic.subscriptions import unsubscribe_meshtastic_callbacks

        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = False
        with patch.object(mu.pub, "unsubscribe", side_effect=RuntimeError("boom")):
            unsubscribe_meshtastic_callbacks()
        assert mu.subscribed_to_messages is True


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestEnsureMeshtasticCallbacksSubscribed:
    def test_subscribes_messages_and_connection_lost(self):
        from mmrelay.meshtastic.subscriptions import (
            ensure_meshtastic_callbacks_subscribed,
        )

        mu.subscribed_to_messages = False
        mu.subscribed_to_connection_lost = False
        with patch.object(mu.pub, "subscribe") as mock_sub:
            ensure_meshtastic_callbacks_subscribed()
            assert mock_sub.call_count == 2
            assert mu.subscribed_to_messages is True
            assert mu.subscribed_to_connection_lost is True

    def test_skips_already_subscribed(self):
        from mmrelay.meshtastic.subscriptions import (
            ensure_meshtastic_callbacks_subscribed,
        )

        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = True
        with patch.object(mu.pub, "subscribe") as mock_sub:
            ensure_meshtastic_callbacks_subscribed()
            mock_sub.assert_not_called()

    def test_clears_tearing_down_flag(self):
        from mmrelay.meshtastic.subscriptions import (
            ensure_meshtastic_callbacks_subscribed,
        )

        mu._callbacks_tearing_down = True
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = True
        with patch.object(mu.pub, "subscribe"):
            ensure_meshtastic_callbacks_subscribed()
        assert mu._callbacks_tearing_down is False

    def test_unsubscribe_messages_success(self):
        from mmrelay.meshtastic.subscriptions import unsubscribe_meshtastic_callbacks

        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = False
        with patch.object(mu.pub, "unsubscribe"):
            unsubscribe_meshtastic_callbacks()
        assert mu.subscribed_to_messages is False

    def test_unsubscribe_connection_lost_success(self):
        from mmrelay.meshtastic.subscriptions import unsubscribe_meshtastic_callbacks

        mu.subscribed_to_messages = False
        mu.subscribed_to_connection_lost = True
        with patch.object(mu.pub, "unsubscribe"):
            unsubscribe_meshtastic_callbacks()
        assert mu.subscribed_to_connection_lost is False

    def test_unsubscribe_connection_lost_generic_exception_keeps_flag(self):
        from mmrelay.meshtastic.subscriptions import unsubscribe_meshtastic_callbacks

        mu.subscribed_to_messages = False
        mu.subscribed_to_connection_lost = True
        with patch.object(mu.pub, "unsubscribe", side_effect=RuntimeError("fail")):
            unsubscribe_meshtastic_callbacks()
        assert mu.subscribed_to_connection_lost is True
