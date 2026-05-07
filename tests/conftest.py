"""
Pytest configuration and fixtures for MMRelay tests.

This file sets up comprehensive mocking for external dependencies
to ensure tests can run without requiring actual hardware or network connections.
"""

import os
import sys

# Add src directory to path to allow for package imports
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)

import asyncio
import contextlib
import gc
import inspect
import logging
import queue
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Generator
from unittest.mock import MagicMock

import pytest
from pubsub import pub

import tests.mocks  # noqa: F401  # Must be imported before application imports to install mocks

# Mock all external dependencies before any application imports can occur.
from tests.ble_cleanup import (  # noqa: E402
    _drain_future_result_safely,
    _safe_is_done,
    cleanup_ble_future_state,
)
from tests.sqlite_provenance import _conn_provenance  # noqa: E402

# AsyncMock cleanup tuning:
# Full generation-2 gc.collect() is relatively expensive on newer CPython runtimes
# and can dominate teardown time when run after every qualifying test. We still do
# cheap generation-0 collection every time, and periodically run a full collection
# to reclaim cyclic coroutine objects before they leak into unrelated tests.
_ASYNCMOCK_FULL_GC_INTERVAL = 25
_asyncmock_cleanup_invocations = 0


def _drain_awaitable_result_safely(awaitable: Any, timeout: float = 0.2) -> None:
    """
    Best-effort completion of a coroutine/awaitable from synchronous teardown code.

    Handles raw coroutines, asyncio.Task, and asyncio.Future objects appropriately.
    """
    if not inspect.isawaitable(awaitable):
        return

    def _consume_result(done_task: asyncio.Future[Any]) -> None:
        with contextlib.suppress(
            asyncio.CancelledError,
            asyncio.InvalidStateError,
        ):
            done_task.exception()

    if isinstance(awaitable, asyncio.Future):
        try:
            loop = awaitable.get_loop()
        except (AttributeError, RuntimeError):
            loop = None

        if loop is not None and not loop.is_closed() and loop.is_running():
            same_loop = False
            with contextlib.suppress(RuntimeError):
                same_loop = asyncio.get_running_loop() is loop
            if same_loop:
                awaitable.add_done_callback(_consume_result)
            return
        if loop is not None and not loop.is_closed():
            try:
                loop.run_until_complete(asyncio.wait_for(awaitable, timeout=timeout))
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                pass
            except RuntimeError as exc:
                logger = logging.getLogger(__name__)
                logger.debug(
                    "Unexpected RuntimeError in drain_awaitable_result_safely: %s",
                    exc,
                )
        return

    if asyncio.iscoroutine(awaitable):
        coro = awaitable
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            task = loop.create_task(coro)
            task.add_done_callback(_consume_result)
            return

        temp_loop = asyncio.new_event_loop()
        try:
            temp_loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            pass
        except RuntimeError as exc:
            logger = logging.getLogger(__name__)
            logger.debug(
                "Unexpected RuntimeError in drain_awaitable_result_safely: %s",
                exc,
            )
        finally:
            temp_loop.close()


def _cancel_and_drain_future_like(future_obj: Any, *, timeout: float = 0.2) -> None:
    """
    Best-effort cancel and drain for asyncio/concurrent futures.
    """
    if future_obj is None:
        return
    cancel_fn = getattr(future_obj, "cancel", None)
    if callable(cancel_fn) and not _safe_is_done(future_obj):
        with contextlib.suppress(RuntimeError):
            cancel_fn()
    _drain_future_result_safely(future_obj, timeout=timeout)


def _cancel_and_join_timer_like(timer_obj: Any, *, timeout: float = 0.2) -> None:
    """
    Best-effort timer cancellation and brief join for deterministic teardown.
    """
    if timer_obj is None:
        return

    cancel_fn = getattr(timer_obj, "cancel", None)
    if callable(cancel_fn):
        with contextlib.suppress(RuntimeError, TypeError):
            cancel_fn()

    is_alive_fn = getattr(timer_obj, "is_alive", None)
    join_fn = getattr(timer_obj, "join", None)
    if callable(is_alive_fn) and callable(join_fn):
        with contextlib.suppress(RuntimeError, TypeError):
            if is_alive_fn():
                join_fn(timeout=timeout)


