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
        """
        Initialize the object and create an empty `data` dictionary for storing arbitrary key/value pairs.
        """
        self.data = {}


class MockStaticmapsContext:
    def __init__(self):
        """
        Create a lightweight test double for a Staticmaps rendering context.

        Sets attributes used by tests:
        - objects: list collecting objects added with add_object.
        - tile_provider: configured tile provider or None.
        - zoom: current zoom level or None.
        """
        self.objects = []
        self.tile_provider = None
        self.zoom = None

    def set_tile_provider(self, provider):
        """
        Assigns the map tile provider used by the rendering context.

        Parameters:
            provider: A tile-provider callable or an object implementing the renderer's provider interface. If a callable, it is expected to accept tile coordinates and zoom (commonly `x, y, z`) and return the tile data (for example image bytes or an image-like object).
        """
        self.tile_provider = provider

    def set_zoom(self, zoom):
        """
        Set the rendering zoom level for the context.

        Parameters:
            zoom (int|float): Zoom level to apply; stored on the context as the `zoom` attribute.
        """
        self.zoom = zoom

    def add_object(self, obj):
        """
        Add a renderable object to the rendering context.

        Parameters:
            obj: A renderable object compatible with the context's rendering API; it will be appended to the context's internal `objects` list for later rendering.
        """
        self.objects.append(obj)

    def render_pillow(self, _width, _height):
        """
        Render the map into a Pillow-compatible image (mock) for testing.

        Parameters:
            _width (int): Output image width in pixels (unused in mock).
            _height (int): Output image height in pixels (unused in mock).

        Returns:
            PIL.Image.Image (MagicMock): A MagicMock that mimics a Pillow Image object (suitable for tests that call image methods like `save`).
        """
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
        """
        Create a MockLatLng from latitude and longitude expressed in decimal degrees.

        Parameters:
            lat (float): Latitude in decimal degrees.
            lon (float): Longitude in decimal degrees.

        Returns:
            MockLatLng: A mock LatLng object created via MockLatLng.from_degrees.
        """
        return MockLatLng.from_degrees(lat, lon)


sys.modules["staticmaps"] = MockStaticmapsModule()


@pytest.fixture(autouse=True)
def meshtastic_loop_safety(monkeypatch):
    """
    Module-scoped pytest fixture that provides a dedicated asyncio event loop for tests that interact with mmrelay.meshtastic_utils.

    Creates a fresh event loop, assigns it to mmrelay.meshtastic_utils.event_loop for the duration of the test module, yields the loop to tests, and on teardown cancels any remaining tasks, awaits their completion, closes the loop, and clears the global event loop reference.

    Yields:
        asyncio.AbstractEventLoop: a new event loop isolated to the test module.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    monkeypatch.setattr(mu, "event_loop", loop)

    yield loop

    # Teardown: Clean up the loop with timeout protection
    try:
        tasks = asyncio.all_tasks(loop=loop)
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                # Create a timeout task to prevent hanging
                async def wait_with_timeout():
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=1.0,  # 1 second timeout
                    )

                # Run the timeout-wrapped wait
                if loop.is_running():
                    # If loop is running, we can't use run_until_complete
                    # Just proceed without waiting
                    pass
                else:
                    loop.run_until_complete(wait_with_timeout())
            except (asyncio.TimeoutError, Exception):
                # If timeout or any other exception, continue cleanup
                pass
    finally:
        try:
            if not loop.is_running():
                loop.close()
        except Exception:
            pass
        try:
            asyncio.set_event_loop(None)
        except Exception:
            pass


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
        "test_async_db_utils",
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

        # Check if this is an AsyncMock coroutine and handle it specially
        if "AsyncMockMixin" in coro.__qualname__:
            # AsyncMock coroutines need to be consumed by sending None to them
            try:
                # Send None to start and complete the coroutine
                coro.send(None)
            except StopIteration as e:
                # Coroutine completed successfully
                result = e.value
                future = Future()
                future.set_result(result)
                return future
            except Exception as e:
                # Coroutine failed
                future = Future()
                future.set_exception(e)
                return future
        else:
            # Regular coroutines - run in a new loop
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
                try:
                    temp_loop.close()
                except Exception:
                    pass  # Ignore errors during cleanup

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


# Removed comprehensive_cleanup fixture as it was causing hanging during pytest teardown


@pytest.fixture
def mock_event_loop(monkeypatch):
    """
    Patch asyncio loop helpers so run_in_executor executes callables synchronously during tests.

    Replace asyncio.get_running_loop and asyncio.get_event_loop with wrappers that ensure the returned event loop's run_in_executor invokes the callable immediately on the current thread and returns an already-completed Future with the callable's result or exception. This prevents background thread creation from run_in_executor calls and makes test behavior and teardown deterministic.
    """

    original_get_running_loop = asyncio.get_running_loop
    original_get_event_loop = asyncio.get_event_loop

    def _patch_loop(loop: asyncio.AbstractEventLoop) -> asyncio.AbstractEventLoop:
        """
        Patch an event loop so its `run_in_executor` executes callables immediately on the loop's thread.

        Replaces `loop.run_in_executor` with a synchronous implementation that ignores the executor argument and returns an `asyncio.Future` already resolved with the callable's return value or exception. Also sets `loop._mmrelay_run_in_executor_patched = True` to mark the loop as patched. If `loop` is None or already patched, the input is returned unchanged.

        Parameters:
            loop (asyncio.AbstractEventLoop | None): Event loop to patch; may be None.

        Returns:
            asyncio.AbstractEventLoop | None: The same loop instance (patched) or None if input was None.
        """
        if loop is None:
            return loop
        if getattr(loop, "_mmrelay_run_in_executor_patched", False):
            return loop

        def run_in_executor_sync(_executor, func, *args, **kwargs):
            """
            Invoke a callable immediately on the current thread.

            Parameters:
                _executor: Ignored; present for API compatibility with executor-style APIs.
                func (Callable): Callable to invoke.
                *args: Positional arguments forwarded to `func`.
                **kwargs: Keyword arguments forwarded to `func`.

            Returns:
                asyncio.Future: Future whose result is the value returned by `func`, or whose exception is the exception raised by `func`.
            """
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
        """
        Get the currently running asyncio event loop patched for test compatibility.

        The returned loop has its `run_in_executor` implementation replaced so executor callables run synchronously, enabling deterministic behavior in tests.

        Returns:
            asyncio.AbstractEventLoop: The active event loop whose `run_in_executor` executes callables synchronously.
        """
        loop = original_get_running_loop()
        return _patch_loop(loop)

    def patched_get_event_loop():
        """
        Return the current asyncio event loop after applying test-specific patches.

        Calls the original event loop getter to obtain the active loop, then passes it to _patch_loop and returns the patched loop. The patched loop exposes a run_in_executor that executes callables synchronously and returns a completed Future with the callable's result or exception, preventing background thread creation during tests.
        """
        loop = original_get_event_loop()
        return _patch_loop(loop)

    monkeypatch.setattr(asyncio, "get_running_loop", patched_get_running_loop)
    monkeypatch.setattr(asyncio, "get_event_loop", patched_get_event_loop)

    yield
