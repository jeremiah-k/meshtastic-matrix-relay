# Matrix Dual-Library Compatibility

## Default Provider: mindroom-nio

**mindroom-nio is now the default Matrix provider.** It replaces upstream
`matrix-nio` as the base dependency. The `mmrelay[e2e]` extra installs
`mindroom-nio[e2e]` (vodozemac backend).

`matrix-nio` remains legacy-supported **only through a controlled manual
replacement workflow**. There are no `mmrelay[matrix-nio]` extras because
extras add dependencies on top of the base `mindroom-nio`; they cannot
remove it. Installing both `matrix-nio` and `mindroom-nio` in the same
environment is always a conflict.

## Problem Statement

MMRelay imports the `nio` namespace for Matrix support. Historically that
namespace came from upstream `matrix-nio`. The project now uses the
`mindroom-nio` fork as the default provider without assuming the two
distributions can coexist in one Python environment.

Both providers expose the same import namespace, so runtime code must detect
capabilities from the imported `nio` package and its optional crypto backend.
Provider metadata is useful for diagnostics and install guidance, but it should
not drive behavior when a direct capability check is available.

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
  in normal installs. If both distributions are installed, report that clearly,
  disable E2EE, and do not rely on imported `nio` capabilities to enable
  encryption.
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

- `matrix-nio` missing crypto should mention `python-olm` and
  `matrix-nio[e2e]`.
- `mindroom-nio` missing crypto should mention `vodozemac` and
  `mindroom-nio[e2e]`.
- If both known providers are installed, the diagnostic should tell the user to
  uninstall one `nio` namespace owner before enabling E2EE.

## Packaging Contract

**mindroom-nio is the default Matrix provider.** The base `mmrelay` install
brings in `mindroom-nio`. The `mmrelay[e2e]` extra installs
`mindroom-nio[e2e]`.

**matrix-nio is legacy-supported through manual replacement only.** There are
no `mmrelay[matrix-nio]` extras because extras add to base dependencies and
cannot remove them. The recommended workflow:

```bash
# Default install (mindroom-nio, no E2EE)
pip install mmrelay

# Default E2EE install (mindroom-nio with vodozemac)
pip install 'mmrelay[e2e]'

# Legacy matrix-nio (manual replacement)
pip install mmrelay
pip uninstall mindroom-nio
pip install 'matrix-nio==0.25.2'

# Legacy matrix-nio with E2EE (manual replacement)
pip install mmrelay
pip uninstall mindroom-nio
pip install 'matrix-nio[e2e]==0.25.2'
```

**Mixing matrix-nio and mindroom-nio in the same environment is always a
conflict.** The extra system cannot represent this cleanly; manual replacement
is the only supported path.

> **Note**: `docs/COMPATIBILITY.md` is the canonical compatibility and
> deprecation inventory. This plan should stay aligned with it.

## Hard Warning: conflicting nio namespace

`matrix-nio` and `mindroom-nio` both own the `nio` import namespace. They
**MUST NOT** be installed in the same Python environment. If both are present:

1. `both_known_providers_installed` is set to `True`.
2. `encryption_available` is forced to `False`.
3. E2EE is blocked regardless of what each provider individually supports.
4. A clear conflict diagnostic is emitted.
5. The user is told to uninstall one provider.

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

### For mindroom-nio (default provider)

- `mindroom-nio` without E2EE extras reports missing `vodozemac`.
- `mindroom-nio` with `mindroom-nio[e2e]` / `vodozemac` reports E2EE ready.
- `mmrelay[e2e]` installs `mindroom-nio[e2e]`.

### For matrix-nio (legacy provider, manual replacement only)

- `matrix-nio` without E2EE extras reports missing `python-olm`.
- `matrix-nio` with `matrix-nio[e2e]` / `python-olm` reports E2EE ready.
- Custom install workflow: `pip install mmrelay` → `pip uninstall mindroom-nio` → `pip install 'matrix-nio[e2e]==0.25.2'`.

### Cross-cutting

- Encrypted-room send blocking uses the unified E2EE status.
- Windows unsupported handling remains unchanged.
- Normal unencrypted Matrix operation does not import hard crypto dependencies.
- The CLI delegates to `get_matrix_capabilities()` instead of hardcoding `olm` imports.
- If both providers are detected, encryption is disabled with a conflict diagnostic.
- No extra installs both providers.
