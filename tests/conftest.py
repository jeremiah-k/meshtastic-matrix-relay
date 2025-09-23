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
import logging

# Preserve references to built-in modules that should NOT be mocked
import queue
import threading
import time
from concurrent.futures import Future
from unittest.mock import MagicMock

import pytest

# Mock all external dependencies before any imports can occur
# This prevents ImportError and allows tests to run in isolation
meshtastic_mock = MagicMock()
sys.modules["meshtastic"] = meshtastic_mock
sys.modules["meshtastic.protobuf"] = MagicMock()
sys.modules["meshtastic.protobuf.portnums_pb2"] = MagicMock()
sys.modules["meshtastic.protobuf.portnums_pb2"].PortNum = MagicMock()
sys.modules["meshtastic.protobuf.portnums_pb2"].PortNum.DETECTION_SENSOR_APP = 1
sys.modules["meshtastic.protobuf.mesh_pb2"] = MagicMock()
sys.modules["meshtastic.ble_interface"] = MagicMock()
sys.modules["meshtastic.serial_interface"] = MagicMock()
sys.modules["meshtastic.tcp_interface"] = MagicMock()
sys.modules["meshtastic.mesh_interface"] = MagicMock()
meshtastic_mock.BROADCAST_ADDR = "^all"

nio_mock = MagicMock()
sys.modules["nio"] = nio_mock
sys.modules["nio.events"] = MagicMock()
sys.modules["nio.events.room_events"] = MagicMock()
sys.modules["nio.event_builders"] = MagicMock()

pil_mock = MagicMock()
pil_image_mock = MagicMock()
pil_imagedraw_mock = MagicMock()
sys.modules["PIL"] = pil_mock
sys.modules["PIL.Image"] = pil_image_mock
sys.modules["PIL.ImageDraw"] = pil_imagedraw_mock
pil_mock.Image = pil_image_mock
pil_mock.ImageDraw = pil_imagedraw_mock

certifi_mock = MagicMock()
certifi_mock.where.return_value = "/fake/cert/path.pem"
sys.modules["certifi"] = certifi_mock

serial_mock = MagicMock()
sys.modules["serial"] = serial_mock
sys.modules["serial.tools"] = MagicMock()
sys.modules["serial.tools.list_ports"] = MagicMock()

sys.modules["bleak"] = MagicMock()
sys.modules["pubsub"] = MagicMock()
sys.modules["matplotlib"] = MagicMock()
sys.modules["matplotlib.pyplot"] = MagicMock()
sys.modules["requests"] = MagicMock()
sys.modules["markdown"] = MagicMock()
sys.modules["haversine"] = MagicMock()
sys.modules["schedule"] = MagicMock()
sys.modules["platformdirs"] = MagicMock()
sys.modules["py_staticmaps"] = MagicMock()


# Now that mocks are in place, we can import the application code
import mmrelay.meshtastic_utils as mu  # noqa: E402

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


# Create proper mock classes that can be used with isinstance()
class MockMatrixRoom:
    pass


class MockReactionEvent:
    pass


class MockRoomMessageEmote:
    pass


class MockRoomMessageNotice:
    pass


class MockRoomMessageText:
    pass


class MockRoomEncryptionEvent:
    pass


class MockMegolmEvent:
    pass


class MockWhoamiError(Exception):
    def __init__(self, message="Whoami error"):
        """
        Initialize the Whoami error exception.

        Parameters:
            message (str): Human-readable error message. Defaults to "Whoami error".

        Attributes:
            message (str): The provided message (also available as the exception's first arg).
        """
        super().__init__(message)
        self.message = message


nio_mock.AsyncClientConfig = MagicMock()
nio_mock.MatrixRoom = MockMatrixRoom
nio_mock.ReactionEvent = MockReactionEvent
nio_mock.RoomMessageEmote = MockRoomMessageEmote
nio_mock.RoomMessageNotice = MockRoomMessageNotice
nio_mock.RoomMessageText = MockRoomMessageText
nio_mock.RoomEncryptionEvent = MockRoomEncryptionEvent
nio_mock.MegolmEvent = MockMegolmEvent
nio_mock.UploadResponse = MagicMock()
nio_mock.WhoamiError = MockWhoamiError
sys.modules["nio.events.room_events"].RoomMemberEvent = MagicMock()