# Now that mocks are in place, we can import the application code
import mmrelay.meshtastic_utils as mu  # noqa: E402
from mmrelay.constants.network import CONNECTION_TYPE_SERIAL  # noqa: E402
from tests.constants import (  # noqa: E402
    TEST_BOT_USER_ID,
    TEST_MATRIX_HOMESERVER,
    TEST_ROOM_ID,
    TEST_ROOM_ID_1,
    TEST_ROOM_ID_2,
    TEST_USER_ID,
)

# Store references to prevent accidental mocking
_BUILTIN_MODULES = {
    "queue": queue,
    "logging": logging,
    "asyncio": asyncio,
    "threading": threading,
    "time": time,
}


def ensure_builtins_not_mocked():
    """
    Restore any standard library modules that were replaced with mocks during test setup.

    This function iterates the internal _BUILTIN_MODULES mapping and, for each entry whose
    corresponding module in sys.modules appears to be a mock (detected by the presence of
    a "_mock_name" attribute), replaces that mocked entry with the original module object
    from _BUILTIN_MODULES. It also ensures the logging module is restored if it was mocked.

    Side effects:
    - Mutates sys.modules entries for built-in modules when mocks are detected.
    """
    for name, module in _BUILTIN_MODULES.items():
        if name in sys.modules and hasattr(sys.modules[name], "_mock_name"):
            sys.modules[name] = module
    import logging

    if hasattr(logging, "_mock_name"):
        sys.modules["logging"] = _BUILTIN_MODULES["logging"]


# Ensure built-in modules are not accidentally mocked
ensure_builtins_not_mocked()


@pytest.fixture(autouse=True)
def meshtastic_loop_safety(monkeypatch, request):
    """
    Function-scoped pytest fixture that provides a dedicated asyncio event loop for tests that interact with mmrelay.meshtastic_utils.

    Creates a fresh event loop, assigns it to mmrelay.meshtastic_utils.event_loop for the duration of each test function, yields the loop to tests, and on teardown cancels any remaining tasks, awaits their completion, closes the loop, and clears the global event loop reference.

    When the ``asyncio`` marker is present on the test (set automatically by
    pytest-asyncio under ``asyncio_mode=auto``), this fixture yields ``None``
    without creating or managing a loop so that pytest-asyncio owns the event
    loop lifecycle for async tests.

    Yields:
        asyncio.AbstractEventLoop | None: a new event loop isolated to each
        test function, or ``None`` when the test is async (asyncio marker set).
    """
    if request.node.get_closest_marker("asyncio"):
        monkeypatch.setattr(mu, "event_loop", None, raising=False)
        yield
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    monkeypatch.setattr(mu, "event_loop", loop)

    yield loop

    # Teardown: Clean up the loop
    try:
        tasks = asyncio.all_tasks(loop=loop)
        for task in tasks:
            task.cancel()
        if tasks:
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
    finally:
        loop.close()
        asyncio.set_event_loop(None)


@pytest.fixture(autouse=True)
def reset_plugin_loader_cache():
    """
    Pytest fixture that resets plugin loader caches before and after each test to prevent leakage of mocked objects between tests.

    This helps avoid issues such as AsyncMock warnings caused by stale plugin instances persisting across test runs.
    """
    import mmrelay.plugin_loader as pl

    pl._reset_caches_for_tests()
    yield
    pl._reset_caches_for_tests()


@pytest.fixture
def mock_config():
    """Default mock configuration used by TestMain tests."""
    return {
        "matrix": {
            "homeserver": TEST_MATRIX_HOMESERVER,
            "access_token": "test_token",
            "bot_user_id": TEST_BOT_USER_ID,
        },
        "matrix_rooms": [
            {"id": TEST_ROOM_ID_1, "meshtastic_channel": 0},
            {"id": TEST_ROOM_ID_2, "meshtastic_channel": 1},
        ],
        "meshtastic": {
            "connection_type": CONNECTION_TYPE_SERIAL,
            "serial_port": "/dev/ttyUSB0",
            "message_delay": 2.0,
        },
        "database": {"msg_map": {"wipe_on_restart": False}},
    }


