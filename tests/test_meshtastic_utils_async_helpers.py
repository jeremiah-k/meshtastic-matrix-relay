import asyncio
from concurrent.futures import Future
from unittest.mock import MagicMock, patch

from mmrelay.meshtastic_utils import _get_name_safely, _make_awaitable, _wait_for_result


def test_make_awaitable_wraps_future(meshtastic_loop_safety):
    future = Future()
    wrapped = _make_awaitable(future, loop=meshtastic_loop_safety)

    future.set_result("ok")
    result = meshtastic_loop_safety.run_until_complete(wrapped)

    assert wrapped is not future
    assert result == "ok"


def test_wait_for_result_none_returns_false():
    assert _wait_for_result(None, timeout=0.1) is False


def test_wait_for_result_asyncio_future_uses_loop(meshtastic_loop_safety):
    future = meshtastic_loop_safety.create_future()
    future.set_result("done")

    result = _wait_for_result(future, timeout=0.1, loop=meshtastic_loop_safety)

    assert result == "done"


def test_wait_for_result_result_method_typeerror_fallback():
    class ResultOnly:
        def result(self):
            """
            Retrieve the object's result value.
            
            Returns:
                str: The result string "value".
            """
            return "value"

    result = _wait_for_result(ResultOnly(), timeout=0.1)

    assert result == "value"


def test_wait_for_result_target_loop_running_uses_threadsafe():
    loop = asyncio.new_event_loop()
    future = loop.create_future()
    future.set_result("done")

    result_future = MagicMock()
    result_future.result.return_value = "threadsafe"

    def fake_threadsafe(coro, _loop):
        """
        Close the provided coroutine and return a pre-bound result future.
        
        Parameters:
            coro (coroutine): The coroutine object to close; its execution is discarded.
            _loop: The event loop passed for signature compatibility (unused).
        
        Returns:
            result_future: A future-like object previously bound in the surrounding scope.
        """
        coro.close()
        return result_future

    with (
        patch.object(loop, "is_running", return_value=True),
        patch.object(loop, "is_closed", return_value=False),
        patch(
            "mmrelay.meshtastic_utils.asyncio.run_coroutine_threadsafe",
            side_effect=fake_threadsafe,
        ),
    ):
        result = _wait_for_result(future, timeout=0.1, loop=loop)

    loop.close()

    assert result == "threadsafe"


def test_wait_for_result_running_loop_threadsafe():
    class DummyLoop:
        def is_closed(self):
            """
            Report whether the resource is closed.
            
            Returns:
                True if the resource is closed, False otherwise.
            """
            return False

        def is_running(self):
            """
            Report whether the loop is running.
            
            Returns:
                bool: `True` if the loop is running, `False` otherwise.
            """
            return True

    result_future = MagicMock()
    result_future.result.return_value = "running"

    def fake_threadsafe(coro, _loop):
        """
        Close the provided coroutine and return a pre-bound result future.
        
        Parameters:
            coro (coroutine): The coroutine object to close; its execution is discarded.
            _loop: The event loop passed for signature compatibility (unused).
        
        Returns:
            result_future: A future-like object previously bound in the surrounding scope.
        """
        coro.close()
        return result_future

    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        future.set_result("done")
        with (
            patch(
                "mmrelay.meshtastic_utils.asyncio.get_running_loop",
                return_value=DummyLoop(),
            ),
            patch(
                "mmrelay.meshtastic_utils.asyncio.run_coroutine_threadsafe",
                side_effect=fake_threadsafe,
            ),
        ):
            result = _wait_for_result(future, timeout=0.1)
    finally:
        loop.close()

    assert result == "running"


def test_wait_for_result_running_loop_not_running():
    loop = asyncio.new_event_loop()
    try:
        with patch(
            "mmrelay.meshtastic_utils.asyncio.get_running_loop", return_value=loop
        ):

            async def _sample():
                """
                Provide the literal string "sync-loop".
                
                Returns:
                    str: The string "sync-loop".
                """
                return "sync-loop"

            result = _wait_for_result(_sample(), timeout=0.1)
    finally:
        loop.close()

    assert result == "sync-loop"


def test_wait_for_result_new_loop_path():
    async def _sample():
        """
        Return the literal string "new-loop".
        
        Returns:
            result (str): The string "new-loop".
        """
        return "new-loop"

    result = _wait_for_result(_sample(), timeout=0.1)

    assert result == "new-loop"


def test_get_name_safely_returns_sender_on_exception():
    def _bad_lookup(_sender):
        """
        Raise a TypeError to simulate a failing name lookup.
        
        Parameters:
            _sender: Ignored; present only to match the expected callable signature.
        
        Raises:
            TypeError: always raised with message "boom".
        """
        raise TypeError("boom")

    assert _get_name_safely(_bad_lookup, 123) == "123"