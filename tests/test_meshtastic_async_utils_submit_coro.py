"""
Tests for _submit_coro fallback paths in meshtastic/async_utils.py.

Covers lines 192-209: the no-running-loop fallback that creates a temporary
event loop (with Runner or manual new_event_loop) to execute a coroutine.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestSubmitCoroNoLoopFallback:
    """Test _submit_coro when there is no running event loop."""

    def _make_coro(self):
        """
        Create and return a coroutine that resolves to 42.
        
        Returns:
            coroutine: A coroutine object which returns the integer 42 when awaited.
        """
        async def coro():
            return 42

        return coro()

    def test_uses_runner_when_available(self):
        from mmrelay.meshtastic.async_utils import _submit_coro

        mu.event_loop = None

        class FakeRunner:
            def __init__(self):
                """
                Initialize the instance with a MagicMock runner used to emulate asyncio Runner behavior in tests.
                
                Stores a MagicMock object on self._runner for capturing and asserting runner interactions.
                """
                self._runner = MagicMock()

            def __enter__(self):
                """
                Enter the context manager and provide the context object.
                
                Returns:
                    The context manager instance (`self`).
                """
                return self

            def __exit__(self, *args):
                """
                Do nothing when exiting the context manager.
                
                Parameters:
                    *args: Values provided by the context-management protocol (exception type, value, traceback); intentionally ignored.
                """
                pass

            def run(self, coro):
                """
                Run the given coroutine in a fresh temporary event loop and return its result.
                
                Parameters:
                    coro: The coroutine object to execute.
                
                Returns:
                    The value produced by the coroutine.
                    
                Description:
                    Creates a new event loop for the duration of the call, runs the coroutine to completion on that loop, closes the loop, and clears the global event loop reference.
                """
                asyncio.set_event_loop(asyncio.new_event_loop())
                try:
                    return asyncio.get_event_loop().run_until_complete(coro)
                finally:
                    asyncio.get_event_loop().close()
                    asyncio.set_event_loop(None)

        fake_runner_cls = FakeRunner

        mock_asyncio = MagicMock(spec=asyncio)
        mock_asyncio.Runner = fake_runner_cls
        mock_asyncio.get_running_loop.side_effect = RuntimeError("no running loop")
        mock_asyncio.AbstractEventLoop = asyncio.AbstractEventLoop

        coro = self._make_coro()
        with patch.object(mu, "asyncio", mock_asyncio):
            result = _submit_coro(coro)
        assert result is not None
        assert result.result(timeout=2) == 42

    def test_fallback_to_new_event_loop_without_runner(self):
        from mmrelay.meshtastic.async_utils import _submit_coro

        mu.event_loop = None

        real_asyncio = asyncio
        mock_asyncio = MagicMock(spec=asyncio)
        mock_asyncio.Runner = None
        mock_asyncio.get_running_loop.side_effect = RuntimeError("no running loop")
        mock_asyncio.new_event_loop = real_asyncio.new_event_loop
        mock_asyncio.set_event_loop = real_asyncio.set_event_loop
        mock_asyncio.AbstractEventLoop = real_asyncio.AbstractEventLoop

        coro = self._make_coro()
        with patch.object(mu, "asyncio", mock_asyncio):
            result = _submit_coro(coro)
        assert result is not None
        assert result.result(timeout=2) == 42

    def test_fallback_sets_event_loop_none_on_cleanup(self):
        from mmrelay.meshtastic.async_utils import _submit_coro

        mu.event_loop = None

        real_asyncio = asyncio
        call_log = []

        mock_asyncio = MagicMock(spec=asyncio)
        mock_asyncio.Runner = None
        mock_asyncio.get_running_loop.side_effect = RuntimeError("no running loop")
        mock_asyncio.new_event_loop = real_asyncio.new_event_loop
        mock_asyncio.AbstractEventLoop = real_asyncio.AbstractEventLoop

        original_set = real_asyncio.set_event_loop

        def tracking_set(loop):
            """
            Record a call to set_event_loop and forward it to the original setter.
            
            Parameters:
                loop (asyncio.AbstractEventLoop | None): The event loop being installed, or `None` to clear the current loop.
            """
            call_log.append(("set_event_loop", loop))
            original_set(loop)

        mock_asyncio.set_event_loop = tracking_set

        coro = self._make_coro()
        with patch.object(mu, "asyncio", mock_asyncio):
            result = _submit_coro(coro)
        assert result is not None
        assert result.result(timeout=2) == 42
        set_calls = [c for c in call_log if c[0] == "set_event_loop"]
        assert any(c[1] is None for c in set_calls)
