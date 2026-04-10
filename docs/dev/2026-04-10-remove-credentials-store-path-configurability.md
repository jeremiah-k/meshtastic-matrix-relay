# Remove `credentials_path` and `store_path` Configurability

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the ability for users to configure custom `credentials_path` and `store_path` locations via config keys or the `MMRELAY_CREDENTIALS_PATH` env var. These paths will always be derived from `MMRELAY_HOME`, which already provides full control over data location.

**Architecture:** Currently the code checks a 3-level resolution chain for `credentials_path` (env var → top-level config → `matrix.credentials_path`) and a 2-level chain for `store_path` (`matrix.encryption.store_path` → `matrix.e2ee.store_path`). All of these will be removed. The default paths (`<MMRELAY_HOME>/matrix/credentials.json` and `<MMRELAY_HOME>/matrix/store/`) already handle every deployment scenario via `MMRELAY_HOME`. The `InvalidCredentialsPathTypeError` exception class will be removed. The `get_explicit_credentials_path()` function will be removed. All callers will be simplified to use only the default path functions.

**Tech Stack:** Python 3.10+, pytest, asyncio

## Versioning and Migration

This is a breaking change and must be called out in the release notes and changelog. Users should remove any `credentials_path` or `store_path` config entries and rely on `MMRELAY_HOME` for path control instead.

External integrations that read `MatrixAuthInfo.credentials_path` or pass `credentials_path` to `save_credentials()` will break and need to be updated before the release ships.

Add migration guidance that users must remove legacy path overrides before upgrading to the release that includes this breaking change, and include a short note in the changelog/release announcement so the upgrade path is explicit.

---

## Background

### Why These Are Unnecessary

Every deployment method already controls data paths via `MMRELAY_HOME`:

| Deployment     | How it works                                                                                                    |
| -------------- | --------------------------------------------------------------------------------------------------------------- |
| Local pip/pipx | Default `~/.mmrelay` → credentials at `~/.mmrelay/matrix/credentials.json`, store at `~/.mmrelay/matrix/store/` |
| Docker         | `MMRELAY_HOME=/data` env var → credentials at `/data/matrix/credentials.json`, store at `/data/matrix/store/`   |
| Kubernetes     | Same — PVC mount + `MMRELAY_HOME` env var                                                                       |
| Helm           | `matrixAuth` handles credentials; `MMRELAY_HOME` handles all paths                                              |

No Helm chart, K8s manifest, or Docker compose file references `credentials_path` or `store_path`. The "Tip (Kubernetes): set credentials_path to a writable PVC location" in the sample config was misleading — `MMRELAY_HOME` already solves this.

### Deprecation Warnings

Add warnings when the following legacy config keys or environment variables are present but ignored:

- top-level `credentials_path`
- `matrix.credentials_path`
- `matrix.e2ee.store_path`
- `matrix.encryption.store_path`
- `MMRELAY_CREDENTIALS_PATH`

### Why `credentials_path` Shouldn't Be Top-Level

Credentials are a Matrix concept. The code supports checking both `config["credentials_path"]` (top-level) and `config["matrix"]["credentials_path"]` (nested), which is confusing. Since we're removing configurability entirely, this becomes moot — but it's worth noting as part of the rationale.

### The INNO_SETUP_GUIDE Exception

`docs/dev/INNO_SETUP_GUIDE.md:382` shows the Windows installer generating `store_path` in config. This is a dev/internal doc with an example, not actual installer code. It should be updated to remove the `store_path` line, but the installer script itself (`scripts/mmrelay.iss`) should be checked too.

---

## Files Affected

### Source files to modify

