# BLE Dual-Library Compatibility (mmrelay + mtjk)

## Scope

`mmrelay` currently supports two Meshtastic BLE interface contracts:

1. Modern mtjk-style BLE interface (exports typed BLE exceptions)
2. Legacy upstream-style BLE interface (no typed BLE exceptions)

Reference fork for the modern interface contract:
[mtjk (Meshtastic Python fork)](https://github.com/jeremiah-k/mtjk).

This document records the technical contract so BLE changes in either repo do not
reintroduce double-reconnect loops, scan regressions, or constructor/connect
timeouts.

## Why This Exists (Short Version)

`mmrelay` needed tighter connection lifecycle control (startup, reconnect,
shutdown, worker watchdogs, and message flow consistency) while still staying
compatible with older Meshtastic BLE implementations.

## Integration Boundary

- `mmrelay` is the top-level reconnect orchestrator.
- `mtjk` provides BLE transport/interface behavior.
- Capability detection is feature-based, not version-based.
- Modern mtjk owns targeted stale BlueZ cleanup when typed BLE exception
  capabilities are present, and explicit-address validation.

Primary integration point:

- `src/mmrelay/meshtastic/connection.py`

The gate is constructor signature introspection for
`BLEInterface.__init__(..., auto_reconnect=...)`.

## Capability Modes

### Modern managed mode

Used when typed BLE exception capabilities are present (mtjk exports
`MeshtasticBLEError`, `BLEDiscoveryError`, `BLEConnectionTimeoutError`, etc.):

- `mmrelay` passes `auto_reconnect=False` when the constructor supports it
- `mmrelay` owns retry/backoff sequencing
- `mmrelay` owns executor/degraded-state/generation lifecycle, bootstrap
  sequencing, and late-future cleanup
- explicit-address retries stay direct (no discovery scan from `mmrelay`)
- constructor watchdog includes grace budget for staged setup behavior
- app-level stale BlueZ pre-cleanup is skipped because the library owns
  targeted cleanup internally

`auto_reconnect` support alone does **not** mean mtjk owns stale cleanup;
only typed BLE exception presence gates the skip.

### Legacy compatibility mode

Used when typed BLE exception capabilities are absent or signature introspection is unavailable:

- `mmrelay` does not pass `auto_reconnect` when unsupported
- compatibility decision remains sticky for that attempt
- `mmrelay` avoids assuming modern staged connect semantics
- `mmrelay` keeps daemon-thread timeout wrappers and explicit stale-address
  pre-cleanup for older libraries

## Typed BLE Exceptions

When available from `meshtastic.ble_interface`, `mmrelay` imports typed mtjk BLE
exceptions through guarded capability checks:

- `BLEAddressMismatchError`: hard explicit-target mismatch. `mmrelay` rolls back
  the attempt and does not retry.
- `BLEDiscoveryError` / `BLEDeviceNotFoundError`: transient discovery/setup
  failure. `mmrelay` rolls back and retries with normal backoff.
- `BLEConnectionTimeoutError`: BLE-library timeout. `mmrelay` rolls back and uses
  the timeout/backoff path before generic `TimeoutError` handling.
- `BLEDBusTransportError`: BlueZ/DBus transport failure. `mmrelay` logs
  `str(err)` for operators, logs DBus diagnostics at debug level, and retries.
- `BLEConnectionSuppressedError`: duplicate/recent connection gate suppression.
  `mmrelay` rolls back, resets process-local BLE gate state when available, and
  retries.

Legacy string and broad BLE error fallbacks remain for older Meshtastic installs.
The broad `BLEInterface.BLEError` fallback must run only after typed
non-discovery exceptions are excluded, because modern mtjk aliases
`BLEInterface.BLEError` to the typed BLE base class.

## Addressing and Discovery Rules

### Explicit BLE address (`AA:BB:...`)

- Treat as transport/reconnect workflow.
- Use direct connect + cleanup + backoff.
- Do not add scan fallback from `mmrelay`.
- Prefer `iface.bleAddress`, then `iface.ble_address`, for connected MAC
  extraction. Only 12-hex-character sanitized MACs are accepted; device names are
  not returned from `_extract_ble_address_from_interface`.
- Delegate address normalization to mtjk `sanitize_address()` when available,
  falling back to local separator removal for legacy installs.

### Identifier/no-address flows

- Discovery remains valid and library-owned.
- `mmrelay` should not force explicit-address semantics onto identifier flows.
- Use `iface.address` when a configured identifier or device name is needed for
  logs; do not treat it as a validated BLE MAC unless it sanitizes to 12 hex
  characters.

## Shutdown Contract

Modern mtjk exposes bounded `BLEInterface.disconnect(timeout=...)` and
`close(timeout=...)`. `mmrelay` tries those APIs first and avoids repeating
interface-level daemon-thread wrappers when they succeed. If a library rejects
the `timeout` keyword, `mmrelay` falls back to the legacy no-arg calls, explicit
client cleanup, and daemon-thread timeout wrappers.

## Startup vs Reconnect Policy

### Startup

- Do one best-effort stale-address cleanup before creating interface when typed
  BLE exception capabilities are absent (legacy mode only).
- Build one interface instance per attempt.
- Use bounded constructor timeout + watchdog.

### Reconnect

- Prefer cleanup/reset/recreate over reusing uncertain interface state.
- Keep retry backoff behavior explicit and bounded.
- Avoid discovery insertion for explicit-address retries.

## Timeout Budget Coupling

`mmrelay` wait budget for concurrent callers must align with BLE constructor
watchdog inputs:

- `BLE_INTERFACE_CREATE_TIMEOUT_FLOOR_SECS`
- `BLE_INTERFACE_CREATE_GRACE_SECS`
- `BLE_CONNECT_TIMEOUT_SECS`

Caller-side wait max should include all three terms to avoid a second caller
timing out while a first attempt is still within watchdog budget.

## Cross-Repo Maintenance Notes

- Prefer capability checks (`inspect.signature`, guarded `hasattr`) over version
  branching.
- Do not assume mtjk internals from `mmrelay` unless guarded.
- Keep explicit-address direct-only behavior consistent in both repos.
- Keep discovery fallback behavior limited to identifier/no-address paths.
