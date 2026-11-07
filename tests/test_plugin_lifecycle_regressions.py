"""Plugin loader and scheduler lifecycle regressions."""

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import pytest

import mmrelay.plugin_loader as plugin_loader


@pytest.fixture(autouse=True)
def restore_loader_config():
    """Keep real load_plugins() calls from leaking config across test cases."""
    original = plugin_loader.config
    try:
        yield
    finally:
        plugin_loader.config = original


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

    def test_stale_cleanup_watcher_adopts_new_timed_out_scheduler(self):
        """A still-live watcher must not abandon a newer timed-out owner."""
        old_join_entered = threading.Event()
        release_old_join = threading.Event()
        new_join_entered = threading.Event()
        release_new_join = threading.Event()

        class Worker:
            def __init__(self, entered, release):
                self.entered = entered
                self.release = release

            def is_alive(self):
                return True

            def join(self, timeout=None):
                if timeout is not None:
                    return
                self.entered.set()
                assert self.release.wait(3)

        old = Worker(old_join_entered, release_old_join)
        successor = Worker(new_join_entered, release_new_join)
        plugin_loader.schedule = MagicMock()
        plugin_loader._global_scheduler_thread = old
        plugin_loader._global_scheduler_stop_event = threading.Event()
        try:
            with (
                patch.object(plugin_loader, "SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS", 0.01),
                patch.object(plugin_loader, "start_global_scheduler") as restart,
            ):
                plugin_loader.stop_global_scheduler()
                watcher = plugin_loader._global_scheduler_cleanup_thread
                self.assertIsNotNone(watcher)
                self.assertTrue(old_join_entered.wait(2))
                with plugin_loader._global_scheduler_lock:
                    # T1 finished and T2 took ownership while W1 was still
                    # joining T1; then T2 timed out before W1 could check.
                    plugin_loader._global_scheduler_thread = successor
                    plugin_loader._global_scheduler_stop_event = threading.Event()
                    plugin_loader._global_scheduler_stop_event.set()
                    plugin_loader._global_scheduler_restart_requested = True
                release_old_join.set()
                self.assertTrue(new_join_entered.wait(2))
                self.assertIs(plugin_loader._global_scheduler_cleanup_thread, watcher)
                release_new_join.set()
                watcher.join(2)
                self.assertFalse(watcher.is_alive())
                self.assertIsNone(plugin_loader._global_scheduler_thread)
                self.assertIsNone(plugin_loader._global_scheduler_cleanup_thread)
                self.assertFalse(plugin_loader._global_scheduler_restart_requested)
                restart.assert_called_once_with()
        finally:
            release_old_join.set()
            release_new_join.set()

    def test_stale_cleanup_watcher_retires_already_exited_successor(self):
        """An exited replacement still needs its deferred restart processed."""
        old_join_entered = threading.Event()
        release_old_join = threading.Event()

        class OldWorker:
            def is_alive(self):
                return True

            def join(self, timeout=None):
                if timeout is None:
                    old_join_entered.set()
                    assert release_old_join.wait(3)

        class ExitedWorker:
            def is_alive(self):
                return False

            def join(self, timeout=None):
                pass

        plugin_loader.schedule = MagicMock()
        plugin_loader._global_scheduler_thread = OldWorker()
        plugin_loader._global_scheduler_stop_event = threading.Event()
        try:
            with (
                patch.object(plugin_loader, "SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS", 0.01),
                patch.object(plugin_loader, "start_global_scheduler") as restart,
            ):
                plugin_loader.stop_global_scheduler()
                watcher = plugin_loader._global_scheduler_cleanup_thread
                self.assertIsNotNone(watcher)
                self.assertTrue(old_join_entered.wait(2))
                with plugin_loader._global_scheduler_lock:
                    plugin_loader._global_scheduler_thread = ExitedWorker()
                    plugin_loader._global_scheduler_stop_event = threading.Event()
                    plugin_loader._global_scheduler_stop_event.set()
                    plugin_loader._global_scheduler_restart_requested = True
                release_old_join.set()
                watcher.join(2)
                self.assertFalse(watcher.is_alive())
                self.assertIsNone(plugin_loader._global_scheduler_thread)
                self.assertIsNone(plugin_loader._global_scheduler_cleanup_thread)
                self.assertFalse(plugin_loader._global_scheduler_restart_requested)
                restart.assert_called_once_with()
        finally:
            release_old_join.set()

    def test_explicit_restart_consumes_stale_deferred_request(self):
        """Replacing a dead worker also consumes its unfulfilled restart flag."""
        dead_worker = MagicMock()
        dead_worker.is_alive.return_value = False
        plugin_loader._global_scheduler_thread = dead_worker
        plugin_loader._global_scheduler_stop_event = threading.Event()
        plugin_loader._global_scheduler_stop_event.set()
        plugin_loader._global_scheduler_restart_requested = True
        plugin_loader.schedule = MagicMock()
        try:
            plugin_loader.start_global_scheduler()
            self.assertFalse(plugin_loader._global_scheduler_restart_requested)
            self.assertIsNot(plugin_loader._global_scheduler_thread, dead_worker)
        finally:
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


