# Matrix Dual-Library Compatibility

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

## Compatibility Boundary

Matrix provider detection belongs in `mmrelay.matrix.compat`. That module owns:

- Provider metadata detection with `importlib.metadata`.
- Optional crypto capability detection through lazy guarded imports:
  `olm`, `vodozemac`, `nio.crypto`, and `nio.store`.
- A small immutable capabilities object for runtime decisions, diagnostics, and
  tests.
- Provider-aware E2EE unavailable messages and install guidance.
- Cache helpers so normal runtime checks are stable and tests can isolate
  capability scenarios.

Runtime code should consume this compatibility boundary instead of checking
`olm`, `vodozemac`, `nio.crypto`, or package metadata directly.

## E2EE Readiness Contract

E2EE readiness requires a usable crypto backend and a usable store:

- Legacy upstream mode: `python-olm`, `nio.crypto.OlmDevice`, and
  `nio.store.SqliteStore`.
- Mindroom mode: `vodozemac`, `nio.crypto.ENCRYPTION_ENABLED is True`, and
  `nio.store.SqliteStore`.

The public E2EE status shape remains stable for callers. User-facing issues and
fix instructions should be provider-aware:

- `matrix-nio` missing crypto should mention `python-olm`,
  `matrix-nio[e2e]`, or `mmrelay[e2e]`.
- `mindroom-nio` missing crypto should mention `vodozemac` and
  `mindroom-nio[e2e]`.
- If both known providers are installed, the diagnostic should tell the user to
  uninstall one `nio` namespace owner before enabling E2EE.

## Packaging Contract

MMRelay keeps `matrix-nio` as the default Matrix provider. The `mmrelay[e2e]`
extra remains upstream-compatible and installs `matrix-nio[e2e]`.

Mindroom extras are explicit replacement-provider install targets:

- `mindroom`
- `mindroom-e2e`

These extras must not be presented as safe to install alongside the default
provider. User documentation and diagnostics should say that mindroom users must
replace `matrix-nio` in their environment because both distributions provide
the `nio` namespace.

## Runtime API Surface

The compatibility contract covers all MMRelay calls and imports against `nio`:

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

## Test Expectations

- Capability tests should patch internals of `mmrelay.matrix.compat` only.
- Runtime E2EE tests should patch the compatibility boundary, such as
  `mmrelay.matrix.auth.get_matrix_capabilities` or
  `mmrelay.e2ee_utils.get_matrix_capabilities`.
- Tests should not pretend to simulate missing crypto by patching old internal
  import paths that no longer control E2EE readiness.
- Coverage should include both provider families, missing crypto extras,
  provider-aware messages, and the dual-provider namespace conflict.

## Risks

- Both provider distributions can be installed at once while only one owns the
  importable `nio` namespace on disk. Detection must avoid false certainty in
  this case.
- `matrix-nio` and `mindroom-nio` may both expose legacy names such as
  `OlmDevice` even when backed by different crypto libraries.
- Packaging changes can break existing install workflows if extras imply that
  both namespace owners can coexist.

## Validation Checklist

- `matrix-nio` without E2EE extras reports missing `python-olm`.
- `matrix-nio` with E2EE extras remains ready.
- `mindroom-nio` without E2EE extras reports missing `vodozemac`.
- `mindroom-nio` with E2EE extras reports ready.
- Encrypted-room send blocking uses the unified E2EE status.
- Windows unsupported handling remains unchanged.
- Normal unencrypted Matrix operation does not import hard crypto dependencies.

## Open Questions

- What exact `mindroom-nio` version should be pinned once the fork publishes a
  stable release for MMRelay consumption?
