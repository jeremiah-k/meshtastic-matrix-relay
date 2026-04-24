# mmrelay BLE Follow-Up After mtjk BLE Reliability PR

## Context

`mmrelay` and `mtjk` have been co-developed. The recent `mtjk` BLE library work (merged to `maint-423-1` / post-`2.7.8.post2`) provides:

- typed BLE exception hierarchy (`MeshtasticBLEError`, `BLEDiscoveryError`, `BLEDeviceNotFoundError`, `BLEConnectionTimeoutError`, `BLEConnectionSuppressedError`, `BLEAddressMismatchError`, `BLEDBusTransportError`)
- explicit-address post-connect mismatch validation
- `BLEInterface.BLEError = MeshtasticBLEError` (all typed exceptions catchable as `BLEInterface.BLEError`)
- `BLEInterface.bleAddress` (camelCase primary) with `ble_address` shim — **returns only actual BLE MACs**, not device names
- `BLEClient.bleAddress` with `ble_address` shim
- public `sanitize_address()` export from both `meshtastic.interfaces.ble` and `meshtastic.ble_interface`
- public bounded `BLEInterface.disconnect(timeout=...)` / `close(timeout=...)` with timeout-budget slicing across shutdown stages
- targeted Linux/BlueZ stale cleanup + one retry in library orchestrator
- `_safe_close_client()` fallback when `await_timeout` is unsupported by the underlying client
- `BLEDBusTransportError.__str__()` returns the normalized Meshtastic-facing message
- `BLEDeviceNotFoundError.identifier` property for Bleak compatibility

This follow-up keeps `mmrelay` app-specific orchestration (executor/generation/degraded-state/retry policy), but removes avoidable duplication and brittle string matching where the library now has explicit API support.

## Recommended Changes (Priority Order)

### P0 - Required to leverage new mtjk behavior safely

1. Bump mtjk dependency to a release or exact commit that includes this BLE PR.
   - Do not depend on a mutable branch such as `develop` for normal dependency
     resolution; use the first released version containing these BLE changes, or
     pin to the exact commit hash if no release exists yet.
   - An exact release/tag/commit is required so builds remain reproducible.

2. Add capability-aware typed exception imports in `mmrelay` facade layer.
   - File target: `src/mmrelay/meshtastic_utils.py`
   - Prefer importing these when available:
     - `MeshtasticBLEError`
     - `BLEConnectionSuppressedError`
     - `BLEDiscoveryError`
     - `BLEDeviceNotFoundError`
     - `BLEConnectionTimeoutError`
     - `BLEAddressMismatchError`
     - `BLEDBusTransportError`
   - Keep fallback behavior for older library versions (no hard import break).
   - Also import `sanitize_address` when available.

3. Replace message heuristics with exception-type checks first.
   - File target: `src/mmrelay/meshtastic/ble.py`
   - Update:
     - `_is_ble_duplicate_connect_suppressed_error(...)`
     - `_is_ble_discovery_error(...)`
   - Policy:
     - use `isinstance(...)` against typed exceptions first
     - keep existing string-token fallback for legacy mtjk/upstream compatibility.
   - Example:
     ```python
     if isinstance(exc, BLEConnectionSuppressedError):
         return True
     # fallback to existing string check
     ```

4. Handle typed BLE exceptions explicitly in retry loop.
   - File target: `src/mmrelay/meshtastic/connection.py`
   - Add dedicated handling branches **before** the generic `except Exception`:
     - `BLEConnectionTimeoutError` → timeout/backoff path (distinct from generic `TimeoutError`)
     - `BLEDiscoveryError` / `BLEDeviceNotFoundError` → transient discovery/setup path (same as current `BLEDiscoveryTransientError`)
     - `BLEDBusTransportError` → BlueZ transport-specific guidance/backoff
     - `BLEAddressMismatchError` → hard explicit-target mismatch; log clearly, roll back, return `None`, and do not retry
     - `BLEConnectionSuppressedError` → reset gate and retry/backoff
   - Keep broad `BLEInterface.BLEError` discovery fallback after typed
     non-discovery exclusions so it does not swallow
     `BLEAddressMismatchError`, `BLEConnectionTimeoutError`,
     `BLEDBusTransportError`, or `BLEConnectionSuppressedError`.

5. Prefer library `bleAddress` property before manual path-walking.
   - File target: `src/mmrelay/meshtastic/ble.py`
   - Update `_extract_ble_address_from_interface(...)`:
     - first read `iface.bleAddress`, then `iface.ble_address`, when present
     - then fallback to current manual probing for older versions
   - **Important**: `bleAddress` now returns `None` for non-address identifiers (device names). If mmrelay needs the configured identifier for display/logging, use `iface.address` directly, not `iface.bleAddress`.

