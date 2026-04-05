# Matrix Utils Decomposition Plan

## Document Status

- **Phase**: 0 — Documentation only
- **Date**: 2026-04-05
- **Target file**: `src/mmrelay/matrix_utils.py` (5073 lines)
- **Proposed output**: Package at `src/mmrelay/matrix/`

---

## 1. Goals

1. Decompose the 5073-line `matrix_utils.py` monolith into focused, cohesive modules.
2. Maintain 100% backward compatibility for existing imports from `mmrelay.matrix_utils`.
3. Keep test monkeypatch targets stable while reducing local complexity.
4. Separate Matrix bootstrap/auth, relay, reply, event, and media logic into independently testable units.
5. Prepare for a later state-owner migration, but do **not** do it in the decomposition PRs.

---

## 2. Non-Goals

- No public import breakage during any phase.
- No state-owner migration in the same refactor.
- No broad renaming during moves.
- No new central state object.
- No test patch-target changes during decomposition.
- No large behavioral rewrites of login, sync, encryption, or event handling while moving code.

---

## 3. Current File Analysis

### 3.1 Import Inventory

**Stdlib** (lines 1-25):
`asyncio`, `getpass`, `html`, `importlib`, `inspect`, `io`, `json`, `logging`, `os`, `re`, `secrets`, `ssl`, `sys`, `time`, `dataclasses.dataclass`, `json.JSONDecodeError`, `types.SimpleNamespace`, `typing.{TYPE_CHECKING, Any, Awaitable, Callable, Dict, Generator, Optional, Tuple, cast}`, `urllib.parse.urlparse`

**Third-party** (lines 35-66):
`nio.{AsyncClient, AsyncClientConfig, DiscoveryInfoError, DiscoveryInfoResponse, MatrixRoom, MegolmEvent, ProfileGetDisplayNameError, ProfileGetDisplayNameResponse, ReactionEvent, RoomMessageEmote, RoomMessageNotice, RoomMessageText, SyncError, ToDeviceError, ToDeviceResponse, UploadError, UploadResponse}`

`nio.events.room_events.RoomMemberEvent`

Conditional typing / invite import:

- `InviteMemberEvent` from `nio` or `nio.events.invite_events` (lines 59-64)

Image handling:

- `PIL.Image` (line 66)

**Internal** (lines 68-157):

- `mmrelay.config as config_module`
- `mmrelay.cli_utils.{_create_ssl_context, msg_require_auth_login, msg_retry_auth_login}`
- `mmrelay.config.{InvalidCredentialsPathTypeError, async_load_credentials, get_e2ee_store_dir, get_explicit_credentials_path, get_meshtastic_config_value, save_credentials}`
- `mmrelay.constants.app.WINDOWS_PLATFORM`
- `mmrelay.constants.config.*` (Matrix and E2EE credential/config names)
- `mmrelay.constants.database.DEFAULT_MSGS_TO_KEEP`
- `mmrelay.constants.domain.MATRIX_EVENT_TYPE_ROOM_MESSAGE`
- `mmrelay.constants.formats.*`
- `mmrelay.constants.messages.*`
- `mmrelay.constants.network.*`
- `mmrelay.db_utils.{async_prune_message_map, async_store_message_map, get_message_map_by_matrix_event_id}`
- `mmrelay.log_utils.get_logger`
- `mmrelay.meshtastic_utils.{connect_meshtastic, send_text_reply}`
- `mmrelay.message_queue.{get_message_queue, queue_message}`
- `mmrelay.paths.{E2EENotSupportedError, get_credentials_path}`

**Conditional imports / guarded dependencies**:

- `nio.exceptions` and `nio.responses` via `importlib.import_module(...)` to populate `Nio*` exception aliases (lines 160-183)
- `jsonschema` optional import with fallback validation error type (lines 192-211)

**Lazy imports inside functions**:

- `from mmrelay.meshtastic_utils import logger as meshtastic_logger` (lines 1346, 1394, 3950, 4297)
- `from mmrelay.config import is_e2ee_enabled` (line 1753)
- `from mmrelay.e2ee_utils import ...` (lines 2354, 3296)
- `from mmrelay.config import is_e2ee_enabled, load_config` (line 2758)
- `from nio.api import Api` (line 2912)
- `from mmrelay.plugin_loader import load_plugins` (line 4716)

