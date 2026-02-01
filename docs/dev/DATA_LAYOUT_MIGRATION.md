# Data Layout Migration Notes (v1.2.x)

## Summary

MMRelay currently supports two filesystem layouts:

1. Legacy layout (v1.2.9 and earlier):
   - base_dir == data_dir
   - Linux/macOS default: `~/.mmrelay`
   - Common files:
     - `~/.mmrelay/credentials.json`
     - `~/.mmrelay/meshtastic.sqlite` (+ `-wal`, `-shm`)
     - `~/.mmrelay/logs/`
     - `~/.mmrelay/plugins/`
     - `~/.mmrelay/store/`

2. New layout (v1.2.10+ opt-in):
   - base_dir is the root; data lives under `<base_dir>/data`
   - Common files:
     - `<base_dir>/credentials.json`
     - `<base_dir>/data/meshtastic.sqlite` (+ `-wal`, `-shm`)
     - `<base_dir>/logs/`
     - `<base_dir>/plugins/`
     - `<base_dir>/store/`

The current release prioritizes backward compatibility and safe upgrades.

## Current Behavior (as of v1.2.11)

### Directory resolution

- `base_dir`:
  - `MMRELAY_BASE_DIR` / `--base-dir` takes precedence.
  - Legacy override `MMRELAY_DATA_DIR` / `--data-dir` is respected.
  - Linux/macOS default: `~/.mmrelay`
  - Windows default: `platformdirs.user_data_dir(APP_NAME, APP_AUTHOR)`

- `data_dir`:
  - If `MMRELAY_DATA_DIR` is set, use that path (or its legacy `data/` child if it already contains data).
  - Otherwise:
    - Linux/macOS default: `<base_dir>/data`
    - Windows default: platformdirs user data dir **unless** new layout is explicitly enabled.

### Credential lookup

- Order:
  1. Explicit path (`MMRELAY_CREDENTIALS_PATH`, `credentials_path` in config)
  2. Config-adjacent path
  3. Base/data fallbacks

This matches runtime lookup and the `mmrelay auth status` CLI.

### Docker defaults

- Docker uses the legacy layout by default to avoid breaking existing installs.
- The image sets `MMRELAY_DATA_DIR=/app/data` and relies on the dynamic log path.
- This keeps:
  - `credentials.json` at `/app/data/credentials.json`
  - DB at `/app/data/meshtastic.sqlite`
  - logs under `/app/data/logs`

### Database migration

- Migration is executed inside `get_db_path()`.
- When new layout is explicitly enabled and the new DB path does not exist:
  - We select the most recently updated legacy DB (including `-wal`/`-shm`) and move it to the new location.
  - This includes migrating the `-wal` and `-shm` sidecars when present.

This is intentionally _inside_ `get_db_path()` to ensure any code path that needs the DB triggers migration, even if it does not go through `main.py`.

## Why We Kept Migration in get_db_path()

For safety in this release, we avoid introducing ordering dependencies. Moving migration to startup (e.g., `main.py`) risks missing migrations in non-standard entry points or tooling that calls DB helpers directly.

Keeping the migration in `get_db_path()`:

- Ensures migration occurs when the DB is actually needed.
- Avoids breaking unexpected execution paths.
- Preserves backwards-compatible behavior with minimal risk.

## Future Cleanup (Potential Breaking Changes)

If we want to fully unify the layout in a future major release, the safest path looks like this:

1. Deprecate and remove `MMRELAY_DATA_DIR` / `--data-dir`.
2. Make the new layout the only layout (base_dir root + `<base_dir>/data`).
3. Move migration to an explicit startup step or a dedicated command (e.g., `mmrelay config migrate-layout`).
4. Update Docker defaults to mount the host directory at `/app` and drop legacy fallbacks.
5. Require credentials at `<base_dir>/credentials.json` (or explicit path).
6. Update all docs and samples to match the unified layout.

This would be a breaking change, so it should be staged with clear warnings and a release note migration guide.

## Optional Opt-In Today

To use the new layout now (without breaking legacy users):

1. Set `MMRELAY_BASE_DIR=/app` (or use `--base-dir /app`).
2. Ensure a persistent mount at `/app` (not just `/app/data`).
3. Move `meshtastic.sqlite` to `<base_dir>/data/meshtastic.sqlite` if needed.
4. Move `credentials.json` to `<base_dir>/credentials.json` (or set `MMRELAY_CREDENTIALS_PATH`).