@pytest.fixture(autouse=True)
def cleanup_asyncmock_objects(request):
    """
    Yield to the test and, after it completes, run a targeted garbage-collection pass to suppress "never awaited" RuntimeWarning messages for tests that commonly create AsyncMock objects.

    This fixture inspects the executing test's filename and, when it matches known AsyncMock-using patterns, performs garbage collection while suppressing RuntimeWarning messages about never-awaited coroutines.

    Parameters:
        request (pytest.FixtureRequest): Pytest request object used to determine the executing test's filename.
    """
    yield

    # Only force garbage collection for tests that might create AsyncMock objects
    test_file = request.node.fspath.basename

    # List of test files/patterns that use AsyncMock
    asyncmock_patterns = [
        "test_async_patterns",
        "test_matrix_utils",
        "test_matrix_utils_auth",
        "test_matrix_utils_core",
        "test_matrix_utils_invite",
        "test_matrix_utils_media",
        "test_matrix_utils_relay",
        "test_matrix_utils_replies",
        "test_mesh_relay_plugin",
        "test_map_plugin",
        "test_meshtastic_utils",
        "test_base_plugin",
        "test_telemetry_plugin",
        "test_performance_stress",
        "test_main",
        "test_health_plugin",
        "test_error_boundaries",
        "test_integration_scenarios",
        "test_help_plugin",
        "test_ping_plugin",
        "test_nodes_plugin",
    ]

    if any(pattern in test_file for pattern in asyncmock_patterns):
        global _asyncmock_cleanup_invocations
        import warnings

        _asyncmock_cleanup_invocations += 1
        # Suppress RuntimeWarning about unawaited coroutines during cleanup
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", category=RuntimeWarning, message=".*never awaited.*"
            )
            gc.collect(0)
            if _asyncmock_cleanup_invocations % _ASYNCMOCK_FULL_GC_INTERVAL == 0:
                gc.collect()


@pytest.fixture(autouse=True)
def mock_submit_coro(monkeypatch, request):
    """
    Replace mmrelay.meshtastic_utils._submit_coro with a test helper that ensures passed coroutines are executed and awaited so AsyncMock coroutines run to completion.

    This pytest fixture patches the module-level _submit_coro to a mock implementation that schedules a coroutine on an available running event loop when possible, otherwise runs it synchronously in a temporary loop. It yields control to the test and restores the original function on teardown.

    When the ``no_global_mocks`` marker is applied to the test, this fixture does nothing,
    allowing tests to exercise real async scheduling and thread boundaries.
    """
    if request.node.get_closest_marker("no_global_mocks"):
        yield
        return

    import asyncio
    import inspect

    def mock_submit(coro, loop=None):
        """
        Schedule and execute a coroutine on an available asyncio event loop.

        Prefers the currently running event loop, falls back to a provided running loop, and if neither is available
        runs the coroutine synchronously in a temporary loop. If the argument is not a coroutine, nothing is scheduled.

        Parameters:
            coro: The coroutine to execute.
            loop: Optional event loop to prefer when scheduling the coroutine.

        Returns:
            `Task` if the coroutine is scheduled on a running loop, `Future` containing the result or exception if
            executed synchronously, or `None` if `coro` is not a coroutine.
        """
        if not inspect.iscoroutine(coro):  # Not a coroutine
            return None

        # Prefer the currently running loop (pytest-asyncio) to avoid spawning many temporary loops
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop and running_loop.is_running():
            return running_loop.create_task(coro)

        target_loop = loop if isinstance(loop, asyncio.AbstractEventLoop) else None
        if target_loop and target_loop.is_running():
            return target_loop.create_task(coro)

        # Fallback: run synchronously in a temporary loop
        temp_loop = asyncio.new_event_loop()
        try:
            result = temp_loop.run_until_complete(coro)
            future = Future()
            future.set_result(result)
            return future
        except Exception as e:
            future = Future()
            future.set_exception(e)
            return future
        finally:
            temp_loop.close()

    monkeypatch.setattr(mu, "_submit_coro", mock_submit)
    yield


def _fast_submit(coro, loop=None):
    """
    Create a completed Future representing immediate coroutine submission.

    Returns None for non-coroutines; otherwise completes the Future with None.
    """
    if not inspect.iscoroutine(coro):
        return None
    # Explicitly close to avoid "coroutine was never awaited" warnings
    coro.close()
    done = Future()
    done.set_result(None)
    return done


def _fast_wait(result_future, timeout, loop=None):
    """
    Resolve a Future-like object to its value, returning False for None.
    """
    if result_future is None:
        return False
    if isinstance(result_future, Future):
        return result_future.result(timeout=timeout)
    return result_future


@pytest.fixture
def fast_async_helpers():
    """
    Provide helper functions to submit/await coroutines instantly in tests.

    Returns:
        tuple[Callable, Callable]: (fast_submit, fast_wait)
    """
    return _fast_submit, _fast_wait


@pytest.fixture
def done_future():
    """
    Return a Future object that is already completed with a result of None.

    Returns:
        Future: A completed Future with its result set to None.
    """
    asyncio.get_event_loop()
    f = Future()
    f.set_result(None)
    return f