These lazy imports are likely load-bearing for circular-dependency control and should be preserved during moves unless the dependency graph becomes simpler.

### 3.2 Exception / Data Class Inventory

| Name                           |     Lines | Type             |
| ------------------------------ | --------: | ---------------- |
| `MissingMatrixRoomsError`      |   233-240 | custom exception |
| `MatrixSyncTimeoutError`       |   243-252 | custom exception |
| `MatrixSyncFailedError`        |   255-264 | custom exception |
| `MatrixSyncFailedDetailsError` |   267-283 | custom exception |
| `MatrixAuthInfo`               | 1440-1446 | dataclass        |
| `ImageUploadError`             | 4876-4894 | custom exception |

### 3.3 Module-Level Globals Inventory

| Variable                             |      Line | Type / Role                                          |
| ------------------------------------ | --------: | ---------------------------------------------------- |
| `NIO_COMM_EXCEPTIONS`                |       185 | tuple of nio/network exception types                 |
| `jsonschema`                         |       193 | optional module handle                               |
| `JSONSCHEMA_VALIDATION_ERROR`        | 204 / 211 | explicit validation exception alias                  |
| `SYNC_RETRY_EXCEPTIONS`              |       213 | tuple for sync retry handling                        |
| `WHOAMI_USER_ID_FALLBACK_EXCEPTIONS` |       219 | login/user-id fallback tuple                         |
| `logger`                             |       230 | module logger                                        |
| `_MIME_TYPE_MAP`                     |       286 | image format → MIME type map                         |
| `config`                             |      1191 | facade-owned config dict                             |
| `matrix_homeserver`                  |      1194 | homeserver URL                                       |
| `matrix_rooms`                       |      1195 | room mapping config                                  |
| `matrix_access_token`                |      1196 | Matrix access token                                  |
| `bot_user_id`                        |      1197 | bot MXID                                             |
| `bot_user_name`                      |      1198 | display name / username cache                        |
| `bot_start_time`                     |      1199 | wall-clock startup timestamp (ms)                    |
| `bot_start_monotonic_secs`           |      1202 | monotonic startup timestamp                          |
| `matrix_client`                      |      1242 | active `AsyncClient`                                 |
| `_MATRIX_STARTUP_SYNC_LOCK`          |      1245 | async lock for Matrix bootstrap / sync serialization |

### 3.4 Function Inventory by Category

#### Room mapping / alias helpers

| Function                         |   Lines |
| -------------------------------- | ------: |
| `_is_room_alias`                 | 296-304 |
| `_get_valid_device_id`           | 307-320 |
| `_extract_localpart_from_mxid`   | 323-338 |
| `_is_room_mapped`                | 341-358 |
| `_iter_room_alias_entries`       | 361-450 |
| `_resolve_aliases_in_mapping`    | 453-482 |
| `_update_room_id_in_mapping`     | 485-509 |
| `_display_room_channel_mappings` | 512-596 |
| `_create_mapping_info`           | 863-897 |

#### Config / prefix / formatting helpers

| Function                              |     Lines |
| ------------------------------------- | --------: |
| `_first_nonblank_str`                 |   599-606 |
| `_can_auto_create_credentials`        |   609-627 |
| `_normalize_bot_user_id`              |   630-694 |
| `_get_msgs_to_keep_config`            |   697-745 |
| `_get_detailed_matrix_error_message`  |   748-860 |
| `get_interaction_settings`            |   900-975 |
| `message_storage_enabled`             |   978-985 |
| `_add_truncated_vars`                 |  988-1007 |
| `_escape_leading_prefix_for_markdown` | 1010-1027 |
| `validate_prefix_format`              | 1030-1048 |
| `get_meshtastic_prefix`               | 1051-1122 |
| `get_matrix_prefix`                   | 1125-1187 |

#### Bot state / Meshtastic bridge helpers

| Function                                |     Lines |
| --------------------------------------- | --------: |
| `_estimate_clock_rollback_ms`           | 1205-1227 |
| `_refresh_bot_start_timestamps`         | 1230-1239 |
| `get_displayname`                       | 1248-1261 |
| `bot_command`                           | 1264-1318 |
| `_connect_meshtastic`                   | 1321-1328 |
| `_get_meshtastic_interface_and_channel` | 1331-1365 |
| `_handle_detection_sensor_packet`       | 1368-1436 |

#### Credentials / auth / client bootstrap