- `src/mmrelay/config.py` — Remove `get_explicit_credentials_path()`, `InvalidCredentialsPathTypeError`, `_resolve_credentials_path()`, simplify `save_credentials()`, `load_credentials()`, `get_credentials_search_paths()`
- `src/mmrelay/paths.py` — No changes needed (default path functions are correct)
- `src/mmrelay/matrix_utils.py` — Remove re-exports of deleted functions, update `MatrixAuthInfo` dataclass
- `src/mmrelay/matrix/credentials.py` — Simplify `_resolve_credentials_save_path()`, remove `credentials_path` from auth resolution
- `src/mmrelay/matrix/sync_bootstrap.py` — Simplify login flow, remove `credentials_path` tracking
- `src/mmrelay/matrix/auth.py` — Remove `credentials_path` from `restore_e2ee_session()`, remove `store_path` config reading from `_configure_e2ee()`
- `src/mmrelay/cli.py` — Remove `_validate_credentials_json()` references to explicit path, remove `store_path` reading from `_validate_e2ee_config()`
- `src/mmrelay/cli_utils.py` — Remove `store_path` override handling in `_cleanup_local_session_data()`
- `src/mmrelay/constants/messages.py` — Remove or keep `LEGACY_CREDENTIALS_WARNING_MSG` (still used for legacy _location_ warnings, not path config)
- `src/mmrelay/tools/sample_config.yaml` — Remove `credentials_path` references in comments (lines 18-19, 29)

### Test files to modify

- `tests/test_config_edge_cases.py` — Remove/update ~15 tests for `get_explicit_credentials_path`, `InvalidCredentialsPathTypeError`, `_resolve_credentials_path`, `MMRELAY_CREDENTIALS_PATH` env var
- `tests/test_config.py` — Remove/update ~8 tests for explicit path saving, `MMRELAY_CREDENTIALS_PATH` env var
- `tests/test_matrix_utils_auth_credentials.py` — Remove/update tests for explicit credentials path
- `tests/test_matrix_utils_credentials_resolve.py` — Update tests that pass config with `credentials_path`
- `tests/test_matrix_utils_connect_credentials.py` — Remove `test_connect_matrix_explicit_credentials_path_is_used`, update `InvalidCredentialsPathTypeError` test
- `tests/test_matrix_auth_discovery.py` — Update tests that save to `credentials_path`
- `tests/test_matrix_utils_sync_bootstrap_login.py` — Update tests patching `save_credentials` with `credentials_path`
- `tests/test_matrix_utils_auth_e2ee.py` — Remove/update `test_connect_matrix_e2ee_store_path_from_config`, `test_connect_matrix_e2ee_store_path_precedence_encryption`, `test_connect_matrix_e2ee_store_path_uses_e2ee_section`, keep `test_connect_matrix_e2ee_store_path_default`
- `tests/test_matrix_utils_connect_e2ee.py` — Update E2EE connect tests
- `tests/test_cli.py` — Remove/update `_validate_e2ee_config` tests with `store_path`
- `tests/test_cli_utils.py` — Remove/update `test_cleanup_config_override_store_path`
- `tests/test_e2ee_utils.py` — Update fixtures that include `credentials_path`
- `tests/test_matrix_utils_auth_login.py` — Update login tests
- `tests/test_matrix_utils_auth_logout.py` — Update logout tests
- `tests/test_cli_paths.py` — Verify still correct
- `tests/test_paths.py` — Verify still correct
- `tests/test_paths_coverage.py` — Verify still correct
- `tests/test_auth_flow_fixes.py` — Update `test_windows_credentials_path_handling`
- `tests/test_async_patterns.py` — Update async credential loading tests
- `tests/test_migrate.py` — Verify migration tests still pass (migration handles _legacy file locations_, not config overrides)
- `tests/test_migrate_rollback_auto.py` — Verify rollback tests
- `tests/test_cli_targeted.py` — Update if needed

### Documentation files to modify

- `docs/ADVANCED_CONFIGURATION.md` — Remove `MMRELAY_CREDENTIALS_PATH` from env var table
- `docs/E2EE.md` — Remove `store_path` reference at line 184
- `docs/dev/INNO_SETUP_GUIDE.md` — Remove `store_path` from example at line 382

---

## Task 1: Remove `credentials_path` from `config.py`

**Files:**

- Modify: `src/mmrelay/config.py`

This is the core change. All credentials path resolution collapses to: use `get_credentials_path()` from `paths.py` (which returns `<MMRELAY_HOME>/matrix/credentials.json`).

- [ ] **Step 1: Remove `InvalidCredentialsPathTypeError`**

Remove the class at lines 278-287:

```python
class InvalidCredentialsPathTypeError(TypeError):
    """Raised when credentials_path is not a string."""
    def __init__(self) -> None:
        super().__init__("credentials_path must be a string")
```

