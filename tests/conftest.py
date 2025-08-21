"""
Pytest configuration and fixtures for MMRelay tests.

This file sets up comprehensive mocking for external dependencies
to ensure tests can run without requiring actual hardware or network connections.
"""

import asyncio
import logging

# Preserve references to built-in modules that should NOT be mocked
import queue
import sys
import threading
import time
from concurrent.futures import Future
from unittest.mock import MagicMock

import pytest

import mmrelay.meshtastic_utils as mu

# Mock all external dependencies before any imports
# This prevents ImportError and allows tests to run in isolation


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
    Restores original Python built-in modules if they have been accidentally mocked during testing.

    This function checks for mocked versions of critical built-in modules and replaces them with their original references. It also reloads the logging module if it was mocked to ensure proper logging functionality is maintained.
    """
    for name, module in _BUILTIN_MODULES.items():
        if name in sys.modules and hasattr(sys.modules[name], "_mock_name"):
            # Restore the original module if it was mocked
            sys.modules[name] = module

    # Extra protection for logging system - but DON'T reload it!
    # Reloading logging can cause system freezes and deadlocks
    import logging

    if hasattr(logging, "_mock_name"):
        # If logging got mocked, restore from our saved reference instead of reloading
        sys.modules["logging"] = _BUILTIN_MODULES["logging"]


# Mock Meshtastic modules comprehensively
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

# Set up meshtastic constants
meshtastic_mock.BROADCAST_ADDR = "^all"

# Mock Matrix-nio modules comprehensively
nio_mock = MagicMock()
sys.modules["nio"] = nio_mock
sys.modules["nio.events"] = MagicMock()
sys.modules["nio.events.room_events"] = MagicMock()
sys.modules["nio.event_builders"] = MagicMock()


# Create proper mock classes for nio that can be used with isinstance()
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
    """Mock WhoamiError that inherits from Exception for isinstance checks."""

    def __init__(self, message="Whoami error"):
        super().__init__(message)
        self.message = message


# Mock specific nio classes that are imported directly
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

# Mock RoomMemberEvent from nio.events.room_events
sys.modules["nio.events.room_events"].RoomMemberEvent = MagicMock()


# Mock PIL/Pillow
# Create proper PIL mock classes that work with real imports
class MockPILImage:
    """Mock PIL Image class that can be used as a spec."""

    def save(self, *args, **kwargs):
        """Mock save method for PIL Image."""
        pass


# Create a mock that allows attribute access like the real PIL module
pil_mock = MagicMock()
pil_image_mock = MagicMock()
pil_image_mock.Image = MockPILImage
pil_imagedraw_mock = MagicMock()

sys.modules["PIL"] = pil_mock
sys.modules["PIL.Image"] = pil_image_mock
sys.modules["PIL.ImageDraw"] = pil_imagedraw_mock

# Also set attributes on the main PIL mock for direct access
pil_mock.Image = pil_image_mock
pil_mock.ImageDraw = pil_imagedraw_mock

# Mock other external dependencies (but avoid Python built-ins)
# Mock certifi with proper where() function
certifi_mock = MagicMock()
certifi_mock.where.return_value = "/fake/cert/path.pem"
sys.modules["certifi"] = certifi_mock


# Don't mock ssl module - it can interfere with logging and other system components
# Instead, we'll mock ssl.create_default_context at the test level when needed
# Create proper exception class for serial
class SerialException(Exception):
    """Mock SerialException for testing."""

    pass


# Create serial module with proper exception
serial_mock = MagicMock()
serial_mock.SerialException = SerialException
sys.modules["serial"] = serial_mock
sys.modules["serial.tools"] = MagicMock()
sys.modules["serial.tools.list_ports"] = MagicMock()


# Create proper exception classes for bleak that inherit from Exception
class BleakError(Exception):
    """Mock BleakError exception for testing."""

    pass


class BleakDBusError(BleakError):
    """Mock BleakDBusError exception for testing."""

    pass


# Create a proper module-like object for bleak.exc
class BleakExcModule:
    BleakError = BleakError
    BleakDBusError = BleakDBusError


sys.modules["bleak"] = MagicMock()
sys.modules["bleak.exc"] = BleakExcModule()

# Also add the exceptions to the main bleak module for direct import
sys.modules["bleak"].BleakError = BleakError
sys.modules["bleak"].BleakDBusError = BleakDBusError
sys.modules["pubsub"] = MagicMock()
sys.modules["matplotlib"] = MagicMock()
sys.modules["matplotlib.pyplot"] = MagicMock()
sys.modules["requests"] = MagicMock()
sys.modules["markdown"] = MagicMock()
sys.modules["haversine"] = MagicMock()
sys.modules["schedule"] = MagicMock()
sys.modules["platformdirs"] = MagicMock()
sys.modules["py_staticmaps"] = MagicMock()


# Create proper mock classes for s2sphere
class MockLatLng:
    """Mock LatLng class for s2sphere."""

    @classmethod
    def from_degrees(cls, lat, lng):
        return cls()


class MockLatLngRect:
    """Mock LatLngRect class for s2sphere."""

    @classmethod
    def from_point(cls, point):
        return cls()


class MockS2Module:
    LatLng = MockLatLng
    LatLngRect = MockLatLngRect


sys.modules["s2sphere"] = MockS2Module()

# Don't mock Rich at all - it can interfere with logging handlers
# Rich is optional and tests should work without it
# If Rich is needed for specific tests, mock it at the test level


@pytest.fixture(autouse=True)
def meshtastic_loop_safety(monkeypatch):
    """
    Module-scoped pytest fixture that creates and manages a dedicated asyncio event loop for tests using meshtastic_utils.

    Ensures that a fresh event loop is used for each test module, assigns it to meshtastic_utils, and performs thorough cleanup of all tasks and the loop after tests complete to prevent AsyncMock contamination and event loop leakage.

    Yields:
        loop (asyncio.AbstractEventLoop): The newly created event loop for use in tests.
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
    Forces garbage collection after tests likely to use AsyncMock to prevent "never awaited" warnings.

    This fixture runs after tests whose filenames match known patterns associated with AsyncMock usage, ensuring that AsyncMock objects are promptly cleaned up and do not trigger warnings in subsequent tests.
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
            warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*never awaited.*")
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
