import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturesTimeoutError
from unittest.mock import patch

import pytest

import mmrelay.meshtastic_utils as mu


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestCoerceNonnegativeFloat:
    def test_bool_rejected(self):
        from mmrelay.meshtastic.async_utils import _coerce_nonnegative_float

        assert _coerce_nonnegative_float(True, 1.5) == 1.5

    def test_infinite_rejected(self):
        from mmrelay.meshtastic.async_utils import _coerce_nonnegative_float

        assert _coerce_nonnegative_float(float("inf"), 2.0) == 2.0

    def test_negative_rejected(self):
        from mmrelay.meshtastic.async_utils import _coerce_nonnegative_float

        assert _coerce_nonnegative_float(-1.0, 3.0) == 3.0


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestCoercePositiveInt:
    def test_bool_rejected(self):
        from mmrelay.meshtastic.async_utils import _coerce_positive_int

        assert _coerce_positive_int(True, 5) == 5

    def test_zero_rejected(self):
        from mmrelay.meshtastic.async_utils import _coerce_positive_int

        assert _coerce_positive_int(0, 10) == 10

    def test_negative_rejected(self):
        from mmrelay.meshtastic.async_utils import _coerce_positive_int

        assert _coerce_positive_int(-3, 10) == 10


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestCoercePositiveFloat:
    def test_bool_rejected(self):
        from mmrelay.meshtastic.async_utils import _coerce_positive_float

        result = _coerce_positive_float(True, 5.0, "test_setting")
        assert result == 5.0

    def test_zero_rejected(self):
        from mmrelay.meshtastic.async_utils import _coerce_positive_float

        result = _coerce_positive_float(0, 5.0, "test_setting")
        assert result == 5.0


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestCoerceBool:
    def test_bool_values(self):
        from mmrelay.meshtastic.async_utils import _coerce_bool

        assert _coerce_bool(True, False, "t") is True
        assert _coerce_bool(False, True, "f") is False

    def test_string_yes_values(self):
        from mmrelay.meshtastic.async_utils import _coerce_bool

        for v in ["1", "true", "yes", "on", "TRUE", "YES", "ON"]:
            assert _coerce_bool(v, False, "t") is True

    def test_string_no_values(self):
        from mmrelay.meshtastic.async_utils import _coerce_bool

        for v in ["", "0", "false", "no", "off", "FALSE", "NO", "OFF"]:
            assert _coerce_bool(v, True, "f") is False

    def test_numeric_values(self):
        from mmrelay.meshtastic.async_utils import _coerce_bool

        assert _coerce_bool(1, False, "t") is True
        assert _coerce_bool(0, True, "f") is False
        assert _coerce_bool(0.0, True, "f") is False

    def test_unrecognized_value(self):
        from mmrelay.meshtastic.async_utils import _coerce_bool

        assert _coerce_bool("maybe", True, "t") is True


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestSubmitCoro:
    def test_non_awaitable_returns_none(self):
        from mmrelay.meshtastic.async_utils import _submit_coro

        result = _submit_coro("not_a_coroutine")
        assert result is None

    def test_awaitable_wrapped(self):
        from mmrelay.meshtastic.async_utils import _submit_coro

        async def coro():
            return 42

        c = coro()
        try:
            result = _submit_coro(c)
            assert result is not None
        finally:
            c.close()


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestWaitForResult:
    def test_concurrent_future_timeout(self):
        from mmrelay.meshtastic.async_utils import _wait_for_result

        fut = Future()
        with pytest.raises(FuturesTimeoutError):
            _wait_for_result(fut, timeout=0.01)

    def test_concurrent_future_success(self):
        from mmrelay.meshtastic.async_utils import _wait_for_result

        fut = Future()
        fut.set_result("ok")
        result = _wait_for_result(fut, timeout=1.0)
        assert result == "ok"

    def test_none_returns_false(self):
        from mmrelay.meshtastic.async_utils import _wait_for_result

        result = _wait_for_result(None, timeout=1.0)
        assert result is False


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestFireAndForget:
    def test_non_coroutine_returns(self):
        from mmrelay.meshtastic.async_utils import _fire_and_forget

        _fire_and_forget("not_a_coroutine")

    def test_submit_returns_none(self):
        from mmrelay.meshtastic.async_utils import _fire_and_forget

        async def coro():
            pass

        c = coro()
        try:
            with patch.object(mu, "_submit_coro", return_value=None):
                _fire_and_forget(c)
        finally:
            c.close()


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestRunBlockingWithTimeout:
    def test_action_raises(self):
        from mmrelay.meshtastic.async_utils import _run_blocking_with_timeout

        with pytest.raises(ValueError, match="test error"):
            _run_blocking_with_timeout(
                lambda: (_ for _ in ()).throw(ValueError("test error")),
                timeout=2.0,
                label="test-action",
            )

    def test_action_times_out(self):

        from mmrelay.meshtastic.async_utils import _run_blocking_with_timeout

        block_event = threading.Event()

        def blocker():
            block_event.wait(timeout=10)

        try:
            with pytest.raises(TimeoutError):
                _run_blocking_with_timeout(blocker, timeout=0.1, label="test-timeout")
        finally:
            block_event.set()

    def test_action_succeeds(self):
        from mmrelay.meshtastic.async_utils import _run_blocking_with_timeout

        _run_blocking_with_timeout(lambda: None, timeout=2.0, label="test-ok")


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestWaitForFutureResultWithShutdown:
    def test_raises_on_shutdown(self):
        from mmrelay.meshtastic.async_utils import (
            _wait_for_future_result_with_shutdown,
        )

        mu.shutting_down = True
        fut = Future()
        with pytest.raises(TimeoutError, match="Shutdown"):
            _wait_for_future_result_with_shutdown(fut, timeout_seconds=5)

    def test_raises_on_deadline(self):
        from concurrent.futures import TimeoutError as FuturesTimeoutError

        from mmrelay.meshtastic.async_utils import (
            _wait_for_future_result_with_shutdown,
        )

        mu.shutting_down = False
        fut = Future()
        with pytest.raises(FuturesTimeoutError):
            _wait_for_future_result_with_shutdown(fut, timeout_seconds=0.01)

    def test_returns_on_success(self):
        from mmrelay.meshtastic.async_utils import (
            _wait_for_future_result_with_shutdown,
        )

        mu.shutting_down = False
        fut = Future()
        fut.set_result(42)
        result = _wait_for_future_result_with_shutdown(fut, timeout_seconds=5)
        assert result == 42