@pytest.fixture(autouse=True)
def reset_path_overrides():
    """
    Autouse pytest fixture that resets CLI and programmatic path overrides before and after each test.

    Ensures CLI overrides (--home, --base-dir, --data-dir) and any programmatic overrides managed by mmrelay.paths are cleared so they do not leak between tests.
    """
    import mmrelay.paths

    # Reset mmrelay.paths override
    mmrelay.paths.reset_home_override()

    yield

    # Reset mmrelay.paths override again for safety
    mmrelay.paths.reset_home_override()


@pytest.fixture(autouse=True)
def reset_banner_flag():
    """
    Reset the mmrelay.main module's banner-printed flag before each test.

    This autouse pytest fixture sets mmrelay.main._banner_printed to False and yields once so the test executes with the cleared flag.
    """
    import mmrelay.main

    mmrelay.main._banner_printed = False
    yield


@pytest.fixture
def reset_meshtastic_globals():
    """
    Temporarily reset key module-level state in mmrelay.meshtastic_utils for a test and restore it on teardown.

    Saves the original values of attributes such as `config`, `meshtastic_client`, reconnect/shutdown flags and tasks,
    subscription flags, and internal futures; sets those attributes to clean defaults for duration of the test,
    yields control to test, and restores the saved values on teardown. The module's `logger` and `event_loop`
    are intentionally left unchanged.
    """
    import mmrelay.meshtastic_utils as mu

    # Store original values (excluding logger and event_loop to keep them functional)
    original_values = {
        "config": getattr(mu, "config", None),
        "meshtastic_client": getattr(mu, "meshtastic_client", None),
        "meshtastic_iface": getattr(mu, "meshtastic_iface", None),
        "reconnecting": getattr(mu, "reconnecting", False),
        "shutting_down": getattr(mu, "shutting_down", False),
        "reconnect_task": getattr(mu, "reconnect_task", None),
        "reconnect_task_future": getattr(mu, "reconnect_task_future", None),
        "_connect_attempt_lock": getattr(mu, "_connect_attempt_lock", None),
        "_connect_attempt_condition": getattr(mu, "_connect_attempt_condition", None),
        "_connect_attempt_in_progress": getattr(
            mu, "_connect_attempt_in_progress", False
        ),
        "subscribed_to_messages": getattr(mu, "subscribed_to_messages", False),
        "subscribed_to_connection_lost": getattr(
            mu, "subscribed_to_connection_lost", False
        ),
        "_callbacks_tearing_down": getattr(mu, "_callbacks_tearing_down", False),
        "_metadata_future": getattr(mu, "_metadata_future", None),
        "_metadata_future_started_at": getattr(mu, "_metadata_future_started_at", None),
        "_ble_future": getattr(mu, "_ble_future", None),
        "_ble_future_address": getattr(mu, "_ble_future_address", None),
        "_ble_future_started_at": getattr(mu, "_ble_future_started_at", None),
        "_ble_future_timeout_secs": getattr(mu, "_ble_future_timeout_secs", None),
        "_ble_timeout_counts": dict(getattr(mu, "_ble_timeout_counts", None) or {}),
        "_ble_executor_orphaned_workers_by_address": dict(
            getattr(mu, "_ble_executor_orphaned_workers_by_address", None) or {}
        ),
        "_ble_generation_by_address": dict(
            getattr(mu, "_ble_generation_by_address", None) or {}
        ),
        "_ble_iface_generation_by_id": dict(
            getattr(mu, "_ble_iface_generation_by_id", None) or {}
        ),
        "_ble_teardown_unresolved_by_generation": dict(
            getattr(mu, "_ble_teardown_unresolved_by_generation", None) or {}
        ),
        "_health_probe_request_deadlines": dict(
            getattr(mu, "_health_probe_request_deadlines", {})
        ),
        "_metadata_executor_orphaned_workers": getattr(
            mu, "_metadata_executor_orphaned_workers", 0
        ),
        "_ble_future_watchdog_secs": getattr(mu, "_ble_future_watchdog_secs", None),
        "_ble_timeout_reset_threshold": getattr(
            mu, "_ble_timeout_reset_threshold", None
        ),
        "_ble_scan_timeout_secs": getattr(mu, "_ble_scan_timeout_secs", None),
        "_ble_future_stale_grace_secs": getattr(
            mu, "_ble_future_stale_grace_secs", None
        ),
        "_ble_interface_create_timeout_secs": getattr(
            mu, "_ble_interface_create_timeout_secs", None
        ),
        "RELAY_START_TIME": getattr(mu, "RELAY_START_TIME", None),
        "_relay_active_client_id": getattr(mu, "_relay_active_client_id", None),
        "_relay_connection_started_monotonic_secs": getattr(
            mu, "_relay_connection_started_monotonic_secs", None
        ),
        "_relay_rx_time_clock_skew_secs": getattr(
            mu, "_relay_rx_time_clock_skew_secs", None
        ),
        "_relay_startup_drain_deadline_monotonic_secs": getattr(
            mu, "_relay_startup_drain_deadline_monotonic_secs", None
        ),
        "_relay_startup_drain_expiry_timer": getattr(
            mu, "_relay_startup_drain_expiry_timer", None
        ),
        "_pending_connect_time_probe_timer": getattr(
            mu, "_pending_connect_time_probe_timer", None
        ),
        "_relay_startup_drain_complete_event": getattr(
            mu, "_relay_startup_drain_complete_event", None
        ),
        "_relay_reconnect_prestart_bootstrap_deadline_monotonic_secs": getattr(
            mu, "_relay_reconnect_prestart_bootstrap_deadline_monotonic_secs", None
        ),
        "_startup_packet_drain_applied": getattr(
            mu, "_startup_packet_drain_applied", False
        ),
        "_ble_executor_degraded_addresses": set(
            getattr(mu, "_ble_executor_degraded_addresses", None) or set()
        ),
        "_metadata_executor_degraded": getattr(
            mu, "_metadata_executor_degraded", False
        ),
        "_ble_executor": getattr(mu, "_ble_executor", None),
        "_metadata_executor": getattr(mu, "_metadata_executor", None),
    }

    # Reset mutable globals to a clean state; keep logger and event_loop usable
    mu.config = None
    mu.meshtastic_client = None
    mu.meshtastic_iface = None
    mu.reconnecting = False
    mu.shutting_down = False
    mu.reconnect_task = None
    mu.reconnect_task_future = None
    mu.subscribed_to_messages = False
    mu.subscribed_to_connection_lost = False
    mu._callbacks_tearing_down = False
    mu._metadata_future = None
    mu._metadata_future_started_at = None
    cleanup_ble_future_state(mu)
    mu._ble_timeout_counts = {}
    mu._ble_executor_orphaned_workers_by_address = {}
    mu._ble_generation_by_address = {}
    mu._ble_iface_generation_by_id = {}
    mu._ble_teardown_unresolved_by_generation = {}
    mu._metadata_executor_orphaned_workers = 0
    mu._ble_executor_degraded_addresses = set()
    mu._metadata_executor_degraded = False
    mu._ble_future_watchdog_secs = getattr(
        mu,
        "BLE_FUTURE_WATCHDOG_SECS",
        None,
    )
    mu._ble_timeout_reset_threshold = getattr(
        mu,
        "BLE_TIMEOUT_RESET_THRESHOLD",
        None,
    )
    mu._ble_scan_timeout_secs = getattr(
        mu,
        "BLE_SCAN_TIMEOUT_SECS",
        None,
    )
    mu._ble_future_stale_grace_secs = getattr(
        mu,
        "BLE_FUTURE_STALE_GRACE_SECS",
        None,
    )
    mu._ble_interface_create_timeout_secs = getattr(
        mu,
        "BLE_INTERFACE_CREATE_TIMEOUT_FLOOR_SECS",
        None,
    )
    mu.RELAY_START_TIME = time.time()
    mu._relay_active_client_id = None
    mu._relay_connection_started_monotonic_secs = time.monotonic()
    mu._relay_rx_time_clock_skew_secs = None
    mu._relay_startup_drain_deadline_monotonic_secs = None
    startup_drain_timer = getattr(mu, "_relay_startup_drain_expiry_timer", None)
    _cancel_and_join_timer_like(startup_drain_timer, timeout=0.2)
    mu._relay_startup_drain_expiry_timer = None
    pending_connect_probe_timer = getattr(mu, "_pending_connect_time_probe_timer", None)
    _cancel_and_join_timer_like(pending_connect_probe_timer, timeout=0.2)
    mu._pending_connect_time_probe_timer = None
    startup_drain_complete_event = getattr(
        mu, "_relay_startup_drain_complete_event", None
    )
    if isinstance(startup_drain_complete_event, threading.Event):
        startup_drain_complete_event.set()
    else:
        startup_drain_complete_event = threading.Event()
        startup_drain_complete_event.set()
        mu._relay_startup_drain_complete_event = startup_drain_complete_event
    mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs = None
    mu._startup_packet_drain_applied = False
    mu._health_probe_request_deadlines = {}
    connect_condition = getattr(mu, "_connect_attempt_condition", None)
    if connect_condition is not None:
        with connect_condition:
            mu._connect_attempt_in_progress = False
            connect_condition.notify_all()

    yield
    try:
        iface = getattr(mu, "meshtastic_iface", None)
        if iface is not None:
            disconnect_iface = getattr(mu, "_disconnect_ble_interface", None)
            if callable(disconnect_iface):
                with contextlib.suppress(
                    asyncio.CancelledError,
                    asyncio.TimeoutError,
                    OSError,
                    RuntimeError,
                ):
                    disconnect_iface(iface, reason="test-reset")
            else:
                for method_name in ("disconnect", "close"):
                    iface_method = getattr(iface, method_name, None)
                    if not callable(iface_method):
                        continue
                    with contextlib.suppress(
                        asyncio.CancelledError,
                        asyncio.TimeoutError,
                        OSError,
                        RuntimeError,
                    ):
                        maybe_awaitable = iface_method()
                        _drain_awaitable_result_safely(maybe_awaitable, timeout=0.2)
                    break

        _cancel_and_drain_future_like(getattr(mu, "reconnect_task", None), timeout=0.2)
        _cancel_and_drain_future_like(
            getattr(mu, "reconnect_task_future", None), timeout=0.2
        )
        _cancel_and_drain_future_like(
            getattr(mu, "_metadata_future", None), timeout=0.2
        )
        startup_drain_timer = getattr(mu, "_relay_startup_drain_expiry_timer", None)
        _cancel_and_join_timer_like(startup_drain_timer, timeout=0.2)
        mu._relay_startup_drain_expiry_timer = None
        pending_connect_probe_timer = getattr(
            mu, "_pending_connect_time_probe_timer", None
        )
        _cancel_and_join_timer_like(pending_connect_probe_timer, timeout=0.2)
        mu._pending_connect_time_probe_timer = None
        mu.reconnect_task = None
        mu.reconnect_task_future = None
        mu._metadata_future = None
        mu._metadata_future_started_at = None
        if mu.subscribed_to_messages:
            with contextlib.suppress(Exception):
                pub.unsubscribe(mu.on_meshtastic_message, "meshtastic.receive")
        if mu.subscribed_to_connection_lost:
            with contextlib.suppress(Exception):
                pub.unsubscribe(
                    mu.on_lost_meshtastic_connection, "meshtastic.connection.lost"
                )
        mu.subscribed_to_messages = False
        mu.subscribed_to_connection_lost = False
        mu._callbacks_tearing_down = False
        cleanup_ble_future_state(mu)
        mu.shutdown_shared_executors()
        mu.meshtastic_iface = None
        mu.meshtastic_client = None
        connect_condition = getattr(mu, "_connect_attempt_condition", None)
        if connect_condition is not None:
            with connect_condition:
                mu._connect_attempt_in_progress = False
                connect_condition.notify_all()
    finally:
        for attr_name, original_value in original_values.items():
            setattr(mu, attr_name, original_value)


