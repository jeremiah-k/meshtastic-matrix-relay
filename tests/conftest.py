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
    def __init__(self, message: str = "Whoami error") -> None:
        """
        Create a Whoami error carrying a human-readable message.

        Parameters:
            message (str): Error message describing the condition. Defaults to "Whoami error".

        Attributes:
            message (str): The provided error message (also available as the exception's first argument).
        """
        super().__init__(message)
        self.message: str = message


class MockSyncError(Exception):
    def __init__(
        self,
        message: str = "Sync error",
        status_code: str | None = None,
        retry_after_ms: int | None = None,
        soft_logout: bool = False,
    ):
        """
        Create a mock SyncError carrying the attributes used by matrix-nio for tests.

        Parameters:
            message (str): Human-readable error message.
            status_code (str | None): Optional error status code returned by the server.
            retry_after_ms (int | None): Optional suggested retry delay in milliseconds.
            soft_logout (bool): Whether the error indicates a soft logout condition.
        """
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retry_after_ms = retry_after_ms
        self.soft_logout = soft_logout


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
nio_mock.SyncError = MockSyncError
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
    Replace mmrelay.meshtastic_utils._submit_coro with a test helper that ensures passed coroutines are executed and awaited so AsyncMock coroutines run to completion.

    This pytest fixture patches the module-level _submit_coro to a mock implementation that schedules a coroutine on an available running event loop when possible, otherwise runs it synchronously in a temporary loop. It yields control to the test and restores the original function on teardown.
    """
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
    Temporarily reset key module-level state in mmrelay.meshtastic_utils for a test and restore it on teardown.

    Saves the original values of attributes such as `config`, `meshtastic_client`, reconnect/shutdown flags and tasks,
    subscription flags, and internal futures; sets those attributes to clean defaults for the duration of the test,
    yields control to the test, and restores the saved values on teardown. The module's `logger` and `event_loop`
    are intentionally left unchanged.
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
        "_metadata_future": getattr(mu, "_metadata_future", None),
        "_ble_future": getattr(mu, "_ble_future", None),
    }

    # Reset mutable globals to a clean state; keep logger and event_loop usable
    mu.config = None
    mu.meshtastic_client = None
    mu.reconnecting = False
    mu.shutting_down = False
    mu.reconnect_task = None
    mu.subscribed_to_messages = False
    mu.subscribed_to_connection_lost = False
    mu._metadata_future = None
    mu._ble_future = None

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


@pytest.fixture
def mock_room():
    """
    Provide a MagicMock representing a Matrix room for tests.

    Returns:
        MagicMock: A mock room object with `room_id` set to "!room:matrix.org".
    """
    mock_room = MagicMock()
    mock_room.room_id = "!room:matrix.org"
    return mock_room


@pytest.fixture
def mock_event():
    """
    Create a mock Matrix message event object for tests.

    The returned MagicMock simulates a typical incoming message event and has the
    attributes `sender`, `body`, `source`, and `server_timestamp` set to sample
    values.

    Returns:
        MagicMock: Mock event with `sender` set to "@user:matrix.org",
        `body` set to "Hello, world!", `source` set to {"content": {"body": "Hello, world!"}},
        and `server_timestamp` set to 1234567890.
    """
    mock_event = MagicMock()
    mock_event.sender = "@user:matrix.org"
    mock_event.body = "Hello, world!"
    mock_event.source = {"content": {"body": "Hello, world!"}}
    mock_event.server_timestamp = 1234567890
    return mock_event


@pytest.fixture
def test_config():
    """
    Fixture providing a sample configuration for Meshtastic ↔ Matrix integration used by tests.

    Returns:
        dict: Configuration with keys:
          - meshtastic: dict with
              - broadcast_enabled (bool): whether broadcasting to mesh is enabled.
              - prefix_enabled (bool): whether Meshtastic message prefixes are applied.
              - prefix_format (str): format string for message prefixes (supports truncated vars).
              - message_interactions (dict): interaction toggles, e.g. {'reactions': bool, 'replies': bool}.
              - meshnet_name (str): logical mesh network name used in templates.
          - matrix_rooms: list of room mappings where each item is a dict containing:
              - id (str): Matrix room ID (e.g. "!room:matrix.org").
              - meshtastic_channel (int): Meshtastic channel number.
          - matrix: dict with
              - bot_user_id (str): Matrix user ID of the bot.
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
                "id": "!room:matrix.org",
                "meshtastic_channel": 0,
            }
        ],
        "matrix": {"bot_user_id": "@bot:matrix.org"},
    }