def test_shutdown_interrupts_custom_plugin_preparation_between_directories(tmp_path):
    """An interrupted import must not continue to another custom plugin."""
    from unittest.mock import patch

    first = tmp_path / "alpha"
    second = tmp_path / "beta"
    first.mkdir()
    second.mkdir()
    entered = threading.Event()
    release = threading.Event()
    calls = []
    outcomes = []

    def import_plugin(path, **_kwargs):
        calls.append(path)
        if path == str(first):
            entered.set()
            assert release.wait(3)
        return []

    with (
        patch.object(plugin_loader, "plugins_loaded", False),
        patch.object(plugin_loader, "sorted_active_plugins", []),
        patch.object(
            plugin_loader, "get_custom_plugin_dirs", return_value=[str(tmp_path)]
        ),
        patch.object(plugin_loader, "get_community_plugin_dirs", return_value=[]),
        patch.object(
            plugin_loader, "load_plugins_from_directory", side_effect=import_plugin
        ),
        patch.object(plugin_loader, "start_global_scheduler") as start_scheduler,
        patch.object(plugin_loader, "stop_global_scheduler"),
    ):
        loading = threading.Thread(
            target=lambda: outcomes.append(
                plugin_loader.load_plugins(
                    {
                        "custom-plugins": {
                            "alpha": {"active": True},
                            "beta": {"active": True},
                        }
                    }
                )
            )
        )
        shutdown = threading.Thread(target=plugin_loader.shutdown_plugins)
        try:
            loading.start()
            assert entered.wait(3)
            shutdown.start()
            assert plugin_loader._plugin_shutdown_requested.wait(3)
        finally:
            release.set()
            loading.join(3)
            if shutdown.ident is not None:
                shutdown.join(3)
        assert not loading.is_alive() and not shutdown.is_alive()
        assert calls == [str(first)]
        assert outcomes == [[]]
        assert not plugin_loader.plugins_loaded
        start_scheduler.assert_not_called()


def test_shutdown_interrupts_community_preparation_before_next_clone(tmp_path):
    """Do not perform another network operation after a blocked clone returns."""
    from unittest.mock import patch

    entered = threading.Event()
    release = threading.Event()
    clones = []
    outcomes = []

    def clone_repo(url, *_args):
        clones.append(url)
        entered.set()
        assert release.wait(3)
        return True

    config = {
        "community-plugins": {
            "one": {
                "active": True,
                "repository": "https://github.com/example/one.git",
                "branch": "main",
            },
            "two": {
                "active": True,
                "repository": "https://github.com/example/two.git",
                "branch": "main",
            },
        }
    }
    with (
        patch.object(plugin_loader, "plugins_loaded", False),
        patch.object(plugin_loader, "sorted_active_plugins", []),
        patch.object(plugin_loader, "get_custom_plugin_dirs", return_value=[]),
        patch.object(
            plugin_loader, "get_community_plugin_dirs", return_value=[str(tmp_path)]
        ),
        patch.object(plugin_loader, "clone_or_update_repo", side_effect=clone_repo),
        patch.object(plugin_loader, "_install_requirements_for_repo") as install,
        patch.object(plugin_loader, "load_plugins_from_directory") as import_plugin,
        patch.object(plugin_loader, "start_global_scheduler") as start_scheduler,
        patch.object(plugin_loader, "stop_global_scheduler"),
    ):
        loading = threading.Thread(
            target=lambda: outcomes.append(plugin_loader.load_plugins(config))
        )
        shutdown = threading.Thread(target=plugin_loader.shutdown_plugins)
        try:
            loading.start()
            assert entered.wait(3)
            shutdown.start()
            assert plugin_loader._plugin_shutdown_requested.wait(3)
        finally:
            release.set()
            loading.join(3)
            if shutdown.ident is not None:
                shutdown.join(3)
        assert not loading.is_alive() and not shutdown.is_alive()
        assert clones == ["https://github.com/example/one.git"]
        assert outcomes == [[]]
        assert not plugin_loader.plugins_loaded
        install.assert_not_called()
        import_plugin.assert_not_called()
        start_scheduler.assert_not_called()


