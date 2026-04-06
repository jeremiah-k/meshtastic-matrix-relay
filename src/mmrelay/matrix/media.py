import io
import os
from types import SimpleNamespace

from nio import UploadError, UploadResponse
from PIL import Image

import mmrelay.matrix_utils as facade
from mmrelay.constants.domain import MATRIX_EVENT_TYPE_ROOM_MESSAGE

__all__ = [
    "ImageUploadError",
    "upload_image",
    "send_room_image",
    "send_image",
]


class ImageUploadError(RuntimeError):
    """Raised when Matrix image upload fails."""

    def __init__(
        self,
        upload_response: UploadError | UploadResponse | SimpleNamespace | None,
    ):
        """
        Create an ImageUploadError and attach the underlying upload response or error.

        Parameters:
            upload_response: The underlying upload error or response object (or None). If present, its `message`
                attribute will be included in the exception text and the object will be stored on the instance as
                `upload_response`.

        """
        message = getattr(upload_response, "message", "Unknown error")
        super().__init__(f"Image upload failed: {message}")
        self.upload_response = upload_response


async def upload_image(
    client: facade.AsyncClient, image: Image.Image, filename: str
) -> UploadResponse | UploadError | SimpleNamespace:
    """
    Upload an image to the Matrix content repository and return the upload result.

    Parameters:
        client (AsyncClient): Matrix nio client used to perform the upload.
        image (PIL.Image.Image): Pillow image to upload.
        filename (str): Filename used to infer the image MIME type and as the uploaded filename.

    Returns:
        UploadResponse on success (contains `content_uri`).
        On failure, a SimpleNamespace-like object with `message` and optional `status_code` attributes describing the error.
    """
    image_format = os.path.splitext(filename)[1][1:].upper() or "PNG"
    if image_format == "JPG":
        image_format = "JPEG"

    buffer = io.BytesIO()
    try:
        image.save(buffer, format=image_format)
        content_type = facade._MIME_TYPE_MAP.get(image_format, "image/png")
    except (ValueError, KeyError, OSError):
        facade.logger.warning(
            f"Unsupported image format '{image_format}' for {filename}. Falling back to PNG."
        )
        buffer.seek(0)
        buffer.truncate(0)
        image.save(buffer, format="PNG")
        content_type = "image/png"

    image_data = buffer.getvalue()

    try:
        response, _ = await client.upload(
            io.BytesIO(image_data),
            content_type=content_type,
            filename=filename,
            filesize=len(image_data),
        )
    except facade.NIO_COMM_EXCEPTIONS as e:
        facade.logger.exception("Image upload failed due to a network error")
        upload_error = SimpleNamespace(message=str(e), status_code=None)
        return upload_error
    else:
        return response


async def send_room_image(
    client: facade.AsyncClient,
    room_id: str,
    upload_response: UploadResponse | UploadError | SimpleNamespace | None,
    filename: str = "image.png",
) -> None:
    """
    Send an uploaded image to a Matrix room.

    If `upload_response` exposes a `content_uri`, sends an `m.image` message referencing that URI and using `filename` as the body. If `content_uri` is missing, logs an error and raises ImageUploadError.

    Parameters:
        client (AsyncClient): Matrix client used to send the message.
        room_id (str): Target Matrix room ID.
        upload_response (UploadResponse | UploadError | SimpleNamespace | None): Result from an upload operation; must provide a `content_uri` attribute on success.
        filename (str): Filename to include as the message body (defaults to "image.png").

    Raises:
        ImageUploadError: If `upload_response` does not contain a `content_uri`.
    """
    content_uri = getattr(upload_response, "content_uri", None)
    if content_uri:
        await client.room_send(
            room_id=room_id,
            message_type=MATRIX_EVENT_TYPE_ROOM_MESSAGE,
            content={
                "msgtype": "m.image",
                "url": content_uri,
                "body": filename,
            },
        )
    else:
        facade.logger.error(
            f"Upload failed: {getattr(upload_response, 'message', 'Unknown error')}"
        )
        raise facade.ImageUploadError(upload_response)


async def send_image(
    client: facade.AsyncClient,
    room_id: str,
    image: Image.Image,
    filename: str = "image.png",
) -> None:
    """
    Upload a Pillow Image to the Matrix content repository and send it to a room.

    Uploads the provided PIL Image, stores it in the client's content repository, and sends it to the specified room as an `m.image` message using the given filename.

    Raises:
        ImageUploadError: If the upload or send operation fails.
    """
    response = await facade.upload_image(client=client, image=image, filename=filename)
    await facade.send_room_image(
        client, room_id, upload_response=response, filename=filename
    )