- [ ] **Step 2: Remove `get_explicit_credentials_path()`**

Remove the entire function at lines 290-323. This is the function that checks `MMRELAY_CREDENTIALS_PATH` env var → `config["credentials_path"]` → `config["matrix"]["credentials_path"]`.

- [ ] **Step 3: Simplify `get_credentials_search_paths()`**

At lines 182-250, the function accepts `explicit_path` parameter. Remove this parameter. The function should only build the candidate list from the default path (`get_credentials_path()`), config-adjacent dirs, `~/.mmrelay/credentials.json`, and legacy dirs. Remove any logic that handles explicit path overrides.

Update the signature to:

```python
def get_credentials_search_paths(
    *, config_paths: Iterable[str] | None = None,
    include_base_data: bool = True,
) -> list[str]:
```

Inside the function, remove the block that handles `explicit_path` (it's used as the first candidate). The first candidate should now always be the unified default path.

- [ ] **Step 4: Simplify `get_candidate_credentials_paths()`**

At lines 254-275, this is a backward-compatible alias. Remove the `explicit_path` parameter since the underlying function no longer accepts it. Update to:

```python
def get_candidate_credentials_paths(
    *, config_paths: Iterable[str] | None = None,
    include_base_data: bool = True,
) -> list[str]:
    return get_credentials_search_paths(
        config_paths=config_paths,
        include_base_data=include_base_data,
    )
```

- [ ] **Step 5: Simplify `load_credentials()`**

At lines 775-894, remove the `get_explicit_credentials_path()` call (line ~794). The function should use `get_credentials_search_paths()` without an explicit path override. The `config_override` parameter can remain for loading config from a specific source, but the credentials path is always the default.

Remove:

```python
explicit_path = get_explicit_credentials_path(config_for_paths)
```

And the call to `get_credentials_search_paths` becomes:

```python
candidates = get_credentials_search_paths(
    config_paths=config_path_candidates,
    include_base_data=True,
)
```

- [ ] **Step 6: Simplify `save_credentials()`**

At lines 917-988, remove the `credentials_path` parameter and all resolution logic that checks env vars/config. The function should always save to `get_credentials_path()` (from `paths.py`).

New signature:

```python
def save_credentials(credentials: dict[str, Any]) -> None:
```

The target path is always:

```python
target_path = get_credentials_path()
```

Keep the directory creation, JSON writing, and permission-setting logic. Remove the `_expand_path` and `path_module.normpath` calls since `get_credentials_path()` already returns a clean `Path`.

- [ ] **Step 7: Remove `_resolve_credentials_path()`**

Remove the entire function at lines 1362-1417. This was used by CLI tools to resolve the credentials path with config/env overrides. Callers should use `get_credentials_path()` directly.

- [ ] **Step 8: Run targeted tests to verify nothing imports the removed symbols**

Run: `python -c "from mmrelay.config import InvalidCredentialsPathTypeError"`
Expected: ImportError (confirming removal)

Run: `python -c "from mmrelay.config import get_explicit_credentials_path"`
Expected: ImportError (confirming removal)

Run: `python -c "from mmrelay.config import save_credentials, load_credentials, get_candidate_credentials_paths"`
Expected: No error (these functions still exist)

- [ ] **Step 9: Commit**

```bash
git add src/mmrelay/config.py
git commit -m "refactor: remove credentials_path configurability from config.py

Remove get_explicit_credentials_path(), InvalidCredentialsPathTypeError,
_resolve_credentials_path(), and simplify save_credentials() and
load_credentials() to always use the default path derived from MMRELAY_HOME."
```

---

## Task 2: Remove `store_path` config reading from `matrix/auth.py`

**Files:**

- Modify: `src/mmrelay/matrix/auth.py`

The `_configure_e2ee()` function (lines 19-158) reads `store_path` from `matrix.encryption.store_path` and `matrix.e2ee.store_path`. After this change, it will always use the default `get_e2ee_store_dir()`.

- [ ] **Step 1: Simplify `_configure_e2ee()` store path resolution**

In `_configure_e2ee()`, remove the config override logic at lines ~88-103:

```python
# REMOVE this block:
store_override = None
if isinstance(matrix_section, dict):
    encryption_section = matrix_section.get("encryption")
    e2ee_section = matrix_section.get("e2ee")
    if isinstance(encryption_section, dict):
        store_override = encryption_section.get("store_path")
    if not store_override and isinstance(e2ee_section, dict):
        store_override = e2ee_section.get("store_path")

if isinstance(store_override, str) and store_override:
    e2ee_store_path = os.path.expanduser(store_override)
else:
    e2ee_store_path = str(
        await asyncio.to_thread(facade.get_e2ee_store_dir)
    )
```

Replace with:

```python
e2ee_store_path = str(
    await asyncio.to_thread(facade.get_e2ee_store_dir)
)
```

This always uses the default path `<MMRELAY_HOME>/matrix/store`.

- [ ] **Step 2: Commit**

```bash
git add src/mmrelay/matrix/auth.py
git commit -m "refactor: remove store_path config override from _configure_e2ee()"
```

---

## Task 3: Update `matrix_utils.py` facade

**Files:**

- Modify: `src/mmrelay/matrix_utils.py`

- [ ] **Step 1: Remove imports of deleted functions**

At line ~61, remove `get_explicit_credentials_path` from the import:

```python
from mmrelay.config import (
    async_load_credentials,
    get_explicit_credentials_path,  # REMOVE THIS
    save_credentials,
)
```

At line ~116, remove `get_credentials_path` import if it was only used for re-export of the explicit path function (check first — it may still be used elsewhere in the facade).

- [ ] **Step 2: Update `MatrixAuthInfo` dataclass**

At lines ~304-311, the `credentials_path` field tracks where credentials were loaded from. After removing configurability, this is always `str(get_credentials_path())`. Remove the field from the dataclass:

```python
@dataclass
class MatrixAuthInfo:
    homeserver: str
    access_token: str
    user_id: str
    device_id: Optional[str]
    credentials: dict[str, Any] | None
    # credentials_path removed — always default path
```

- [ ] **Step 3: Search for all references to `credentials_path` in this file and update them**

Any code that reads `auth_info.credentials_path` or passes `credentials_path` to `save_credentials` needs updating. The `save_credentials` call sites should now use `save_credentials(credentials)` without a path argument.

- [ ] **Step 4: Commit**

```bash
git add src/mmrelay/matrix_utils.py
git commit -m "refactor: remove credentials_path from facade and MatrixAuthInfo"
```

---

## Task 4: Update `matrix/credentials.py`

**Files:**

- Modify: `src/mmrelay/matrix/credentials.py`

- [ ] **Step 1: Simplify `_resolve_credentials_save_path()`**

At lines 30-52, this function checks `get_explicit_credentials_path` then falls back to `get_credentials_path()`. Remove the explicit path check:

```python
def _resolve_credentials_save_path(config_data: dict[str, Any] | None) -> str | None:
    return str(facade.get_credentials_path())
```

- [ ] **Step 2: Update `_resolve_and_load_credentials()`**

This function (lines 91-364) populates `credentials_path` in the returned `MatrixAuthInfo`. Since `MatrixAuthInfo` no longer has that field, remove all assignments to `credentials_path` in the return statements. The function should no longer track where credentials came from — it just loads them from the default location.

- [ ] **Step 3: Commit**

```bash
git add src/mmrelay/matrix/credentials.py
git commit -m "refactor: simplify credentials path resolution in credentials.py"
```

---

## Task 5: Update `matrix/sync_bootstrap.py`

**Files:**

- Modify: `src/mmrelay/matrix/sync_bootstrap.py`

- [ ] **Step 1: Simplify login flow**

At lines ~718-749, remove the `credentials_path` tracking variable and the `_resolve_credentials_save_path` call. Replace with direct use of `facade.get_credentials_path()`.

- [ ] **Step 2: Simplify save after login**

At lines ~1017-1027, remove the `credentials_path` variable and pass `credentials` only to `save_credentials()`:

```python
await asyncio.to_thread(facade.save_credentials, credentials)
facade.logger.info("Credentials saved to %s", facade.get_credentials_path())
```

- [ ] **Step 3: Commit**

```bash
git add src/mmrelay/matrix/sync_bootstrap.py
git commit -m "refactor: remove credentials_path tracking from sync_bootstrap"
```

---

## Task 6: Update `matrix/auth.py` session restore

**Files:**

- Modify: `src/mmrelay/matrix/auth.py`

- [ ] **Step 1: Update `restore_e2ee_session()`**

At lines ~277, the function saves credentials back to `auth_info.credentials_path`:

```python
await asyncio.to_thread(
    facade.save_credentials,
    auth_info.credentials,
    credentials_path=auth_info.credentials_path,
)
```

Since `MatrixAuthInfo` no longer has `credentials_path` and `save_credentials` no longer takes a path arg, change to:

```python
await asyncio.to_thread(
    facade.save_credentials,
    auth_info.credentials,
)
```

- [ ] **Step 2: Commit**

```bash
git add src/mmrelay/matrix/auth.py
git commit -m "refactor: simplify credentials save in restore_e2ee_session"
```

---

## Task 7: Update `cli.py`

**Files:**

- Modify: `src/mmrelay/cli.py`

- [ ] **Step 1: Update `_validate_credentials_json()`**

At lines ~555-614, remove the import and call to `get_explicit_credentials_path` and `InvalidCredentialsPathTypeError`. Simplify to use `get_credentials_search_paths()` without explicit path.

Remove:

```python
from mmrelay.config import (
    InvalidCredentialsPathTypeError,  # REMOVE
    get_explicit_credentials_path,     # REMOVE
)
```

Remove the explicit path resolution:

```python
# REMOVE:
try:
    explicit_path = get_explicit_credentials_path(config or relay_config)
except InvalidCredentialsPathTypeError:
    _get_logger().error("Invalid credentials_path: %s", exc)
    return False
```

Update the candidates call:

```python
candidates = get_credentials_search_paths(
    config_paths=[config_path] if config_path else None,
)
```

- [ ] **Step 2: Update `_validate_e2ee_config()`**

At lines ~775-781, remove the `store_path` reading from config:

```python
# REMOVE:
store_path = e2ee_config.get("store_path") or encryption_config.get("store_path")
if store_path:
    expanded_path = os.path.expanduser(store_path)
    if not os.path.exists(expanded_path):
        print(f"Info: E2EE store directory will be created: {expanded_path}")
```

- [ ] **Step 3: Update `_find_credentials_json_path()`**

At lines ~878-897, same pattern as `_validate_credentials_json` — remove `get_explicit_credentials_path` usage.

- [ ] **Step 4: Search for any remaining references**

Search `cli.py` for `credentials_path`, `get_explicit_credentials_path`, `InvalidCredentialsPathTypeError`, `MMRELAY_CREDENTIALS_PATH`, `store_path`. Update or remove all remaining references.

- [ ] **Step 5: Commit**

```bash
git add src/mmrelay/cli.py
git commit -m "refactor: remove credentials_path and store_path config handling from CLI"
```

---

## Task 8: Update `cli_utils.py`

**Files:**

- Modify: `src/mmrelay/cli_utils.py`

- [ ] **Step 1: Simplify `_cleanup_local_session_data()`**

At lines ~423-468, remove the code that reads `store_path` from config and adds it to the cleanup set:

```python
# REMOVE this block:
for section in ("e2ee", "encryption"):
    section_cfg = matrix_cfg.get(section, {})
    override = os.path.expanduser(section_cfg.get("store_path", ""))
    if override:
        candidate_store_paths.add(override)
```

The function should only clean up the default store dir.

- [ ] **Step 2: Commit**

```bash
git add src/mmrelay/cli_utils.py
git commit -m "refactor: remove store_path override cleanup from cli_utils"
```

---

## Task 9: Update `sample_config.yaml`

**Files:**

- Modify: `src/mmrelay/tools/sample_config.yaml`

- [ ] **Step 1: Remove credentials_path references**

Remove line 18-19 (the K8s tip):

```yaml
# REMOVE:
# Tip (Kubernetes): set credentials_path to a writable PVC location
# credentials_path: /data/matrix/credentials.json
```

Remove line 29 (legacy method example):

```yaml
# REMOVE:
#credentials_path: /data/matrix/credentials.json
```

- [ ] **Step 2: Commit**

```bash
git add src/mmrelay/tools/sample_config.yaml
git commit -m "docs: remove credentials_path from sample config"
```

---

## Task 10: Add deprecation warnings for ignored legacy overrides

**Files:**

- Modify: `src/mmrelay/config.py`
- Modify: `src/mmrelay/cli.py`
- Modify: `tests/test_config_edge_cases.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add centralized warning logic in `config.py`**

Add a helper in `src/mmrelay/config.py` that checks parsed config data plus the
`MMRELAY_CREDENTIALS_PATH` environment variable and logs warnings when ignored
legacy path overrides are present:

- top-level `credentials_path`
- `matrix.credentials_path`
- `matrix.e2ee.store_path`
- `matrix.encryption.store_path`
- `MMRELAY_CREDENTIALS_PATH`

Call this helper from the main config-loading path so warnings are emitted once
per load, not from every downstream auth or E2EE code path.

- [ ] **Step 2: Keep warning scope narrow and migration-oriented**

Warnings should explain that these keys/env vars are ignored, that
`MMRELAY_HOME` is now the only supported path control, and that users should
remove the legacy settings from their config or deployment manifests.

- [ ] **Step 3: Update tests**

Add or update tests in `tests/test_config_edge_cases.py` and `tests/test_cli.py`
to verify:

- warnings are emitted for each ignored legacy key/env var
- warnings are not duplicated excessively during a single config load
- config loading still succeeds after warning

- [ ] **Step 4: Commit**

```bash
git add src/mmrelay/config.py src/mmrelay/cli.py tests/test_config_edge_cases.py tests/test_cli.py
git commit -m "feat: warn on ignored credentials_path and store_path overrides"
```

---

## Task 11: Update documentation

**Files:**

- Modify: `docs/ADVANCED_CONFIGURATION.md`
- Modify: `docs/E2EE.md`
- Modify: `docs/dev/INNO_SETUP_GUIDE.md`

- [ ] **Step 1: Remove `MMRELAY_CREDENTIALS_PATH` from ADVANCED_CONFIGURATION.md**

Find and remove the row for `MMRELAY_CREDENTIALS_PATH` in the environment variable mapping table (around line 331). Also remove any narrative text that describes configuring credentials_path.

- [ ] **Step 2: Remove `store_path` from E2EE.md**

At line ~184, remove:

```yaml
# store_path: ~/.mmrelay/matrix/store
```

And any surrounding text that presents it as a configurable option.

- [ ] **Step 3: Update INNO_SETUP_GUIDE.md**

At line 382, remove the `store_path` line from the Pascal example:

```pascal
// REMOVE this line:
'    store_path: ''' + InstallDir + '\e2ee_store''' + #13#10;
```

The E2EE config should just be:

```pascal
config := config + 'matrix:' + #13#10 +
          '  e2ee:' + #13#10 +
          '    enabled: true' + #13#10;
```

- [ ] **Step 4: Commit**

```bash
git add docs/ADVANCED_CONFIGURATION.md docs/E2EE.md docs/dev/INNO_SETUP_GUIDE.md
git commit -m "docs: remove credentials_path and store_path configurability documentation"
```

---

## Task 12: Update test files (batch 1 — config tests)

**Files:**

- Modify: `tests/test_config_edge_cases.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_matrix_utils_auth_credentials.py`

These tests heavily reference `get_explicit_credentials_path`, `InvalidCredentialsPathTypeError`, `MMRELAY_CREDENTIALS_PATH` env var, and `_resolve_credentials_path`.

- [ ] **Step 1: Update `tests/test_config_edge_cases.py`**

Remove or rewrite these tests:

- `test_get_explicit_credentials_path_no_config` — DELETE (function removed)
- `test_get_explicit_credentials_path_non_string` — DELETE (exception removed)
- `test_get_explicit_credentials_path_matrix_section_non_string` — DELETE
- `test_get_explicit_credentials_path_matrix_section_returns_path` — DELETE
- `test_get_explicit_credentials_path_matrix_section_empty_returns_none` — DELETE
- `test_resolve_credentials_path_relay_config` — DELETE (function removed)
- `test_resolve_credentials_path_matrix_section` — DELETE
- `test_resolve_credentials_path_default` — REWRITE to test `get_credentials_path()` from paths.py
- `test_resolve_credentials_path_directory_appends_filename` — DELETE
- `test_resolve_credentials_path_empty_dirname` — DELETE
- `test_resolve_credentials_path_env_var` — DELETE (MMRELAY_CREDENTIALS_PATH removed)
- `test_save_credentials_writes_to_file` — REWRITE to not use MMRELAY_CREDENTIALS_PATH env var
- `test_load_credentials_path_error_returns_none` — REWRITE (no more explicit path)
- `test_credentials_path_error_message` — DELETE (exception removed)
- `test_get_e2ee_store_dir_*` — KEEP as-is (these test the default path, not config overrides)

- [ ] **Step 2: Update `tests/test_config.py`**

- `test_save_credentials_with_explicit_path` — DELETE (no more explicit path parameter)
- `test_save_credentials_trailing_separator_treated_as_dir` — DELETE
- `test_save_credentials_directory_as_path` — DELETE
- `test_save_credentials_actual_directory_path` — DELETE
- `test_save_credentials_altsep_path_detection` — DELETE
- `test_save_credentials_empty_config_dir_uses_base_dir` — REWRITE (simpler path logic)
- `test_save_credentials_verification` — REWRITE (no explicit path)
- `test_save_credentials_windows_error_guidance` — KEEP or REWRITE (still handles Windows errors)
- `test_load_credentials_*` — KEEP (tests loading from default locations)

- [ ] **Step 3: Update `tests/test_matrix_utils_auth_credentials.py`**

- `test_load_credentials_success` — REWRITE if it uses explicit path
- `test_load_credentials_file_not_exists` — KEEP
- `test_save_credentials` — REWRITE to not pass explicit path or use MMRELAY_CREDENTIALS_PATH env var

- [ ] **Step 4: Commit**

```bash
git add tests/test_config_edge_cases.py tests/test_config.py tests/test_matrix_utils_auth_credentials.py
git commit -m "test: update config tests for credentials_path removal"
```

---

## Task 13: Update test files (batch 2 — matrix and CLI tests)

**Files:**

- Modify: `tests/test_matrix_utils_credentials_resolve.py`
- Modify: `tests/test_matrix_utils_connect_credentials.py`
- Modify: `tests/test_matrix_auth_discovery.py`
- Modify: `tests/test_matrix_utils_sync_bootstrap_login.py`
- Modify: `tests/test_matrix_utils_auth_e2ee.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_cli_utils.py`
- Modify: `tests/test_e2ee_utils.py`
- Modify: `tests/test_matrix_utils_auth_login.py`
- Modify: `tests/test_matrix_utils_auth_logout.py`
- Modify: `tests/test_auth_flow_fixes.py`
- Modify: `tests/test_async_patterns.py`

- [ ] **Step 1: Update `tests/test_matrix_utils_credentials_resolve.py`**

All 14 tests in this file test `_resolve_and_load_credentials`. Remove any that test `credentials_path` as a parameter or return value. Update return value assertions to not check `credentials_path` field (it no longer exists on `MatrixAuthInfo`).

- [ ] **Step 2: Update `tests/test_matrix_utils_connect_credentials.py`**

- `test_connect_matrix_explicit_credentials_path_is_used` — DELETE
- `test_connect_matrix_invalid_credentials_path_type_error_falls_back_to_config_auth` — DELETE (exception removed)
- Update remaining tests that reference `credentials_path` in config dicts

- [ ] **Step 3: Update `tests/test_matrix_auth_discovery.py`**

All tests that call `save_credentials` with `credentials_path=` need updating to call `save_credentials(credentials)` only.

- [ ] **Step 4: Update `tests/test_matrix_utils_sync_bootstrap_login.py`**

- `test_login_matrix_bot_no_credentials_path` — REWRITE (no more credentials_path resolution)
- Update all tests that patch `save_credentials` with `credentials_path` kwarg

- [ ] **Step 5: Update `tests/test_matrix_utils_auth_e2ee.py`**

- `test_connect_matrix_e2ee_store_path_from_config` — DELETE
- `test_connect_matrix_e2ee_store_path_precedence_encryption` — DELETE
- `test_connect_matrix_e2ee_store_path_uses_e2ee_section` — DELETE
- `test_connect_matrix_e2ee_store_path_default` — KEEP (tests default behavior)
- `test_connect_matrix_e2ee_store_missing_db_files_warns` — REWRITE if it sets store_path in config
- `test_connect_matrix_e2ee_missing_sqlite_store` — REWRITE if needed
- `test_login_matrix_bot_e2ee_store_path_created` — KEEP or REWRITE (tests dir creation, which still happens)

- [ ] **Step 6: Update `tests/test_cli.py`**

- `test_validate_e2ee_config_e2ee_enabled_with_store_path` — DELETE
- `test_validate_e2ee_config_legacy_store_path` — DELETE
- Update any tests that set `credentials_path` in mock config dicts

- [ ] **Step 7: Update `tests/test_cli_utils.py`**

- `test_cleanup_config_override_store_path` — DELETE

- [ ] **Step 8: Update remaining test files**

For `test_e2ee_utils.py`, `test_matrix_utils_auth_login.py`, `test_matrix_utils_auth_logout.py`, `test_auth_flow_fixes.py`, `test_async_patterns.py`:

- Remove `credentials_path` from config dicts in test fixtures
- Remove `credentials_path` from `MatrixAuthInfo` constructor calls
- Remove `credentials_path=` keyword arguments to `save_credentials` calls

- [ ] **Step 9: Commit**

```bash
git add tests/
git commit -m "test: update matrix and CLI tests for credentials_path/store_path removal"
```

---

## Task 14: Verify and run full test suite

- [ ] **Step 1: Run full test suite with coverage**

Run: `python -m pytest -v --cov --tb=short --timeout=60`
Expected: All tests pass

- [ ] **Step 2: Run lint checks**

Run: `.trunk/trunk check --fix --all`
Expected: No errors

- [ ] **Step 3: Verify no references to removed symbols remain**

Run: `grep -r "get_explicit_credentials_path\|InvalidCredentialsPathTypeError\|_resolve_credentials_path" src/ tests/ --include="*.py"`
Expected: No matches

Run: `grep -r "MMRELAY_CREDENTIALS_PATH" src/ tests/ --include="*.py"`
Expected: Matches only the centralized deprecation-warning path and related tests

Run: `grep -r -nE 'get\("store_path"\)|section_cfg\.get\("store_path"\)|store_override' src/mmrelay/ --include="*.py"`
Expected: No matches

This final grep only checks for config-based `store_path` override reads. Other
runtime `store_path` identifiers may still remain where they refer to the
default E2EE store path or AsyncClient kwargs.

- [ ] **Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: cleanup after credentials_path/store_path removal"
```

---

## Summary of Removed Functionality

| Removed                                          | Replacement                                     |
| ------------------------------------------------ | ----------------------------------------------- |
| `MMRELAY_CREDENTIALS_PATH` env var               | `MMRELAY_HOME` controls all paths               |
| `config["credentials_path"]` (top-level)         | Always `<MMRELAY_HOME>/matrix/credentials.json` |
| `config["matrix"]["credentials_path"]`           | Always `<MMRELAY_HOME>/matrix/credentials.json` |
| `config["matrix"]["e2ee"]["store_path"]`         | Always `<MMRELAY_HOME>/matrix/store/`           |
| `config["matrix"]["encryption"]["store_path"]`   | Always `<MMRELAY_HOME>/matrix/store/`           |
| `get_explicit_credentials_path()`                | `get_credentials_path()` from `paths.py`        |
| `InvalidCredentialsPathTypeError`                | No longer needed                                |
| `_resolve_credentials_path()`                    | `get_credentials_path()` from `paths.py`        |
| `credentials_path` param on `save_credentials()` | Always saves to default                         |
| `credentials_path` field on `MatrixAuthInfo`     | Always default path                             |

## What Remains Unchanged

- `get_credentials_path()` in `paths.py` — returns `<MMRELAY_HOME>/matrix/credentials.json`
- `get_e2ee_store_dir()` in `paths.py` — returns `<MMRELAY_HOME>/matrix/store/`
- `MMRELAY_HOME` env var — still controls the base directory for all data
- `--home` CLI flag — still sets MMRELAY_HOME
- Legacy location detection and migration — still works (detects files in old dirs, not config overrides)
- All other config keys and env vars — unchanged
