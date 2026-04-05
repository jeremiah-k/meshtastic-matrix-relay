# Meshtastic Utils Decomposition Plan

## Document Status

- **Phase**: 0 — Documentation only
- **Date**: 2026-04-04
- **Target file**: `src/mmrelay/meshtastic_utils.py` (5824 lines)
- **Output**: Package at `src/mmrelay/meshtastic/`

---

## 1. Goals

1. Decompose the 5824-line monolith into focused, cohesive modules
2. Maintain 100% backward compatibility for existing imports
3. Enable independent testing of subsystems
4. Prepare for eventual state-object migration (but don't do it now)

---

## 2. Non-Goals

- No public import breakage during any phase
- No state-owner migration in the same refactor
- No broad renaming during moves
- No introducing a central state object yet
- No test path changes during decomposition (tests continue patching `mmrelay.meshtastic_utils.*`)

---

## 3. Current File Analysis

### 3.1 Import Inventory

**Stdlib** (lines 1-17): `asyncio`, `atexit`, `contextlib`, `functools`, `importlib`, `importlib.util`, `inspect`, `io`, `logging`, `math`, `sys`, `threading`, `time`, `concurrent.futures.{Future, ThreadPoolExecutor, TimeoutError}`, `typing.{Any, Awaitable, Callable, Coroutine, cast}`

**Third-party** (lines 19-27): `meshtastic`, `meshtastic.ble_interface`, `meshtastic.serial_interface`, `meshtastic.tcp_interface`, `serial`, `serial.tools.list_ports`, `meshtastic.protobuf.{admin_pb2, mesh_pb2, portnums_pb2}`, `pubsub.pub`, `pubsub.core.topicexc.TopicNameError`

**Internal** (lines 29-117):

- `mmrelay.config.get_meshtastic_config_value`
- `mmrelay.constants.config.*` (12 names)
- `mmrelay.constants.database.{PROTO_NODE_NAME_LONG, PROTO_NODE_NAME_SHORT}`
- `mmrelay.constants.domain.METADATA_OUTPUT_MAX_LENGTH`
- `mmrelay.constants.formats.*` (7 names)
- `mmrelay.constants.messages.*` (3 names)
- `mmrelay.constants.network.*` (47 names)
- `mmrelay.db_utils.{NodeNameState, get_longname, get_message_map_by_meshtastic_id, get_shortname, save_longname, save_shortname, sync_name_tables_if_changed}`
- `mmrelay.log_utils.get_logger`
- `mmrelay.runtime_utils.is_running_as_service`

**Conditional imports** (lines 119-131):

- `BLE_AVAILABLE` detection via `importlib.util.find_spec("bleak")` (line 120)
- `BleakDBusError`, `BleakError` from `bleak.exc` (line 127-130)

**Lazy imports inside functions**:

- `mmrelay.plugin_loader.load_plugins` inside `_run_meshtastic_plugins` (line 2477)
- `mmrelay.matrix_utils.get_interaction_settings` inside `on_meshtastic_message` (line 5169)
- `mmrelay.matrix_utils.matrix_relay` inside `on_meshtastic_message` (line 5189)
- `mmrelay.matrix_utils.get_matrix_prefix` inside `on_meshtastic_message` (lines 5247, 5298, 5455)
- `mmrelay.matrix_utils.logger as meshtastic_logger` in `matrix_utils.py` (lazy imports of meshtastic_utils logger)
- `mmrelay.db_utils.{get_longname, get_shortname}` inside `_get_node_display_name` (line 3441)
- `meshtastic.mesh_interface.BROADCAST_NUM` inside `on_meshtastic_message` (line 5212)
- `bleak.BleakScanner` inside `_scan_for_ble_address` (line 2022)
- `bleak.BleakClient` inside `_disconnect_ble_by_address` (line 2925)
- `rich.progress.*` inside `reconnect` (line 3894)

### 3.2 Exception Classes

| Class                           | Line    | Extends        |
| ------------------------------- | ------- | -------------- |
| `BleExecutorDegradedError`      | 133-136 | `Exception`    |
| `MetadataExecutorDegradedError` | 320-327 | `RuntimeError` |

### 3.3 Module-Level Globals Inventory

| Variable                                                      | Line    | Type                         | Description                               |
| ------------------------------------------------------------- | ------- | ---------------------------- | ----------------------------------------- |
| `BLE_AVAILABLE`                                               | 120     | `bool`                       | Whether bleak is installed                |
| `BleakDBusError`                                              | 127     | `type`                       | Fallback: `Exception`                     |
| `BleakError`                                                  | 129     | `type`                       | Fallback: `Exception`                     |
| `_ble_gate_reset_callable`                                    | 152     | `Callable or None`           | Optional BLE gate reset function          |
| `_ble_gating_module`                                          | 153     | `Any or None`                | Imported BLE gating module                |
| `config`                                                      | 140     | `Any`                        | Global config dict, set by `set_config()` |
| `matrix_rooms`                                                | 145     | `list[dict]`                 | Room mappings                             |
| `logger`                                                      | 148     | `Logger`                     | Module logger                             |
| `MESHTASTIC_TEXT_ENCODING`                                    | 168     | `str`                        | `"utf-8"`                                 |
| `RELAY_START_TIME`                                            | 171     | `float`                      | `time.time()` at connection start         |
| `_relay_connection_started_monotonic_secs`                    | 174     | `float`                      | Monotonic timestamp for connection        |
| `_relay_rx_time_clock_skew_secs`                              | 176     | `float or None`              | Per-connection rxTime skew                |
| `_relay_rx_time_clock_skew_lock`                              | 177     | `threading.Lock`             | Guards skew state                         |
| `_relay_startup_drain_deadline_monotonic_secs`                | 182     | `float or None`              | Startup packet drain window               |
| `_startup_packet_drain_applied`                               | 184     | `bool`                       | Whether drain was applied                 |
| `_relay_reconnect_prestart_bootstrap_deadline_monotonic_secs` | 187     | `float or None`              | Reconnect prestart bootstrap              |
| `meshtastic_client`                                           | 191     | `Any`                        | Active Meshtastic client                  |
| `_relay_active_client_id`                                     | 193     | `int or None`                | Session guard for callbacks               |
| `meshtastic_iface`                                            | 194     | `Any`                        | BLE interface singleton                   |
| `event_loop`                                                  | 195     | `AbstractEventLoop or None`  | Set by `main.py`                          |
| `meshtastic_lock`                                             | 197     | `threading.Lock`             | Client access lock                        |
| `_connect_attempt_lock`                                       | 202     | `threading.RLock`            | Serializes connect attempts               |
| `_connect_attempt_condition`                                  | 203     | `threading.Condition`        | Wait/notify for connect attempts          |
| `_connect_attempt_in_progress`                                | 204     | `bool`                       | Connect attempt flag                      |
| `_CONNECT_ATTEMPT_WAIT_POLL_SECS`                             | 205     | `float`                      | `1.0`                                     |
| `_CONNECT_ATTEMPT_WAIT_MAX_SECS`                              | 206     | `float`                      | `5.0`                                     |
| `_CONNECT_ATTEMPT_BLE_WAIT_MAX_SECS`                          | 207-209 | `float`                      | BLE wait budget                           |
| `reconnecting`                                                | 211     | `bool`                       | Reconnection in progress                  |
| `shutting_down`                                               | 212     | `bool`                       | Shutdown flag                             |
| `reconnect_task`                                              | 214     | `asyncio.Task or None`       | Reconnect task ref                        |
| `reconnect_task_future`                                       | 215     | `asyncio.Future or None`     | Reconnect worker future                   |
| `meshtastic_iface_lock`                                       | 216-218 | `threading.Lock`             | BLE interface creation lock               |
| `meshtastic_sub_lock`                                         | 221     | `threading.Lock`             | Subscription state lock                   |
| `subscribed_to_messages`                                      | 222     | `bool`                       | Pubsub subscription flag                  |
| `subscribed_to_connection_lost`                               | 223     | `bool`                       | Pubsub subscription flag                  |
| `_callbacks_tearing_down`                                     | 225     | `bool`                       | Guard during unsubscribe                  |
| `_metadata_executor`                                          | 291     | `ThreadPoolExecutor or None` | Shared metadata executor                  |
| `_metadata_future`                                            | 292     | `Future or None`             | Active metadata future                    |
| `_metadata_future_started_at`                                 | 293     | `float or None`              | Metadata future start time                |
| `_metadata_future_lock`                                       | 294     | `threading.Lock`             | Metadata state lock                       |
| `_metadata_executor_orphaned_workers`                         | 295     | `int`                        | Orphaned worker count                     |
| `_health_probe_request_deadlines`                             | 296     | `dict[int, float]`           | Probe tracking map                        |
| `_health_probe_request_lock`                                  | 297     | `threading.Lock`             | Probe tracking lock                       |
| `_ble_executor`                                               | 301     | `ThreadPoolExecutor or None` | Shared BLE executor                       |
| `_ble_executor_lock`                                          | 302     | `threading.Lock`             | BLE executor state lock                   |
| `_ble_future`                                                 | 303     | `Future or None`             | Active BLE future                         |
| `_ble_future_address`                                         | 304     | `str or None`                | BLE future target address                 |
| `_ble_future_started_at`                                      | 305     | `float or None`              | BLE future start time                     |
| `_ble_future_timeout_secs`                                    | 306     | `float or None`              | BLE future timeout                        |
| `_ble_timeout_counts`                                         | 307     | `dict[str, int]`             | Per-address timeout count                 |
| `_ble_executor_orphaned_workers_by_address`                   | 308     | `dict[str, int]`             | Per-address orphan count                  |
| `_ble_timeout_lock`                                           | 309     | `threading.Lock`             | BLE timeout state lock                    |
| `_ble_future_watchdog_secs`                                   | 310     | `float`                      | `BLE_FUTURE_WATCHDOG_SECS`                |
| `_ble_timeout_reset_threshold`                                | 311     | `int`                        | `BLE_TIMEOUT_RESET_THRESHOLD`             |
| `_ble_scan_timeout_secs`                                      | 312     | `float`                      | `BLE_SCAN_TIMEOUT_SECS`                   |
| `_ble_future_stale_grace_secs`                                | 313     | `float`                      | `BLE_FUTURE_STALE_GRACE_SECS`             |
| `_ble_interface_create_timeout_secs`                          | 314     | `float`                      | `BLE_INTERFACE_CREATE_TIMEOUT_FLOOR_SECS` |
| `_ble_executor_degraded_addresses`                            | 316     | `set[str]`                   | Degraded BLE addresses                    |
| `_metadata_executor_degraded`                                 | 317     | `bool`                       | Metadata executor degraded flag           |

### 3.4 Function Inventory with Categories

| Function                                           | Lines     | Category          |
| -------------------------------------------------- | --------- | ----------------- |
| `ensure_meshtastic_callbacks_subscribed`           | 228-243   | subscriptions     |
| `unsubscribe_meshtastic_callbacks`                 | 246-286   | subscriptions     |
| `_shutdown_shared_executors`                       | 436-502   | executors         |
| `shutdown_shared_executors`                        | 505-511   | executors         |
| `reset_executor_degraded_state`                    | 514-620   | executors         |
| `_get_ble_executor`                                | 626-640   | executors         |
| `_get_metadata_executor`                           | 643-657   | executors         |
| `_clear_metadata_future_if_current`                | 660-668   | executors         |
| `_reset_metadata_executor_for_stale_probe`         | 671-751   | executors         |
| `_schedule_metadata_future_cleanup`                | 753-791   | executors         |
| `_submit_metadata_probe`                           | 794-853   | executors         |
| `_coerce_nonnegative_float`                        | 330-342   | async_utils       |
| `_coerce_positive_int`                             | 345-357   | async_utils       |
| `_is_ble_duplicate_connect_suppressed_error`       | 360-373   | ble               |
| `_reset_ble_connection_gate_state`                 | 376-402   | ble               |
| `_normalize_room_channel`                          | 405-433   | messaging         |
| `_coerce_positive_int_id`                          | 856-866   | async_utils       |
| `_coerce_int_id`                                   | 869-878   | async_utils       |
| `_coerce_positive_float`                           | 881-903   | async_utils       |
| `_coerce_bool`                                     | 906-935   | async_utils       |
| `_parse_refresh_interval_seconds`                  | 938-954   | node_refresh      |
| `get_nodedb_refresh_interval_seconds`              | 957-991   | node_refresh      |
| `_snapshot_node_name_rows`                         | 994-1031  | node_refresh      |
| `refresh_node_name_tables`                         | 1034-1120 | node_refresh      |
| `_extract_packet_request_id`                       | 1123-1144 | health            |
| `_prune_health_probe_tracking`                     | 1147-1158 | health            |
| `_track_health_probe_request_id`                   | 1161-1179 | health            |
| `_seed_connect_time_skew`                          | 1182-1267 | health            |
| `_is_health_probe_response_packet`                 | 1270-1288 | health            |
| `_claim_health_probe_response_and_maybe_calibrate` | 1291-1343 | health            |
| `_set_probe_ack_flag_from_packet`                  | 1346-1386 | health            |
| `_missing_local_node_ack_state_error`              | 1389-1393 | health            |
| `_missing_received_nak_error`                      | 1396-1400 | health            |
| `_failed_probe_ack_state_error`                    | 1403-1407 | health            |
| `_missing_ack_state_error`                         | 1410-1414 | health            |
| `_metadata_probe_ack_timeout_error`                | 1417-1423 | health            |
| `_missing_probe_transport_error`                   | 1426-1430 | health            |
| `_missing_probe_wait_error`                        | 1433-1437 | health            |
| `_reset_probe_ack_state`                           | 1440-1454 | health            |
| `_handle_probe_ack_callback`                       | 1457-1487 | health            |
| `_wait_for_probe_ack`                              | 1490-1518 | health            |
| `_probe_device_connection`                         | 1521-1598 | health            |
| `_submit_coro`                                     | 1601-1677 | async_utils       |
| `_fire_and_forget`                                 | 2133-2173 | async_utils       |
| `_make_awaitable`                                  | 2176-2194 | async_utils       |
| `_run_blocking_with_timeout`                       | 2197-2249 | async_utils       |
| `_wait_for_result`                                 | 2252-2340 | async_utils       |
| `_wait_for_future_result_with_shutdown`            | 2343-2383 | async_utils       |
| `_resolve_plugin_timeout`                          | 2386-2427 | plugins           |
| `_resolve_plugin_result`                           | 2430-2459 | plugins           |
| `_run_meshtastic_plugins`                          | 2462-2519 | plugins           |
| `_get_name_safely`                                 | 2522-2536 | metadata          |
| `_get_name_or_none`                                | 2539-2555 | metadata          |
| `_normalize_firmware_version`                      | 2558-2574 | metadata          |
| `_extract_firmware_version_from_metadata`          | 2577-2602 | metadata          |
| `_extract_firmware_version_from_client`            | 2605-2628 | metadata          |
| `_missing_metadata_probe_error`                    | 2631-2637 | metadata          |
| `_get_device_metadata`                             | 2640-2824 | metadata          |
| `_sanitize_ble_address`                            | 2827-2842 | ble               |
| `_validate_ble_connection_address`                 | 2845-2908 | ble               |
| `_disconnect_ble_by_address`                       | 2911-3134 | ble               |
| `_disconnect_ble_interface`                        | 3137-3325 | ble               |
| `_get_packet_details`                              | 3328-3379 | messaging         |
| `_get_portnum_name`                                | 3382-3408 | messaging         |
| `_get_node_display_name`                           | 3411-3449 | messaging         |
| `serial_port_exists`                               | 3452-3463 | connection        |
| `_get_connection_retry_wait_time`                  | 3466-3487 | connection        |
| `_get_connect_time_probe_settings`                 | 3490-3524 | connection        |
| `_schedule_connect_time_calibration_probe`         | 3527-3579 | connection        |
| `_rollback_connect_attempt_state`                  | 3582-3635 | connection        |
| `connect_meshtastic`                               | 3638-3709 | connection        |
| `_connect_meshtastic_impl`                         | 3712-4701 | connection        |
| `on_lost_meshtastic_connection`                    | 4704-4873 | events            |
| `reconnect`                                        | 4876-4948 | events            |
| `on_meshtastic_message`                            | 4951-5538 | events            |
| `requires_continuous_health_monitor`               | 5541-5571 | health            |
| `check_connection`                                 | 5574-5749 | health            |
| `send_text_reply`                                  | 5752-5811 | messaging         |
| `sendTextReply`                                    | 5814      | messaging (alias) |
| `_clear_ble_future`                                | 1680-1699 | ble               |
| `_schedule_ble_future_cleanup`                     | 1702-1745 | ble               |
| `_attach_late_ble_interface_disposer`              | 1748-1804 | ble               |
| `_record_ble_timeout`                              | 1807-1821 | ble               |
| `_ensure_ble_worker_available`                     | 1824-1881 | ble               |
| `_maybe_reset_ble_executor`                        | 1884-2006 | ble               |
| `_scan_for_ble_address`                            | 2009-2082 | ble               |
| `_is_ble_discovery_error`                          | 2085-2130 | ble               |

### 3.5 Cross-Category Dependency Graph

```
events ──────> messaging helpers (_normalize_room_channel, _get_portnum_name, etc.)
events ──────> plugins (_run_meshtastic_plugins)
events ──────> health (_claim_health_probe_response_and_maybe_calibrate, _seed_connect_time_skew)
events ──────> async_utils (_fire_and_forget, _submit_coro)
events ──────> metadata (_get_name_safely, _get_name_or_none)
events ──────> subscriptions (reads subscription flags)
events ──────> connection (reads meshtastic_client, _relay_active_client_id)

connection ──> ble (_disconnect_ble_interface, _sanitize_ble_address, _scan_for_ble_address, etc.)
connection ──> executors (_submit_metadata_probe, _get_ble_executor)
connection ──> metadata (_get_device_metadata)
connection ──> health (_schedule_connect_time_calibration_probe)
connection ──> subscriptions (ensure_meshtastic_callbacks_subscribed)
connection ──> node_refresh (get_nodedb_refresh_interval_seconds via main.py)
connection ──> async_utils (_wait_for_future_result_with_shutdown, _run_blocking_with_timeout)

health ──────> executors (_submit_metadata_probe)
health ──────> connection (calls on_lost_meshtastic_connection)
health ──────> events (reconnect triggers)
health ──────> async_utils (_coerce_positive_float, _coerce_bool)

executors ──> async_utils (no direct deps, but threading patterns)

ble ────────> async_utils (_wait_for_result, _fire_and_forget, _run_blocking_with_timeout)
ble ────────> executors (_get_ble_executor, _maybe_reset_ble_executor)

node_refresh ──> async_utils (asyncio.to_thread)
node_refresh ──> reads reconnecting, meshtastic_client

plugins ────> async_utils (_submit_coro, _wait_for_result)
plugins ────> lazy imports plugin_loader

messaging ──> mostly self-contained, reads MESHTASTIC_TEXT_ENCODING global
```

### 3.6 High-Risk Functions

1. **`_connect_meshtastic_impl`** (lines 3712-4701, ~990 lines): The single largest function. Deeply coupled to BLE executors, connection state, timing state, and rollback logic.
2. **`on_lost_meshtastic_connection`** (lines 4704-4873): Mutates many globals, interacts with BLE executor state.
3. **`on_meshtastic_message`** (lines 4951-5538): Complex control flow with lazy imports, reads many globals.
4. **`check_connection`** (lines 5574-5749): Async loop calling into executor/probe machinery.

---

## 4. State Ownership Decision

**Decision: Keep all mutable globals in the `meshtastic_utils` facade for all phases 1-6.**

### Rationale

1. **`config.py` attachment by module name**: `config.py:1238` has `elif module_name == "meshtastic_utils"` which calls `set_config(module)` passing the meshtastic_utils module object. This sets `module.config = passed_config` and `module.matrix_rooms = passed_config["matrix_rooms"]` (line 1221-1222). Changing the module name would break this.

2. **`main.py` direct attribute writes**: `main.py` writes to 7+ globals directly:
   - `meshtastic_utils.event_loop = loop` (line 385)
   - `meshtastic_utils.shutting_down = True` (line 467)
   - `meshtastic_utils.meshtastic_client = await ...` (line 708)
   - `meshtastic_utils.meshtastic_client = None` (line 632)
   - `meshtastic_utils.meshtastic_iface = None` (line 599, 633)
   - `meshtastic_utils.reconnect_task = None` (line 667)
   - `meshtastic_utils.reconnect_task_future = None` (line 678)

3. **`message_queue.py` lazy imports**: Line 859: `from mmrelay.meshtastic_utils import meshtastic_client, reconnecting` — reads globals at runtime.

4. **Test patches**: 1300+ references to `mmrelay.meshtastic_utils.*` in test files. The `reset_meshtastic_globals` fixture in `conftest.py` (line 933) directly sets attributes on the module object.

5. **Plugin lazy imports**: 10 plugin files lazily `from mmrelay.meshtastic_utils import connect_meshtastic` inside methods.

### Mechanism

- The facade (`meshtastic_utils.py`) becomes a thin re-export layer
- All globals remain defined in the facade
- Facade imports implementation functions from submodules
- Submodules access shared state via `import mmrelay.meshtastic_utils as facade` and read/write `facade.GLOBAL_NAME`

---

## 5. Proposed Package Layout

```
src/mmrelay/meshtastic/
    __init__.py          # Package init, re-exports for "from mmrelay.meshtastic import X"
    subscriptions.py     # Pubsub subscription lifecycle
    executors.py         # Executor/future/degraded-state machinery
    node_refresh.py      # Node metadata + name refresh
    health.py            # Health probe / ACK / rxTime skew calibration
    async_utils.py       # General async/timeout/plugin execution helpers
    plugins.py           # Plugin timeout/result wrappers
    metadata.py          # Metadata fetch / firmware extraction
    ble.py               # BLE-specific connection/disconnect logic
    connection.py        # Connection lifecycle + runtime handlers
    events.py            # Event handlers (connection lost, message received)
    messaging.py         # Message formatting and reply
```

---

## 6. Function-to-Module Mapping

### 6.1 async_utils.py — General async/timeout helpers

| Function                                | Lines     | Public | Globals Read           | Globals Written | Cross-Module Deps                           |
| --------------------------------------- | --------- | ------ | ---------------------- | --------------- | ------------------------------------------- |
| `_coerce_nonnegative_float`             | 330-342   | No     | none                   | none            | none                                        |
| `_coerce_positive_int`                  | 345-357   | No     | none                   | none            | none                                        |
| `_coerce_positive_int_id`               | 856-866   | No     | none                   | none            | none                                        |
| `_coerce_int_id`                        | 869-878   | No     | none                   | none            | none                                        |
| `_coerce_positive_float`                | 881-903   | No     | `logger`               | none            | none                                        |
| `_coerce_bool`                          | 906-935   | No     | `logger`               | none            | none                                        |
| `_submit_coro`                          | 1601-1677 | No     | `event_loop`, `logger` | none            | none                                        |
| `_fire_and_forget`                      | 2133-2173 | No     | none                   | none            | calls `_submit_coro`                        |
| `_make_awaitable`                       | 2176-2194 | No     | none                   | none            | none                                        |
| `_run_blocking_with_timeout`            | 2197-2249 | No     | `logger`               | none            | none                                        |
| `_wait_for_result`                      | 2252-2340 | No     | `logger`               | none            | calls `_make_awaitable`, `_fire_and_forget` |
| `_wait_for_future_result_with_shutdown` | 2343-2383 | No     | `shutting_down`        | none            | none                                        |

### 6.2 plugins.py — Plugin execution

| Function                  | Lines     | Public | Globals Read | Globals Written | Cross-Module Deps                        |
| ------------------------- | --------- | ------ | ------------ | --------------- | ---------------------------------------- |
| `_resolve_plugin_timeout` | 2386-2427 | No     | `logger`     | none            | none                                     |
| `_resolve_plugin_result`  | 2430-2459 | No     | `logger`     | none            | calls `_submit_coro`, `_wait_for_result` |
| `_run_meshtastic_plugins` | 2462-2519 | No     | `logger`     | none            | lazy: `plugin_loader.load_plugins`       |

### 6.3 messaging.py — Message formatting and reply

| Function                  | Lines     | Public | Globals Read                         | Globals Written | Cross-Module Deps          |
| ------------------------- | --------- | ------ | ------------------------------------ | --------------- | -------------------------- |
| `_normalize_room_channel` | 405-433   | No     | `logger`                             | none            | none                       |
| `_get_packet_details`     | 3328-3379 | No     | none                                 | none            | none                       |
| `_get_portnum_name`       | 3382-3408 | No     | none                                 | none            | `portnums_pb2`             |
| `_get_node_display_name`  | 3411-3449 | No     | none                                 | none            | lazy: `db_utils`           |
| `send_text_reply`         | 5752-5811 | Yes    | `logger`, `MESHTASTIC_TEXT_ENCODING` | none            | `mesh_pb2`, `portnums_pb2` |
| `sendTextReply`           | 5814      | Yes    | (alias)                              | none            | `send_text_reply`          |

### 6.4 subscriptions.py — Pubsub subscription lifecycle

| Function                                 | Lines   | Public | Globals Read                                                                     | Globals Written                                                                      | Cross-Module Deps                                                              |
| ---------------------------------------- | ------- | ------ | -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------ |
| `ensure_meshtastic_callbacks_subscribed` | 228-243 | Yes    | `meshtastic_sub_lock`, `subscribed_to_messages`, `subscribed_to_connection_lost` | `_callbacks_tearing_down`, `subscribed_to_messages`, `subscribed_to_connection_lost` | `on_meshtastic_message`, `on_lost_meshtastic_connection` (via pub.subscribe)   |
| `unsubscribe_meshtastic_callbacks`       | 246-286 | Yes    | `meshtastic_sub_lock`, `subscribed_to_messages`, `subscribed_to_connection_lost` | `_callbacks_tearing_down`, `subscribed_to_messages`, `subscribed_to_connection_lost` | `on_meshtastic_message`, `on_lost_meshtastic_connection` (via pub.unsubscribe) |

### 6.5 node_refresh.py — Node metadata + name refresh

| Function                              | Lines     | Public | Globals Read                           | Globals Written | Cross-Module Deps                                                  |
| ------------------------------------- | --------- | ------ | -------------------------------------- | --------------- | ------------------------------------------------------------------ |
| `_parse_refresh_interval_seconds`     | 938-954   | No     | none                                   | none            | `math`                                                             |
| `get_nodedb_refresh_interval_seconds` | 957-991   | Yes    | `config`, `logger`                     | none            | `config.get_meshtastic_config_value`, constants                    |
| `_snapshot_node_name_rows`            | 994-1031  | No     | `meshtastic_lock`, `meshtastic_client` | none            | `db_utils.PROTO_NODE_NAME_*`                                       |
| `refresh_node_name_tables`            | 1034-1120 | Yes    | `reconnecting`, `logger`               | none            | `_snapshot_node_name_rows`, `db_utils.sync_name_tables_if_changed` |

### 6.6 executors.py — Executor/future/degraded-state machinery

| Function                                   | Lines   | Public | Globals Read                                                                                                                                                                                                                                                                                                                                                                   | Globals Written                                   | Cross-Module Deps                                                                                                                                    |
| ------------------------------------------ | ------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_shutdown_shared_executors`               | 436-502 | No     | `_ble_executor`, `_ble_future`, `_ble_future_address`, `_ble_future_started_at`, `_ble_future_timeout_secs`, `_metadata_executor`, `_metadata_future`, `_metadata_future_started_at`, `_health_probe_request_deadlines`, `_metadata_executor_orphaned_workers`, `_ble_executor_orphaned_workers_by_address`, `_ble_executor_degraded_addresses`, `_metadata_executor_degraded` | all of the above                                  | none                                                                                                                                                 |
| `shutdown_shared_executors`                | 505-511 | Yes    | none                                                                                                                                                                                                                                                                                                                                                                           | none                                              | calls `_shutdown_shared_executors`                                                                                                                   |
| `reset_executor_degraded_state`            | 514-620 | Yes    | `_ble_executor_degraded_addresses`, `_metadata_executor_degraded`, `_metadata_executor_orphaned_workers`, `_ble_executor_orphaned_workers_by_address`, `_ble_executor`, `_metadata_executor`, `logger`                                                                                                                                                                         | all of the above                                  | none                                                                                                                                                 |
| `_get_ble_executor`                        | 626-640 | No     | `_ble_executor`                                                                                                                                                                                                                                                                                                                                                                | `_ble_executor`                                   | none                                                                                                                                                 |
| `_get_metadata_executor`                   | 643-657 | No     | `_metadata_executor`                                                                                                                                                                                                                                                                                                                                                           | `_metadata_executor`                              | none                                                                                                                                                 |
| `_clear_metadata_future_if_current`        | 660-668 | No     | `_metadata_future`                                                                                                                                                                                                                                                                                                                                                             | `_metadata_future`, `_metadata_future_started_at` | none                                                                                                                                                 |
| `_reset_metadata_executor_for_stale_probe` | 671-751 | No     | `_metadata_executor_degraded`, `_metadata_executor_orphaned_workers`, `_metadata_executor`, `_metadata_future`, `_metadata_future_started_at`, `logger`                                                                                                                                                                                                                        | all of the above                                  | none                                                                                                                                                 |
| `_schedule_metadata_future_cleanup`        | 753-791 | No     | `_metadata_future`, `logger`                                                                                                                                                                                                                                                                                                                                                   | none                                              | calls `_reset_metadata_executor_for_stale_probe`                                                                                                     |
| `_submit_metadata_probe`                   | 794-853 | No     | `_metadata_future`, `_metadata_future_started_at`, `_metadata_executor_degraded`, `logger`                                                                                                                                                                                                                                                                                     | `_metadata_future`, `_metadata_future_started_at` | calls `_get_metadata_executor`, `_clear_metadata_future_if_current`, `_schedule_metadata_future_cleanup`, `_reset_metadata_executor_for_stale_probe` |

### 6.7 health.py — Health probe / ACK / rxTime skew calibration

| Function                                           | Lines     | Public | Globals Read                                                                                                                                                                                                                                                | Globals Written                                                                                 | Cross-Module Deps                                                                                                                                                                                                  |
| -------------------------------------------------- | --------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `_extract_packet_request_id`                       | 1123-1144 | No     | none                                                                                                                                                                                                                                                        | none                                                                                            | calls `_coerce_positive_int_id`                                                                                                                                                                                    |
| `_prune_health_probe_tracking`                     | 1147-1158 | No     | `_health_probe_request_deadlines`                                                                                                                                                                                                                           | `_health_probe_request_deadlines`                                                               | none                                                                                                                                                                                                               |
| `_track_health_probe_request_id`                   | 1161-1179 | No     | `_health_probe_request_lock`                                                                                                                                                                                                                                | `_health_probe_request_deadlines`                                                               | calls `_prune_health_probe_tracking`, `_coerce_positive_int_id`                                                                                                                                                    |
| `_seed_connect_time_skew`                          | 1182-1267 | No     | `RELAY_START_TIME`, `_relay_connection_started_monotonic_secs`, `_relay_startup_drain_deadline_monotonic_secs`, `_relay_reconnect_prestart_bootstrap_deadline_monotonic_secs`, `_relay_rx_time_clock_skew_secs`, `_relay_rx_time_clock_skew_lock`, `logger` | `_relay_rx_time_clock_skew_secs`, `_relay_reconnect_prestart_bootstrap_deadline_monotonic_secs` | none                                                                                                                                                                                                               |
| `_is_health_probe_response_packet`                 | 1270-1288 | No     | `_health_probe_request_lock`, `_health_probe_request_deadlines`                                                                                                                                                                                             | none                                                                                            | calls `_extract_packet_request_id`, `_coerce_int_id`                                                                                                                                                               |
| `_claim_health_probe_response_and_maybe_calibrate` | 1291-1343 | No     | `_health_probe_request_lock`, `_health_probe_request_deadlines`, `_relay_rx_time_clock_skew_secs`, `_relay_rx_time_clock_skew_lock`, `logger`                                                                                                               | `_health_probe_request_deadlines`, `_relay_rx_time_clock_skew_secs`                             | calls `_extract_packet_request_id`, `_coerce_int_id`, `_prune_health_probe_tracking`                                                                                                                               |
| `_set_probe_ack_flag_from_packet`                  | 1346-1386 | No     | none                                                                                                                                                                                                                                                        | none                                                                                            | calls `_coerce_int_id`                                                                                                                                                                                             |
| `_missing_local_node_ack_state_error`              | 1389-1393 | No     | none                                                                                                                                                                                                                                                        | none                                                                                            | none                                                                                                                                                                                                               |
| `_missing_received_nak_error`                      | 1396-1400 | No     | none                                                                                                                                                                                                                                                        | none                                                                                            | none                                                                                                                                                                                                               |
| `_failed_probe_ack_state_error`                    | 1403-1407 | No     | none                                                                                                                                                                                                                                                        | none                                                                                            | none                                                                                                                                                                                                               |
| `_missing_ack_state_error`                         | 1410-1414 | No     | none                                                                                                                                                                                                                                                        | none                                                                                            | none                                                                                                                                                                                                               |
| `_metadata_probe_ack_timeout_error`                | 1417-1423 | No     | none                                                                                                                                                                                                                                                        | none                                                                                            | none                                                                                                                                                                                                               |
| `_missing_probe_transport_error`                   | 1426-1430 | No     | none                                                                                                                                                                                                                                                        | none                                                                                            | none                                                                                                                                                                                                               |
| `_missing_probe_wait_error`                        | 1433-1437 | No     | none                                                                                                                                                                                                                                                        | none                                                                                            | none                                                                                                                                                                                                               |
| `_reset_probe_ack_state`                           | 1440-1454 | No     | none                                                                                                                                                                                                                                                        | none                                                                                            | none                                                                                                                                                                                                               |
| `_handle_probe_ack_callback`                       | 1457-1487 | No     | none                                                                                                                                                                                                                                                        | none                                                                                            | calls `_set_probe_ack_flag_from_packet`, error factories                                                                                                                                                           |
| `_wait_for_probe_ack`                              | 1490-1518 | No     | none                                                                                                                                                                                                                                                        | none                                                                                            | calls `_reset_probe_ack_state`, `_metadata_probe_ack_timeout_error`                                                                                                                                                |
| `_probe_device_connection`                         | 1521-1598 | No     | `logger`                                                                                                                                                                                                                                                    | none                                                                                            | calls `_reset_probe_ack_state`, `_track_health_probe_request_id`, `_handle_probe_ack_callback`, `_wait_for_probe_ack`, `_run_blocking_with_timeout`, `_missing_probe_transport_error`, `_missing_probe_wait_error` |
| `requires_continuous_health_monitor`               | 5541-5571 | Yes    | none                                                                                                                                                                                                                                                        | none                                                                                            | calls `_coerce_bool`                                                                                                                                                                                               |
| `check_connection`                                 | 5574-5749 | Yes    | `config`, `meshtastic_client`, `shutting_down`, `reconnecting`, `logger`                                                                                                                                                                                    | `meshtastic_client` (read), `config` (read)                                                     | calls `_submit_metadata_probe`, `_probe_device_connection`, `_coerce_positive_float`, `_coerce_bool`, `on_lost_meshtastic_connection`                                                                              |

### 6.8 metadata.py — Metadata fetch / firmware extraction

| Function                                  | Lines     | Public | Globals Read | Globals Written | Cross-Module Deps                                                                                                                                 |
| ----------------------------------------- | --------- | ------ | ------------ | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_get_name_safely`                        | 2522-2536 | No     | none         | none            | none                                                                                                                                              |
| `_get_name_or_none`                       | 2539-2555 | No     | none         | none            | none                                                                                                                                              |
| `_normalize_firmware_version`             | 2558-2574 | No     | none         | none            | none                                                                                                                                              |
| `_extract_firmware_version_from_metadata` | 2577-2602 | No     | none         | none            | calls `_normalize_firmware_version`                                                                                                               |
| `_extract_firmware_version_from_client`   | 2605-2628 | No     | none         | none            | calls `_extract_firmware_version_from_metadata`                                                                                                   |
| `_missing_metadata_probe_error`           | 2631-2637 | No     | none         | none            | none                                                                                                                                              |
| `_get_device_metadata`                    | 2640-2824 | No     | `logger`     | none            | calls `_extract_firmware_version_from_client`, `_submit_metadata_probe`, `_normalize_firmware_version`, `_extract_firmware_version_from_metadata` |

### 6.9 ble.py — BLE-specific connection/disconnect logic

| Function                                     | Lines     | Public | Globals Read                                                                                                                                                                                                                                                                                             | Globals Written                                                                                                   | Cross-Module Deps                                                                                                                                 |
| -------------------------------------------- | --------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_is_ble_duplicate_connect_suppressed_error` | 360-373   | No     | none                                                                                                                                                                                                                                                                                                     | none                                                                                                              | none                                                                                                                                              |
| `_reset_ble_connection_gate_state`           | 376-402   | No     | `_ble_gate_reset_callable`, `logger`                                                                                                                                                                                                                                                                     | none                                                                                                              | none                                                                                                                                              |
| `_clear_ble_future`                          | 1680-1699 | No     | `_ble_future`, `_ble_future_address`, `_ble_executor_lock`, `_ble_timeout_lock`, `_ble_timeout_counts`                                                                                                                                                                                                   | `_ble_future`, `_ble_future_address`, `_ble_future_started_at`, `_ble_future_timeout_secs`, `_ble_timeout_counts` | none                                                                                                                                              |
| `_schedule_ble_future_cleanup`               | 1702-1745 | No     | `_ble_future`, `_ble_executor_lock`, `_ble_future_watchdog_secs`, `logger`                                                                                                                                                                                                                               | none                                                                                                              | calls `_coerce_positive_float`, `_coerce_positive_int`, `_maybe_reset_ble_executor`                                                               |
| `_attach_late_ble_interface_disposer`        | 1748-1804 | No     | `meshtastic_iface_lock`, `meshtastic_iface`, `logger`                                                                                                                                                                                                                                                    | none                                                                                                              | calls `_disconnect_ble_interface`                                                                                                                 |
| `_record_ble_timeout`                        | 1807-1821 | No     | `_ble_timeout_lock`, `_ble_timeout_counts`                                                                                                                                                                                                                                                               | `_ble_timeout_counts`                                                                                             | none                                                                                                                                              |
| `_ensure_ble_worker_available`               | 1824-1881 | No     | `_ble_executor_lock`, `_ble_future`, `_ble_future_started_at`, `_ble_future_timeout_secs`, `_ble_future_address`, `_ble_future_stale_grace_secs`, `_ble_timeout_reset_threshold`, `logger`                                                                                                               | none                                                                                                              | calls `_coerce_nonnegative_float`, `_coerce_positive_int`, `_reset_ble_connection_gate_state`, `_record_ble_timeout`, `_maybe_reset_ble_executor` |
| `_maybe_reset_ble_executor`                  | 1884-2006 | No     | `_ble_executor`, `_ble_future`, `_ble_future_address`, `_ble_future_started_at`, `_ble_future_timeout_secs`, `_ble_executor_lock`, `_ble_executor_orphaned_workers_by_address`, `_ble_executor_degraded_addresses`, `_ble_timeout_reset_threshold`, `_ble_timeout_counts`, `_ble_timeout_lock`, `logger` | all of the above                                                                                                  | calls `_coerce_positive_int`                                                                                                                      |
| `_scan_for_ble_address`                      | 2009-2082 | No     | `BLE_AVAILABLE`, `logger`                                                                                                                                                                                                                                                                                | none                                                                                                              | none                                                                                                                                              |
| `_is_ble_discovery_error`                    | 2085-2130 | No     | none                                                                                                                                                                                                                                                                                                     | none                                                                                                              | none                                                                                                                                              |
| `_sanitize_ble_address`                      | 2827-2842 | No     | none                                                                                                                                                                                                                                                                                                     | none                                                                                                              | none                                                                                                                                              |
| `_validate_ble_connection_address`           | 2845-2908 | No     | `logger`                                                                                                                                                                                                                                                                                                 | none                                                                                                              | calls `_sanitize_ble_address`                                                                                                                     |
| `_disconnect_ble_by_address`                 | 2911-3134 | No     | `event_loop`, `logger`                                                                                                                                                                                                                                                                                   | none                                                                                                              | calls `_fire_and_forget`                                                                                                                          |
| `_disconnect_ble_interface`                  | 3137-3325 | No     | `logger`                                                                                                                                                                                                                                                                                                 | none                                                                                                              | calls `_run_blocking_with_timeout`, `_wait_for_result`                                                                                            |

### 6.10 connection.py — Connection lifecycle + runtime handlers

| Function                                   | Lines     | Public | Globals Read                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Globals Written                                                                                                                                                                                                                                                                                                                                                                                                                            | Cross-Module Deps                                                                              |
| ------------------------------------------ | --------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| `serial_port_exists`                       | 3452-3463 | Yes    | none                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | none                                                                                                                                                                                                                                                                                                                                                                                                                                       | none                                                                                           |
| `_get_connection_retry_wait_time`          | 3466-3487 | No     | none                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | none                                                                                                                                                                                                                                                                                                                                                                                                                                       | none                                                                                           |
| `_get_connect_time_probe_settings`         | 3490-3524 | No     | none                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | none                                                                                                                                                                                                                                                                                                                                                                                                                                       | calls `_coerce_bool`, `_coerce_positive_float`                                                 |
| `_schedule_connect_time_calibration_probe` | 3527-3579 | No     | `logger`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | none                                                                                                                                                                                                                                                                                                                                                                                                                                       | calls `_get_connect_time_probe_settings`, `_submit_metadata_probe`, `_probe_device_connection` |
| `_rollback_connect_attempt_state`          | 3582-3635 | No     | `meshtastic_client`, `meshtastic_iface`, `meshtastic_lock`, `_relay_active_client_id`, `_relay_startup_drain_deadline_monotonic_secs`, `_startup_packet_drain_applied`, `_relay_reconnect_prestart_bootstrap_deadline_monotonic_secs`, `_relay_rx_time_clock_skew_lock`, `logger`                                                                                                                                                                                                                                                                                                                                                                                                              | `meshtastic_client`, `meshtastic_iface`, `_relay_active_client_id`, `_relay_startup_drain_deadline_monotonic_secs`, `_startup_packet_drain_applied`, `_relay_reconnect_prestart_bootstrap_deadline_monotonic_secs`                                                                                                                                                                                                                         | calls `_disconnect_ble_interface`                                                              |
| `connect_meshtastic`                       | 3638-3709 | Yes    | `_connect_attempt_in_progress`, `_connect_attempt_condition`, `_connect_attempt_lock`, `shutting_down`, `config`, `logger`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | `_connect_attempt_in_progress`                                                                                                                                                                                                                                                                                                                                                                                                             | calls `_connect_meshtastic_impl`                                                               |
| `_connect_meshtastic_impl`                 | 3712-4701 | No     | `meshtastic_client`, `meshtastic_iface`, `shutting_down`, `reconnecting`, `config`, `meshtastic_lock`, `meshtastic_iface_lock`, `_ble_future`, `_ble_executor_lock`, `_ble_future_address`, `_ble_executor_degraded_addresses`, `_ble_future_started_at`, `_ble_future_timeout_secs`, `_relay_active_client_id`, `RELAY_START_TIME`, `_relay_connection_started_monotonic_secs`, `_relay_rx_time_clock_skew_secs`, `_relay_startup_drain_deadline_monotonic_secs`, `_relay_reconnect_prestart_bootstrap_deadline_monotonic_secs`, `_startup_packet_drain_applied`, `matrix_rooms`, `_health_probe_request_lock`, `_health_probe_request_deadlines`, `_relay_rx_time_clock_skew_lock`, `logger` | `meshtastic_client`, `meshtastic_iface`, `config`, `matrix_rooms`, `_ble_future`, `_ble_future_address`, `_ble_future_started_at`, `_ble_future_timeout_secs`, `_relay_active_client_id`, `RELAY_START_TIME`, `_relay_connection_started_monotonic_secs`, `_relay_rx_time_clock_skew_secs`, `_relay_startup_drain_deadline_monotonic_secs`, `_startup_packet_drain_applied`, `_relay_reconnect_prestart_bootstrap_deadline_monotonic_secs` | calls nearly everything in ble, executors, metadata, health, subscriptions, messaging          |
| `reconnect`                                | 4876-4948 | Yes    | `reconnecting`, `shutting_down`, `reconnect_task_future`, `config`, `logger`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | `reconnecting`, `reconnect_task_future`                                                                                                                                                                                                                                                                                                                                                                                                    | calls `connect_meshtastic`                                                                     |

### 6.11 events.py — Event handlers

| Function                        | Lines     | Public | Globals Read                                                                                                                                                                                                                                                                                                                                                                                                                  | Globals Written                                                                                                                                                                                                    | Cross-Module Deps                                                                                                                                                                                                                                                                                                                                |
| ------------------------------- | --------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `on_lost_meshtastic_connection` | 4704-4873 | Yes    | `meshtastic_client`, `_relay_active_client_id`, `meshtastic_iface`, `reconnecting`, `shutting_down`, `event_loop`, `meshtastic_lock`, `subscribed_to_connection_lost`, `_callbacks_tearing_down`, `_ble_future`, `_ble_future_address`, `_ble_executor_lock`, `_ble_executor`, `_ble_future_started_at`, `_ble_future_timeout_secs`, `_ble_executor_degraded_addresses`, `_ble_timeout_lock`, `_ble_timeout_counts`, `logger` | `reconnecting`, `meshtastic_client`, `_relay_active_client_id`, `meshtastic_iface`, `_ble_future`, `_ble_future_address`, `_ble_future_started_at`, `_ble_future_timeout_secs`, `_ble_executor`, `_reconnect_task` | calls `_disconnect_ble_interface`, `_attach_late_ble_interface_disposer`, `reset_executor_degraded_state`, `reconnect`                                                                                                                                                                                                                           |
| `on_meshtastic_message`         | 4951-5538 | Yes    | `meshtastic_client`, `_relay_active_client_id`, `shutting_down`, `config`, `_relay_startup_drain_deadline_monotonic_secs`, `_relay_rx_time_clock_skew_lock`, `RELAY_START_TIME`, `_relay_rx_time_clock_skew_secs`, `reconnecting`, `subscribed_to_messages`, `_callbacks_tearing_down`, `event_loop`, `matrix_rooms`, `logger`                                                                                                | `_relay_rx_time_clock_skew_secs`, `_relay_startup_drain_deadline_monotonic_secs`                                                                                                                                   | calls `_claim_health_probe_response_and_maybe_calibrate`, `_extract_packet_request_id`, `_get_portnum_name`, `_seed_connect_time_skew`, `_get_packet_details`, `_get_node_display_name`, `_normalize_room_channel`, `_get_name_safely`, `_get_name_or_none`, `_fire_and_forget`, `_run_meshtastic_plugins`, lazy: `matrix_utils.*`, `db_utils.*` |

---

## 7. Import/Export Compatibility Strategy

### 7.1 The Facade Pattern

`src/mmrelay/meshtastic_utils.py` transforms from a monolith into a thin re-export layer:

```python
# src/mmrelay/meshtastic_utils.py  (after decomposition)
"""Facade module — re-exports from mmrelay.meshtastic submodules.

This module retains all module-level globals for backward compatibility.
External code and tests continue to import from and patch this module.
"""

# --- All module-level globals remain here ---
config = None
matrix_rooms = []
logger = ...
MESHTASTIC_TEXT_ENCODING = "utf-8"
RELAY_START_TIME = ...
# ... (all 50+ globals stay defined here) ...

# --- Re-exports from submodules ---
from mmrelay.meshtastic.async_utils import (
    _coerce_nonnegative_float,
    _coerce_positive_int,
    _coerce_positive_int_id,
    _coerce_int_id,
    _coerce_positive_float,
    _coerce_bool,
    _submit_coro,
    _fire_and_forget,
    _make_awaitable,
    _run_blocking_with_timeout,
    _wait_for_result,
    _wait_for_future_result_with_shutdown,
)

from mmrelay.meshtastic.subscriptions import (
    ensure_meshtastic_callbacks_subscribed,
    unsubscribe_meshtastic_callbacks,
)

# ... etc for all other submodules ...
```

### 7.2 Backward Compatibility Guarantees

| Import Pattern                                                         | Status | Reason                                      |
| ---------------------------------------------------------------------- | ------ | ------------------------------------------- |
| `from mmrelay.meshtastic_utils import connect_meshtastic`              | Works  | Facade re-exports                           |
| `from mmrelay.meshtastic_utils import logger`                          | Works  | Global stays in facade                      |
| `import mmrelay.meshtastic_utils as mu; mu.event_loop = loop`          | Works  | Global stays in facade                      |
| `from mmrelay.meshtastic_utils import meshtastic_client, reconnecting` | Works  | Globals stay in facade                      |
| `set_config(meshtastic_utils, config)`                                 | Works  | `config.py:1238` matches "meshtastic_utils" |
| `@patch("mmrelay.meshtastic_utils._submit_coro")`                      | Works  | Facade re-exports the name                  |
| `mu.shutdown_shared_executors()`                                       | Works  | Re-exported function                        |
| `meshtastic_utils.meshtastic_client = None`                            | Works  | Attribute on facade module                  |

### 7.3 Submodule State Access Pattern

Submodules that need to read/write shared globals use:

```python
# src/mmrelay/meshtastic/events.py
import mmrelay.meshtastic_utils as _facade

def on_meshtastic_message(packet, interface):
    client = _facade.meshtastic_client
    if _facade.shutting_down:
        return
    _facade._relay_rx_time_clock_skew_secs = ...
```

This ensures:

- All state lives in one place (the facade)
- `set_config(module)` still works
- Test patches on `mmrelay.meshtastic_utils.*` still work
- No circular import issues (submodules import the facade, not each other)

---

## 8. Rollout Phases

### Phase 1: Scaffold package + move lowest-risk helpers (async_utils, plugins, messaging)

**Risk**: Low. These are mostly pure helpers with minimal global access.

**Files to create**:

- `src/mmrelay/meshtastic/__init__.py` (empty or minimal)
- `src/mmrelay/meshtastic/async_utils.py`
- `src/mmrelay/meshtastic/plugins.py`
- `src/mmrelay/meshtastic/messaging.py`

**Functions to move**:

async_utils.py (12 functions):

- `_coerce_nonnegative_float` (lines 330-342)
- `_coerce_positive_int` (lines 345-357)
- `_coerce_positive_int_id` (lines 856-866)
- `_coerce_int_id` (lines 869-878)
- `_coerce_positive_float` (lines 881-903)
- `_coerce_bool` (lines 906-935)
- `_submit_coro` (lines 1601-1677)
- `_fire_and_forget` (lines 2133-2173)
- `_make_awaitable` (lines 2176-2194)
- `_run_blocking_with_timeout` (lines 2197-2249)
- `_wait_for_result` (lines 2252-2340)
- `_wait_for_future_result_with_shutdown` (lines 2343-2383)

plugins.py (3 functions):

- `_resolve_plugin_timeout` (lines 2386-2427)
- `_resolve_plugin_result` (lines 2430-2459)
- `_run_meshtastic_plugins` (lines 2462-2519)

messaging.py (6 functions + 1 alias):

- `_normalize_room_channel` (lines 405-433)
- `_get_packet_details` (lines 3328-3379)
- `_get_portnum_name` (lines 3382-3408)
- `_get_node_display_name` (lines 3411-3449)
- `send_text_reply` (lines 5752-5811)
- `sendTextReply` (line 5814, alias)

**State access**: async_utils reads `event_loop`, `shutting_down`, `logger`. plugins reads `logger`. messaging reads `logger`, `MESHTASTIC_TEXT_ENCODING`.

**Verification**: `python -m pytest -v --timeout=60`

### Phase 2: Move node_refresh + subscriptions

**Risk**: Low-medium. Relatively isolated.

**Files to create**:

- `src/mmrelay/meshtastic/node_refresh.py`
- `src/mmrelay/meshtastic/subscriptions.py`

**Functions to move**:

node_refresh.py (4 functions):

- `_parse_refresh_interval_seconds` (lines 938-954)
- `get_nodedb_refresh_interval_seconds` (lines 957-991)
- `_snapshot_node_name_rows` (lines 994-1031)
- `refresh_node_name_tables` (lines 1034-1120)

subscriptions.py (2 functions):

- `ensure_meshtastic_callbacks_subscribed` (lines 228-243)
- `unsubscribe_meshtastic_callbacks` (lines 246-286)

**State access**: node_refresh reads `reconnecting`, `meshtastic_client`, `meshtastic_lock`, `config`, `logger`. subscriptions reads/writes subscription flags.

**Verification**: `python -m pytest -v --timeout=60`

### Phase 3: Move executor infrastructure

**Risk**: Medium. Complex machinery but self-contained.

**Files to create**:

- `src/mmrelay/meshtastic/executors.py`

**Functions to move** (8 functions):

- `_shutdown_shared_executors` (lines 436-502)
- `shutdown_shared_executors` (lines 505-511)
- `reset_executor_degraded_state` (lines 514-620)
- `_get_ble_executor` (lines 626-640)
- `_get_metadata_executor` (lines 643-657)
- `_clear_metadata_future_if_current` (lines 660-668)
- `_reset_metadata_executor_for_stale_probe` (lines 671-751)
- `_schedule_metadata_future_cleanup` (lines 753-791)
- `_submit_metadata_probe` (lines 794-853)

Also move exception classes:

- `BleExecutorDegradedError` (lines 133-136)
- `MetadataExecutorDegradedError` (lines 320-327)

**State access**: All executor-related globals (see Section 12).

**Verification**: `python -m pytest -v --timeout=60`

### Phase 4: Move health/probe logic

**Risk**: Medium-high. ACK callbacks and startup skew state.

**Files to create**:

- `src/mmrelay/meshtastic/health.py`

**Functions to move** (18 functions):

- `_extract_packet_request_id` (lines 1123-1144)
- `_prune_health_probe_tracking` (lines 1147-1158)
- `_track_health_probe_request_id` (lines 1161-1179)
- `_seed_connect_time_skew` (lines 1182-1267)
- `_is_health_probe_response_packet` (lines 1270-1288)
- `_claim_health_probe_response_and_maybe_calibrate` (lines 1291-1343)
- `_set_probe_ack_flag_from_packet` (lines 1346-1386)
- `_missing_local_node_ack_state_error` (lines 1389-1393)
- `_missing_received_nak_error` (lines 1396-1400)
- `_failed_probe_ack_state_error` (lines 1403-1407)
- `_missing_ack_state_error` (lines 1410-1414)
- `_metadata_probe_ack_timeout_error` (lines 1417-1423)
- `_missing_probe_transport_error` (lines 1426-1430)
- `_missing_probe_wait_error` (lines 1433-1437)
- `_reset_probe_ack_state` (lines 1440-1454)
- `_handle_probe_ack_callback` (lines 1457-1487)
- `_wait_for_probe_ack` (lines 1490-1518)
- `_probe_device_connection` (lines 1521-1598)
- `requires_continuous_health_monitor` (lines 5541-5571)
- `check_connection` (lines 5574-5749)

**Verification**: `python -m pytest -v --timeout=60`

### Phase 5: Move BLE internals

**Risk**: High. Executor state and connection coupling.

**Files to create**:

- `src/mmrelay/meshtastic/ble.py`
- `src/mmrelay/meshtastic/metadata.py`

**Functions to move** (ble.py — 14 functions):

- `_is_ble_duplicate_connect_suppressed_error` (lines 360-373)
- `_reset_ble_connection_gate_state` (lines 376-402)
- `_clear_ble_future` (lines 1680-1699)
- `_schedule_ble_future_cleanup` (lines 1702-1745)
- `_attach_late_ble_interface_disposer` (lines 1748-1804)
- `_record_ble_timeout` (lines 1807-1821)
- `_ensure_ble_worker_available` (lines 1824-1881)
- `_maybe_reset_ble_executor` (lines 1884-2006)
- `_scan_for_ble_address` (lines 2009-2082)
- `_is_ble_discovery_error` (lines 2085-2130)
- `_sanitize_ble_address` (lines 2827-2842)
- `_validate_ble_connection_address` (lines 2845-2908)
- `_disconnect_ble_by_address` (lines 2911-3134)
- `_disconnect_ble_interface` (lines 3137-3325)

**Functions to move** (metadata.py — 7 functions):

- `_get_name_safely` (lines 2522-2536)
- `_get_name_or_none` (lines 2539-2555)
- `_normalize_firmware_version` (lines 2558-2574)
- `_extract_firmware_version_from_metadata` (lines 2577-2602)
- `_extract_firmware_version_from_client` (lines 2605-2628)
- `_missing_metadata_probe_error` (lines 2631-2637)
- `_get_device_metadata` (lines 2640-2824)

**Verification**: `python -m pytest -v --timeout=60`

### Phase 6: Move connection + event lifecycle (LAST)

**Risk**: Highest. `_connect_meshtastic_impl` alone is ~990 lines.

**Files to create**:

- `src/mmrelay/meshtastic/connection.py`
- `src/mmrelay/meshtastic/events.py`

**Functions to move** (connection.py — 7 functions):

- `serial_port_exists` (lines 3452-3463)
- `_get_connection_retry_wait_time` (lines 3466-3487)
- `_get_connect_time_probe_settings` (lines 3490-3524)
- `_schedule_connect_time_calibration_probe` (lines 3527-3579)
- `_rollback_connect_attempt_state` (lines 3582-3635)
- `connect_meshtastic` (lines 3638-3709)
- `_connect_meshtastic_impl` (lines 3712-4701)
- `reconnect` (lines 4876-4948)

**Functions to move** (events.py — 2 functions):

- `on_lost_meshtastic_connection` (lines 4704-4873)
- `on_meshtastic_message` (lines 4951-5538)

**Verification**: `python -m pytest -v --timeout=60`

### Phase 7: Cleanup pass (only after all phases green)

- Remove dead imports from facade
- Tighten submodule interfaces
- Consider test path migration
- NOT part of the decomposition PRs

---

## 9. Non-Negotiable Refactor Rules

1. **No public import breakage in any phase** — every existing `from mmrelay.meshtastic_utils import X` must continue to work
2. **No state-owner swap in the same refactor** — globals stay in the facade module
3. **No broad renaming during moves** — function names, global names, class names stay the same
4. **Each phase moves one concern** — don't batch BLE + health + connection in one PR
5. **Every moved module must be covered by passing tests** — run full suite after each phase
6. **Submodules NEVER own runtime state in phases 1-6** — all globals in facade
7. **Submodules use `import mmrelay.meshtastic_utils as facade` for shared state** — not direct global declarations

---

## 10. Known Risks and Mitigations

| #   | Risk                                                                                | Mitigation                                                                                                |
| --- | ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| 1   | **Facade state drift** — submodules accidentally define their own copies of globals | Submodules never own state; always go through facade. Lint rule: no `global X` in submodules.             |
| 2   | **Circular imports** — submodules importing each other                              | Helper modules don't import each other. Use facade for shared state. Lazy imports for cross-package deps. |
| 3   | **Test patch breakage** — 1300+ test references to `mmrelay.meshtastic_utils.*`     | Facade re-exports all moved names. Tests continue patching `mmrelay.meshtastic_utils.*`.                  |
| 4   | **Config attachment** — `config.py:1238` hardcodes `"meshtastic_utils"`             | Config string unchanged. Facade keeps `config` and `matrix_rooms` attributes.                             |
| 5   | **Reconnect/BLE regressions** — complex state machine interactions                  | Dedicated phases with test runs. No phase mixes BLE + health + connection.                                |
| 6   | **`_connect_meshtastic_impl` complexity** — ~990 lines, reads/writes 20+ globals    | Moved LAST, after all helper layers are stable. No refactoring of internals.                              |
| 7   | **`main.py` attribute writes** — 7+ direct writes to facade globals                 | All globals stay in facade. `main.py` imports unchanged.                                                  |
| 8   | **`atexit.register(shutdown_shared_executors)`** — module-level side effect         | Keep registration in facade. Executor functions imported from submodule.                                  |
| 9   | **`BLE_AVAILABLE` conditional import** — module-level detection                     | Keep in facade. Submodules reference `facade.BLE_AVAILABLE`.                                              |
| 10  | **`_ble_gate_reset_callable` detection** — module-level import                      | Keep in facade. `ble.py` references `facade._ble_gate_reset_callable`.                                    |

---

## 11. Agent Work Packets

### Phase 1 Agent Prompt

````
## Task: Create async_utils.py, plugins.py, and messaging.py submodules

### Files to create:
1. `src/mmrelay/meshtastic/__init__.py` — empty file
2. `src/mmrelay/meshtastic/async_utils.py`
3. `src/mmrelay/meshtastic/plugins.py`
4. `src/mmrelay/meshtastic/messaging.py`

### Files to modify:
- `src/mmrelay/meshtastic_utils.py` — add re-exports at top, remove moved function bodies (replace with imports from submodules)

### Functions to move to async_utils.py (copy exact implementations):
- _coerce_nonnegative_float (lines 330-342)
- _coerce_positive_int (lines 345-357)
- _coerce_positive_int_id (lines 856-866)
- _coerce_int_id (lines 869-878)
- _coerce_positive_float (lines 881-903)
- _coerce_bool (lines 906-935)
- _submit_coro (lines 1601-1677)
- _fire_and_forget (lines 2133-2173)
- _make_awaitable (lines 2176-2194)
- _run_blocking_with_timeout (lines 2197-2249)
- _wait_for_result (lines 2252-2340)
- _wait_for_future_result_with_shutdown (lines 2343-2383)

State access pattern: Use `import mmrelay.meshtastic_utils as _facade` at top. Access `_facade.event_loop`, `_facade.logger`, `_facade.shutting_down` as needed.

### Functions to move to plugins.py:
- _resolve_plugin_timeout (lines 2386-2427)
- _resolve_plugin_result (lines 2430-2459)
- _run_meshtastic_plugins (lines 2462-2519)

State: `_facade.logger`. Keep lazy import of `mmrelay.plugin_loader.load_plugins`.

### Functions to move to messaging.py:
- _normalize_room_channel (lines 405-433)
- _get_packet_details (lines 3328-3379)
- _get_portnum_name (lines 3382-3408)
- _get_node_display_name (lines 3411-3449)
- send_text_reply (lines 5752-5811)
- sendTextReply = send_text_reply (line 5814)

State: `_facade.logger`, `_facade.MESHTASTIC_TEXT_ENCODING`. Keep lazy import of db_utils in _get_node_display_name.

### Facade wiring:
In meshtastic_utils.py, add at the top (after globals):
```python
from mmrelay.meshtastic.async_utils import *
from mmrelay.meshtastic.plugins import *
from mmrelay.meshtastic.messaging import *
````

Use explicit **all** in each submodule to list exported names.
Remove the original function bodies from meshtastic_utils.py.

### Verification:

Run: `python -m pytest -v --timeout=60`

### DO NOT:

- Stash or reset git state
- Run linting
- Modify test files
- Change function signatures
- Move any globals out of meshtastic_utils.py

```

### Phase 2 Agent Prompt

```

## Task: Create node_refresh.py and subscriptions.py submodules

### Files to create:

1. `src/mmrelay/meshtastic/node_refresh.py`
2. `src/mmrelay/meshtastic/subscriptions.py`

### Files to modify:

- `src/mmrelay/meshtastic_utils.py` — add re-exports, remove moved function bodies

### Functions to move to node_refresh.py:

- \_parse_refresh_interval_seconds (lines 938-954)
- get_nodedb_refresh_interval_seconds (lines 957-991)
- \_snapshot_node_name_rows (lines 994-1031)
- refresh_node_name_tables (lines 1034-1120)

State: `import mmrelay.meshtastic_utils as _facade`. Access \_facade.config, \_facade.meshtastic_client, \_facade.meshtastic_lock, \_facade.reconnecting, \_facade.logger.

### Functions to move to subscriptions.py:

- ensure_meshtastic_callbacks_subscribed (lines 228-243)
- unsubscribe_meshtastic_callbacks (lines 246-286)

State: \_facade.meshtastic_sub_lock, \_facade.subscribed_to_messages, \_facade.subscribed_to_connection_lost, \_facade.\_callbacks_tearing_down.

IMPORTANT: These functions reference on_meshtastic_message and on_lost_meshtastic_connection by name for pub.subscribe/unsubscribe. Since those are in events.py (not yet moved), use lazy import or reference them via \_facade.

### Facade wiring:

Add re-exports for all moved names. Remove originals.

### Verification:

Run: `python -m pytest -v --timeout=60`

### DO NOT:

- Stash or reset git state
- Run linting
- Modify test files

```

### Phase 3 Agent Prompt

```

## Task: Create executors.py submodule

### Files to create:

1. `src/mmrelay/meshtastic/executors.py`

### Files to modify:

- `src/mmrelay/meshtastic_utils.py`

### Classes to move:

- BleExecutorDegradedError (lines 133-136)
- MetadataExecutorDegradedError (lines 320-327)

### Functions to move:

- \_shutdown_shared_executors (lines 436-502)
- shutdown_shared_executors (lines 505-511)
- reset_executor_degraded_state (lines 514-620)
- \_get_ble_executor (lines 626-640)
- \_get_metadata_executor (lines 643-657)
- \_clear_metadata_future_if_current (lines 660-668)
- \_reset_metadata_executor_for_stale_probe (lines 671-751)
- \_schedule_metadata_future_cleanup (lines 753-791)
- \_submit_metadata_probe (lines 794-853)

State: All executor-related globals stay in facade. Access via \_facade.
IMPORTANT: The atexit.register(shutdown_shared_executors) call (line 623) stays in the facade.

### Verification:

Run: `python -m pytest -v --timeout=60`

### DO NOT:

- Stash or reset git state
- Run linting
- Modify test files

```

### Phase 4 Agent Prompt

```

## Task: Create health.py submodule

### Files to create:

1. `src/mmrelay/meshtastic/health.py`

### Files to modify:

- `src/mmrelay/meshtastic_utils.py`

### Functions to move (18 functions):

- \_extract_packet_request_id (lines 1123-1144)
- \_prune_health_probe_tracking (lines 1147-1158)
- \_track_health_probe_request_id (lines 1161-1179)
- \_seed_connect_time_skew (lines 1182-1267)
- \_is_health_probe_response_packet (lines 1270-1288)
- \_claim_health_probe_response_and_maybe_calibrate (lines 1291-1343)
- \_set_probe_ack_flag_from_packet (lines 1346-1386)
- [all error factory functions lines 1389-1437]
- \_reset_probe_ack_state (lines 1440-1454)
- \_handle_probe_ack_callback (lines 1457-1487)
- \_wait_for_probe_ack (lines 1490-1518)
- \_probe_device_connection (lines 1521-1598)
- requires_continuous_health_monitor (lines 5541-5571)
- check_connection (lines 5574-5749)

State: Access all health globals via \_facade. check_connection calls on_lost_meshtastic_connection (in events.py, not yet moved) — reference via \_facade.

Cross-deps: health calls into executors (_submit_metadata_probe) and async_utils (\_coerce_\*). Import from sibling submodules or facade.

### Verification:

Run: `python -m pytest -v --timeout=60`

### DO NOT:

- Stash or reset git state
- Run linting
- Modify test files

```

### Phase 5 Agent Prompt

```

## Task: Create ble.py and metadata.py submodules

### Files to create:

1. `src/mmrelay/meshtastic/ble.py`
2. `src/mmrelay/meshtastic/metadata.py`

### Files to modify:

- `src/mmrelay/meshtastic_utils.py`

### ble.py functions (14):

- \_is_ble_duplicate_connect_suppressed_error (lines 360-373)
- \_reset_ble_connection_gate_state (lines 376-402)
- \_clear_ble_future (lines 1680-1699)
- \_schedule_ble_future_cleanup (lines 1702-1745)
- \_attach_late_ble_interface_disposer (lines 1748-1804)
- \_record_ble_timeout (lines 1807-1821)
- \_ensure_ble_worker_available (lines 1824-1881)
- \_maybe_reset_ble_executor (lines 1884-2006)
- \_scan_for_ble_address (lines 2009-2082)
- \_is_ble_discovery_error (lines 2085-2130)
- \_sanitize_ble_address (lines 2827-2842)
- \_validate_ble_connection_address (lines 2845-2908)
- \_disconnect_ble_by_address (lines 2911-3134)
- \_disconnect_ble_interface (lines 3137-3325)

State: All BLE globals via \_facade. References facade.BLE_AVAILABLE, facade.\_ble_gate_reset_callable, facade.event_loop, facade.meshtastic_iface, facade.meshtastic_iface_lock, facade.logger.

Cross-deps: ble.py calls _fire_and_forget, \_run_blocking_with_timeout, \_wait_for_result (from async_utils), \_coerce_\* (from async_utils). Import from sibling submodule.

### metadata.py functions (7):

- \_get_name_safely (lines 2522-2536)
- \_get_name_or_none (lines 2539-2555)
- \_normalize_firmware_version (lines 2558-2574)
- \_extract_firmware_version_from_metadata (lines 2577-2602)
- \_extract_firmware_version_from_client (lines 2605-2628)
- \_missing_metadata_probe_error (lines 2631-2637)
- \_get_device_metadata (lines 2640-2824)

State: \_facade.logger. Calls \_submit_metadata_probe (from executors submodule).

### Verification:

Run: `python -m pytest -v --timeout=60`

### DO NOT:

- Stash or reset git state
- Run linting
- Modify test files

```

### Phase 6 Agent Prompt

```

## Task: Create connection.py and events.py submodules (FINAL MOVE)

### Files to create:

1. `src/mmrelay/meshtastic/connection.py`
2. `src/mmrelay/meshtastic/events.py`

### Files to modify:

- `src/mmrelay/meshtastic_utils.py` — after this phase, should be a thin facade with only globals and re-exports

### connection.py functions (8):

- serial_port_exists (lines 3452-3463)
- \_get_connection_retry_wait_time (lines 3466-3487)
- \_get_connect_time_probe_settings (lines 3490-3524)
- \_schedule_connect_time_calibration_probe (lines 3527-3579)
- \_rollback_connect_attempt_state (lines 3582-3635)
- connect_meshtastic (lines 3638-3709)
- \_connect_meshtastic_impl (lines 3712-4701) ← ~990 lines, move as-is
- reconnect (lines 4876-4948)

State: Nearly all globals accessed via \_facade. This is the most state-heavy module.

Cross-deps: Calls into ble.py, executors.py, metadata.py, health.py, subscriptions.py, messaging.py. Import from sibling submodules or facade.

### events.py functions (2):

- on_lost_meshtastic_connection (lines 4704-4873)
- on_meshtastic_message (lines 4951-5538) ← ~590 lines, move as-is

State: Many globals via \_facade.

Cross-deps: Calls many helpers from other submodules. Import from siblings or facade.

### After this phase:

The facade `meshtastic_utils.py` should contain ONLY:

1. Import statements
2. Module-level globals (all 50+)
3. Conditional BLE import setup
4. atexit.register(shutdown_shared_executors)
5. Re-exports from all submodules
6. The `if __name__ == "__main__"` block (line 5817-5824)

### Verification:

Run: `python -m pytest -v --timeout=60`

### DO NOT:

- Stash or reset git state
- Run linting
- Modify test files
- Refactor \_connect_meshtastic_impl internals

```

---

## 12. Global Variable Ownership Map

| Variable | Facade Line | Read By Submodule | Written By Submodule | External Access |
|----------|------------|-------------------|---------------------|-----------------|
| `BLE_AVAILABLE` | 120 | ble, health | — | — |
| `BleakDBusError` | 127 | ble | — | — |
| `BleakError` | 129 | ble | — | — |
| `_ble_gate_reset_callable` | 152 | ble | — | — |
| `_ble_gating_module` | 153 | — | — | — |
| `config` | 140 | node_refresh, health, events, connection | connection, facade init | main.py, config.py, tests |
| `matrix_rooms` | 145 | events, connection | connection | main.py, config.py, tests |
| `logger` | 148 | all submodules | — | main.py (as meshtastic_logger) |
| `MESHTASTIC_TEXT_ENCODING` | 168 | messaging | — | — |
| `RELAY_START_TIME` | 171 | health, events, connection | connection | conftest.py |
| `_relay_connection_started_monotonic_secs` | 174 | health, connection | connection | conftest.py |
| `_relay_rx_time_clock_skew_secs` | 176 | health, events, connection | health, events, connection | conftest.py |
| `_relay_rx_time_clock_skew_lock` | 177 | health, events, connection | — | — |
| `_relay_startup_drain_deadline_monotonic_secs` | 182 | events, connection | connection | conftest.py |
| `_startup_packet_drain_applied` | 184 | connection | connection | conftest.py |
| `_relay_reconnect_prestart_bootstrap_deadline_monotonic_secs` | 187 | health, connection | connection | conftest.py |
| `meshtastic_client` | 191 | node_refresh, health, connection, events, message_queue | connection, events | main.py, conftest.py, message_queue.py |
| `_relay_active_client_id` | 193 | events, connection | connection | conftest.py |
| `meshtastic_iface` | 194 | connection, ble, events | connection, events | main.py, conftest.py |
| `event_loop` | 195 | async_utils, ble, events, connection | main.py | main.py, conftest.py |
| `meshtastic_lock` | 197 | node_refresh, connection | — | — |
| `_connect_attempt_lock` | 202 | connection | — | conftest.py |
| `_connect_attempt_condition` | 203 | connection | — | conftest.py |
| `_connect_attempt_in_progress` | 204 | connection | connection | conftest.py |
| `_CONNECT_ATTEMPT_WAIT_POLL_SECS` | 205 | connection | — | — |
| `_CONNECT_ATTEMPT_WAIT_MAX_SECS` | 206 | connection | — | — |
| `_CONNECT_ATTEMPT_BLE_WAIT_MAX_SECS` | 207-209 | connection | — | — |
| `reconnecting` | 211 | node_refresh, health, events, connection | connection, events | main.py, conftest.py, message_queue.py |
| `shutting_down` | 212 | async_utils, connection, events, health | main.py | main.py, conftest.py |
| `reconnect_task` | 214 | events | events, connection | main.py, conftest.py |
| `reconnect_task_future` | 215 | connection | connection, events | main.py, conftest.py |
| `meshtastic_iface_lock` | 216-218 | connection, ble | — | — |
| `meshtastic_sub_lock` | 221 | subscriptions | — | — |
| `subscribed_to_messages` | 222 | subscriptions, events | subscriptions | conftest.py |
| `subscribed_to_connection_lost` | 223 | subscriptions, events | subscriptions | conftest.py |
| `_callbacks_tearing_down` | 225 | subscriptions, events | subscriptions | conftest.py |
| `_metadata_executor` | 291 | executors | executors | conftest.py |
| `_metadata_future` | 292 | executors, connection | executors, connection | conftest.py |
| `_metadata_future_started_at` | 293 | executors, connection | executors | conftest.py |
| `_metadata_future_lock` | 294 | executors | — | — |
| `_metadata_executor_orphaned_workers` | 295 | executors | executors | conftest.py |
| `_health_probe_request_deadlines` | 296 | health, executors, connection | health, executors, connection | conftest.py |
| `_health_probe_request_lock` | 297 | health | — | — |
| `_ble_executor` | 301 | ble, executors | ble, executors, events | conftest.py |
| `_ble_executor_lock` | 302 | ble, executors | — | — |
| `_ble_future` | 303 | ble, connection, events | ble, connection, events | conftest.py |
| `_ble_future_address` | 304 | ble, events | ble, connection, events | conftest.py |
| `_ble_future_started_at` | 305 | ble, events | ble, connection, events | conftest.py |
| `_ble_future_timeout_secs` | 306 | ble, events | ble, connection, events | conftest.py |
| `_ble_timeout_counts` | 307 | ble, executors | ble, executors | conftest.py |
| `_ble_executor_orphaned_workers_by_address` | 308 | ble, executors | ble, executors | conftest.py |
| `_ble_timeout_lock` | 309 | ble | — | — |
| `_ble_future_watchdog_secs` | 310 | ble | — | conftest.py |
| `_ble_timeout_reset_threshold` | 311 | ble | — | conftest.py |
| `_ble_scan_timeout_secs` | 312 | connection (via ble) | — | conftest.py |
| `_ble_future_stale_grace_secs` | 313 | ble | — | conftest.py |
| `_ble_interface_create_timeout_secs` | 314 | connection (via ble) | — | conftest.py |
| `_ble_executor_degraded_addresses` | 316 | ble, executors, connection | ble, executors | conftest.py |
| `_metadata_executor_degraded` | 317 | executors | executors | conftest.py |

---

## Appendix A: External Consumers of meshtastic_utils

### main.py (direct attribute writes)
- `meshtastic_utils.event_loop = loop` (line 385)
- `meshtastic_utils.shutting_down = True` (line 467)
- `meshtastic_utils.meshtastic_client = ...` (line 708, 632)
- `meshtastic_utils.meshtastic_iface = None` (line 599, 633)
- `meshtastic_utils.reconnect_task = None` (line 667)
- `meshtastic_utils.reconnect_task_future = None` (line 678)
- `meshtastic_utils.unsubscribe_meshtastic_callbacks()` (line 680)
- `meshtastic_utils._disconnect_ble_interface(...)` (line 595)
- `meshtastic_utils._run_blocking_with_timeout(...)` (line 605)
- `meshtastic_utils.shutdown_shared_executors()` (line 852, 1166)
- `meshtastic_utils.check_connection` (line 88, 767)
- `meshtastic_utils.requires_continuous_health_monitor(config)` (line 301)
- `meshtastic_utils.get_nodedb_refresh_interval_seconds(config)` (line 1006)
- `meshtastic_utils.refresh_node_name_tables(...)` (line 871)
- `meshtastic_utils.connect_meshtastic` (line 76)
- `meshtastic_utils.logger` (line 77)
- `set_config(meshtastic_utils, config)` (line 1225)

### config.py (module name matching)
- Line 1238: `elif module_name == "meshtastic_utils":`
- Lines 1221-1222: sets `module.matrix_rooms`, `module.config`

### message_queue.py (lazy import)
- Line 859: `from mmrelay.meshtastic_utils import meshtastic_client, reconnecting`

### matrix_utils.py (lazy imports)
- Line 153: `from mmrelay.meshtastic_utils import connect_meshtastic, send_text_reply`
- Line 1346: `from mmrelay.meshtastic_utils import logger as meshtastic_logger`
- Line 1394, 3950, 4297: same logger import

### Plugin files (lazy imports)
- `base_plugin.py:574,615`: `from mmrelay.meshtastic_utils import connect_meshtastic`
- `weather_plugin.py:530,667`: `from mmrelay.meshtastic_utils import connect_meshtastic`
- `ping_plugin.py:115`: `from mmrelay.meshtastic_utils import connect_meshtastic`
- `mesh_relay_plugin.py:311`: `from mmrelay.meshtastic_utils import connect_meshtastic`
- `telemetry_plugin.py:29`: `from mmrelay.meshtastic_utils import _get_portnum_name`
- `nodes_plugin.py:104`: `from mmrelay.meshtastic_utils import connect_meshtastic`
- `health_plugin.py:56`: `from mmrelay.meshtastic_utils import connect_meshtastic`
- `map_plugin.py:110`: `from mmrelay.meshtastic_utils import connect_meshtastic`
- `drop_plugin.py:16`: `from mmrelay.meshtastic_utils import connect_meshtastic`

### Test files (imports and patches)
Over 1300 references to `mmrelay.meshtastic_utils.*` across test files. Key test files:
- `tests/conftest.py`: `reset_meshtastic_globals` fixture, `meshtastic_loop_safety`, `mock_submit_coro`
- `tests/test_meshtastic_utils.py`: 500+ patch references
- `tests/test_meshtastic_utils_edge_cases.py`
- `tests/test_meshtastic_utils_health.py`
- `tests/test_meshtastic_utils_connect_paths.py`
- `tests/test_meshtastic_utils_reconnect.py`
- `tests/test_meshtastic_utils_reconnect_paths.py`
- `tests/test_meshtastic_utils_reconnect_bootstrap_coverage.py`
- `tests/test_meshtastic_utils_skew_drain_coverage.py`
- `tests/test_meshtastic_utils_client_cleanup_coverage.py`
- `tests/test_meshtastic_utils_callback_lifecycle.py`
- `tests/test_meshtastic_utils_async_helpers.py`
- `tests/test_meshtastic_utils_coverage.py`
- `tests/test_meshtastic_utils_message_paths.py`
- `tests/test_meshtastic_utils_event_guards_coverage.py`
- `tests/test_integration_scenarios.py`
- `tests/test_performance_stress.py`
- `tests/test_error_boundaries.py`
- `tests/test_main.py`
```
