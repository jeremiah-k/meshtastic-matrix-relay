# MMRelay implicit-state-machine audit — November 6, 2026

**Repository:** `meshtastic-matrix-relay` (`mmrelay`), `main` at
`1bc2ea53698d96a099e7ebd6d152fa8101dceda6` (v1.4.1).
**Attribution:** Original authors Geoff Whittington and Jeremiah K.; maintainer
Jeremiah K. These identities and existing history remain unchanged.

**Dependency checkouts:** `mtjk/develop` at
`5a2d0d8020d0bebb8723f333840888590f6c47e7` (v2.7.11.post12) and
`mindroom-nio/main` at `b256773daf74d354cfd4c46515b02bb1f6bb0e58`
(v1.3.0). The MMRelay lock/pins select this `mtjk`, and select `mindroom-nio`
1.3.0 on Python >=3.12; **Python 3.11 still selects mindroom-nio 0.40.0**.
The latter older revision was not supplied. Do not assume equivalence between
those two Matrix dependency branches.

## Scope and evaluation method

Inspected lifecycle ownership, shared mutable state, transitions, cancellation,
retry, and cross-thread handoffs under `src/mmrelay`, with focused source tracing
in the supplied dependency repositories. Identified machines are grouped by
_resource being owned_, not by the number of booleans or locks. A state machine
is problematic when multiple flags permit contradictory combinations, a late
callback can act on a later owner's resources, a timed-out worker is treated as
stopped, or cancellation leaves ownership ambiguous.

**Dispositions:** **Fixed** = a companion lifecycle PR modifies the corresponding transition;
**Preserved** = an existing ownership mechanism already enforces the relevant
invariant, and rewriting it would risk compatibility; **Monitor** = an
operationally meaningful edge not conclusively validated without full
integration dependencies and/or real devices. "Preserved" does **not** mean
exhaustively proved by hardware or the full test suite.

## High-risk machines — transitions and fixes

### 1. Outbound Meshtastic queue

`src/mmrelay/message_queue.py` (`MessageQueue`).

| Phase         | Legal next phases        | Admission / owner rule                                         |
| ------------- | ------------------------ | -------------------------------------------------------------- |
| `STOPPED`     | `RUNNING`                | Construct executor successfully **before** publishing running  |
| `RUNNING`     | `STOPPING`               | Producers allowed, one processing task, queue-owned executor   |
| `STOPPING`    | `STOPPED`, `FAILED_STOP` | No new sends/producers; wait for task and executor termination |
| `FAILED_STOP` | `STOPPED`                | No new owner until all previous resources are confirmed exited |

The phase is available additively as `get_status()["phase"]` without
removing existing compatibility flags (`running`, `stop_failed`). `_lock`
serializes lifecycle transitions; `_run_generation` is the producer admission
epoch and `_stop_generation` fences stop watchdogs. The phase and status fields
are sampled under the same lock.

**Corrected:** a producer blocked by a full queue cannot enqueue into a
completely different run after stop/restart; synchronous `wait=True` on an
active asyncio loop will fail instead of deadlocking the processor; a late
stop-timeout watchdog cannot poison a successfully recovered/new run; executor
constructor failure leaves the phase `STOPPED`; an unsent dequeued message is
returned to the front on processor cancellation. Cancellation **does not**
requeue a send that may already have executed in the executor (ambiguous
in-flight delivery), since that could duplicate traffic. The queue is
in-memory and does not promise crash-persistent delivery.

**Monitor:** the pre-existing connection-error retry path can resend after a
transport error whose outcome is uncertain. Packet-level idempotency is not
established across Matrix/Meshtastic, so do not silently change retry policy
without transport-backed duplicate-delivery tests.

### 2. Plugin scheduler

`src/mmrelay/plugin_loader.py` global `schedule` runner.

- **Idle** → **Running**: one daemon worker and its own stop Event.
- **Running** → **Stopping**: stop Event asserted; join with bounded timeout.
- **Stopping** → **Idle**: worker has actually exited, then clear jobs/owner.
- **Stopping** → **Timed-out owner retained**: worker remains referenced and
  cannot be replaced. A later explicit start cannot overlap `run_pending`.
- A failing `run_pending` is isolated and logged so the scheduler's lifecycle
  does not silently die; job retry behavior still belongs to `schedule`.

**Corrected:** timeout previously cleared the thread and Event unconditionally,
allowing two workers to run plugin jobs simultaneously. Shutdown now also
stops the scheduler when there were zero _active_ plugins, since startup can
start it even with no active plugin instances.

### 3. Plugin loader vs. shutdown

`src/mmrelay/plugin_loader.py`: lifecycle lock, immediate shutdown signal,
shutdown request reference count, and monotonic shutdown epoch.

