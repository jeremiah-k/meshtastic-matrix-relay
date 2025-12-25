# Tests for fire-and-forget functionality

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestFireAndForget(unittest.TestCase):
    """Test cases for _fire_and_forget function."""

    @patch("mmrelay.meshtastic_utils.logger")
    def test_fire_and_forget_with_exception(self, mock_logger):
        """Test _fire_and_forget schedules and logs exceptions."""
        from mmrelay.meshtastic_utils import _fire_and_forget

        async def failing_coro():
            raise ValueError("Test exception")

        loop = asyncio.new_event_loop()
        try:
            _fire_and_forget(failing_coro(), loop=loop)
            loop.run_until_complete(asyncio.sleep(0.1))

            mock_logger.error.assert_called_once_with(
                "Exception in fire-and-forget task", exc_info=unittest.mock.ANY
            )
        finally:
            loop.close()

    @patch("mmrelay.meshtastic_utils.logger")
    def test_fire_and_forget_with_cancelled_task(self, mock_logger):
        """Test _fire_and_forget handles CancelledError silently."""
        from mmrelay.meshtastic_utils import _fire_and_forget

        async def cancelled_coro():
            raise asyncio.CancelledError()

        loop = asyncio.new_event_loop()
        try:
            _fire_and_forget(cancelled_coro(), loop=loop)
            loop.run_until_complete(asyncio.sleep(0.1))

            mock_logger.error.assert_not_called()
        finally:
            loop.close()

    @patch("mmrelay.meshtastic_utils.logger")
    def test_fire_and_forget_with_success(self, mock_logger):
        """Test _fire_and_forget doesn't log on successful completion."""
        from mmrelay.meshtastic_utils import _fire_and_forget

        async def successful_coro():
            return "success"

        loop = asyncio.new_event_loop()
        try:
            _fire_and_forget(successful_coro(), loop=loop)
            loop.run_until_complete(asyncio.sleep(0.1))

            mock_logger.error.assert_not_called()
        finally:
            loop.close()

    @patch("mmrelay.meshtastic_utils.logger")
    def test_fire_and_forget_with_none_coro(self, mock_logger):
        """Test _fire_and_forget handles None input gracefully."""
        from mmrelay.meshtastic_utils import _fire_and_forget

        loop = asyncio.new_event_loop()
        try:
            _fire_and_forget(None, loop=loop)
            loop.run_until_complete(asyncio.sleep(0.1))

            mock_logger.error.assert_not_called()
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
