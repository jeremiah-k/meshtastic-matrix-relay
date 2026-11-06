"""Plugin loader and scheduler lifecycle regressions."""

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import mmrelay.plugin_loader as plugin_loader


class SchedulerLifecycleRegressions(unittest.TestCase):
    def setUp(self):
        self.previous = (
            plugin_loader.schedule,
            plugin_loader._global_scheduler_thread,
            plugin_loader._global_scheduler_stop_event,
            plugin_loader._global_scheduler_restart_requested,
            plugin_loader._global_scheduler_cleanup_thread,
            plugin_loader.sorted_active_plugins,
            plugin_loader.plugins_loaded,
        )
        plugin_loader._global_scheduler_thread = None
        plugin_loader._global_scheduler_stop_event = None
        plugin_loader._global_scheduler_restart_requested = False
        plugin_loader._global_scheduler_cleanup_thread = None
        plugin_loader.sorted_active_plugins = []
        plugin_loader.plugins_loaded = False

    def tearDown(self):
        (
            plugin_loader.schedule,
            plugin_loader._global_scheduler_thread,
            plugin_loader._global_scheduler_stop_event,
            plugin_loader._global_scheduler_restart_requested,
            plugin_loader._global_scheduler_cleanup_thread,
            plugin_loader.sorted_active_plugins,
            plugin_loader.plugins_loaded,
        ) = self.previous

    def test_timed_out_stop_retains_worker_and_rejects_overlap(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        class Schedule:
            def run_pending(self):
                calls.append(1)
                entered.set()
                release.wait(3)

            def clear(self):
                pass

        plugin_loader.schedule = Schedule()
        try:
            plugin_loader.start_global_scheduler()
            self.assertTrue(entered.wait(2))
            original = plugin_loader._global_scheduler_thread
            with patch.object(
                plugin_loader, "SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS", 0.01
            ):
                plugin_loader.stop_global_scheduler()
            self.assertIs(plugin_loader._global_scheduler_thread, original)
            plugin_loader.start_global_scheduler()
            self.assertIs(plugin_loader._global_scheduler_thread, original)
            self.assertEqual(len(calls), 1)
        finally:
            release.set()
            plugin_loader.stop_global_scheduler()
        self.assertIsNone(plugin_loader._global_scheduler_thread)

    def test_timed_out_stop_can_restart_without_losing_new_plugin_jobs(self):
        entered = threading.Event()
        release = threading.Event()
        restarted = threading.Event()
        observed_jobs = []
        active = 0
        peak = 0
        guard = threading.Lock()

        class Schedule:
            def __init__(self):
                self.jobs = ["old"]
                self.first = True

            def run_pending(self):
                nonlocal active, peak
                with guard:
                    active += 1
                    peak = max(peak, active)
                try:
                    if self.first:
                        self.first = False
                        entered.set()
                        release.wait(3)
                    else:
                        observed_jobs.append(tuple(self.jobs))
                        restarted.set()
                finally:
                    with guard:
                        active -= 1

            def clear(self):
                self.jobs.clear()

        scheduler = Schedule()
        plugin_loader.schedule = scheduler
        try:
            plugin_loader.start_global_scheduler()
            self.assertTrue(entered.wait(2))
            first = plugin_loader._global_scheduler_thread
            with patch.object(
                plugin_loader, "SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS", 0.01
            ):
                plugin_loader.stop_global_scheduler()
            self.assertIs(plugin_loader._global_scheduler_thread, first)
            self.assertEqual(scheduler.jobs, [])
            scheduler.jobs.append("new")
            plugin_loader.start_global_scheduler()
            self.assertTrue(plugin_loader._global_scheduler_restart_requested)
            release.set()
            self.assertTrue(restarted.wait(2))
            self.assertIn(("new",), observed_jobs)
            self.assertEqual(peak, 1)
            self.assertIsNot(plugin_loader._global_scheduler_thread, first)
        finally:
            release.set()
            plugin_loader.stop_global_scheduler()

    def test_loader_serializes_duplicate_starts(self):
        started = threading.Event()
        release = threading.Event()
        calls = []
        results = []

        def loader(_config):
            calls.append("start")
            started.set()
            self.assertTrue(release.wait(2))
            plugin_loader.plugins_loaded = True
            return ["loaded"]

        with patch.object(plugin_loader, "_load_plugins_impl", side_effect=loader):
            first = threading.Thread(
                target=lambda: results.append(plugin_loader.load_plugins())
            )
            second = threading.Thread(
                target=lambda: results.append(plugin_loader.load_plugins())
            )
            first.start()
            self.assertTrue(started.wait(2))
            second.start()
            release.set()
            first.join(2)
            second.join(2)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(calls, ["start"])

    def test_shutdown_request_is_visible_before_loader_releases(self):
        entered = threading.Event()
        release = threading.Event()
        observed = []
        stopped = threading.Event()

        def loader(_config):
            entered.set()
            self.assertTrue(release.wait(2))
            observed.append(plugin_loader._plugin_shutdown_requested.is_set())
            return []

        with (
            patch.object(plugin_loader, "_load_plugins_impl", side_effect=loader),
            patch.object(
                plugin_loader, "_shutdown_plugins_impl", side_effect=stopped.set
            ),
        ):
            loading = threading.Thread(target=plugin_loader.load_plugins)
            loading.start()
            self.assertTrue(entered.wait(2))
            shutdown = threading.Thread(target=plugin_loader.shutdown_plugins)
            shutdown.start()
            deadline = time.monotonic() + 2
            while (
                not plugin_loader._plugin_shutdown_requested.is_set()
                and time.monotonic() < deadline
            ):
                time.sleep(0.001)
            self.assertFalse(stopped.is_set())
            release.set()
            loading.join(2)
            shutdown.join(2)
            self.assertEqual(observed, [True])
            self.assertTrue(stopped.is_set())
            self.assertFalse(plugin_loader._plugin_shutdown_requested.is_set())

    def test_waiting_loader_cannot_cross_shutdown_epoch(self):
        original_epoch = plugin_loader._plugin_shutdown_epoch

        class InterleavingLock:
            def __enter__(self):
                plugin_loader._plugin_shutdown_epoch += 1
                return self

            def __exit__(self, *exc):
                return False

        try:
            with (
                patch.object(
                    plugin_loader, "_plugin_lifecycle_lock", InterleavingLock()
                ),
                patch.object(plugin_loader, "_load_plugins_impl") as loader,
            ):
                self.assertEqual(plugin_loader.load_plugins(), [])
                loader.assert_not_called()
        finally:
            plugin_loader._plugin_shutdown_epoch = original_epoch

    def test_concurrent_shutdown_requests_share_one_shutdown_signal(self):
        first_started = threading.Event()
        allow_first = threading.Event()
        second_observed = []
        calls = []

        def stop_impl():
            calls.append(1)
            if len(calls) == 1:
                first_started.set()
                self.assertTrue(allow_first.wait(2))
            else:
                second_observed.append(
                    plugin_loader._plugin_shutdown_requested.is_set()
                )

        with patch.object(
            plugin_loader, "_shutdown_plugins_impl", side_effect=stop_impl
        ):
            first = threading.Thread(target=plugin_loader.shutdown_plugins)
            second = threading.Thread(target=plugin_loader.shutdown_plugins)
            first.start()
            self.assertTrue(first_started.wait(2))
            second.start()
            deadline = time.monotonic() + 2
            while (
                plugin_loader._plugin_shutdown_requests != 2
                and time.monotonic() < deadline
            ):
                time.sleep(0.001)
            self.assertEqual(plugin_loader._plugin_shutdown_requests, 2)
            allow_first.set()
            first.join(2)
            second.join(2)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
        self.assertEqual(second_observed, [True])
        self.assertFalse(plugin_loader._plugin_shutdown_requested.is_set())
        self.assertEqual(plugin_loader._plugin_shutdown_requests, 0)

    def test_no_active_plugins_still_shuts_down_scheduler(self):
        scheduler = MagicMock()
        scheduler.is_alive.return_value = False
        plugin_loader._global_scheduler_thread = scheduler
        plugin_loader._global_scheduler_stop_event = threading.Event()
        plugin_loader.schedule = MagicMock()
        plugin_loader.plugins_loaded = True
        plugin_loader.shutdown_plugins()
        self.assertIsNone(plugin_loader._global_scheduler_thread)
        self.assertFalse(plugin_loader.plugins_loaded)
        plugin_loader.schedule.clear.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