| Function                             |     Lines |
| ------------------------------------ | --------: |
| `_resolve_credentials_save_path`     | 1449-1471 |
| `_missing_credentials_keys`          | 1474-1489 |
| `_resolve_and_load_credentials`      | 1492-1725 |
| `_configure_e2ee`                    | 1728-1863 |
| `_initialize_matrix_client`          | 1866-1909 |
| `_perform_matrix_login`              | 1912-2039 |
| `_maybe_upload_e2ee_keys`            | 2042-2061 |
| `_close_matrix_client_after_failure` | 2064-2089 |
| `_perform_initial_sync`              | 2092-2306 |
| `_post_sync_setup`                   | 2309-2436 |
| `connect_matrix`                     | 2439-2583 |
| `login_matrix_bot`                   | 2586-3191 |
| `join_matrix_room`                   | 3194-3284 |

#### Relay / reply / media helpers

| Function                          |     Lines |
| --------------------------------- | --------: |
| `_get_e2ee_error_message`         | 3287-3304 |
| `_retry_backoff_delay`            | 3307-3323 |
| `_send_matrix_message_with_retry` | 3326-3427 |
| `matrix_relay`                    | 3430-3749 |
| `truncate_message`                | 3752-3768 |
| `strip_quoted_lines`              | 3771-3783 |
| `get_user_display_name`           | 3786-3837 |
| `format_reply_message`            | 3840-3911 |
| `send_reply_to_meshtastic`        | 3914-4041 |
| `handle_matrix_reply`             | 4044-4158 |
| `upload_image`                    | 4897-4947 |
| `send_room_image`                 | 4950-4985 |
| `send_image`                      | 4988-5000 |

#### Event handlers

| Function                |     Lines |
| ----------------------- | --------: |
| `on_decryption_failure` | 4161-4270 |
| `on_room_message`       | 4274-4873 |
| `on_room_member`        | 5003-5012 |
| `on_invite`             | 5015-5073 |

### 3.5 Cross-Category Dependency Graph

```text
room_mapping ───> prefix/config helpers
room_mapping ───> matrix_client, matrix_rooms, logger

prefix/config ──> config constants
prefix/config ──> no heavy runtime state except config/logger

command_bridge ─> meshtastic_utils.connect_meshtastic
command_bridge ─> meshtastic_utils.send_text_reply
command_bridge ─> matrix_client, bot_user_id, config

credentials/auth ─> config_module + config helpers + paths + ssl + nio
credentials/auth ─> matrix_client, matrix_homeserver, matrix_access_token, bot_user_id
credentials/auth ─> e2ee helpers (lazy imports)

sync_bootstrap ──> credentials/auth
sync_bootstrap ──> room_mapping helpers
sync_bootstrap ──> matrix_client, matrix_rooms, bot_start_time, bot_start_monotonic_secs

relay/reply ────> queue_message/get_message_queue
relay/reply ────> db_utils message-map helpers
relay/reply ────> meshtastic logger (lazy import)
relay/reply ────> matrix_client, config, bot_user_id, bot_user_name

events ─────────> relay/reply helpers
events ─────────> command_bridge helpers
events ─────────> plugin_loader (lazy import)
events ─────────> matrix_client, matrix_rooms, bot_user_id, config, bot_start_time

media ──────────> matrix_client + image upload/send flows
```

### 3.6 High-Risk Functions

1. **`login_matrix_bot`** (2586-3191, ~606 lines)  
   Large orchestration function touching credentials, client login, whoami fallback, sync/bootstrap, room mapping, and config.

2. **`on_room_message`** (4274-4873, ~600 lines)  
   The event hot path. Handles decryption failures, stale-message filtering, replies, commands, plugins, Meshtastic relay, and queue semantics.

3. **`matrix_relay`** (3430-3749, ~320 lines)  
   Heavy message-send path with retries, E2EE behavior, room selection, and message mapping side effects.

4. **`_perform_initial_sync`** (2092-2306, ~215 lines)  
   Complex sync bootstrap / retry behavior with substantial Matrix client interaction.

5. **`_resolve_and_load_credentials`** (1492-1725, ~234 lines)  
   Credential discovery + validation + compatibility behavior.

---

## 4. State Ownership Decision

**Decision: Keep all mutable globals in the `matrix_utils` facade for all decomposition phases.**

### Rationale

