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
meshtastic_mock.BROADCAST_NUM = 4294967295
sys.modules["meshtastic.mesh_interface"].BROADCAST_NUM = 4294967295
sys.modules["meshtastic.mesh_interface"].BROADCAST_ADDR = "^all"

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


class RequestException(Exception):
    pass


class HTTPError(RequestException):
    pass


class ConnectionError(RequestException):
    pass


class Timeout(RequestException):
    pass


class MockRequestsExceptions:
    RequestException = RequestException
    HTTPError = HTTPError
    ConnectionError = ConnectionError
    Timeout = Timeout


sys.modules["requests"].exceptions = MockRequestsExceptions()

# Add top-level aliases for code that uses requests.RequestException directly
sys.modules["requests"].RequestException = RequestException
sys.modules["requests"].HTTPError = HTTPError
sys.modules["requests"].ConnectionError = ConnectionError
sys.modules["requests"].Timeout = Timeout
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
    Color = MagicMock
    Circle = MagicMock

    @staticmethod
    def create_latlng(lat, lon):
        """
        Create a MockLatLng representing the given geographic coordinates.

        Parameters:
            lat (float): Latitude in degrees.
            lon (float): Longitude in degrees.

        Returns:
            MockLatLng: A mock LatLng object for the supplied coordinates.
        """
        return MockLatLng.from_degrees(lat, lon)


sys.modules["staticmaps"] = MockStaticmapsModule()


@pytest.fixture(autouse=True)
def meshtastic_loop_safety(monkeypatch):
    """
    Function-scoped pytest fixture that provides a dedicated asyncio event loop for tests that interact with mmrelay.meshtastic_utils.

    Creates a fresh event loop, assigns it to mmrelay.meshtastic_utils.event_loop for the duration of each test function, yields the loop to tests, and on teardown cancels any remaining tasks, awaits their completion, closes the loop, and clears the global event loop reference.

    Yields:
        asyncio.AbstractEventLoop: a new event loop isolated to each test function.
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

        # Prefer the currently running loop (pytest-asyncio) to avoid spawning many temporary loops
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop and running_loop.is_running():
            return running_loop.create_task(coro)

        target_loop = loop
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
    Reset the mmrelay.main module's banner-printed flag before each test.

    This autouse pytest fixture sets mmrelay.main._banner_printed to False and yields once so the test executes with the cleared flag.
    """
    import mmrelay.main

    mmrelay.main._banner_printed = False
    yield


@pytest.fixture
def reset_meshtastic_globals():
    """
    Reset and restore key globals in mmrelay.meshtastic_utils to ensure test isolation.

    Saves the module-level state for attributes such as `config`, `meshtastic_client`,
    reconnect-related flags and tasks, and subscription flags; sets those attributes
    to a clean default state for the duration of a test, yields control to the
    test, and restores the original values on teardown. The fixture intentionally
    does not modify the module's logger or event loop references.
    """
    import mmrelay.meshtastic_utils as mu

    # Store original values (excluding logger and event_loop to keep them functional)
    original_values = {
        "config": getattr(mu, "config", None),
        "meshtastic_client": getattr(mu, "meshtastic_client", None),
        "reconnecting": getattr(mu, "reconnecting", False),
        "shutting_down": getattr(mu, "shutting_down", False),
        "reconnect_task": getattr(mu, "reconnect_task", None),
        "subscribed_to_messages": getattr(mu, "subscribed_to_messages", False),
        "subscribed_to_connection_lost": getattr(
            mu, "subscribed_to_connection_lost", False
        ),
    }

    # Reset mutable globals to a clean state; keep logger and event_loop usable
    mu.config = None
    mu.meshtastic_client = None
    mu.reconnecting = False
    mu.shutting_down = False
    mu.reconnect_task = None
    mu.subscribed_to_messages = False
    mu.subscribed_to_connection_lost = False

    yield

    # Restore original values (including Nones) to avoid state leakage
    for attr_name, original_value in original_values.items():
        setattr(mu, attr_name, original_value)


@pytest.fixture
def comprehensive_cleanup():
    """
    Pytest fixture that performs a thorough cleanup of async resources, event loops, executors, and non-daemon threads after a test.

    When used as an autouse fixture, it yields to the test and on teardown:
    - cancels pending asyncio tasks and waits for their completion,
    - shuts down the loop's default executor (if any) and closes the event loop,
    - clears the global event loop reference,
    - runs garbage collection before and after thread cleanup,
    - joins any remaining non-daemon threads for a short timeout.

    This prevents resource warnings about unclosed sockets, executors, or event loops and reduces flaky CI failures related to lingering async resources.
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


@pytest.fixture(autouse=True)
def mock_to_thread(monkeypatch):
    """
    Mock asyncio.to_thread to run synchronously for tests.

    This avoids creating separate threads during testing, ensuring that code designed to run
    in a thread (via asyncio.to_thread) executes immediately in the main thread. This simplifies
    testing with mocks (which are often not thread-safe) and ensures deterministic execution.
    """

    async def _to_thread(func, *args, **kwargs):
        """
        Execute a callable on the current thread and return its result.

        Parameters:
                func (Callable): The callable to invoke.
                *args: Positional arguments to pass to `func`.
                **kwargs: Keyword arguments to pass to `func`.

        Returns:
                The value returned by `func`. Exceptions raised by `func` propagate to the caller.
        """
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
