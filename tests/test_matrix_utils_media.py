import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nio import RoomSendError

from mmrelay.constants.domain import MATRIX_EVENT_TYPE_ROOM_MESSAGE
from mmrelay.matrix_utils import (
    ImageUploadError,
    send_image,
    send_room_image,
    upload_image,
)


@pytest.mark.asyncio
async def test_send_room_image_raises_on_room_send_error():
    mock_client = MagicMock()
    error_resp = RoomSendError(message="forbidden", status_code="M_FORBIDDEN")
    mock_client.room_send = AsyncMock(return_value=error_resp)

    mock_upload = MagicMock()
    mock_upload.content_uri = "mxc://example.com/test"

    with pytest.raises(ImageUploadError):
        await send_room_image(mock_client, "!room:matrix.org", mock_upload, "test.png")


@patch("mmrelay.matrix_utils.io.BytesIO")
async def test_upload_image(mock_bytesio):
    """
    Test that the `upload_image` function correctly uploads an image to Matrix and returns the upload response.
    This test mocks the PIL Image object, a BytesIO buffer, and the Matrix client to verify that the image is saved, uploaded, and the expected response is returned.
    """
    from PIL import Image

    # Mock PIL Image
    mock_image = MagicMock(spec=Image.Image)
    mock_buffer = MagicMock()
    mock_bytesio.return_value = mock_buffer
    mock_buffer.getvalue.return_value = b"fake_image_data"

    # Mock Matrix client - use MagicMock to prevent coroutine warnings
    mock_client = MagicMock()
    mock_client.upload = AsyncMock()
    mock_upload_response = MagicMock()
    mock_client.upload.return_value = (mock_upload_response, None)

    result = await upload_image(mock_client, mock_image, "test.png")

    # Verify image was saved and uploaded
    mock_image.save.assert_called_once()
    mock_client.upload.assert_called_once()
    assert result == mock_upload_response


async def test_send_room_image():
    """
    Test that an uploaded image is correctly sent to a Matrix room using the provided client and upload response.
    """
    # Use MagicMock to prevent coroutine warnings
    mock_client = MagicMock()
    mock_client.room_send = AsyncMock()
    mock_upload_response = MagicMock()
    mock_upload_response.content_uri = "mxc://matrix.org/test123"

    await send_room_image(
        mock_client, "!room:matrix.org", mock_upload_response, "test.png"
    )

    # Verify room_send was called with correct parameters
    mock_client.room_send.assert_called_once()
    call_args = mock_client.room_send.call_args
    assert call_args[1]["room_id"] == "!room:matrix.org"
    assert call_args[1]["message_type"] == MATRIX_EVENT_TYPE_ROOM_MESSAGE
    content = call_args[1]["content"]
    assert content["msgtype"] == "m.image"
    assert content["url"] == "mxc://matrix.org/test123"
    assert content["body"] == "test.png"


async def test_send_room_image_raises_on_missing_content_uri():
    """
    Ensure send_room_image raises a clear error when upload_response lacks a content_uri.
    """
    mock_client = MagicMock()
    mock_client.room_send = AsyncMock()
    mock_upload_response = MagicMock()
    mock_upload_response.content_uri = None

    with pytest.raises(ImageUploadError):
        await send_room_image(
            mock_client, "!room:matrix.org", mock_upload_response, "test.png"
        )


async def test_send_room_image_with_reply_to_event_id():
    """
    Test that send_room_image includes m.relates_to in-reply-to when reply_to_event_id is provided.
    """
    mock_client = MagicMock()
    mock_client.room_send = AsyncMock()
    mock_upload_response = MagicMock()
    mock_upload_response.content_uri = "mxc://matrix.org/test123"

    await send_room_image(
        mock_client,
        "!room:matrix.org",
        mock_upload_response,
        "test.png",
        reply_to_event_id="$event",
    )

    mock_client.room_send.assert_called_once()
    call_args = mock_client.room_send.call_args
    assert call_args[1]["room_id"] == "!room:matrix.org"
    assert call_args[1]["message_type"] == MATRIX_EVENT_TYPE_ROOM_MESSAGE
    content = call_args[1]["content"]
    assert content["msgtype"] == "m.image"
    assert content["url"] == "mxc://matrix.org/test123"
    assert content["body"] == "test.png"
    assert content["m.relates_to"] == {"m.in_reply_to": {"event_id": "$event"}}


async def test_send_image():
    """
    Test that send_image combines upload_image and send_room_image correctly.
    """
    mock_client = MagicMock()
    mock_client.room_send = AsyncMock()
    mock_image = MagicMock()
    mock_upload_response = MagicMock()
    mock_upload_response.content_uri = "mxc://matrix.org/test123"

    with patch(
        "mmrelay.matrix_utils.upload_image", return_value=mock_upload_response
    ) as mock_upload:
        with patch(
            "mmrelay.matrix_utils.send_room_image", return_value=None
        ) as mock_send:
            await send_image(mock_client, "!room:matrix.org", mock_image, "test.png")

            # Verify upload_image was called with correct parameters
            mock_upload.assert_awaited_once_with(
                client=mock_client, image=mock_image, filename="test.png"
            )

            # Verify send_room_image was called with correct parameters
            mock_send.assert_awaited_once_with(
                mock_client,
                "!room:matrix.org",
                upload_response=mock_upload_response,
                filename="test.png",
                reply_to_event_id=None,
            )