- **Unloaded** → **Preparing** → **Activating** → **Loaded**.
- **Any state** + shutdown request → **Teardown pending** → **Unloaded**.
- Concurrent loads serialize. A loader request admitted before a shutdown
  epoch cannot be activated after the teardown has completed.
- A shutdown signal is visible **before** waiting for a worker thread already
  preparing plugins; activation checks it between preparation and each plugin.
- Concurrent shutdown calls cannot clear each other's shutdown signal.

**Corrected:** a cancelled `run_in_executor` startup could continue loading
plugins after cleanup, and a queued load could cross a completed shutdown
boundary. Keep the existing dynamically loaded plugin structure and existing
`sorted_active_plugins` consumer contract; this is an ownership fix, not a
plugin interface rewrite.

**Monitor:** plugin-supplied `.start()`, `.stop()`, and scheduled jobs may do
arbitrary blocking I/O. The global shutdown lock cannot _interrupt_ a hung
plugin, and the enclosing application still has its independent bounded
shutdown policy. No pre-emptive thread killing was introduced.

### 4. Meshtastic reconnection and callback subscription

`src/mmrelay/meshtastic/{events,connection,subscriptions}.py`, with state owned
by `src/mmrelay/meshtastic_utils.py`.

- **Connected** → **Disconnected** → **Reconnect scheduled** → **Connecting** →
  **Connected**, or retry/backoff/error. A reconnect is only marked scheduled
  when the loop is **running and open**; merely having a nonclosed loop is
  insufficient. On scheduling failure the `reconnecting` flag is rolled back.
- **Subscribed** → **Tearing down** → **Unsubscribed**; delayed connection
  callbacks cannot reactivate pubsub while global shutdown is asserted.
- Source interfaces are identified using `_relay_active_client_id` and BLE
  generations, so stale packet/disconnect callbacks are rejected.

**Corrected:** an existing but stopped loop could leave `reconnecting=True`
with no task scheduled. A late connection completion could resubscribe pubsub
callbacks after shutdown. Neither fix changes the source-owning facade API.

### 5. Startup drain and delayed metadata timers

`src/mmrelay/meshtastic/{events,connection}.py`.

- **Drain not armed** → **Drain armed** (deadline + Timer owner) → **Expired**
  (signal readiness) or **Cancelled**.
- **Probe absent** → **Deferred** (Timer owner) → **Submitted**,
  **Invalidated by disconnect/owner change**, or **Abandoned on timer failure**.
- A callback must still be the currently registered Timer identity to act.
  Cancellation alone is not enough: a Timer callback may already be executing.

**Corrected:** stale cancelled drain callbacks cannot clear the new timer,
expire its deadline, or signal readiness. Failed probe Timer `.start()` no
longer leaves a never-started timer registered as pending. The pre-existing
client-identity check on delayed probe submission remains intact.

## Other implicit state machines / disposition inventory

