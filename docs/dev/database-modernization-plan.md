# Meshtastic Matrix Relay — Database Modernization Plan

## Context Snapshot

- **Current state:** Each helper in `src/mmrelay/db_utils.py` opens a fresh SQLite connection, commits, and swallows exceptions. Message mapping writes sometimes run on the asyncio loop thread (`matrix_utils`, `message_queue`). `get_db_path()` retains legacy `db.path` support and aggressive caching.
- **Constraints:** Maintain backwards-compatible call signatures, logging, and error-swallow semantics; ensure `tests/test_db_utils.py` and `tests/test_db_utils_edge_cases.py` continue to pass. No broad configuration explosion—legacy environments should work with defaults.
- **Operational Drivers:** Reduce connection churn, avoid blocking the event loop with DB writes, prepare for future async usage without duplicating sync/async APIs, and keep plugin helpers straightforward.

## Guiding Goals

1. **Stable public surface:** Existing helpers remain callable as-is; any new helpers are additive.
2. **Predictable concurrency:** Centralize connection management, apply SQLite pragmas once, and ensure writers serialize cleanly under load.
3. **Minimal configuration:** Opt-in switches only when critical (e.g., enabling WAL); legacy `db.*` keys kept functional.
4. **Async-friendly without reinvention:** Provide executor wrappers so event-loop code can defer to worker threads without bespoke plumbing.
5. **Observability:** Improve logging around connection lifecycle and busy/error scenarios while preserving current log levels for compatibility.

## Target Architecture

- **`DatabaseManager` (new `src/mmrelay/db_runtime.py`):**
  - Owns a primary `sqlite3.Connection` (`check_same_thread=False`) plus a per-thread cache keyed via `threading.local()` for safe reuse in executors.
  - Applies startup pragmas (e.g., WAL, `busy_timeout`) once per connection; controlled by a single config toggle such as `database.enable_wal`.
  - Exposes context helpers:
    - `with manager.read()` → yields cursor, ensures rollback on failure, no implicit commit.
    - `with manager.write()` → yields cursor, commits on success, rollbacks on exception.
  - Provides coroutine helpers: `await manager.run_async(op, *args, write=True|False)` to dispatch sync callbacks through the default executor.
  - Surfaces `close()` for orderly shutdown (used by tests or future teardown hooks).

- **`db_utils` refactor:**
  - Public functions remain the same signature; internals delegate to `DatabaseManager`.
  - Shared SQL extracted into private helpers to avoid duplication.
  - Exceptions continue to be caught/logged inside wrappers (matches test expectations).
  - Cache handling (`get_db_path`, `clear_db_path_cache`) continues unchanged.

- **Async touchpoints:**
  - `matrix_utils.matrix_relay()` continues using `run_in_executor`, but rewired through `DatabaseManager.run()` for consistency.
  - `message_queue._handle_message_mapping()` switches to the async helper, avoiding blocking the queue processor.

## Implementation Phases

### Phase 1 — Core Manager & Migration (primary focus)

1. Create `db_runtime.DatabaseManager` with connection initialization, pragmas, and executor helper.
2. Wire `initialize_database()` to use the manager for schema setup while respecting existing logs and error flow.
3. Refactor `store_plugin_data`, `get_longname`, `wipe_message_map`, etc., to call into the manager.
4. Update `message_queue._handle_message_mapping` and `matrix_utils.matrix_relay` to use the new async-friendly helper.
5. Add configuration wiring:
   - `database.enable_wal` (default true) applied once per connection.
   - `database.busy_timeout_ms` (default e.g., 5000) for consistent handling.
6. Ensure `set_config` injects the manager instance into `db_utils` (likely via lazy singleton accessor).

### Phase 2 — Ergonomics & Plugins (after Phase 1 stabilizes)

1. Extend plugin data helpers to accept optional `db_manager` argument for batching (default to global manager).
2. Document best practices for plugins (for later wiki update).
3. Audit `update_longnames/shortnames` usage to consider batched writes where available.

### Phase 3 — Security Enhancements (successive iteration)

1. Integrate dependency scanning into `plugin_loader._install_requirements_for_repo()` with a single tool selector (`security.dependency_scan_tool`: `none|safety|pip-audit`).
2. Introduce lightweight allow/deny lists in `src/mmrelay/tools/` with opt-in enforcement.
3. Ensure new configuration options degrade gracefully to current behavior.

## Impacted Modules & Files

- **New:** `src/mmrelay/db_runtime.py`, possibly `tests/fixtures/db_manager.py`.
- **Updated:** `src/mmrelay/db_utils.py`, `src/mmrelay/main.py` (manager bootstrap), `src/mmrelay/message_queue.py`, `src/mmrelay/matrix_utils.py`, plugin helpers in `src/mmrelay/plugins/base_plugin.py`.
- **Docs/Config:** `docs/dev/TESTING_GUIDE.md` (brief note on DB manager usage), `src/mmrelay/tools/sample_config.yaml` (WAL/busy timeout toggles).

## Testing & Validation

- Extend `tests/test_db_utils.py` with fixtures that inject an in-memory manager, verify cursor reuse, and confirm pragmas.
- Enhance `tests/test_db_utils_edge_cases.py` to simulate busy timeouts and ensure wrapped helpers still swallow/log appropriately.
- Add targeted async tests for `manager.run()` to confirm executor scheduling and result propagation.
- Run `python -m pytest -v --cov --tb=short` in venv as baseline; ensure coverage deltas stay positive.

## Documentation Deliverables

- **This plan** (initial artifact, to evolve with implementation).
- **End-of-cycle:** Updated Plugin Development Guide wiki entry reflecting manager usage, plugin storage tips, and revised security workflow (to be delivered as a temporary `.md` file for later wiki migration).

## Open Questions / Follow-Ups

1. Should WAL be optional for Windows installers using FAT/exFAT? Confirm with runtime testing; adjust default if necessary.
2. Do we need a graceful shutdown hook to close the shared connection during application exit? (Likely yes—revisit when wiring `main.py` teardown.)
3. Is an in-memory database option already sufficient for tests, or should we expose a `:memory:` manager for more deterministic unit coverage?
4. Future-proofing: once executor helpers are stable, revisit full async DB API needs; avoid premature duplication now.

## Current Status

- Awaiting implementation kickoff on Phase 1.
- Wiki update deferred until after database changes land (link: Plugin Development Guide on GitHub Wiki).