@pytest.fixture
def reset_matrix_utils_globals():
    """
    Temporarily reset key module-level state in mmrelay.matrix_utils for a test and restore it on teardown.

    Saves the original values of attributes such as `matrix_client`, `matrix_rooms`, and `bot_user_id`;
    sets those attributes to clean defaults for duration of the test, yields control to test,
    and restores the saved values on teardown. The module's `logger` and `config`
    are intentionally left unchanged.
    """
    import mmrelay.matrix_utils

    # Store original values (excluding logger and config to keep them functional)
    original_values = {
        "matrix_client": getattr(mmrelay.matrix_utils, "matrix_client", None),
        "matrix_rooms": getattr(mmrelay.matrix_utils, "matrix_rooms", None),
        "bot_user_id": getattr(mmrelay.matrix_utils, "bot_user_id", None),
    }

    # Reset mutable globals to a clean state; keep logger and config usable
    mmrelay.matrix_utils.matrix_client = None
    mmrelay.matrix_utils.matrix_rooms = None
    mmrelay.matrix_utils.bot_user_id = None

    yield

    # Restore original values (including Nones) to avoid state leakage
    for attr_name, original_value in original_values.items():
        setattr(mmrelay.matrix_utils, attr_name, original_value)


@pytest.fixture
def comprehensive_cleanup():
    """
    Perform thorough cleanup of asyncio resources, executors, event loops, and non-daemon threads after a test.

    When used as an autouse fixture, yields to the test and on teardown cancels pending asyncio tasks and waits for them to finish, shuts down the loop's default executor if present, closes and clears the event loop, runs garbage collection before and after thread cleanup, and joins remaining non-daemon threads to reduce resource leaks and test flakiness.
    """
    yield

    # Force cleanup of all async tasks and event loops
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        if not loop.is_closed():
            # Cancel all pending tasks
            pending_tasks = [
                task for task in asyncio.all_tasks(loop) if not task.done()
            ]
            for task in pending_tasks:
                task.cancel()

            # Wait for cancelled tasks to complete
            if pending_tasks:
                with contextlib.suppress(Exception):
                    loop.run_until_complete(
                        asyncio.gather(*pending_tasks, return_exceptions=True)
                    )

            # Shutdown any remaining executors
            if hasattr(loop, "_default_executor") and loop._default_executor:  # type: ignore[attr-defined]
                executor = loop._default_executor  # type: ignore[attr-defined]
                loop._default_executor = None  # type: ignore[attr-defined]
                executor.shutdown(wait=True)

            # Close the event loop
            loop.close()
    except RuntimeError:
        pass  # No event loop available

    # Set event loop to None to ensure clean state
    asyncio.set_event_loop(None)

    # Force garbage collection to clean up any remaining resources
    gc.collect()

    # Clean up any remaining threads (avoid daemon threads to prevent hangs)
    main_thread = threading.main_thread()
    for thread in threading.enumerate():
        if (
            thread is not main_thread
            and thread.is_alive()
            and not getattr(thread, "daemon", False)
            and hasattr(thread, "join")
        ):
            thread.join(timeout=0.1)

    # Force another garbage collection after thread cleanup
    gc.collect()