async def test_upload_image_sets_content_type_and_uses_filename():
    """Upload should honor detected image content type from filename."""
    uploaded = {}

    class FakeImage:
        def save(self, buffer, _format=None, **kwargs):
            """
            Write JPEG-encoded image data into a binary writable buffer.

            Parameters:
                buffer: A binary writable file-like object that will receive the image bytes.
                _format: Optional image format hint; accepted but not used by this implementation.
            """
            _format = kwargs.get("format", _format)
            buffer.write(b"jpgbytes")

    async def fake_upload(_file_obj, content_type=None, filename=None, filesize=None):
        """
        Simulate a file upload for tests and record the provided metadata.

        Records the provided content_type, filename, and filesize into the shared `uploaded` mapping
        and sets the same attributes on `mock_upload_response` to emulate an upload result.

        Parameters:
            _file_obj: The file-like object to "upload" (ignored by this fake).
            content_type (str|None): MIME type to assign to the upload result.
            filename (str|None): Filename to assign to the upload result.
            filesize (int|None): File size in bytes to assign to the upload result.

        Returns:
            tuple: `(upload_response, None)` where `upload_response` has `content_type`, `filename`,
            and `filesize` attributes set to the provided values.
        """
        uploaded["content_type"] = content_type
        uploaded["filename"] = filename
        uploaded["filesize"] = filesize
        mock_upload_response.content_type = content_type
        mock_upload_response.filename = filename
        mock_upload_response.filesize = filesize
        return mock_upload_response, None

    mock_client = MagicMock()
    mock_upload_response = MagicMock()
    mock_client.upload = AsyncMock(side_effect=fake_upload)

    result = await upload_image(mock_client, FakeImage(), "photo.jpg")  # type: ignore[arg-type]

    assert result == mock_upload_response
    assert mock_upload_response.content_type == "image/jpeg"
    assert mock_upload_response.filename == "photo.jpg"
    assert mock_upload_response.filesize == len(b"jpgbytes")


async def test_upload_image_fallbacks_to_png_on_save_error():
    """Upload should fall back to PNG and set content_type accordingly when initial save fails."""
    calls = []

    class FakeImage:
        def __init__(self):
            """
            Initialize the instance and mark it as the first-run.

            Sets the internal `_first` attribute to True to indicate the instance has not
            performed its primary action yet.
            """
            self._first = True

        def save(self, buffer, _format=None, **kwargs):
            """
            Write image data into a binary buffer; on the first call this implementation raises a ValueError, thereafter it writes PNG bytes.

            Parameters:
                buffer: A binary file-like object with a write(bytes) method that will receive the image data.
                _format (str | None): Optional format hint (ignored by this implementation).

            Raises:
                ValueError: If this is the first invocation and the instance's `_first` flag is set.
            """
            _format = kwargs.get("format", _format)
            calls.append(_format)
            if self._first:
                self._first = False
                raise ValueError("bad format")
            buffer.write(b"pngbytes")

    uploaded = {}

    async def fake_upload(_file_obj, content_type=None, filename=None, filesize=None):
        """
        Test helper that simulates uploading a file and records upload metadata.

        Parameters:
            _file_obj: Ignored file-like object (kept for signature compatibility).
            content_type (str | None): MIME type recorded to the shared `uploaded` mapping.
            filename (str | None): Filename recorded to the shared `uploaded` mapping.
            filesize (int | None): File size recorded to the shared `uploaded` mapping.

        Returns:
            tuple: A pair (upload_result, content_uri) where `upload_result` is an empty
            SimpleNamespace placeholder and `content_uri` is `None`.
        """
        uploaded["content_type"] = content_type
        uploaded["filename"] = filename
        uploaded["filesize"] = filesize
        return SimpleNamespace(), None

    mock_client = MagicMock()
    mock_client.upload = AsyncMock(side_effect=fake_upload)

    await upload_image(mock_client, FakeImage(), "photo.webp")  # type: ignore[arg-type]

    # First attempt uses WEBP, then PNG fallback
    assert calls == ["WEBP", "PNG"]
    assert uploaded["content_type"] == "image/png"
    assert uploaded["filename"] == "photo.webp"
    assert uploaded["filesize"] == len(b"pngbytes")


