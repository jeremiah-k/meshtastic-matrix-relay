# Matrix Dual-Library Compatibility Plan

## Problem Statement

MMRelay imports the `nio` namespace for Matrix support. Historically that
namespace came from upstream `matrix-nio`. The project now needs to support the
`mindroom-nio` fork as an alternate provider without assuming the two
distributions can coexist in one Python environment.

Both providers expose the same import namespace, so runtime code must detect
capabilities from the imported `nio` package and its optional crypto backend.
Provider metadata is useful for diagnostics and install guidance, but it should
not drive behavior when a direct capability check is available.

## Current Failure Mode

With `matrix-nio` replaced by `mindroom-nio`, MMRelay can start and sync, but
E2EE setup is incorrectly disabled because the existing checks hard-require the
legacy `olm` module. `mindroom-nio` uses `vodozemac` and reports encryption
support through `nio.crypto.ENCRYPTION_ENABLED`, so an `olm` import failure does
not mean Matrix E2EE is unavailable.

The visible result is that encrypted rooms are treated as blocked even when the
active provider may be capable of encrypting and decrypting messages.

## Supported Provider Matrix

| Provider namespace owner | Crypto extra state                                                                  | Expected MMRelay mode                                                                                 |
| ------------------------ | ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `matrix-nio`             | no `python-olm` / no usable store                                                   | Unencrypted Matrix only; encrypted room sends blocked with `matrix-nio[e2e]` / `python-olm` guidance  |
| `matrix-nio`             | `python-olm`, `nio.crypto.OlmDevice`, and `nio.store.SqliteStore` available         | E2EE enabled when configured and credentials exist                                                    |
| `mindroom-nio`           | no `vodozemac` / `nio.crypto.ENCRYPTION_ENABLED` false                              | Unencrypted Matrix only; encrypted room sends blocked with `mindroom-nio[e2e]` / `vodozemac` guidance |
| `mindroom-nio`           | `vodozemac`, `nio.crypto.ENCRYPTION_ENABLED`, and `nio.store.SqliteStore` available | E2EE enabled when configured and credentials exist                                                    |
| unknown `nio` provider   | Matrix APIs import, crypto capability unclear                                       | Prefer safe unencrypted behavior and include detected diagnostics in guidance                         |

## Compatibility Principles

- Keep compatibility code inside `mmrelay.matrix`, starting with
  `src/mmrelay/matrix/compat.py`.
- Prefer capability detection over provider or version branching.
- Keep imports lazy and guarded so missing E2EE extras do not break normal
  unencrypted operation.
- Avoid broad exception swallowing. Missing optional modules should be handled
  as optional capability failures; unexpected runtime errors should remain
  visible.
- User-facing install messages must name the relevant backend for the active
  provider: `python-olm` for upstream legacy E2EE and `vodozemac` for
  `mindroom-nio`.
- Treat `matrix-nio` and `mindroom-nio` as mutually exclusive namespace owners
  in normal installs. If both distributions are installed, report that clearly
  and rely on imported `nio` capabilities, not assumptions about ownership.
- Preserve existing public MMRelay APIs and existing matrix-nio behavior.

## Implementation Phases

### Phase 1: Compatibility Module

- Add `mmrelay.matrix.compat`.
- Detect installed provider metadata with `importlib.metadata`.
- Detect runtime crypto capability by importing optional modules only when
  needed:
  - `olm`
  - `vodozemac`
  - `nio.crypto`
  - `nio.store`
- Export a small immutable capabilities object and cache helpers for normal
  runtime plus test isolation.
- Provide a single formatting helper for E2EE-unavailable messages.

### Phase 2: E2EE Detection Refactor

- Replace direct `olm` checks in `mmrelay.e2ee_utils` with
  `detect_matrix_capabilities()`.
- Keep status shape stable for callers.
- Add provider-specific issue text.

### Phase 3: Auth and Client Setup Updates

- Replace direct `olm` checks in `mmrelay.matrix.auth._configure_e2ee`.
- Keep store path resolution and client construction unchanged.
- Ensure logs report the detected provider/backend and correct install hint.

### Phase 4: Packaging Extras Cleanup

- Decide whether MMRelay keeps `matrix-nio` as the default dependency or moves
  Matrix providers behind explicit extras.
- Avoid extras that install both namespace owners into the same environment.
- Document replacement install commands for users testing `mindroom-nio`.

### Phase 5: Runtime API Audit

Audit all direct calls and imports against both providers:

- `AsyncClientConfig`
- `AsyncClient`
- `restore_login`
- `whoami`
- `sync`
- `sync_forever`
- `stop_sync_forever`
- `keys_upload`
- `room_send`
- `upload`
- `get_displayname`
- `room_resolve_alias`
- event classes used by MMRelay, including invite and encrypted-event classes

Fragile imports, such as alternate `InviteMemberEvent` locations, should be
centralized only if they become a recurring compatibility surface.

### Phase 6: Tests and Documentation

- Add focused tests for capability detection across both provider families.
- Add E2EE status and auth setup tests for correct install guidance.
- Add durable docs modeled after the BLE dual-library compatibility contract.

## Risks

- Both provider distributions can be installed at once while only one owns the
  importable `nio` namespace on disk. Detection must avoid false certainty in
  this case.
- `matrix-nio` and `mindroom-nio` may both expose legacy names such as
  `OlmDevice` even when backed by different crypto libraries.
- Some tests currently patch old import locations. Tests should move toward
  patching the compatibility boundary instead of internal import mechanics.
- Packaging changes can break existing install workflows if done too early.

## Validation Checklist

- `matrix-nio` without E2EE extras reports missing `python-olm`.
- `matrix-nio` with E2EE extras remains ready.
- `mindroom-nio` without E2EE extras reports missing `vodozemac`.
- `mindroom-nio` with E2EE extras reports ready.
- Encrypted-room send blocking uses the unified E2EE status.
- Windows unsupported handling remains unchanged.
- Normal unencrypted Matrix operation does not import hard crypto dependencies.

## Open Questions

- Should MMRelay retain `matrix-nio` as the default install and make
  `mindroom-nio` an explicit replacement extra, or should both providers move
  behind extras?
- Should `mmrelay[e2e]` continue to mean upstream `matrix-nio[e2e]`, with a
  separate `mindroom-e2e` extra for the fork?
- What exact `mindroom-nio` version should be pinned once the fork publishes a
  stable release for MMRelay consumption?