1. **`config.py` hardcodes module-name behavior**  
   `config.py` has a `module_name == "matrix_utils"` branch that attaches:
   - `module.config`
   - `module.matrix_rooms`
   - sometimes `module.matrix_homeserver`, `module.matrix_access_token`, `module.bot_user_id`

2. **`main.py` imports directly from `mmrelay.matrix_utils`**  
   `main.py` imports and uses:
   - `connect_matrix`
   - `join_matrix_room`
   - `on_room_message`
   - `on_room_member`
   - `on_invite`
   - `on_decryption_failure`
   - `logger as matrix_logger`
   - `InviteMemberEvent`

3. **Plugins lazily import directly from `mmrelay.matrix_utils`**  
   Examples:
   - `connect_matrix`
   - `ImageUploadError`
   - `send_image`
   - `bot_command`

4. **Meshtastic event handling lazily imports Matrix helpers**  
   `src/mmrelay/meshtastic/events.py` imports:
   - `get_interaction_settings`
   - `get_matrix_prefix`
   - `matrix_relay`

5. **Tests patch `mmrelay.matrix_utils.*` heavily**  
   Current repo search shows roughly **1200+ references** across tests and source to `mmrelay.matrix_utils`, including direct global resets in `tests/conftest.py`.

### Mechanism

- `src/mmrelay/matrix_utils.py` becomes a thin facade
- all mutable globals remain defined there
- facade re-exports implementation functions and selected imported Matrix/nio types
- submodules access shared runtime state through:

```python
import mmrelay.matrix_utils as facade
```

This matches the Meshtastic decomposition pattern and preserves monkeypatch/test compatibility.

---

## 5. Proposed Package Layout

```text
src/mmrelay/matrix/
    __init__.py            # minimal package init
    room_mapping.py        # alias resolution, room mapping display/helpers
    prefixes.py            # prefix formatting, interaction settings, message-storage config
    command_bridge.py      # bot command + Meshtastic bridge helpers
    credentials.py         # credential discovery/load/save path helpers
    auth.py                # client init, login, whoami fallback, E2EE bootstrap pieces
    sync_bootstrap.py      # initial sync, room join/setup, connect/login orchestration
    relay.py               # matrix_relay + retry/send helpers
    replies.py             # reply formatting and Meshtastic reply bridge
    events.py              # on_room_message / on_decryption_failure / room member / invite
    media.py               # image upload/send helpers
```

### Notes

- Keep `matrix_utils.py` as the facade/state owner.
- Keep custom exceptions and dataclasses in the facade unless there is a compelling reason to move them into a tiny `errors.py` / `types.py` module. For the initial decomposition, leaving them facade-owned reduces churn.
- Continue re-exporting nio event/response classes from the facade if tests or runtime imports depend on `mmrelay.matrix_utils.<TypeName>`.

---

## 6. Function-to-Module Mapping

### 6.1 room_mapping.py

Move:

- `_is_room_alias`
- `_get_valid_device_id`
- `_extract_localpart_from_mxid`
- `_is_room_mapped`
- `_iter_room_alias_entries`
- `_resolve_aliases_in_mapping`
- `_update_room_id_in_mapping`
- `_display_room_channel_mappings`
- `_create_mapping_info`

State access:

- `facade.matrix_rooms`
- `facade.matrix_client`
- `facade.logger`

### 6.2 prefixes.py

Move:

- `_first_nonblank_str`
- `_can_auto_create_credentials`
- `_normalize_bot_user_id`
- `_get_msgs_to_keep_config`
- `_get_detailed_matrix_error_message`
- `get_interaction_settings`
- `message_storage_enabled`
- `_add_truncated_vars`
- `_escape_leading_prefix_for_markdown`
- `validate_prefix_format`
- `get_meshtastic_prefix`
- `get_matrix_prefix`

State access:

- mostly `facade.config` and `facade.logger`

### 6.3 command_bridge.py

Move:

- `_estimate_clock_rollback_ms`
- `_refresh_bot_start_timestamps`
- `get_displayname`
- `bot_command`
- `_connect_meshtastic`
- `_get_meshtastic_interface_and_channel`
- `_handle_detection_sensor_packet`

State access:

- `facade.matrix_client`
- `facade.config`
- `facade.bot_user_id`
- `facade.bot_start_time`
- `facade.bot_start_monotonic_secs`

