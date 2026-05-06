"""
Mock external dependencies for MMRelay tests.

Patches sys.modules and provides mock exception/instance classes so tests
can run without requiring actual hardware or network connections.
Intended to be imported by tests/conftest.py (which re-exports symbols).
"""

import sys
from unittest.mock import MagicMock

# ── sys.modules mocking ───────────────────────────────────────────────
# Mock all external dependencies before any imports can occur

meshtastic_mock = MagicMock()
sys.modules["meshtastic"] = meshtastic_mock
sys.modules["meshtastic.protobuf"] = MagicMock()
sys.modules["meshtastic.protobuf.portnums_pb2"] = MagicMock()
sys.modules["meshtastic.protobuf.portnums_pb2"].PortNum = MagicMock()  # type: ignore[attr-defined]
sys.modules["meshtastic.protobuf.portnums_pb2"].PortNum.DETECTION_SENSOR_APP = 1
sys.modules["meshtastic.protobuf.mesh_pb2"] = MagicMock()
sys.modules["meshtastic.ble_interface"] = MagicMock()
sys.modules["meshtastic.serial_interface"] = MagicMock()
sys.modules["meshtastic.tcp_interface"] = MagicMock()
sys.modules["meshtastic.mesh_interface"] = MagicMock()
meshtastic_mock.BROADCAST_ADDR = "^all"
meshtastic_mock.BROADCAST_NUM = 4294967295
sys.modules["meshtastic.mesh_interface"].BROADCAST_NUM = 4294967295  # type: ignore[attr-defined]
sys.modules["meshtastic.mesh_interface"].BROADCAST_ADDR = "^all"  # type: ignore[attr-defined]

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
pubsub_mock = MagicMock()
pubsub_mock.__path__ = ["/fake/pubsub"]  # type: ignore[attr-defined]
sys.modules["pubsub"] = pubsub_mock
sys.modules["pubsub.core"] = MagicMock()
sys.modules["pubsub.core"].__path__ = ["/fake/pubsub/core"]  # type: ignore[attr-defined]
sys.modules["pubsub.core.topicexc"] = MagicMock()


class TopicNameError(ValueError):
    """Mock TopicNameError for testing pubsub subscriptions."""


sys.modules["pubsub.core.topicexc"].TopicNameError = TopicNameError  # type: ignore[attr-defined]
sys.modules["matplotlib"] = MagicMock()
sys.modules["matplotlib.pyplot"] = MagicMock()
sys.modules["requests"] = MagicMock()


class RequestException(Exception):
    pass


class HTTPError(RequestException):
    pass


class ConnectionError(
    RequestException
):  # noqa: A001 - intentionally shadows built-in to mirror requests library
    pass


class Timeout(RequestException):
    pass


class MockRequestsExceptions:
    RequestException = RequestException
    HTTPError = HTTPError
    ConnectionError = ConnectionError
    Timeout = Timeout


sys.modules["requests"].exceptions = MockRequestsExceptions()  # type: ignore[attr-defined]

# Add top-level aliases for code that uses requests.RequestException directly
sys.modules["requests"].RequestException = RequestException  # type: ignore[attr-defined]
sys.modules["requests"].HTTPError = HTTPError  # type: ignore[attr-defined]
sys.modules["requests"].ConnectionError = ConnectionError  # type: ignore[attr-defined]
sys.modules["requests"].Timeout = Timeout  # type: ignore[attr-defined]
sys.modules["markdown"] = MagicMock()
sys.modules["haversine"] = MagicMock()
sys.modules["schedule"] = MagicMock()
sys.modules["platformdirs"] = MagicMock()
sys.modules["py_staticmaps"] = MagicMock()


# ── Matrix / nio mock classes (used with isinstance()) ────────────────


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


class MockRoomSendError(Exception):
    def __init__(
        self, message: str = "Room send error", status_code: str | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


nio_mock.RoomSendError = MockRoomSendError


# Mock ToDevice response classes for isinstance checks
class MockToDeviceResponse:
    """Mock ToDeviceResponse for testing."""

    pass


class MockToDeviceError:
    """Mock ToDeviceError for testing."""

    def __init__(self, message: str = "Error") -> None:
        """
        Initialize the MockToDeviceError with a human-readable message.

        Parameters:
            message (str): Error message stored on the instance and returned by `__str__`.
        """
        self.message: str = message

    def __str__(self) -> str:
        """
        Provide the exception's message as its string representation.

        @returns
            str: The error message stored on the exception.
        """
        return self.message


class MockRoomMemberEvent:
    pass


nio_mock.ToDeviceResponse = MockToDeviceResponse
nio_mock.ToDeviceError = MockToDeviceError
nio_mock.RoomMemberEvent = MockRoomMemberEvent
sys.modules["nio.events.room_events"].RoomMemberEvent = MockRoomMemberEvent


# ── PIL mock ──────────────────────────────────────────────────────────


class MockPILImage:
    def save(self, *args, **kwargs):
        """
        No-op save method that accepts any positional and keyword arguments and does nothing.

        This placeholder satisfies interfaces that expect a `save` method (for example, objects that persist state or files)
        but intentionally performs no action. It accepts arbitrary arguments for compatibility and always returns None.
        """
        pass


pil_image_mock.Image = MockPILImage


# ── BLE / Bleak mocks ─────────────────────────────────────────────────


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


sys.modules["bleak.exc"] = BleakExcModule()  # type: ignore[assignment]
sys.modules["bleak"].BleakError = BleakError  # type: ignore[attr-defined]
sys.modules["bleak"].BleakDBusError = BleakDBusError  # type: ignore[attr-defined]


# ── S2Sphere mocks ────────────────────────────────────────────────────


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


sys.modules["s2sphere"] = MockS2Module()  # type: ignore[assignment]


# ── Staticmaps mocks ──────────────────────────────────────────────────


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


sys.modules["staticmaps"] = MockStaticmapsModule()  # type: ignore[assignment]
