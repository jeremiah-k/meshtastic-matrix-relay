"""
Tests for specific uncovered lines across ble.py, async_utils.py, events.py,
and command_bridge.py.
"""

import errno
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import mmrelay.matrix_utils as matrix_utils
import mmrelay.meshtastic_utils as mu


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestAttachLateBleInterfaceDisposerCleanup:
    """Cover ble.py lines 114-143: late BLE interface disposer cleanup path."""

    def test_late_iface_different_from_active_triggers_cleanup(self):
        from mmrelay.meshtastic.ble import _attach_late_ble_interface_disposer

        late_iface = MagicMock()
        late_iface.client = MagicMock()
        late_iface.address = "AA:BB:CC:DD:EE:FF"
        active_iface = MagicMock()

        mu.meshtastic_iface = active_iface

        mock_future = MagicMock()
        mock_future.cancelled.return_value = False
        mock_future.result.return_value = late_iface

        with patch.object(mu, "_disconnect_ble_interface") as mock_disconnect:
            with patch.object(
                mu, "_get_ble_iface_generation", return_value=("aabbccddeeff", 1)
            ):
                _attach_late_ble_interface_disposer(
                    mock_future, "AA:BB:CC:DD:EE:FF", reason="test"
                )
                callback = mock_future.add_done_callback.call_args[0][0]
                callback(mock_future)

        mock_disconnect.assert_called_once()
        mu.meshtastic_iface = None

    def test_cancelled_callback_returns_early(self):
        from mmrelay.meshtastic.ble import _attach_late_ble_interface_disposer

        mock_future = MagicMock()
        _attach_late_ble_interface_disposer(mock_future, "AA:BB", reason="test")
        callback = mock_future.add_done_callback.call_args[0][0]

        cancelled_future = MagicMock()
        cancelled_future.cancelled.return_value = True
        callback(cancelled_future)

    def test_active_iface_same_as_late_returns_early(self):
        from mmrelay.meshtastic.ble import _attach_late_ble_interface_disposer

        late_iface = MagicMock()
        late_iface.client = MagicMock()

        mu.meshtastic_iface = late_iface

        mock_future = MagicMock()
        mock_future.cancelled.return_value = False
        mock_future.result.return_value = late_iface

        _attach_late_ble_interface_disposer(mock_future, "AA:BB", reason="test")
        callback = mock_future.add_done_callback.call_args[0][0]
        callback(mock_future)
        mu.meshtastic_iface = None

    def test_late_generation_fallback_from_generation_param(self):
        from mmrelay.meshtastic.ble import _attach_late_ble_interface_disposer

        late_iface = MagicMock()
        late_iface.client = MagicMock()
        late_iface.address = "AA:BB:CC"
        active_iface = MagicMock()

        mu.meshtastic_iface = active_iface

        mock_future = MagicMock()
        mock_future.cancelled.return_value = False
        mock_future.result.return_value = late_iface

        with patch.object(mu, "_disconnect_ble_interface") as mock_disconnect:
            with patch.object(
                mu, "_get_ble_iface_generation", return_value=("aabbcc", None)
            ):
                _attach_late_ble_interface_disposer(
                    mock_future,
                    "AA:BB:CC",
                    reason="test",
                    generation=5,
                )
                callback = mock_future.add_done_callback.call_args[0][0]
                callback(mock_future)

        mock_disconnect.assert_called_once()
        call_kwargs = mock_disconnect.call_args
        assert call_kwargs[1]["generation"] == 5
        mu.meshtastic_iface = None

    def test_late_iface_no_disconnect_method_returns_early(self):
        from mmrelay.meshtastic.ble import _attach_late_ble_interface_disposer

        late_iface = MagicMock(spec=["__str__"])

        mock_future = MagicMock()
        mock_future.cancelled.return_value = False
        mock_future.result.return_value = late_iface

        mu.meshtastic_iface = MagicMock()

        _attach_late_ble_interface_disposer(mock_future, "AA:BB", reason="test")
        callback = mock_future.add_done_callback.call_args[0][0]
        callback(mock_future)
        mu.meshtastic_iface = None

    def test_late_iface_exception_in_result_uses_fallback(self):
        from mmrelay.meshtastic.ble import _attach_late_ble_interface_disposer

        fallback_iface = MagicMock()
        fallback_iface.address = "aa:bb:cc:dd:ee:ff"
        active_iface = MagicMock()

        mu.meshtastic_iface = active_iface

        mock_future = MagicMock()
        mock_future.cancelled.return_value = False
        mock_future.result.side_effect = RuntimeError("future failed")

        with patch.object(mu, "_disconnect_ble_interface") as mock_disconnect:
            with patch.object(
                mu, "_get_ble_iface_generation", return_value=("aabbccddeeff", 1)
            ):
                _attach_late_ble_interface_disposer(
                    mock_future,
                    "AA:BB",
                    reason="test",
                    fallback_iface=fallback_iface,
                )
                callback = mock_future.add_done_callback.call_args[0][0]
                callback(mock_future)

        mock_disconnect.assert_called_once()
        mu.meshtastic_iface = None

    def test_late_iface_result_is_none_and_no_fallback_returns_early(self):
        from mmrelay.meshtastic.ble import _attach_late_ble_interface_disposer

        mock_future = MagicMock()
        mock_future.cancelled.return_value = False
        mock_future.result.return_value = None

        _attach_late_ble_interface_disposer(
            mock_future, "AA:BB", reason="test", fallback_iface=None
        )
        callback = mock_future.add_done_callback.call_args[0][0]
        callback(mock_future)

    def test_disconnect_cleanup_exception_suppressed(self):
        from mmrelay.meshtastic.ble import _attach_late_ble_interface_disposer

        late_iface = MagicMock()
        late_iface.client = MagicMock()
        late_iface.address = "AA:BB:CC:DD:EE:FF"
        active_iface = MagicMock()

        mu.meshtastic_iface = active_iface

        mock_future = MagicMock()
        mock_future.cancelled.return_value = False
        mock_future.result.return_value = late_iface

        with patch.object(
            mu, "_disconnect_ble_interface", side_effect=RuntimeError("cleanup boom")
        ):
            with patch.object(
                mu, "_get_ble_iface_generation", return_value=("aabbccddeeff", 1)
            ):
                _attach_late_ble_interface_disposer(
                    mock_future, "AA:BB:CC:DD:EE:FF", reason="test"
                )
                callback = mock_future.add_done_callback.call_args[0][0]
                callback(mock_future)

        mu.meshtastic_iface = None


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestExtractBleAddressFromInterface:
    """Cover ble.py lines 311-334."""

    def test_none_returns_none(self):
        from mmrelay.meshtastic.ble import _extract_ble_address_from_interface

        assert _extract_ble_address_from_interface(None) is None

    def test_iface_address_attribute(self):
        from mmrelay.meshtastic.ble import _extract_ble_address_from_interface

        iface = MagicMock()
        iface.address = "AA:BB:CC:DD:EE:FF"
        del iface.client
        result = _extract_ble_address_from_interface(iface)
        assert result == "aabbccddeeff"

    def test_client_address_attribute(self):
        from mmrelay.meshtastic.ble import _extract_ble_address_from_interface

        iface = MagicMock()
        iface.address = None
        iface.client.address = "11:22:33:44:55:66"
        del iface.client.bleak_client
        result = _extract_ble_address_from_interface(iface)
        assert result == "112233445566"

    def test_bleak_client_address_attribute(self):
        from mmrelay.meshtastic.ble import _extract_ble_address_from_interface

        iface = MagicMock()
        iface.address = None
        iface.client.address = None
        iface.client.bleak_client.address = "AA-BB-CC-DD-EE-FF"
        result = _extract_ble_address_from_interface(iface)
        assert result == "aabbccddeeff"

    def test_no_valid_candidates_returns_none(self):
        from mmrelay.meshtastic.ble import _extract_ble_address_from_interface

        iface = MagicMock()
        iface.address = None
        iface.client.address = None
        iface.client.bleak_client.address = None
        result = _extract_ble_address_from_interface(iface)
        assert result is None

    def test_iface_address_not_string_ignored(self):
        from mmrelay.meshtastic.ble import _extract_ble_address_from_interface

        iface = MagicMock()
        iface.address = 12345
        del iface.client
        result = _extract_ble_address_from_interface(iface)
        assert result is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestAdvanceBleGeneration:
    """Cover ble.py lines 337-353."""

    def test_empty_address_returns_zero(self):
        from mmrelay.meshtastic.ble import _advance_ble_generation

        assert _advance_ble_generation("", transition="test") == 0

    def test_first_generation(self):
        from mmrelay.meshtastic.ble import _advance_ble_generation

        gen = _advance_ble_generation("AA:BB:CC:DD:EE:FF", transition="connect")
        assert gen == 1

    def test_increments_existing(self):
        from mmrelay.meshtastic.ble import _advance_ble_generation

        gen1 = _advance_ble_generation("AA:BB:CC:DD:EE:FF", transition="first")
        gen2 = _advance_ble_generation("AA:BB:CC:DD:EE:FF", transition="second")
        assert gen2 == gen1 + 1


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestGetBleGeneration:
    """Cover ble.py lines 356-362."""

    def test_empty_address_returns_zero(self):
        from mmrelay.meshtastic.ble import _get_ble_generation

        assert _get_ble_generation("") == 0

    def test_unknown_address_returns_zero(self):
        from mmrelay.meshtastic.ble import _get_ble_generation

        assert _get_ble_generation("FF:EE:DD:CC:BB:AA") == 0

    def test_returns_current_generation(self):
        from mmrelay.meshtastic.ble import _advance_ble_generation, _get_ble_generation

        addr = "AA:BB:CC:DD:EE:FF"
        _advance_ble_generation(addr, transition="test")
        assert _get_ble_generation(addr) == 1


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestIsBleGenerationStale:
    """Cover ble.py lines 365-372."""

    def test_empty_address_returns_false(self):
        from mmrelay.meshtastic.ble import _is_ble_generation_stale

        assert _is_ble_generation_stale("", 1) is False

    def test_matching_generation_not_stale(self):
        from mmrelay.meshtastic.ble import (
            _advance_ble_generation,
            _is_ble_generation_stale,
        )

        addr = "AA:BB:CC:DD:EE:FF"
        gen = _advance_ble_generation(addr, transition="test")
        assert _is_ble_generation_stale(addr, gen) is False

    def test_old_generation_is_stale(self):
        from mmrelay.meshtastic.ble import (
            _advance_ble_generation,
            _is_ble_generation_stale,
        )

        addr = "AA:BB:CC:DD:EE:FF"
        gen1 = _advance_ble_generation(addr, transition="first")
        _advance_ble_generation(addr, transition="second")
        assert _is_ble_generation_stale(addr, gen1) is True


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestRegisterBleIfaceGeneration:
    """Cover ble.py lines 375-387."""

    def test_none_iface_noop(self):
        from mmrelay.meshtastic.ble import _register_ble_iface_generation

        _register_ble_iface_generation(None, "AA:BB", 1)
        assert mu._ble_iface_generation_by_id == {}

    def test_empty_address_noop(self):
        from mmrelay.meshtastic.ble import _register_ble_iface_generation

        iface = MagicMock()
        _register_ble_iface_generation(iface, "", 1)
        assert mu._ble_iface_generation_by_id == {}

    def test_zero_generation_noop(self):
        from mmrelay.meshtastic.ble import _register_ble_iface_generation

        iface = MagicMock()
        _register_ble_iface_generation(iface, "AA:BB", 0)
        assert mu._ble_iface_generation_by_id == {}

    def test_negative_generation_noop(self):
        from mmrelay.meshtastic.ble import _register_ble_iface_generation

        iface = MagicMock()
        _register_ble_iface_generation(iface, "AA:BB", -1)
        assert mu._ble_iface_generation_by_id == {}

    def test_valid_registration(self):
        from mmrelay.meshtastic.ble import _register_ble_iface_generation

        iface = MagicMock()
        _register_ble_iface_generation(iface, "AA:BB:CC", 1)
        assert id(iface) in mu._ble_iface_generation_by_id
        assert mu._ble_iface_generation_by_id[id(iface)] == ("aabbcc", 1)


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestGetBleIfaceGeneration:
    """Cover ble.py lines 402-414."""

    def test_none_iface_with_fallback(self):
        from mmrelay.meshtastic.ble import _get_ble_iface_generation

        addr, gen = _get_ble_iface_generation(None, fallback_address="AA:BB")
        assert addr == "aabb"
        assert gen is None

    def test_mapped_iface_returns_registered(self):
        from mmrelay.meshtastic.ble import (
            _get_ble_iface_generation,
            _register_ble_iface_generation,
        )

        iface = MagicMock()
        _register_ble_iface_generation(iface, "AA:BB:CC", 3)
        addr, gen = _get_ble_iface_generation(iface)
        assert addr == "aabbcc"
        assert gen == 3

    def test_fallback_key_with_existing_generation(self):
        from mmrelay.meshtastic.ble import (
            _advance_ble_generation,
            _get_ble_iface_generation,
        )

        addr = "AA:BB:CC"
        gen = _advance_ble_generation(addr, transition="test")
        iface = MagicMock()
        addr_out, gen_out = _get_ble_iface_generation(iface, fallback_address=addr)
        assert addr_out == "aabbcc"
        assert gen_out == gen

    def test_no_fallback_extracts_from_iface(self):
        from mmrelay.meshtastic.ble import (
            _advance_ble_generation,
            _get_ble_iface_generation,
        )

        iface = MagicMock()
        iface.address = "AA:BB:CC:DD:EE:FF"
        del iface.client

        _advance_ble_generation("AA:BB:CC:DD:EE:FF", transition="test")

        addr_out, gen_out = _get_ble_iface_generation(iface)
        assert addr_out == "aabbccddeeff"
        assert gen_out is not None

    def test_no_match_returns_none_generation(self):
        from mmrelay.meshtastic.ble import _get_ble_iface_generation

        iface = MagicMock()
        iface.address = None
        del iface.client
        addr_out, gen_out = _get_ble_iface_generation(iface)
        assert gen_out is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestDiscardBleIfaceGeneration:
    """Cover ble.py lines 417-425."""

    def test_none_iface(self):
        from mmrelay.meshtastic.ble import _discard_ble_iface_generation

        addr, gen = _discard_ble_iface_generation(None)
        assert addr is None
        assert gen is None

    def test_unregistered_iface(self):
        from mmrelay.meshtastic.ble import _discard_ble_iface_generation

        iface = MagicMock()
        addr, gen = _discard_ble_iface_generation(iface)
        assert addr is None
        assert gen is None

    def test_registered_iface(self):
        from mmrelay.meshtastic.ble import (
            _discard_ble_iface_generation,
            _register_ble_iface_generation,
        )

        iface = MagicMock()
        _register_ble_iface_generation(iface, "AA:BB", 2)
        addr, gen = _discard_ble_iface_generation(iface)
        assert addr == "aabb"
        assert gen == 2
        assert id(iface) not in mu._ble_iface_generation_by_id


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestRecordBleTeardownTimeout:
    """Cover ble.py lines 428-437."""

    def test_empty_address_returns_zero(self):
        from mmrelay.meshtastic.ble import _record_ble_teardown_timeout

        assert _record_ble_teardown_timeout("", 1) == 0

    def test_zero_generation_returns_zero(self):
        from mmrelay.meshtastic.ble import _record_ble_teardown_timeout

        assert _record_ble_teardown_timeout("AA:BB", 0) == 0

    def test_records_and_increments(self):
        from mmrelay.meshtastic.ble import _record_ble_teardown_timeout

        count1 = _record_ble_teardown_timeout("AA:BB:CC", 1)
        count2 = _record_ble_teardown_timeout("AA:BB:CC", 1)
        assert count1 == 1
        assert count2 == 2


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestResolveBleTeardownTimeout:
    """Cover ble.py lines 440-470."""

    def test_empty_address(self):
        from mmrelay.meshtastic.ble import _resolve_ble_teardown_timeout

        remaining, stale = _resolve_ble_teardown_timeout("", 1)
        assert remaining == 0
        assert stale is False

    def test_zero_generation(self):
        from mmrelay.meshtastic.ble import _resolve_ble_teardown_timeout

        remaining, stale = _resolve_ble_teardown_timeout("AA:BB", 0)
        assert remaining == 0
        assert stale is False

    def test_resolve_single_entry(self):
        from mmrelay.meshtastic.ble import (
            _advance_ble_generation,
            _record_ble_teardown_timeout,
            _resolve_ble_teardown_timeout,
        )

        gen = _advance_ble_generation("AA:BB", transition="test")
        _record_ble_teardown_timeout("AA:BB", gen)
        remaining, stale = _resolve_ble_teardown_timeout("AA:BB", gen)
        assert remaining == 0
        assert stale is False

    def test_resolve_decrements(self):
        from mmrelay.meshtastic.ble import (
            _advance_ble_generation,
            _record_ble_teardown_timeout,
            _resolve_ble_teardown_timeout,
        )

        gen = _advance_ble_generation("AA:BB", transition="test")
        _record_ble_teardown_timeout("AA:BB", gen)
        _record_ble_teardown_timeout("AA:BB", gen)
        remaining, stale = _resolve_ble_teardown_timeout("AA:BB", gen)
        assert remaining == 1
        assert stale is False

    def test_stale_generation(self):
        from mmrelay.meshtastic.ble import (
            _advance_ble_generation,
            _record_ble_teardown_timeout,
            _resolve_ble_teardown_timeout,
        )

        old_gen = _advance_ble_generation("AA:BB", transition="old")
        _record_ble_teardown_timeout("AA:BB", old_gen)
        _advance_ble_generation("AA:BB", transition="new")
        remaining, stale = _resolve_ble_teardown_timeout("AA:BB", old_gen)
        assert stale is True

    def test_resolve_counts_other_generations_for_same_address(self):
        from mmrelay.meshtastic.ble import (
            _record_ble_teardown_timeout,
            _resolve_ble_teardown_timeout,
        )

        _record_ble_teardown_timeout("AA:BB", 1)
        _record_ble_teardown_timeout("AA:BB", 2)
        remaining, stale = _resolve_ble_teardown_timeout("AA:BB", 1)
        assert remaining == 1


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestGetBleUnresolvedTeardownGenerations:
    """Cover ble.py lines 473-490."""

    def test_empty_address(self):
        from mmrelay.meshtastic.ble import _get_ble_unresolved_teardown_generations

        assert _get_ble_unresolved_teardown_generations("") == []

    def test_no_entries(self):
        from mmrelay.meshtastic.ble import _get_ble_unresolved_teardown_generations

        assert _get_ble_unresolved_teardown_generations("AA:BB") == []

    def test_returns_sorted_entries(self):
        from mmrelay.meshtastic.ble import (
            _get_ble_unresolved_teardown_generations,
            _record_ble_teardown_timeout,
        )

        _record_ble_teardown_timeout("AA:BB", 2)
        _record_ble_teardown_timeout("AA:BB", 1)
        result = _get_ble_unresolved_teardown_generations("AA:BB")
        assert result == [(1, 1), (2, 1)]


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestDisconnectBleInterfaceGenerationResolution:
    """Cover ble.py lines 831-834: generation fallback resolution."""

    def test_generation_none_falls_back_to_mapped(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        iface = MagicMock()
        iface._exit_handler = None
        iface.disconnect.return_value = None
        iface.client = None
        iface.close.return_value = None

        with (
            patch.object(mu, "_get_ble_iface_generation", return_value=("aabb", 5)),
            patch.object(mu.time, "sleep"),
        ):
            _disconnect_ble_interface(iface, reason="test", ble_address="AA:BB")

    def test_generation_none_falls_back_to_get_ble_generation(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        iface = MagicMock()
        iface._exit_handler = None
        iface.disconnect.return_value = None
        iface.client = None
        iface.close.return_value = None

        with (
            patch.object(mu, "_get_ble_iface_generation", return_value=("aabb", None)),
            patch.object(mu, "_get_ble_generation", return_value=7),
            patch.object(mu.time, "sleep"),
        ):
            _disconnect_ble_interface(iface, reason="test", ble_address="AA:BB")


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestDisconnectBleInterfaceCloseSyncAwaitable:
    """Cover ble.py lines 1016-1017: _close_sync awaitable path."""

    def test_close_returns_awaitable(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        iface = MagicMock()
        iface._exit_handler = None
        iface.disconnect.return_value = None
        iface.client = None

        async def async_close():
            return None

        iface.close = async_close

        with (
            patch.object(mu, "_get_ble_iface_generation", return_value=(None, None)),
            patch.object(mu.time, "sleep"),
            patch.object(mu, "_run_blocking_with_timeout"),
        ):
            _disconnect_ble_interface(iface, reason="test")


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestDisconnectBleInterfaceTimeoutError:
    """Cover ble.py line 1033: TimeoutError handler."""

    def test_timeout_error_in_teardown(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        iface = MagicMock()
        iface._exit_handler = None
        iface.disconnect.side_effect = TimeoutError("disconnect timed out")
        iface.client = None
        iface.close.return_value = None

        with (
            patch.object(mu, "_get_ble_iface_generation", return_value=(None, None)),
            patch.object(mu.time, "sleep"),
            patch.object(
                mu, "_run_blocking_with_timeout", side_effect=TimeoutError("block")
            ),
        ):
            _disconnect_ble_interface(iface, reason="test")


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestRunBlockingResolveTeardownOnce:
    """Cover async_utils.py lines 348-349, 356-357, 364-365."""

    def test_resolve_skipped_when_no_recorded_event(self):
        from mmrelay.meshtastic.async_utils import _run_blocking_with_timeout

        ble_address = "AA:BB:CC:DD:EE:FF"
        gen = mu._advance_ble_generation(ble_address, transition="test")

        with pytest.raises(TimeoutError):
            _run_blocking_with_timeout(
                lambda: time.sleep(10),
                timeout=0.05,
                label="test-no-recorded",
                ble_address=ble_address,
                ble_generation=gen,
            )

    def test_resolve_skipped_when_resolve_not_callable(self):
        from mmrelay.meshtastic.async_utils import _run_blocking_with_timeout

        ble_address = "11:22:33:44:55:66"
        gen = mu._advance_ble_generation(ble_address, transition="test")

        release = threading.Event()
        done = threading.Event()

        def _block():
            release.wait(timeout=2.0)
            done.set()

        with (
            patch.object(mu, "_resolve_ble_teardown_timeout", None),
            pytest.raises(TimeoutError),
        ):
            _run_blocking_with_timeout(
                _block,
                timeout=0.05,
                label="test-no-resolve",
                ble_address=ble_address,
                ble_generation=gen,
            )

        release.set()
        done.wait(timeout=1.0)


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestRunBlockingLateCompletionIsStaleFallback:
    """Cover async_utils.py lines 392-404: worker late completion stale fallback."""

    def test_stale_generation_fallback_when_resolve_not_called(self):
        from mmrelay.meshtastic.async_utils import _run_blocking_with_timeout

        ble_address = "AA:11:BB:22:CC:33"
        gen = mu._advance_ble_generation(ble_address, transition="test-stale-fallback")

        release = threading.Event()
        done = threading.Event()

        real_record = mu._record_ble_teardown_timeout

        def _block():
            release.wait(timeout=2.0)
            done.set()

        def _record_and_set_release(addr, g):
            release.set()
            return real_record(addr, g)

        with (
            patch.object(
                mu, "_record_ble_teardown_timeout", side_effect=_record_and_set_release
            ),
            patch.object(mu, "_resolve_ble_teardown_timeout", return_value=(0, True)),
            patch.object(mu, "_is_ble_generation_stale", return_value=True),
            pytest.raises(TimeoutError),
        ):
            _run_blocking_with_timeout(
                _block,
                timeout=0.05,
                label="ble-interface-disconnect-stale-fallback",
                ble_address=ble_address,
                ble_generation=gen,
            )

        done.wait(timeout=1.0)


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestRunBlockingTimeoutRecordingPath:
    """Cover async_utils.py lines 453-470: timeout recording path with callable check."""

    def test_record_not_callable_skips(self):
        from mmrelay.meshtastic.async_utils import _run_blocking_with_timeout

        ble_address = "AA:BB:CC:DD:EE:FF"
        gen = mu._advance_ble_generation(ble_address, transition="test")

        with (
            patch.object(mu, "_record_ble_teardown_timeout", None),
            pytest.raises(TimeoutError),
        ):
            _run_blocking_with_timeout(
                lambda: time.sleep(10),
                timeout=0.05,
                label="test-record-not-callable",
                ble_address=ble_address,
                ble_generation=gen,
            )

    def test_done_set_after_timeout_triggers_resolve(self):
        from mmrelay.meshtastic.async_utils import _run_blocking_with_timeout

        ble_address = "FF:EE:DD:CC:BB:AA"
        gen = mu._advance_ble_generation(ble_address, transition="test-done-after")

        release = threading.Event()
        done = threading.Event()

        def _block():
            release.wait(timeout=2.0)
            done.set()

        with pytest.raises(TimeoutError):
            _run_blocking_with_timeout(
                _block,
                timeout=0.05,
                label="ble-client-disconnect-test",
                ble_address=ble_address,
                ble_generation=gen,
            )

        release.set()
        done.wait(timeout=1.0)
        for _ in range(50):
            if not mu._get_ble_unresolved_teardown_generations(ble_address):
                break
            time.sleep(0.01)
        assert mu._get_ble_unresolved_teardown_generations(ble_address) == []


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestTearDownMeshtasticClientBleGeneration:
    """Cover events.py lines 90-95: BLE teardown with generation registration."""

    def test_ble_interface_with_address_registers_generation(self):
        from mmrelay.meshtastic.events import (
            _tear_down_meshtastic_client_for_disconnect,
        )

        mock_iface = MagicMock()
        mock_iface.address = "AA:BB:CC:DD:EE:FF"
        mock_iface.client.bleak_client.address = "AA:BB:CC:DD:EE:FF"

        mu.meshtastic_client = mock_iface
        mu.meshtastic_iface = mock_iface

        with patch.object(mu, "_disconnect_ble_interface") as mock_disconnect:
            _tear_down_meshtastic_client_for_disconnect("connection_loss")

        mock_disconnect.assert_called_once()
        assert mu.meshtastic_iface is None

    def test_ble_interface_with_no_address_skips_generation(self):
        from mmrelay.meshtastic.events import (
            _tear_down_meshtastic_client_for_disconnect,
        )

        mock_iface = MagicMock()
        mock_iface.address = None
        del mock_iface.client

        mu.meshtastic_client = mock_iface
        mu.meshtastic_iface = mock_iface

        with patch.object(mu, "_extract_ble_address_from_interface", return_value=None):
            with patch.object(mu, "_disconnect_ble_interface") as mock_disconnect:
                _tear_down_meshtastic_client_for_disconnect("test")

        mock_disconnect.assert_called_once()
        assert mu.meshtastic_iface is None


@pytest.mark.usefixtures("reset_meshtastic_globals", "reset_matrix_utils_globals")
class TestParseMatrixMessageCommandRequireMention:
    """Cover command_bridge.py lines 352-356: require_mention with no bot_mxid."""

    def test_require_mention_no_bot_mxid_returns_none(self):
        from mmrelay.matrix.command_bridge import _parse_matrix_message_command

        matrix_utils.bot_user_id = None
        result = _parse_matrix_message_command("!help", ["help"], require_mention=True)
        assert result is None

    def test_require_mention_with_bot_mxid_and_mxid_prefix(self):
        from mmrelay.matrix.command_bridge import _parse_matrix_message_command

        matrix_utils.bot_user_id = "@bot:server.com"
        result = _parse_matrix_message_command(
            "@bot:server.com !help", ["help"], require_mention=True
        )
        assert result is not None
        assert result.command == "help"

    def test_require_mention_no_match(self):
        from mmrelay.matrix.command_bridge import _parse_matrix_message_command

        matrix_utils.bot_user_id = "@bot:server.com"
        result = _parse_matrix_message_command(
            "just some text", ["help"], require_mention=True
        )
        assert result is None

    def test_require_mention_display_name_fallback(self):
        from mmrelay.matrix.command_bridge import _parse_matrix_message_command

        matrix_utils.bot_user_id = "@bot:server.com"
        matrix_utils.bot_user_name = "MyBot"
        result = _parse_matrix_message_command(
            "MyBot: !help", ["help"], require_mention=True
        )
        assert result is not None
        assert result.command == "help"

    def test_empty_bodies_returns_none(self):
        from mmrelay.matrix.command_bridge import _parse_matrix_message_command

        matrix_utils.bot_user_id = "@bot:server.com"
        mock_event = MagicMock()
        mock_event.body = ""
        mock_event.source = {}
        result = _parse_matrix_message_command(
            mock_event, ["help"], require_mention=True
        )
        assert result is None

    def test_require_mention_false_no_mention(self):
        from mmrelay.matrix.command_bridge import _parse_matrix_message_command

        matrix_utils.bot_user_id = "@bot:server.com"
        result = _parse_matrix_message_command("!help", ["help"], require_mention=False)
        assert result is not None
        assert result.command == "help"


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestDisconnectBleInterfaceAsyncDisconnect:
    """Cover ble.py line 873: iscoroutinefunction disconnect path."""

    def test_async_disconnect_method(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        iface = MagicMock()
        iface._exit_handler = None
        iface.client = None
        iface.close.return_value = None

        def make_disconnect():
            async def _disconnect():
                return None

            return _disconnect

        iface.disconnect = make_disconnect()

        def _wait_and_close(coro, **kwargs):
            if hasattr(coro, "close"):
                coro.close()

        with (
            patch.object(mu, "_get_ble_iface_generation", return_value=(None, None)),
            patch.object(mu.time, "sleep"),
            patch.object(mu, "_wait_for_result", side_effect=_wait_and_close),
        ):
            _disconnect_ble_interface(iface, reason="test")


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestDisconnectBleInterfaceSyncAwaitable:
    """Cover ble.py line 889: awaitable result in sync disconnect."""

    def test_sync_disconnect_returns_awaitable(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        iface = MagicMock()
        iface._exit_handler = None

        timeout_kwarg_error = "got an unexpected keyword argument 'timeout'"

        def _disconnect_no_timeout(*_args: object, **_kwargs: object) -> MagicMock:
            if "timeout" in _kwargs:
                raise TypeError(timeout_kwarg_error)
            return MagicMock()

        iface.disconnect.side_effect = _disconnect_no_timeout
        iface.client = None
        iface.close.return_value = None

        with (
            patch.object(mu, "_get_ble_iface_generation", return_value=(None, None)),
            patch.object(mu.time, "sleep"),
            patch.object(mu, "_run_blocking_with_timeout") as mock_blocking,
        ):
            _disconnect_ble_interface(iface, reason="test")
            mock_blocking.assert_called()
            sync_fn = mock_blocking.call_args[0][0]
            with patch.object(mu, "_wait_for_result"):
                sync_fn()


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestDisconnectBleNoDisconnectMethod:
    """Cover ble.py line 918: interface without disconnect()."""

    def test_no_disconnect_method(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        iface = MagicMock(spec=["close", "client"])
        iface.client = None
        iface.close.return_value = None

        with (
            patch.object(mu, "_get_ble_iface_generation", return_value=(None, None)),
            patch.object(mu.time, "sleep"),
        ):
            _disconnect_ble_interface(iface, reason="test")


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestDisconnectBleCloseSyncAwaitableResult:
    """Cover ble.py line 1016-1017: _close_sync with awaitable result."""

    def test_close_returns_awaitable_inside_blocking(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        mock_awaitable = MagicMock()

        iface = MagicMock()
        iface._exit_handler = None
        del iface.disconnect
        iface.client = None

        timeout_kwarg_error = "got an unexpected keyword argument 'timeout'"

        def _close_no_timeout(*_args: object, **_kwargs: object) -> MagicMock:
            if "timeout" in _kwargs:
                raise TypeError(timeout_kwarg_error)
            return mock_awaitable

        iface.close.side_effect = _close_no_timeout

        with (
            patch.object(mu, "_get_ble_iface_generation", return_value=(None, None)),
            patch.object(mu.time, "sleep"),
            patch.object(mu, "_run_blocking_with_timeout") as mock_blocking,
        ):
            _disconnect_ble_interface(iface, reason="test")
            mock_blocking.assert_called()
            close_fn = mock_blocking.call_args[0][0]
            with patch.object(mu, "_wait_for_result"):
                close_fn()


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestDisconnectBleExceptionHandlers:
    """Cover ble.py lines 1033-1035: exception handlers in _disconnect_ble_interface."""

    def test_generic_exception_in_disconnect(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        iface = MagicMock()
        iface._exit_handler = None
        iface.disconnect.side_effect = RuntimeError("unexpected error")
        iface.client = None
        iface.close.return_value = None

        with (
            patch.object(mu, "_get_ble_iface_generation", return_value=(None, None)),
            patch.object(mu.time, "sleep"),
            patch.object(
                mu,
                "_run_blocking_with_timeout",
                side_effect=RuntimeError("block fail"),
            ),
        ):
            _disconnect_ble_interface(iface, reason="test")

    def test_timeout_error_in_disconnect(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        iface = MagicMock()
        iface._exit_handler = None
        iface.disconnect.return_value = None
        iface.client = None
        iface.close.return_value = None

        call_count = 0

        def _sleep_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                pass
            elif call_count > 6:
                raise TimeoutError("simulated timeout")

        with (
            patch.object(mu, "_get_ble_iface_generation", return_value=(None, None)),
            patch.object(mu.time, "sleep", side_effect=_sleep_side_effect),
        ):
            _disconnect_ble_interface(iface, reason="test")


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestDisconnectBleGenerationFallbackToAddress:
    """Cover ble.py line 833: generation fallback to address lookup."""

    def test_generation_none_mapped_none_address_set(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        iface = MagicMock()
        iface._exit_handler = None
        iface.disconnect.return_value = None
        iface.client = None
        iface.close.return_value = None

        with (
            patch.object(
                mu, "_get_ble_iface_generation", return_value=("aabbcc", None)
            ),
            patch.object(mu, "_get_ble_generation", return_value=3),
            patch.object(mu.time, "sleep"),
        ):
            _disconnect_ble_interface(iface, reason="test", ble_address="AA:BB:CC")


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestRunBlockingResolveGuardPaths:
    """Cover async_utils.py lines 348-349, 356-357, 364-365."""

    def test_resolve_called_with_no_ble_generation(self):
        from mmrelay.meshtastic.async_utils import _run_blocking_with_timeout

        release = threading.Event()
        done = threading.Event()

        def _block():
            release.wait(timeout=2.0)
            done.set()

        with (
            pytest.raises(TimeoutError),
            patch.object(mu, "_resolve_ble_teardown_timeout"),
        ):
            _run_blocking_with_timeout(
                _block,
                timeout=0.05,
                label="test-no-gen",
                ble_address="AA:BB",
                ble_generation=None,
            )

        release.set()
        done.wait(timeout=1.0)

    def test_resolve_guard_with_zero_generation(self):
        from mmrelay.meshtastic.async_utils import _run_blocking_with_timeout

        release = threading.Event()
        done = threading.Event()

        def _block():
            release.wait(timeout=2.0)
            done.set()

        real_record = mu._record_ble_teardown_timeout

        def _record_then_release(addr, gen):
            release.set()
            return real_record(addr, gen)

        with (
            patch.object(
                mu, "_record_ble_teardown_timeout", side_effect=_record_then_release
            ),
            patch.object(mu, "_resolve_ble_teardown_timeout") as mock_resolve,
            pytest.raises(TimeoutError),
        ):
            _run_blocking_with_timeout(
                _block,
                timeout=0.05,
                label="test-zero-gen",
                ble_address="AA:BB",
                ble_generation=0,
            )

        done.wait(timeout=1.0)
        mock_resolve.assert_not_called()

    def test_resolve_already_resolved_skips(self):
        from mmrelay.meshtastic.async_utils import _run_blocking_with_timeout

        ble_address = "CC:DD:EE:FF:00:11"
        gen = mu._advance_ble_generation(ble_address, transition="test")

        release = threading.Event()
        done = threading.Event()

        def _block():
            release.wait(timeout=2.0)
            done.set()

        real_resolve = mu._resolve_ble_teardown_timeout
        resolve_count = 0

        def _counting_resolve(addr, g):
            nonlocal resolve_count
            resolve_count += 1
            return real_resolve(addr, g)

        with (
            patch.object(
                mu, "_resolve_ble_teardown_timeout", side_effect=_counting_resolve
            ),
            pytest.raises(TimeoutError),
        ):
            _run_blocking_with_timeout(
                _block,
                timeout=0.05,
                label="ble-interface-disconnect-dup-resolve",
                ble_address=ble_address,
                ble_generation=gen,
            )

        release.set()
        done.wait(timeout=1.0)
        for _ in range(50):
            if not mu._get_ble_unresolved_teardown_generations(ble_address):
                break
            time.sleep(0.01)
        assert mu._get_ble_unresolved_teardown_generations(ble_address) == []


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestRunBlockingStaleGenerationFallback:
    """Cover async_utils.py lines 398-404: is_generation_stale fallback when resolve not called."""

    def test_stale_check_used_when_resolve_skipped(self):
        from mmrelay.meshtastic.async_utils import _run_blocking_with_timeout

        ble_address = "22:33:44:55:66:77"
        gen = mu._advance_ble_generation(ble_address, transition="test-stale")

        release = threading.Event()
        done = threading.Event()

        def _block():
            release.wait(timeout=2.0)
            done.set()

        real_record = mu._record_ble_teardown_timeout

        def _record_and_release(addr, g):
            release.set()
            return real_record(addr, g)

        with (
            patch.object(
                mu, "_record_ble_teardown_timeout", side_effect=_record_and_release
            ),
            patch.object(
                mu,
                "_resolve_ble_teardown_timeout",
                side_effect=lambda *a, **k: (0, None),
            ),
            patch.object(mu, "_is_ble_generation_stale", return_value=True),
            pytest.raises(TimeoutError),
        ):
            _run_blocking_with_timeout(
                _block,
                timeout=0.05,
                label="ble-client-disconnect-stale-check",
                ble_address=ble_address,
                ble_generation=gen,
            )

        done.wait(timeout=1.0)
        for _ in range(50):
            if not mu._get_ble_unresolved_teardown_generations(ble_address):
                break
            time.sleep(0.01)


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestTearDownNonBleOSError:
    """Cover events.py lines 113-115: non-BLE client close with non-EBADF OSError."""

    def test_non_ebadf_oserror_logged(self):
        from mmrelay.meshtastic.events import (
            _tear_down_meshtastic_client_for_disconnect,
        )

        client = MagicMock()
        client.close.side_effect = OSError(errno.EPIPE, "broken pipe")

        mu.meshtastic_client = client
        mu.meshtastic_iface = None

        _tear_down_meshtastic_client_for_disconnect("test")

    def test_generic_exception_on_close_logged(self):
        from mmrelay.meshtastic.events import (
            _tear_down_meshtastic_client_for_disconnect,
        )

        client = MagicMock()
        client.close.side_effect = RuntimeError("unexpected")

        mu.meshtastic_client = client
        mu.meshtastic_iface = None

        _tear_down_meshtastic_client_for_disconnect("test")