async def test_upload_image_fallbacks_to_png_on_oserror():
    """Upload should fall back to PNG when Pillow raises OSError (e.g., RGBA as JPEG)."""
    calls = []

    class FakeImage:
        def __init__(self):
            """
            Initialize the instance and mark it as the first-run.

            Sets the internal `_first` attribute to True to indicate the instance has not
            performed its primary action yet.
            """
            self._first = True

        def save(self, buffer, _format=None, **kwargs):
            """
            Write image data into a binary buffer; on the first call this implementation raises OSError, thereafter it writes PNG bytes.

            Parameters:
                buffer: A binary file-like object with a write(bytes) method that will receive the image data.
                _format (str | None): Optional format hint (ignored by this implementation).

            Raises:
                OSError: If this is the first invocation and the instance's `_first` flag is set.
            """
            _format = kwargs.get("format", _format)
            calls.append(_format)
            if self._first:
                self._first = False
                raise OSError("cannot write mode RGBA as JPEG")
            buffer.write(b"pngbytes")

    uploaded = {}

    async def fake_upload(_file_obj, content_type=None, filename=None, filesize=None):
        """
        Test helper that simulates uploading a file and records upload metadata.

        Parameters:
            _file_obj: Ignored file-like object (kept for signature compatibility).
            content_type (str | None): MIME type recorded to the shared `uploaded` mapping.
            filename (str | None): Filename recorded to the shared `uploaded` mapping.
            filesize (int | None): File size recorded to the shared `uploaded` mapping.

        Returns:
            tuple: A pair (upload_result, content_uri) where `upload_result` is an empty
            SimpleNamespace placeholder and `content_uri` is `None`.
        """
        uploaded["content_type"] = content_type
        uploaded["filename"] = filename
        uploaded["filesize"] = filesize
        return SimpleNamespace(), None

    mock_client = MagicMock()
    mock_client.upload = AsyncMock(side_effect=fake_upload)

    await upload_image(mock_client, FakeImage(), "photo.jpg")  # type: ignore[arg-type]

    # First attempt uses JPEG, then PNG fallback
    assert calls == ["JPEG", "PNG"]
    assert uploaded["content_type"] == "image/png"
    assert uploaded["filename"] == "photo.jpg"
    assert uploaded["filesize"] == len(b"pngbytes")


async def test_upload_image_defaults_to_png_when_mimetype_unknown():
    """Unknown extensions should default to image/png even when save succeeds."""

    class FakeImage:
        def save(self, buffer, _format=None, **kwargs):
            """
            Write a default placeholder byte sequence into the provided writable binary buffer.

            Parameters:
                buffer: A writable binary file-like object with a write(bytes) method; receives the placeholder bytes.
                _format (str, optional): Ignored by this implementation.
            """
            _format = kwargs.get("format", _format)
            buffer.write(b"defaultbytes")

    uploaded = {}

    async def fake_upload(_file_obj, content_type=None, filename=None, filesize=None):
        """
        Test helper that simulates uploading a file and records upload metadata.

        Parameters:
            _file_obj: Ignored file-like object (kept for signature compatibility).
            content_type (str | None): MIME type recorded to the shared `uploaded` mapping.
            filename (str | None): Filename recorded to the shared `uploaded` mapping.
            filesize (int | None): File size recorded to the shared `uploaded` mapping.

        Returns:
            tuple: A pair (upload_result, content_uri) where `upload_result` is an empty
            SimpleNamespace placeholder and `content_uri` is `None`.
        """
        uploaded["content_type"] = content_type
        uploaded["filename"] = filename
        uploaded["filesize"] = filesize
        return SimpleNamespace(), None

    mock_client = MagicMock()
    mock_client.upload = AsyncMock(side_effect=fake_upload)

    await upload_image(mock_client, FakeImage(), "noext")  # type: ignore[arg-type]

    assert uploaded["content_type"] == "image/png"
    assert uploaded["filename"] == "noext"
    assert uploaded["filesize"] == len(b"defaultbytes")


async def test_upload_image_returns_upload_error_on_network_exception():
    """Network errors during upload should be wrapped in UploadError with a safe status_code."""

    class FakeImage:
        def save(self, buffer, _format=None, **kwargs):
            buffer.write(b"pngbytes")

        # Make it compatible with PIL.Image type checking
        @property
        def format(self):
            return "PNG"

    mock_client = MagicMock()
    mock_client.upload = AsyncMock(side_effect=asyncio.TimeoutError("boom"))

    class LocalUploadError:
        def __init__(
            self, message, status_code=None, retry_after_ms=None, soft_logout=False
        ):
            self.message = message
            self.status_code = status_code
            self.retry_after_ms = retry_after_ms
            self.soft_logout = soft_logout

    result = await upload_image(
        mock_client,
        FakeImage(),  # type: ignore[arg-type]
        "photo.png",
    )

    assert hasattr(result, "message")
    assert hasattr(result, "status_code")
    assert result.message == "boom"  # type: ignore[attr-defined]
    assert result.status_code is None  # type: ignore[attr-defined]
    mock_client.upload.assert_awaited_once()