def test_shutdown_during_dependency_install_aborts_before_recording_state(tmp_path):
    """A completed pip process must not restart plugin activation after teardown."""
    from types import SimpleNamespace
    from unittest.mock import patch

    repo = tmp_path / "one"
    repo.mkdir()
    (repo / plugin_loader.PLUGIN_REQUIREMENTS_FILENAME).write_text("six==1.0\n")
    entered = threading.Event()
    release = threading.Event()
    outcomes = []

    def install(*_args, **_kwargs):
        entered.set()
        assert release.wait(3)
        return True

    config = {
        "community-plugins": {
            "one": {
                "active": True,
                "repository": "https://github.com/example/one.git",
                "commit": "a" * 40,
                "install_requirements": True,
            },
        }
    }
    with (
        patch.object(plugin_loader, "plugins_loaded", False),
        patch.object(plugin_loader, "sorted_active_plugins", []),
        patch.object(plugin_loader, "get_custom_plugin_dirs", return_value=[]),
        patch.object(
            plugin_loader, "get_community_plugin_dirs", return_value=[str(tmp_path)]
        ),
        patch.object(plugin_loader, "clone_or_update_repo", return_value=True),
        patch.object(plugin_loader, "_check_commit_pin_for_upstream_updates"),
        patch.object(
            plugin_loader, "_resolve_local_head_commit", return_value="a" * 40
        ),
        patch.object(
            plugin_loader,
            "_effective_requirements_for_repo",
            return_value=SimpleNamespace(allowed=["six==1.0"]),
        ),
        patch.object(
            plugin_loader, "_requirements_install_target_identity", return_value="test"
        ),
        patch.object(
            plugin_loader, "_install_requirements_for_repo", side_effect=install
        ),
        patch.object(plugin_loader, "_save_plugin_state") as save_state,
        patch.object(plugin_loader, "load_plugins_from_directory") as import_plugin,
        patch.object(plugin_loader, "start_global_scheduler") as start_scheduler,
        patch.object(plugin_loader, "stop_global_scheduler"),
    ):
        loading = threading.Thread(
            target=lambda: outcomes.append(plugin_loader.load_plugins(config))
        )
        shutdown = threading.Thread(target=plugin_loader.shutdown_plugins)
        try:
            loading.start()
            assert entered.wait(3)
            shutdown.start()
            assert plugin_loader._plugin_shutdown_requested.wait(3)
        finally:
            release.set()
            loading.join(3)
            if shutdown.ident is not None:
                shutdown.join(3)
        assert not loading.is_alive() and not shutdown.is_alive()
        assert outcomes == [[]]
        save_state.assert_not_called()
        import_plugin.assert_not_called()
        start_scheduler.assert_not_called()


def test_scheduler_does_not_run_until_plugins_finish_starting():
    """Registered jobs must not execute against partly initialized plugins."""
    from mmrelay.plugins.health_plugin import Plugin as HealthPlugin

    steps = []

    def start_plugin(_self):
        steps.append("plugin")

    with (
        patch.object(plugin_loader, "plugins_loaded", False),
        patch.object(plugin_loader, "sorted_active_plugins", []),
        patch.object(plugin_loader, "get_custom_plugin_dirs", return_value=[]),
        patch.object(plugin_loader, "get_community_plugin_dirs", return_value=[]),
        patch.object(HealthPlugin, "start", start_plugin),
        patch.object(
            plugin_loader,
            "start_global_scheduler",
            side_effect=lambda: steps.append("scheduler"),
        ),
    ):
        active = plugin_loader.load_plugins({"plugins": {"health": {"active": True}}})
    assert [plugin.plugin_name for plugin in active] == ["health"]
    assert steps == ["plugin", "scheduler"]


def test_shutdown_during_last_plugin_start_never_launches_scheduler():
    """A shutdown request arriving in plugin.start() must win the handoff."""
    from mmrelay.plugins.health_plugin import Plugin as HealthPlugin

    def request_shutdown(_self):
        plugin_loader._plugin_shutdown_requested.set()

    try:
        with (
            patch.object(plugin_loader, "plugins_loaded", False),
            patch.object(plugin_loader, "sorted_active_plugins", []),
            patch.object(plugin_loader, "get_custom_plugin_dirs", return_value=[]),
            patch.object(plugin_loader, "get_community_plugin_dirs", return_value=[]),
            patch.object(HealthPlugin, "start", request_shutdown),
            patch.object(plugin_loader, "start_global_scheduler") as start_scheduler,
        ):
            active = plugin_loader.load_plugins(
                {"plugins": {"health": {"active": True}}}
            )
            start_scheduler.assert_not_called()
            assert [plugin.plugin_name for plugin in active] == ["health"]
    finally:
        plugin_loader._plugin_shutdown_requested.clear()