### P1 - Strongly recommended cleanup/reduction of duplicate logic

6. Prefer library `sanitize_address()` when available.
   - File target: `src/mmrelay/meshtastic/ble.py`
   - Update `_sanitize_ble_address(...)`:
     - use imported library sanitizer if present
     - keep current local fallback for older versions.
   - This is a drop-in replacement; the library function handles the same separators.

7. Use bounded library shutdown API when available.
   - File targets:
     - `src/mmrelay/meshtastic/ble.py`
     - `src/mmrelay/meshtastic/connection.py`
   - In `_disconnect_ble_interface(...)`, try:
     - `iface.disconnect(timeout=...)` if supported
     - `iface.close(timeout=...)` if supported
   - Keep existing daemon-thread timeout wrappers as fallback for legacy/hanging implementations only.
   - Rationale: library `close(timeout=...)` now slices the budget across management wait, receive thread join, mesh close, client disconnect, and notification unsubscribe stages.

8. Update `_validate_ble_connection_address` comment/logging.
   - File target: `src/mmrelay/meshtastic/ble.py`
   - `_validate_ble_connection_address(...)` currently references "substring matching in device discovery selecting wrong device" in error text.
   - Modern mtjk uses explicit-address validation in the library; substring-match mismatches are now caught as `BLEAddressMismatchError`.
   - Update the comment to note that this is a legacy guard and that modern mtjk catches the mismatch earlier.
   - Keep the helper for dual-library compatibility.

9. Consider `str(err)` for `BLEDBusTransportError` when logging.
   - File target: `src/mmrelay/meshtastic/connection.py`
   - `BLEDBusTransportError.__str__()` now returns the normalized Meshtastic message (not raw DBus formatting).
   - If you log `str(err)` for DBus errors, you now get a human-friendly message while `err.dbus_error_name` and `err.dbus_error_body` preserve the raw metadata for diagnostics.

### P2 - Optional behavior tuning (after P0/P1)

10. Re-evaluate unconditional pre-connect `_disconnect_ble_by_address(...)` for modern mtjk mode.
    - File target: `src/mmrelay/meshtastic/connection.py`
    - Modern mtjk already performs targeted stale cleanup on stale/busy direct-connect failures (stale BlueZ cleanup + retry).
    - Implemented direction:
      - keep startup pre-cleanup for legacy mode
      - skip app-level pre-cleanup when typed BLE capabilities are detected.
    - The stale-cleanup skip is gated by typed BLE capability presence, not by
      `auto_reconnect` support alone.

11. Update compatibility docs/tests for typed-exception-aware behavior.
    - Candidate files:
      - `docs/dev/BLE_DUAL_LIBRARY_COMPATIBILITY.md`
      - `tests/test_meshtastic_ble.py`
      - `tests/test_meshtastic_utils_reconnect_paths.py`
      - `tests/test_meshtastic_connection.py`
    - Add test coverage for each typed exception path in the retry loop.

## What Should Stay in mmrelay (Do Not Remove)

- BLE executor/degraded-state/generation ownership logic
- app-level retry/backoff and bootstrap sequencing
- late-future cleanup and shutdown choreography tied to relay lifecycle
- daemon-thread timeout wrappers for legacy library versions

These are application-level concerns and should remain outside `mtjk`.

## mtjk Changes Since Original Follow-Up (Reference for mmrelay Authors)

The following mtjk fixes were added after the original draft of this document:

| Change                                                       | Impact on mmrelay                                                                               |
| ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| `_safe_close_client()` `await_timeout` fallback              | No action needed; library handles compat clients/mocks                                          |
| `retry_client` cleanup on all non-success paths              | No action needed; library prevents leaks                                                        |
| `BLEDBusTransportError.__str__()` returns normalized message | Use `str(err)` for user-facing logs; access `dbus_error_name`/`dbus_error_body` for diagnostics |
| `bleAddress` tightened to return only BLE MACs               | If you need the configured identifier (name/alias), use `iface.address` directly                |
| timeout fallback uses `_is_unexpected_keyword_error`         | No action needed; library handles compat consistently                                           |
| `BLEDeviceNotFoundError.identifier` property                 | If you catch `BleakDeviceNotFoundError`, you can read `.identifier`                             |

## Suggested Implementation Sequence

1. Dependency bump + capability imports (P0.1, P0.2)
2. Typed-exception-first classification (P0.3)
3. Retry-loop explicit exception branches (P0.4)
4. `ble_address` / `sanitize_address` integration (P0.5, P1.6)
5. Bounded shutdown API adoption (P1.7)
6. Comment/logging cleanup (P1.8, P1.9)
7. Optional pre-cleanup policy tuning (P2.10)
8. Test/doc updates (P2.11)