| #   | Runtime state machine                                          | Owner / source                                                                                      | Key existing transition or risk                                                                                             | Disposition                                                    |
| --- | -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| 6   | Process initialization → startup → health/sync → shutdown      | `main.py`                                                                                           | Shutdown event, signal handler, rollback, bounded thread cleanup, cancellation of pending connectors                        | **Preserved**; app-wide real shutdown requires full runtime    |
| 7   | Service readiness publication / heartbeat                      | `main.py`                                                                                           | Readiness file starts after drain, refreshed until shutdown, then removed                                                   | **Preserved**; timer ownership fixed above                     |
| 8   | Matrix client authentication/bootstrap                         | `matrix/sync_bootstrap.py`                                                                          | Exclusive startup lock, staged client initialization, close client on setup failure; publish after successful setup         | **Preserved**                                                  |
| 9   | Matrix initial sync / invite recovery                          | `matrix/sync_bootstrap.py`                                                                          | Initial sync retry and invite handling, temporary SDK compatibility patch under lock                                        | **Preserved**; requires real `nio` integration                 |
| 10  | Matrix callback registration and room membership               | `main.py`, `matrix/events.py`, `matrix/room_authorization.py`                                       | Joins mapped and plugin-owned rooms before event handling, event routing requires authorized room                           | **Preserved**                                                  |
| 11  | Matrix E2EE own-device verification                            | `matrix/e2ee_identity.py`                                                                           | Query, verify, optional cross-signing reset, server-visibility postcondition                                                | **Preserved**, security-sensitive; no speculative reset        |
| 12  | OAuth device authorization/polling                             | `matrix/oauth.py`, `matrix/oauth_cli.py`                                                            | Pending authorization, token issuance/expiry, error and retry rules                                                         | **Preserved**                                                  |
| 13  | OAuth token refresh and persistence                            | `matrix/oauth_session.py`, `matrix/oauth_store.py`, `matrix/oauth_async.py`                         | asyncio lock + cross-process file lock + atomic replace, cancellation-shielded rotate/store, stamp-based cache invalidation | **Preserved**; avoids token reuse after lost rotation          |
| 14  | OAuth credential deletion / logout                             | `matrix/oauth_cli.py`, `matrix/oauth_store.py`                                                      | Remove persisted credential and revoke according to protocol                                                                | **Preserved**                                                  |
| 15  | Shared SQLite manager startup/close                            | `db_runtime.py`                                                                                     | `_accepting_submissions`, `_closing`, active synchronous count, per-thread connections and executor closure                 | **Preserved**; already has explicit concurrency gates          |
| 16  | Message map retention and reply/reaction correlation           | `db_utils.py`, `message_queue.py`, Matrix/mesh relay paths                                          | Persist mapping only after successful send result; prune by configured retention                                            | **Preserved**; outcome-ambiguous sends require policy decision |
| 17  | Node-name snapshot reconciliation                              | `db_utils.py`, `meshtastic/node_refresh.py`                                                         | Complete vs partial snapshots, merge absent data, atomic name-table sync, prior success watermark                           | **Preserved**; do not replace with partial/empty state         |
| 18  | BLE address-specific connection generation                     | `meshtastic/ble.py`, `meshtastic_utils.py`                                                          | Per-address and per-interface generations fence callbacks, unresolved teardown counts                                       | **Preserved**; mixed mtjk versions require care                |
| 19  | BLE construction timeout / late result                         | `meshtastic/async_utils.py`, `meshtastic/executors.py`                                              | Stale generation check, orphan-worker accounting, bounded cleanup and degraded state                                        | **Preserved**; cannot safely kill native BLE threads           |
| 20  | BLE gate recovery / suppressed duplicate connect               | `meshtastic/ble.py`, `meshtastic/connection.py`                                                     | Optional mtjk BLE gate reset and per-address degraded/suppressed state                                                      | **Preserved**; optional feature detected at runtime            |
| 21  | Serial/TCP/BLE connection attempt rollback                     | `meshtastic/connection.py`                                                                          | Connection-attempt lock, private cancellation per constructor, rollback stale client/flags on failure                       | **Preserved**                                                  |
| 22  | SDK constructor cancellation                                   | `meshtastic/cancellation.py`                                                                        | Invocation-owned cancel Event; passes `cancelEvent` only when supported                                                     | **Preserved**; maintains old mtjk compatibility                |
| 23  | Health heartbeat / ACK pending state                           | `meshtastic/health.py`, `meshtastic/metadata.py`                                                    | Request ID tracking with expiry, conditional ACK/NACK, probe executor degradation                                           | **Preserved**; device-backed ACK ordering not fully reproduced |
| 24  | Metadata executor ownership                                    | `meshtastic/executors.py`                                                                           | Single concurrent metadata future, degraded/orphan accounting, stop cleanup                                                 | **Preserved**; device-specific hung calls not preemptible      |
| 25  | Packet freshness, rxTime skew and bootstrap                    | `meshtastic_utils.py`, `meshtastic/health.py`, `meshtastic/events.py`                               | Session timestamp, monotonic startup windows, skew calibration, single reconnect prestart allowance                         | **Preserved**, timed drain transition fixed above              |
| 26  | Matrix→mesh/mesh→Matrix packet routing and plugin interception | `matrix/relay.py`, `meshtastic/events.py`, `meshtastic/packet_routing.py`, `plugins/base_plugin.py` | Priority/stop-on-handle dispatch, queue delivery, reply/reaction mapping                                                    | **Preserved**; do not introduce duplicate routing              |
| 27  | Plugin dependency staging/installation                         | `plugin_loader.py`                                                                                  | Requirement digest/marker, temp dependency tree and promotion, repo commit/ref pin                                          | **Preserved**; external git/pip failure integration pending    |
| 28  | Custom/community plugin update checkout                        | `plugin_loader.py`                                                                                  | Validated URL/ref, clone/update and stored commit state                                                                     | **Preserved**; no new remote access in audit                   |
| 29  | Legacy/user data and Windows home migration                    | `migrate.py`                                                                                        | Lock, staging, backup, publish/rollback, source validation; Windows path variants                                           | **Preserved**; no migration changes made                       |
| 30  | CLI/request-triggered Meshtastic operations                    | `meshtastic_cli_executor.py`, `plugins/meshtastic_cli_plugin.py`                                    | Serialized CLI operations, command invocation and send completion                                                           | **Preserved**; hardware/CLI runtime required                   |
| 31  | Node refresh supervisor and health task failure propagation    | `main.py`, `meshtastic/node_refresh.py`                                                             | Task lifecycle tracked and failure drives app shutdown instead of silent loss                                               | **Preserved**                                                  |
| 32  | Plugin command dispatch / timers                               | `plugins/base_plugin.py`, `plugin_runtime.py`, `plugin_loader.py`                                   | Shared jobs/queue/database, priority ordering and stop contract                                                             | **Preserved**, scheduler/loader ownership fixed above          |