Cross-module dependencies:

- `facade.connect_meshtastic`
- `facade.send_text_reply`

### 6.4 credentials.py

Move:

- `_resolve_credentials_save_path`
- `_missing_credentials_keys`
- `_resolve_and_load_credentials`

State access:

- `facade.config`
- `facade.matrix_homeserver`
- `facade.matrix_access_token`
- `facade.bot_user_id`
- `facade.logger`

### 6.5 auth.py

Move:

- `_configure_e2ee`
- `_initialize_matrix_client`
- `_perform_matrix_login`
- `_maybe_upload_e2ee_keys`
- `_close_matrix_client_after_failure`

State access:

- `facade.matrix_client`
- `facade.bot_user_id`
- `facade.bot_user_name`
- `facade.matrix_homeserver`
- `facade.matrix_access_token`
- `facade.logger`

### 6.6 sync_bootstrap.py

Move:

- `_perform_initial_sync`
- `_post_sync_setup`
- `connect_matrix`
- `login_matrix_bot`
- `join_matrix_room`

State access:

- `facade.matrix_client`
- `facade.matrix_rooms`
- `facade.bot_start_time`
- `facade.bot_start_monotonic_secs`
- `facade._MATRIX_STARTUP_SYNC_LOCK`
- `facade.logger`

### 6.7 relay.py

Move:

- `_get_e2ee_error_message`
- `_retry_backoff_delay`
- `_send_matrix_message_with_retry`
- `matrix_relay`

State access:

- `facade.matrix_client`
- `facade.bot_user_id`
- `facade.bot_user_name`
- `facade.matrix_rooms`
- `facade.config`
- `facade.logger`

### 6.8 replies.py

Move:

- `truncate_message`
- `strip_quoted_lines`
- `get_user_display_name`
- `format_reply_message`
- `send_reply_to_meshtastic`
- `handle_matrix_reply`

State access:

- `facade.matrix_client`
- `facade.logger`

### 6.9 events.py

Move:

- `on_decryption_failure`
- `on_room_message`
- `on_room_member`
- `on_invite`

State access:

- `facade.matrix_client`
- `facade.matrix_rooms`
- `facade.bot_user_id`
- `facade.bot_user_name`
- `facade.bot_start_time`
- `facade.bot_start_monotonic_secs`
- `facade.config`
- `facade.logger`

### 6.10 media.py

Move:

- `ImageUploadError`
- `upload_image`
- `send_room_image`
- `send_image`

State access:

- `facade.matrix_client`
- `facade.logger`

---

## 7. Import / Export Compatibility Strategy

### 7.1 Facade Pattern

`src/mmrelay/matrix_utils.py` becomes a thin facade that keeps:

1. top-level imports that external consumers expect to access through the module
2. module-level globals
3. custom exceptions / dataclasses (at least initially)
4. re-exports from `mmrelay.matrix.*` submodules

Example shape:

```python
# src/mmrelay/matrix_utils.py
"""Facade module — re-exports from mmrelay.matrix submodules.

This module retains all module-level globals for backward compatibility.
External code and tests continue to import from and patch this module.
"""

# global runtime state stays here
config: dict[str, Any] | None = None
matrix_homeserver: str | None = None
matrix_rooms = None
matrix_access_token: str | None = None
bot_user_id: str | None = None
bot_user_name: str | None = None
bot_start_time = ...
bot_start_monotonic_secs = ...
matrix_client = None
_MATRIX_STARTUP_SYNC_LOCK = asyncio.Lock()

from mmrelay.matrix.room_mapping import ...
from mmrelay.matrix.prefixes import ...
from mmrelay.matrix.command_bridge import ...
# etc.
```

### 7.2 Backward Compatibility Guarantees

These must keep working throughout all phases:

| Pattern                                                                                              | Must keep working |
| ---------------------------------------------------------------------------------------------------- | ----------------- |
| `from mmrelay.matrix_utils import connect_matrix`                                                    | Yes               |
| `from mmrelay.matrix_utils import login_matrix_bot`                                                  | Yes               |
| `from mmrelay.matrix_utils import matrix_relay`                                                      | Yes               |
| `from mmrelay.matrix_utils import get_matrix_prefix, get_meshtastic_prefix`                          | Yes               |
| `from mmrelay.matrix_utils import on_room_message, on_invite, on_room_member, on_decryption_failure` | Yes               |
| `from mmrelay.matrix_utils import ImageUploadError, send_image`                                      | Yes               |
| `from mmrelay.matrix_utils import logger as matrix_logger`                                           | Yes               |
| direct writes like `matrix_utils.matrix_client = None` in tests                                      | Yes               |
| `set_config(matrix_utils, config)`                                                                   | Yes               |

