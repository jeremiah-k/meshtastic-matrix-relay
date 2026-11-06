"""Mesh lifecycle ownership regressions without hardware or optional SDK imports."""

import importlib.util
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class MeshLifecycleRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facade = types.ModuleType("mmrelay.meshtastic_utils")
        cls.facade.pub = MagicMock()
        cls.facade.pub.AUTO_TOPIC = object()
        cls.facade.logger = MagicMock()
        mesh = types.ModuleType("meshtastic")
        mesh.BROADCAST_NUM = 0xFFFFFFFF
        routing = types.ModuleType("mmrelay.meshtastic.packet_routing")
        routing.PacketAction = type(
            "PacketAction",
            (),
            {"RELAY": "relay", "DROP": "drop", "PLUGIN_ONLY": "plugin_only"},
        )
        routing._get_portnum_name = lambda value: str(value)
        routing._is_text_message_portnum = lambda value: True
        routing.classify_packet = lambda *args, **kwargs: None
        pubsub = types.ModuleType("pubsub")
        pubsub_core = types.ModuleType("pubsub.core")
        pubsub_exc = types.ModuleType("pubsub.core.topicexc")
        pubsub_exc.TopicNameError = type("TopicNameError", (Exception,), {})
        replacements = {
            "mmrelay.meshtastic_utils": cls.facade,
            "meshtastic": mesh,
            "mmrelay.meshtastic.packet_routing": routing,
            "pubsub": pubsub,
            "pubsub.core": pubsub_core,
            "pubsub.core.topicexc": pubsub_exc,
        }
        root = Path(__file__).resolve().parents[1] / "src/mmrelay/meshtastic"
        # ``import mmrelay.meshtastic_utils as facade`` resolves through the
        # parent package attribute before falling back to sys.modules, so the
        # stub must be installed in both places or the loaded modules silently
        # bind the real facade (tests/conftest.py imports it at collection).
        real_pkg = importlib.import_module("mmrelay")
        with (
            patch.dict(sys.modules, replacements),
            patch.object(real_pkg, "meshtastic_utils", cls.facade, create=True),
        ):
            for name in ("events", "connection", "subscriptions"):
                spec = importlib.util.spec_from_file_location(
                    f"mmrelay.meshtastic._lifecycle_regression_{name}",
                    root / f"{name}.py",
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                setattr(cls, name, module)
        assert all(
            module.facade is cls.facade
            for module in (cls.events, cls.connection, cls.subscriptions)
        ), "lifecycle modules must bind the facade stub, not the real module"

    def setUp(self):
        self.facade.shutting_down = False
        self.facade.logger.reset_mock()
        self.facade.pub.reset_mock()
        self.facade._relay_rx_time_clock_skew_lock = threading.Lock()
        self.facade._relay_startup_drain_expiry_timer = None
        self.facade._relay_startup_drain_deadline_monotonic_secs = None
        self.facade._pending_connect_time_probe_timer = None
        self.facade.time = MagicMock()
        self.facade.time.monotonic.return_value = 0.0
        self.facade.meshtastic_sub_lock = threading.Lock()
        self.facade.subscribed_to_messages = False
        self.facade.subscribed_to_connection_lost = False
        self.facade._callbacks_tearing_down = True

    def test_reconnect_does_not_claim_ownership_of_stopped_loop(self):
        self.facade.event_loop = MagicMock()
        self.facade.event_loop.is_closed.return_value = False
        self.facade.event_loop.is_running.return_value = False
        self.facade.reconnecting = True
        self.facade.reconnect = MagicMock()
        self.facade.asyncio = MagicMock()
        self.events._schedule_reconnect_after_disconnect()
        self.assertFalse(self.facade.reconnecting)
        self.facade.reconnect.assert_not_called()
        self.facade.asyncio.run_coroutine_threadsafe.assert_not_called()

    def test_stale_startup_drain_timer_cannot_clear_new_timer(self):
        timers = []
        completed = threading.Event()
        self.facade.get_startup_drain_complete_event = lambda: completed
        self.facade._relay_startup_drain_deadline_monotonic_secs = 100.0

        class FakeTimer:
            def __init__(self, delay, action):
                self.action = action
                timers.append(self)

            def start(self):
                pass

            def cancel(self):
                pass

        with patch.object(self.events.threading, "Timer", FakeTimer):
            self.events._schedule_startup_drain_deadline_cleanup(100.0)
            self.events._schedule_startup_drain_deadline_cleanup(100.0)
        self.assertIs(self.facade._relay_startup_drain_expiry_timer, timers[1])
        self.facade.time.monotonic.return_value = 100.0
        timers[0].action()
        self.assertIs(self.facade._relay_startup_drain_expiry_timer, timers[1])
        self.assertFalse(completed.is_set())
        timers[1].action()
        self.assertIsNone(self.facade._relay_startup_drain_expiry_timer)
        self.assertTrue(completed.is_set())

    def test_probe_timer_start_failure_releases_owner(self):
        self.facade._get_connect_time_probe_settings = lambda config, kind: (True, 3.0)
        client = types.SimpleNamespace(localNode=object(), sendData=lambda *args: None)

        class FailingTimer:
            def __init__(self, delay, callback):
                pass

            def start(self):
                raise RuntimeError("no threads available")

            def cancel(self):
                pass

        with (
            patch.object(
                self.connection,
                "_get_connect_probe_stabilization_deadline",
                return_value=(100.0, "startup drain"),
            ),
            patch.object(self.connection.threading, "Timer", FailingTimer),
        ):
            self.connection._schedule_connect_time_calibration_probe(
                client, connection_type="serial", active_config={}
            )
        self.assertIsNone(self.facade._pending_connect_time_probe_timer)
        self.facade.logger.warning.assert_called_once()

    def test_shutdown_does_not_reenable_pubsub_callbacks(self):
        self.facade.shutting_down = True
        self.subscriptions.ensure_meshtastic_callbacks_subscribed()
        self.facade.pub.subscribe.assert_not_called()
        self.assertFalse(self.facade.subscribed_to_messages)
        self.assertFalse(self.facade.subscribed_to_connection_lost)
        self.assertTrue(self.facade._callbacks_tearing_down)

    def test_regular_pubsub_subscription_still_works(self):
        self.facade.on_meshtastic_message = MagicMock()
        self.facade.on_lost_meshtastic_connection = MagicMock()
        self.subscriptions.ensure_meshtastic_callbacks_subscribed()
        self.assertEqual(self.facade.pub.subscribe.call_count, 2)
        self.assertTrue(self.facade.subscribed_to_messages)
        self.assertTrue(self.facade.subscribed_to_connection_lost)
        self.assertFalse(self.facade._callbacks_tearing_down)


if __name__ == "__main__":
    unittest.main()