class MockPILImage:
    def save(self, *args, **kwargs):
        """
        No-op save method that accepts any positional and keyword arguments and does nothing.

        This placeholder satisfies interfaces that expect a `save` method (for example, objects that persist state or files)
        but intentionally performs no action. It accepts arbitrary arguments for compatibility and always returns None.
        """
        pass


pil_image_mock.Image = MockPILImage


class SerialException(Exception):
    pass


serial_mock.SerialException = SerialException


class BleakError(Exception):
    pass


class BleakDBusError(BleakError):
    pass


class BleakExcModule:
    BleakError = BleakError
    BleakDBusError = BleakDBusError


sys.modules["bleak.exc"] = BleakExcModule()
sys.modules["bleak"].BleakError = BleakError
sys.modules["bleak"].BleakDBusError = BleakDBusError


class MockLatLng:
    @classmethod
    def from_degrees(cls, lat, lng):
        """
        Create a new instance representing the given latitude and longitude in degrees.

        This is a stand-in/mock implementation used in tests. Parameters `lat` and `lng`
        are expected to be numeric degrees but are not validated or stored by this mock;
        the method simply returns a new instance of the class.

        Parameters:
            lat (float): Latitude in degrees (mock parameter, not stored).
            lng (float): Longitude in degrees (mock parameter, not stored).

        Returns:
            object: A new instance of the class (empty/mock).
        """
        return cls()


class MockLatLngRect:
    @classmethod
    def from_point(cls, point):
        """
        Create a new instance from a point.

        This stand-in implementation ignores the provided `point` and returns a default instance of the class.
        Parameters:
            point: The input point (ignored by this implementation).
        Returns:
            An instance of `cls`.
        """
        return cls()


class MockS2Module:
    LatLng = MockLatLng
    LatLngRect = MockLatLngRect


sys.modules["s2sphere"] = MockS2Module()


class MockStaticmapsObject:
    def __init__(self):
        self.data = {}


class MockStaticmapsContext:
    def __init__(self):
        self.objects = []
        self.tile_provider = None
        self.zoom = None

    def set_tile_provider(self, provider):
        self.tile_provider = provider

    def set_zoom(self, zoom):
        self.zoom = zoom

    def add_object(self, obj):
        self.objects.append(obj)

    def render_pillow(self, width, height):
        return MagicMock()


class MockStaticmapsModule:
    Object = MockStaticmapsObject
    Context = MockStaticmapsContext
    PillowRenderer = MagicMock
    CairoRenderer = MagicMock
    SvgRenderer = MagicMock
    PixelBoundsT = tuple
    tile_provider_OSM = object()

    @staticmethod
    def create_latlng(lat, lon):
        return MockLatLng.from_degrees(lat, lon)


sys.modules["staticmaps"] = MockStaticmapsModule()


@pytest.fixture(autouse=True)
def meshtastic_loop_safety(monkeypatch):
    """
    Create and provide a dedicated asyncio event loop for tests that interact with meshtastic_utils, and ensure it is fully cleaned up after the test module.

    This module-scoped pytest fixture creates a fresh event loop, assigns it to mmrelay.meshtastic_utils.event_loop for use during tests, yields the loop, then cancels any remaining tasks, waits for them to finish, closes the loop, and clears the global event loop reference on teardown.

    Yields:
        asyncio.AbstractEventLoop: the newly created event loop for the test module.
    """
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


@pytest.fixture(autouse=True)
def cleanup_asyncmock_objects(request):
    """
    Force garbage collection after tests that commonly create AsyncMock objects to avoid "never awaited" RuntimeWarning messages.

    This fixture yields to run the test, then inspects the requesting test filename; if it matches a known set of test-name patterns that use AsyncMock, it runs gc.collect() inside a warnings suppression context that ignores "never awaited" RuntimeWarning messages raised by lingering coroutine objects.

    Parameters:
        request: The pytest `Request` object for the executing test (used to determine the test filename).
    """
    yield

    # Only force garbage collection for tests that might create AsyncMock objects
    test_file = request.node.fspath.basename

    # List of test files/patterns that use AsyncMock
    asyncmock_patterns = [
        "test_async_patterns",
        "test_matrix_utils",
        "test_matrix_utils_edge_cases",
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
        import gc
        import warnings

        # Suppress RuntimeWarning about unawaited coroutines during cleanup
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", category=RuntimeWarning, message=".*never awaited.*"
            )
            gc.collect()