### 7.3 Submodule State Access Pattern

Submodules that need shared state use:

```python
import mmrelay.matrix_utils as facade
```

This ensures:

- one state owner
- stable patch targets
- no state drift between facade and submodules

---

## 8. Rollout Phases

### Phase 1 — Low-risk pure helpers

**Risk**: Low

Create:

- `src/mmrelay/matrix/__init__.py`
- `src/mmrelay/matrix/room_mapping.py`
- `src/mmrelay/matrix/prefixes.py`

Move:

- room alias / mapping helpers
- prefix / interaction / formatting helpers

Why first:

- mostly pure functions
- minimal runtime mutation
- easy to re-export through facade

### Phase 2 — Command bridge + reply/media helpers

**Risk**: Low-medium

Create:

- `src/mmrelay/matrix/command_bridge.py`
- `src/mmrelay/matrix/replies.py`
- `src/mmrelay/matrix/media.py`

Move:

- Meshtastic bridge helpers
- reply formatting / reply bridge
- image upload/send helpers

Why second:

- more runtime interactions than Phase 1, but still substantially less risky than Matrix login/sync or event handlers

### Phase 3 — Credentials + auth primitives

**Risk**: Medium

Create:

- `src/mmrelay/matrix/credentials.py`
- `src/mmrelay/matrix/auth.py`

Move:

- credential path/load/save helpers
- client init, login, E2EE config, client-close helpers

Why third:

- contains more network/runtime behavior but remains more tractable than the full bootstrap functions

### Phase 4 — Sync/bootstrap orchestration

**Risk**: Medium-high

Create:

- `src/mmrelay/matrix/sync_bootstrap.py`

Move:

- `_perform_initial_sync`
- `_post_sync_setup`
- `connect_matrix`
- `login_matrix_bot`
- `join_matrix_room`

Why here:

- these functions orchestrate many helpers and touch most state
- better to move only after Phases 1-3 are stable

### Phase 5 — Relay/send path

**Risk**: Medium-high

Create:

- `src/mmrelay/matrix/relay.py`

Move:

- `_get_e2ee_error_message`
- `_retry_backoff_delay`
- `_send_matrix_message_with_retry`
- `matrix_relay`

Why separate from events:

- large side-effect surface
- heavily patched in tests
- easier to stabilize on its own before event handler moves

### Phase 6 — Event handlers last

**Risk**: Highest

Create:

- `src/mmrelay/matrix/events.py`

Move:

- `on_decryption_failure`
- `on_room_message`
- `on_room_member`
- `on_invite`

Why last:

- hottest and most coupled runtime path
- extensive lazy imports
- high test surface
- easiest place to accidentally introduce subtle regressions

### Phase 7 — Cleanup pass

After all phases are green:

- trim dead imports from the facade
- tighten type hints
- document facade contract
- optionally consider a later state-owner migration

---

## 9. Non-Negotiable Refactor Rules

1. **No public import breakage in any phase.**
2. **No state-owner swap in the decomposition PRs.**
3. **No broad renaming while moving functions.**
4. **Each phase moves one concern.**
5. **Every phase ends with passing tests.**
6. **Submodules never own runtime state in phases 1-6.**
7. **Submodules use `import mmrelay.matrix_utils as facade` for shared state.**
8. **Keep load-bearing lazy imports unless a move clearly eliminates the cycle.**
9. **Preserve nio type/class re-exports on the facade if tests/runtime import them from `matrix_utils`.**

---

## 10. Known Risks and Mitigations

