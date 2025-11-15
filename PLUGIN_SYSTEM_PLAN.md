# Plugin System Work Plan

_Updated: 2025-11-14_

This temporary document tracks the high-level remediation plan for the plugin system. Remove it once the scoped work streams below are complete and captured in long-lived docs.

## Snapshot From Audit

- ✅ `BasePlugin.start/stop` plus `plugin_loader.shutdown_plugins()` now give us deterministic lifecycle hooks.
- ✅ Community plugins clone/update paths honor the host allowlist and dependency filtering defaults.
- ⚠️ Plugins still run in-process with unrestricted filesystem/network access.
- ⚠️ Dependency auto-installation occurs without human approval or provenance checks.
- ⚠️ Plugin configuration remains free-form YAML, so invalid schedules or permissions are only caught at runtime.
- ⚠️ We lack health/performance monitoring, so misbehaving plugins can quietly degrade the relay.

## Work Streams

### 1. Lightweight Permission Guardrails (Critical)

- [ ] Keep all plugins in-process for now, but require each activated plugin to declare a compact capability list in config (`permissions: [matrix-send, radio-send, filesystem]`). Default to permissive for backward compatibility, but log/alert when a plugin escalates.
- [ ] Wire `BasePlugin` helpers (e.g., `send_message`, `send_matrix_message`, `get_plugin_data_dir`) through a central guard that checks the capability list and emits structured warnings if a plugin does something it never declared.
- [ ] Provide a simple `mmrelay plugins explain <name>` command so relay admins can see what a plugin is allowed to do without digging through code.
- [ ] Defer true sandboxing/IPC until we see evidence that in-process guardrails are insufficient—document the desired future direction but don’t block current contributors.

### 2. Supply Chain & Trust Controls (Critical)

- [ ] Change auto-install to a “review + approve” workflow: when a plugin needs dependencies, write them to `~/.mmrelay/plugins/pending/<plugin>.lock` and prompt the operator to run `mmrelay plugins approve <name>` (or set `security.auto_install_deps=true` to keep today’s behavior).
- [x] Allow (but don’t require) pinning a community plugin to a tag/commit right in `config.yaml`; surface a warning when loading from `main`/`master` so admins know they’re living on tip.
- [ ] Integrate an optional dependency scan hook via `security.dependency_scan_tool` (values `none | pip-audit | safety`) so teams can enable it without forcing every contributor to install extra tooling.
- [ ] Record the resolved versions + hashes in the same lock file so we can diff upgrades later; plugin authors don’t need to change anything.

### 3. Configuration & Health Validation (High)

- [ ] Add a thin validation layer (pydantic models) for plugin config entries so we can give actionable errors during `mmrelay cli check-config` instead of runtime tracebacks.
- [ ] Track simple per-plugin health stats (last run, last exception, number of retries) in memory and surface them via `mmrelay plugins status`.
- [ ] Implement a conservative watchdog that disables a plugin after N consecutive crashes and tells the admin exactly how to re-enable once fixed.

### 4. Developer & User Experience (Medium)

- [ ] Ship a cookiecutter-style `mmrelay plugins init` command that outputs the minimal pattern shown in the wiki (single file + optional requirements.txt + README), plus an example `permissions` block so new authors aren’t surprised.
- [ ] Provide pytest fixtures for Meshtastic/Matrix stubs so community authors can write unit tests without spinning up real radios.
- [ ] Expand the wiki Plugin Development Guide with the new review/approval steps and concrete examples sourced from existing community plugins (e.g., GPXTracker, dm-rcv-basic).
- [ ] Extend `mmrelay plugins list/status --verbose` to show config validation results, pending dependency approvals, and declared permissions for quicker debugging.

## Tracking Table

| Priority  | Item                                                      | Status        | Target Release | Notes                                                              |
| --------- | --------------------------------------------------------- | ------------- | -------------- | ------------------------------------------------------------------ |
| Critical  | Permissions guardrails (config + runtime checks)          | ☐ Not started | v1.4           | No plugin code changes required unless they want stricter perms.   |
| Critical  | Supply-chain review & lock files                          | ☐ Not started | v1.4           | Defaults to today’s behavior until admins opt in.                  |
| High      | Config/schema validation                                  | ☐ Not started | v1.4           | Keeps existing YAML layout, just validates early.                  |
| High      | Plugin health metrics + watchdog                          | ☐ Not started | v1.5           | In-process counters only; no new daemons.                          |
| Medium    | Plugin developer tooling                                  | ☐ Not started | v1.5           | Builds on current wiki guidance.                                   |
| Completed | Community plugin commit pinning                           | ☑ Done       | v1.3           | Config supports `commit`/`revision` plus helper for clean logging. |
| Completed | Lifecycle cleanup (`BasePlugin.stop`, scheduler teardown) | ☑ Done       | v1.3           | Verified in audit (`BasePlugin` + `shutdown_plugins`).             |

## Immediate Next Steps

1. Prototype the dependency approval / lock-file flow and gather feedback from current plugin authors before enabling by default.
2. Add config-driven permission declarations plus runtime guardrails (log-only initially) so developers can see what will become stricter later.
3. Implement the plugin config validation models and hook them into `mmrelay cli check-config` to catch mistakes early.