@pytest.fixture(autouse=True)
def mock_to_thread(monkeypatch, request):
    """
    Mock asyncio.to_thread to run synchronously for tests.

    This avoids creating separate threads during testing, ensuring that code designed to run
    in a thread (via asyncio.to_thread) executes immediately in the main thread. This simplifies
    testing with mocks (which are often not thread-safe) and ensures deterministic execution.

    When the ``no_global_mocks`` marker is applied to the test, this fixture does nothing,
    allowing tests to exercise real async scheduling and thread boundaries.
    """
    if request.node.get_closest_marker("no_global_mocks"):
        yield
        return

    async def _to_thread(func, *args, **kwargs):
        """
        Execute the given callable on the current thread and return its result.

        Parameters:
            func (Callable): The callable to invoke.
            *args: Positional arguments to pass to func.
            **kwargs: Keyword arguments to pass to func.

        Returns:
            The value returned by func. Exceptions raised by func propagate to the caller.
        """
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    yield


@pytest.fixture
def mock_room():
    """
    Provide a MagicMock representing a Matrix room for tests.

    Returns:
        MagicMock: A mock room object with `room_id` set to TEST_ROOM_ID.
    """
    mock_room = MagicMock()
    mock_room.room_id = TEST_ROOM_ID
    return mock_room


