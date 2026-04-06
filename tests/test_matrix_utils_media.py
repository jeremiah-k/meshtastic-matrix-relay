from unittest.mock import AsyncMock, MagicMock

import pytest

from mmrelay.matrix.media import RoomSendError
from mmrelay.matrix_utils import ImageUploadError, send_room_image


@pytest.mark.asyncio
async def test_send_room_image_raises_on_room_send_error():
    mock_client = MagicMock()
    error_resp = RoomSendError(message="forbidden", status_code="M_FORBIDDEN")
    mock_client.room_send = AsyncMock(return_value=error_resp)

    mock_upload = MagicMock()
    mock_upload.content_uri = "mxc://example.com/test"

    with pytest.raises(ImageUploadError):
        await send_room_image(mock_client, "!room:matrix.org", mock_upload, "test.png")