@pytest.fixture(autouse=True)
def mock_submit_coro(monkeypatch):
    """
    Pytest fixture that replaces the `_submit_coro` function in `meshtastic_utils` with a mock that synchronously runs and awaits coroutines in a temporary event loop.

    This ensures that AsyncMock coroutines are properly awaited during tests, preventing "never awaited" warnings and allowing side effects to occur as expected.
    """
    import asyncio
    import inspect

    def mock_submit(coro, loop=None):
        """
        Synchronously runs a coroutine in a temporary event loop and returns a Future with its result or exception.

        If the input is not a coroutine, returns None. This function is designed to ensure that AsyncMock coroutines are properly awaited during testing, preventing "never awaited" warnings and triggering any side effects.

        Parameters:
            coro: The coroutine to execute.
            loop: Unused; present for compatibility.

        Returns:
            Future: A Future containing the result or exception from the coroutine, or None if the input is not a coroutine.
        """
        if not inspect.iscoroutine(coro):  # Not a coroutine
            return None

        # For AsyncMock coroutines, we need to actually await them to get the result
        # and prevent "never awaited" warnings, while also triggering any side effects
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


# Ensure built-in modules are not accidentally mocked
ensure_builtins_not_mocked()


@pytest.fixture(autouse=True)
def reset_custom_data_dir():
    """
    Autouse pytest fixture that resets mmrelay.config.custom_data_dir to None for each test and restores its original value afterwards.

    Before the test runs, stores the current value of mmrelay.config.custom_data_dir (if any) and sets it to None to ensure tests do not share or depend on a persistent custom data directory. After the test yields, the original value is restored.
    """
    import mmrelay.config

    # Store original value
    original_custom_data_dir = getattr(mmrelay.config, "custom_data_dir", None)

    # Reset to None before test
    mmrelay.config.custom_data_dir = None

    yield

    # Restore original value after test
    mmrelay.config.custom_data_dir = original_custom_data_dir


@pytest.fixture(autouse=True)
def reset_banner_flag():
    """
    Autouse pytest fixture that resets mmrelay.main._banner_printed to False before each test.

    This ensures the module-level banner-printed flag does not persist state between tests. The fixture yields once to allow the test to run with the reset state.
    """
    import mmrelay.main

    mmrelay.main._banner_printed = False
    yield


@pytest.fixture
def comprehensive_cleanup():
    """
    Comprehensive resource cleanup fixture for tests that create async resources.

    This fixture ensures all system resources are properly cleaned up after tests,
    preventing resource warnings about unclosed sockets and event loops.
    Particularly important for Python 3.10 compatibility in CI environments.
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
            if hasattr(loop, "_default_executor") and loop._default_executor:
                executor = loop._default_executor
                loop._default_executor = None
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


@pytest.fixture
def mock_event_loop(monkeypatch):
    """
    Patch asyncio loop helpers so `run_in_executor` executes synchronously for the active event loop.

    The fixture intercepts calls to ``asyncio.get_running_loop`` (and ``get_event_loop`` for
    backwards-compat) so the returned loop has its ``run_in_executor`` method replaced with a
    synchronous implementation. The replacement executes the callable immediately on the current
    thread and returns a ``Future`` completed with the result, mirroring the contract expected by
    callers while avoiding background thread creation that can prolong test teardown.
    """

    original_get_running_loop = asyncio.get_running_loop
    original_get_event_loop = asyncio.get_event_loop

    def _patch_loop(loop: asyncio.AbstractEventLoop) -> asyncio.AbstractEventLoop:
        if loop is None:
            return loop
        if getattr(loop, "_mmrelay_run_in_executor_patched", False):
            return loop

        def run_in_executor_sync(_executor, func, *args, **kwargs):
            future = loop.create_future()
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                future.set_exception(exc)
            else:
                future.set_result(result)
            return future

        loop.run_in_executor = run_in_executor_sync  # type: ignore[assignment]
        loop._mmrelay_run_in_executor_patched = True  # type: ignore[attr-defined]
        return loop

    def patched_get_running_loop():
        loop = original_get_running_loop()
        return _patch_loop(loop)

    def patched_get_event_loop():
        loop = original_get_event_loop()
        return _patch_loop(loop)

    monkeypatch.setattr(asyncio, "get_running_loop", patched_get_running_loop)
    monkeypatch.setattr(asyncio, "get_event_loop", patched_get_event_loop)

    yield