@pytest.fixture
def mock_event():
    """
    Create a mock Matrix message event object for tests.

    The returned MagicMock simulates a typical incoming message event and has the
    attributes `sender`, `body`, `source`, and `server_timestamp` set to sample
    values.

    Returns:
        MagicMock: Mock event with `sender` set to TEST_USER_ID,
        `body` set to "Hello, world!", `source` set to {"content": {"body": "Hello, world!"}},
        and `server_timestamp` set to 1234567890.
    """
    mock_event = MagicMock()
    mock_event.sender = TEST_USER_ID
    mock_event.body = "Hello, world!"
    mock_event.source = {"content": {"body": "Hello, world!"}}
    mock_event.server_timestamp = 1234567890
    return mock_event


@pytest.fixture
def test_config():
    """
    Provide a sample Meshtastic-Matrix integration configuration for tests.

    Returns:
        dict: A configuration dict with keys:
          - meshtastic: dict containing broadcast_enabled, prefix_enabled, prefix_format,
            message_interactions, and meshnet_name.
          - matrix_rooms: list of room mapping dicts each with `id` and `meshtastic_channel`.
          - matrix: dict containing `bot_user_id`.
    """
    return {
        "meshtastic": {
            "broadcast_enabled": True,
            "prefix_enabled": True,
            "prefix_format": "{display5}[M]: ",
            "message_interactions": {"reactions": False, "replies": False},
            "meshnet_name": "test_mesh",
        },
        "matrix_rooms": [
            {
                "id": TEST_ROOM_ID,
                "meshtastic_channel": 0,
            }
        ],
        "matrix": {"bot_user_id": TEST_BOT_USER_ID},
    }