The inventory covers **state-owning runtime subsystems**, not every local
`if` statement: e.g. a pure formatter, parser, codec, or one-off HTTP response
branch is not a separate long-lived state machine. Existing explicit
locks/generations deserve retention rather than invasive replacement.

## Compatibility decisions

1. `mmrelay.meshtastic_utils` and `mmrelay.matrix_utils` are intentionally
   state-owning facades; submodule imports occur after facade globals are
   defined. Keep facade import ordering and monkeypatch targets stable.
2. `mmrelay` owns reconnect scheduling. Do not silently enable mtjk's separate
   BLE auto-reconnect or duplicate construction flows.
3. Existing internal mtjk compatibility shims for packet IDs and raw sends are
   used intentionally. These patches do not alter packet wire formats.
4. Mindroom recovery of rotated E2EE device keys remains supported; optional
   limited-timeline recovery/backfill remains _disabled_ by MMRelay default.
5. No schema changes, dependency changes, security bypasses, or migration
   rewrites. All commits in the patch series retain **Jeremiah K** authorship;
   original git history and project metadata remain untouched.

## Regression coverage and validation limits

**New tests** (`tests/test_state_machine_regressions.py`,
`tests/test_plugin_lifecycle_regressions.py`,
`tests/test_mesh_lifecycle_regressions.py`) exercise queue start, full-queue
wait, stop/restart admission, cancellation and watchdog recovery; global
scheduler timed-out ownership; plugin load/shutdown handoff, epoch fencing and
concurrent shutdown requests; stale reconnect loop; stale startup drain timer;
probe timer startup failure; shutdown subscription suppression and regular
subscription behavior. Existing dependency-light queue and scheduler tests
are also run.

The audit environment ran Python 3.11 against an offline dependency set that
does **not** contain all runtime requirements (notably `meshtastic`, `nio`,
`pubsub` and associated lockfile dependencies), so full `uv sync --locked` /
project pytest collection cannot complete there. Consequently **do not
interpret these tests as a hardware/Matrix E2EE integration sign-off**. The supplemental mesh tests load
the individual modules with narrowly stubbed SDK/facade imports solely to
exercise ownership transitions, not to simulate real transport delivery.

Validation procedures on a complete CI/target environment:

```bash
uv sync --locked --extra test --extra dev
uv run --no-sync python -m pytest -v --cov --tb=short
uv run --no-sync python -m pytest -v tests/test_state_machine_regressions.py tests/test_plugin_lifecycle_regressions.py tests/test_mesh_lifecycle_regressions.py
uv run --no-sync black --check --line-length 88 src/mmrelay/message_queue.py src/mmrelay/plugin_loader.py src/mmrelay/meshtastic tests/test_state_machine_regressions.py tests/test_plugin_lifecycle_regressions.py tests/test_mesh_lifecycle_regressions.py
```

**Recommended integration fault injections:** while Matrix E2EE sync is active,
kill a BLE/TCP connection and reconnect through all retry paths; block a real
plugin scheduled job longer than scheduler timeout, then request start;
stop/replace the event loop during a disconnect callback; close the relay
while a real `mtjk` send is in an ambiguous in-flight state; race OAuth
refresh across processes; force database close with pending synchronous
transactions; and exercise Windows staging/rollback. The patch does not claim
those system-level scenarios passed offline.

## Change management / remaining decisions

- Queue's existing connection-error retransmission semantics can produce
  duplicates after an ambiguous radio failure; resolving this requires an
  explicit transport-level delivery/idempotency policy, not an opportunistic
  change in a lifecycle patch.
- Plugin `.start()` / `.stop()` code may block indefinitely and cannot be
  preempted safely. For externally supplied plugins, bound individual plugin
  work or terminate the whole process after application shutdown deadlines.
- Transport or SDK callbacks that race native BLE teardown still need
  device-backed validation. Per-address generation fencing is kept as-is.
- OAuth/E2EE and migration state machines were evaluated for ownership risks
  and left unchanged deliberately; they are **not** declared fully verified
  merely because source inspection found existing locks and postconditions.