| #   | Risk                                                                    | Mitigation                                                                                               |
| --- | ----------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| 1   | **Facade state drift** — submodules accidentally define duplicate state | Keep all mutable globals in `matrix_utils.py`; submodules always use `facade.*`.                         |
| 2   | **`config.py` module-name coupling**                                    | Keep the module name and state-owner pattern unchanged.                                                  |
| 3   | **Main/plugin import breakage**                                         | Facade re-exports all moved names.                                                                       |
| 4   | **Circular imports with Meshtastic helpers**                            | Preserve lazy imports where currently used; avoid forcing top-level cross-imports.                       |
| 5   | **`login_matrix_bot` complexity**                                       | Move only after helper/auth layers are stable.                                                           |
| 6   | **`on_room_message` regressions**                                       | Move last; do not refactor internals during the move.                                                    |
| 7   | **nio type/class import surface changes**                               | Keep re-exported nio classes/types available from the facade.                                            |
| 8   | **Test patch breakage**                                                 | Tests continue patching `mmrelay.matrix_utils.*`; facade remains the patch surface.                      |
| 9   | **E2EE startup/sync regressions**                                       | Keep `_perform_initial_sync`, `_post_sync_setup`, and `_configure_e2ee` behavior unchanged while moving. |
| 10  | **Message relay side effects**                                          | Move `matrix_relay` separately from `on_room_message` to reduce blast radius.                            |

---

## 11. External Consumers of `matrix_utils`

### 11.1 `main.py`

`main.py` imports and relies on:

- `InviteMemberEvent`
- `connect_matrix`
- `join_matrix_room`
- `logger as matrix_logger`
- `on_decryption_failure`
- `on_invite`
- `on_room_member`
- `on_room_message`
- `set_config(matrix_utils, config)`

### 11.2 Plugins

Examples of lazy imports from plugins:

- `connect_matrix`
- `ImageUploadError`
- `send_image`
- `bot_command`

### 11.3 Meshtastic event pipeline

`src/mmrelay/meshtastic/events.py` lazily imports:

- `get_interaction_settings`
- `get_matrix_prefix`
- `matrix_relay`

### 11.4 Tests

Current repository search shows roughly **1225 references** to `mmrelay.matrix_utils` across `src/` and `tests/`.
Important examples:

- `tests/conftest.py` resets `matrix_client`, `matrix_rooms`, `bot_user_id`
- extensive patching of `mmrelay.matrix_utils.logger`, `AsyncClient`, `matrix_client`, `matrix_relay`, `connect_matrix`, `login_matrix_bot`, and event handlers
- tests import nio-ish classes and custom exceptions from `mmrelay.matrix_utils`

---

## 12. Suggested Initial Agent Packet

```text
Task: Create the first Matrix decomposition plan implementation wave

Context:
- Target file: src/mmrelay/matrix_utils.py (~5073 lines)
- Facade/state owner remains src/mmrelay/matrix_utils.py
- Public patch/import surface must remain stable
- Do not modify tests in this wave

Wave 1 only:
Create:
- src/mmrelay/matrix/__init__.py
- src/mmrelay/matrix/room_mapping.py
- src/mmrelay/matrix/prefixes.py

Move to room_mapping.py:
- _is_room_alias
- _get_valid_device_id
- _extract_localpart_from_mxid
- _is_room_mapped
- _iter_room_alias_entries
- _resolve_aliases_in_mapping
- _update_room_id_in_mapping
- _display_room_channel_mappings
- _create_mapping_info

Move to prefixes.py:
- _first_nonblank_str
- _can_auto_create_credentials
- _normalize_bot_user_id
- _get_msgs_to_keep_config
- _get_detailed_matrix_error_message
- get_interaction_settings
- message_storage_enabled
- _add_truncated_vars
- _escape_leading_prefix_for_markdown
- validate_prefix_format
- get_meshtastic_prefix
- get_matrix_prefix

Rules:
- Keep all globals in matrix_utils.py
- Submodules must use `import mmrelay.matrix_utils as facade` for shared state
- Re-export moved functions from matrix_utils.py
- Preserve signatures, names, logging, and behavior exactly
- Do not change public import paths
- Do not run linting
- Do not modify tests

Verification:
- Run targeted matrix_utils tests
- Then run full tests covering matrix_utils if practical
- Report exact commands run and result summary
```

---

## 13. Coordinator Recommendation

The best decomposition order for `matrix_utils.py` is:

1. **pure helpers and room mapping**
2. **prefix/config helpers**
3. **Meshtastic bridge + reply/media helpers**
4. **credentials/auth primitives**
5. **sync/bootstrap orchestration**
6. **relay path**
7. **event handlers last**

This follows the same successful pattern used for the Meshtastic decomposition:

- start with low-risk helpers
- keep the facade as state owner
- move orchestration late
- leave the hottest event path for the final wave