@pytest.fixture(scope="session", autouse=True)
def isolate_mmrelay_home(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[Path, None, None]:
    """
    Create and set an isolated MMRELAY_HOME directory for the test session and restore the original value afterwards.

    Parameters:
        tmp_path_factory (pytest.TempPathFactory): Factory used to create a temporary directory for the isolated home.

    Yields:
        Path: Path to the temporary MMRELAY_HOME directory provided to the test.

    Description:
        Sets the MMRELAY_HOME environment variable to a temporary directory so tests do not write to the user's real home. Restores the original MMRELAY_HOME value (or unsets it) when the fixture completes.
    """
    tmp_home = tmp_path_factory.mktemp("mmrelay_test_home")
    # Store original if any
    original_home = os.environ.get("MMRELAY_HOME")
    os.environ["MMRELAY_HOME"] = str(tmp_home)

    yield tmp_home

    # Restore or unset
    if original_home is not None:
        os.environ["MMRELAY_HOME"] = original_home
    else:
        os.environ.pop("MMRELAY_HOME", None)


@pytest.fixture
def clean_migration_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Path, None, None]:
    """
    Provide a temporary clean MMRELAY_HOME directory for migration tests.

    Creates a directory at tmp_path / "clean_migration_home", sets the `MMRELAY_HOME`
    environment variable to that path, forces mmrelay.paths to re-resolve the home
    location, and removes any existing `migration_completed.flag` so tests run with
    no prior migration state.

    Yields:
        Path: Path to the created clean home directory.
    """
    home = tmp_path / "clean_migration_home"
    home.mkdir()

    # Override MMRELAY_HOME for this specific test
    monkeypatch.setenv("MMRELAY_HOME", str(home))

    # Force paths module to re-resolve home from the updated env var
    import mmrelay.paths

    mmrelay.paths.reset_home_override()

    # Ensure no migration state file exists
    state_file = home / "migration_completed.flag"
    state_file.unlink(missing_ok=True)

    yield home


@pytest.fixture(scope="session", autouse=True)
def _install_sqlite_provenance() -> Generator[None, None, None]:
    """
    Install sqlite3 connection provenance tracking for the test session.

    Patches `sqlite3.connect` to record metadata for each created connection so leaked connections can be reported during test failures, and restores the original `sqlite3.connect` on teardown.
    """
    _conn_provenance.install()
    yield
    _conn_provenance.uninstall()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """
    Set the current test nodeid for sqlite connection provenance tracking before any test phase runs.

    This ensures connections created during setup/fixtures are attributed to the correct test,
    rather than inheriting a stale nodeid from a previous test.
    """
    _conn_provenance.set_nodeid(item.nodeid)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """
    Pytest hook wrapper that appends a report section listing any tracked open sqlite connections when a test fails, and clears the provenance registry at teardown.

    Parameters:
        item: pytest.Item
            The test item being executed.
        call: pytest.CallInfo
            The call phase information passed by pytest; this function yields to allow the default report generation to proceed.

    Notes:
        - This is a pytest hook wrapper (generator-style) and yields once to obtain the test report.
        - When the report indicates a failure in the "call" phase, any open sqlite connections tracked by the global provenance recorder are added to the report as a section named "sqlite-connection-provenance".
        - On the "teardown" phase, the provenance registry is cleared to reset state between tests.
    """
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        open_conns = _conn_provenance.report_open()
        if open_conns:
            stacks = []
            for entry in open_conns:
                stacks.append(
                    f"  LEAKED conn={entry['conn_id']} db={entry['db_path']} "
                    f"created_in={entry['test_nodeid']} thread={entry['thread_name']}\n"
                    f"{entry['creation_stack']}"
                )
            report.sections.append(
                (
                    "sqlite-connection-provenance",
                    f"\n{len(open_conns)} OPEN sqlite connections at failure:\n"
                    + "\n---\n".join(stacks),
                )
            )
    if report.when == "teardown":
        _conn_provenance.clear_by_nodeid(item.nodeid)
