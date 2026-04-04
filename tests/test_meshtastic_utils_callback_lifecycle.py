import time
from unittest.mock import patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.meshtastic_utils import (
    _connect_meshtastic_impl,
    connect_meshtastic,
    ensure_meshtastic_callbacks_subscribed,
    unsubscribe_meshtastic_callbacks,
)


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestEnsureCallbacksSubscribed:
    def test_subscribes_to_both_topics(self):
        mu.subscribed_to_messages = False
        mu.subscribed_to_connection_lost = False

        with patch("mmrelay.meshtastic_utils.pub.subscribe") as mock_subscribe:
            ensure_meshtastic_callbacks_subscribed()

        assert mock_subscribe.call_count == 2
        mock_subscribe.assert_any_call(mu.on_meshtastic_message, "meshtastic.receive")
        mock_subscribe.assert_any_call(
            mu.on_lost_meshtastic_connection, "meshtastic.connection.lost"
        )
        assert mu.subscribed_to_messages is True
        assert mu.subscribed_to_connection_lost is True

    def test_idempotent_does_not_double_subscribe(self):
        mu.subscribed_to_messages = False
        mu.subscribed_to_connection_lost = False

        with patch("mmrelay.meshtastic_utils.pub.subscribe") as mock_subscribe:
            ensure_meshtastic_callbacks_subscribed()
            ensure_meshtastic_callbacks_subscribed()

        assert mock_subscribe.call_count == 2

    def test_skips_already_subscribed_topics(self):
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = True

        with patch("mmrelay.meshtastic_utils.pub.subscribe") as mock_subscribe:
            ensure_meshtastic_callbacks_subscribed()

        mock_subscribe.assert_not_called()

    def test_partial_subscription_only_subscribes_missing(self):
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = False

        with patch("mmrelay.meshtastic_utils.pub.subscribe") as mock_subscribe:
            ensure_meshtastic_callbacks_subscribed()

        assert mock_subscribe.call_count == 1
        mock_subscribe.assert_called_once_with(
            mu.on_lost_meshtastic_connection, "meshtastic.connection.lost"
        )
        assert mu.subscribed_to_messages is True
        assert mu.subscribed_to_connection_lost is True


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestUnsubscribeCallbacks:
    def test_unsubscribes_from_both_topics_when_subscribed(self):
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = True

        with patch("mmrelay.meshtastic_utils.pub.unsubscribe") as mock_unsubscribe:
            unsubscribe_meshtastic_callbacks()

        assert mock_unsubscribe.call_count == 2
        mock_unsubscribe.assert_any_call(mu.on_meshtastic_message, "meshtastic.receive")
        mock_unsubscribe.assert_any_call(
            mu.on_lost_meshtastic_connection, "meshtastic.connection.lost"
        )
        assert mu.subscribed_to_messages is False
        assert mu.subscribed_to_connection_lost is False

    def test_suppresses_exception_from_unsubscribe_messages(self):
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = False

        with patch(
            "mmrelay.meshtastic_utils.pub.unsubscribe",
            side_effect=RuntimeError("boom"),
        ):
            unsubscribe_meshtastic_callbacks()

        assert mu.subscribed_to_messages is True

    def test_suppresses_exception_from_unsubscribe_connection_lost(self):
        mu.subscribed_to_messages = False
        mu.subscribed_to_connection_lost = True

        with patch(
            "mmrelay.meshtastic_utils.pub.unsubscribe",
            side_effect=RuntimeError("boom"),
        ):
            unsubscribe_meshtastic_callbacks()

        assert mu.subscribed_to_connection_lost is True

    def test_suppresses_exception_from_both_unsubscribes(self):
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = True

        with patch(
            "mmrelay.meshtastic_utils.pub.unsubscribe",
            side_effect=RuntimeError("boom"),
        ):
            unsubscribe_meshtastic_callbacks()

        assert mu.subscribed_to_messages is True
        assert mu.subscribed_to_connection_lost is True

    def test_idempotent_when_already_unsubscribed(self):
        mu.subscribed_to_messages = False
        mu.subscribed_to_connection_lost = False

        with patch("mmrelay.meshtastic_utils.pub.unsubscribe") as mock_unsubscribe:
            unsubscribe_meshtastic_callbacks()

        mock_unsubscribe.assert_not_called()

    def test_unsubscribes_only_subscribed_topics(self):
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = False

        with patch("mmrelay.meshtastic_utils.pub.unsubscribe") as mock_unsubscribe:
            unsubscribe_meshtastic_callbacks()

        assert mock_unsubscribe.call_count == 1
        mock_unsubscribe.assert_called_once_with(
            mu.on_meshtastic_message, "meshtastic.receive"
        )


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestConnectMeshtasticShutdownGuard:
    def test_returns_none_when_shutting_down_while_waiting_for_connect(self):
        mu._connect_attempt_in_progress = True
        mu.shutting_down = True

        start = time.monotonic()
        result = connect_meshtastic(passed_config=None)
        elapsed = time.monotonic() - start

        assert result is None
        assert elapsed < 0.2


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestConnectMeshtasticImplGuards:
    def test_returns_none_when_shutting_down(self):
        mu.shutting_down = True

        result = _connect_meshtastic_impl(passed_config=None, force_connect=False)

        assert result is None

    def test_returns_none_when_reconnecting_and_not_force_connect(self):
        mu.reconnecting = True
        mu.shutting_down = False

        result = _connect_meshtastic_impl(passed_config=None, force_connect=False)

        assert result is None

    def test_proceeds_when_reconnecting_but_force_connect_is_true(self):
        mu.reconnecting = True
        mu.shutting_down = False
        mu.meshtastic_client = None
        mu.config = None

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = _connect_meshtastic_impl(passed_config=None, force_connect=True)

        assert result is None
        assert not any(
            "Reconnection already in progress" in str(c.args)
            for c in mock_logger.debug.call_args_list
        )
        assert any(
            "No configuration available" in str(c.args)
            for c in mock_logger.error.call_args_list
        )
