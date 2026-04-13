# BLE Dual-Library Compatibility (mmrelay + mtjk)

## Scope

`mmrelay` currently supports two Meshtastic BLE interface contracts:

1. Modern mtjk-style BLE interface (supports `auto_reconnect` constructor kwarg)
2. Legacy upstream-style BLE interface (no `auto_reconnect` kwarg)

Reference fork for the modern interface contract:
[mtjk (Meshtastic Python fork)](https://github.com/jeremiah-k/meshtastic-python).

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

Primary integration point:

- `src/mmrelay/meshtastic/connection.py`

The gate is constructor signature introspection for
`BLEInterface.__init__(..., auto_reconnect=...)`.

## Capability Modes

### Modern managed mode

Used when `auto_reconnect` is supported:

- `mmrelay` passes `auto_reconnect=False`
- `mmrelay` owns retry/backoff sequencing
- explicit-address retries stay direct (no discovery scan from `mmrelay`)
- constructor watchdog includes grace budget for staged setup behavior

### Legacy compatibility mode

Used when `auto_reconnect` is absent or signature introspection is unavailable:

- `mmrelay` does not pass `auto_reconnect`
- compatibility decision remains sticky for that attempt
- `mmrelay` avoids assuming modern staged connect semantics

## Addressing and Discovery Rules

### Explicit BLE address (`AA:BB:...`)

- Treat as transport/reconnect workflow.
- Use direct connect + cleanup + backoff.
- Do not add scan fallback from `mmrelay`.

### Identifier/no-address flows

- Discovery remains valid and library-owned.
- `mmrelay` should not force explicit-address semantics onto identifier flows.

## Startup vs Reconnect Policy

### Startup

- Do one best-effort stale-address cleanup before creating interface.
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
