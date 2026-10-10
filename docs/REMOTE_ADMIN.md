# Remote admin

The `remote_admin` core plugin lets authorized Matrix users run mtjk admin commands against **remote** mesh nodes from one designated Matrix room. Commands are parsed and dispatched by mtjk's own CLI machinery, in-process, over the relay's existing Meshtastic connection — there is no second connection to the node, and the feature works whether the relay is attached over serial, TCP, or BLE.

## Enabling

Create a Matrix room for admin work, invite the bot (it accepts the invite automatically), and configure:

```yaml
plugins:
  remote_admin:
    active: true
    admin_room: "!room_id:matrix.org"
    #power_level: 50
    #allow_destructive: false
    #timeout: 120
    #max_output_chars: 2000
```

`admin_room` must be a **Matrix room ID** (beginning `!`), not an alias
(beginning `#`). Copy the room ID from your Matrix client; aliases are not
stable authorization identifiers. An invalid value leaves the plugin inert.

The admin room does **not** go in `matrix_rooms`. Plugin-owned rooms never relay to the mesh, so ops chatter stays in Matrix. Messages are still readable by the bot when the room is encrypted and E2EE is enabled.

## Authorization

Authorization is the admin room's own `m.room.power_levels`: a sender must hold at least `power_level` (default 50) **in that room**. This is why the plugin uses a designated room rather than DMs — a DM grants its creator power level 100 automatically, so power levels inside a DM carry no authorization meaning. Senders below the threshold get a refusal and a ⛔ reaction; the attempt is logged.

## Command surface

`!admin --dest <node> <mtjk flags>` — for example:

- `!admin --dest !a4bf9d0c --get lora.region`
- `!admin --dest !a4bf9d0c --reboot`
- `!admin --dest !a4bf9d0c --request-connection-status`
- `!admin --dest !a4bf9d0c --set-owner "Fire Lookout"`

`!admin help` lists the usage and the effective settings.

Remote administration runs through mtjk's public `executeCommand` API on the relay's existing connection. During development, MMRelay installs mtjk directly from the **moving `develop` branch**; this is temporary and should be replaced with a reviewed release pin before #738 merges. The embedded API is versioned and exposes a finite subset of the CLI. MMRelay applies a second, narrower policy: only explicitly approved remote admin actions are accepted, and a command not supported by the installed embedded API is refused before any radio action.

- **Read-only (allowed by default):** `--get`, `--get-ui-config`, `--request-connection-status`, `--get-canned-message`, `--get-ringtone`
- **Routine admin (allowed by default):** `--set`, `--reboot`, `--set-owner`, `--set-owner-short`, `--set-time`, `--set-is-unmessageable`, canned-message/ringtone writes, `--ch-add`, `--ch-set`, `--ch-enable`, `--ch-disable`, `--setlat/--setlon/--setalt`, `--remove-position`, favorite/ignore/mute toggles, `--remove-node`, `--backup-preferences`, and `--pos-fields` (reads without operands, writes with operands); modifiers `--ack`, `--ch-index`, and `--channel-fetch-attempts`. The standalone `--dry-run` flag is not available through the current embedded API, and is refused rather than emulated.
- **Destructive (only with `allow_destructive: true`, and only when supported by mtjk's embedded capabilities):** `--shutdown`, `--factory-reset`, `--factory-reset-device`, `--reset-nodedb`, `--delete-file`, `--restore-preferences`, `--remove-backup-preferences`, `--enter-dfu`, `--reboot-ota`, `--set-ham`, `--ch-preset` (and the deprecated `--ch-*slow/fast/mod/turbo` shorthands), `--ch-del`, `--ch-set-url`/`--seturl`, `--ch-add-url`, `--gpio-wrb`, `--send-input-event` (with its input companions)
- **Separate or disabled operations:** The new embedded API captures `--get-canned-message` and `--get-ringtone` results, so those are now allowed. `--device-metadata`, `--gpio-rd`, `--request-telemetry`, and `--request-position` remain outside MMRelay's narrower administration policy even where mtjk supports them; the policy does not expand automatically when mtjk adds commands.
- **Local or separate-destination commands:** `--key-verify` operates on the relay radio even with a remote `--dest`, so all key-verification flags are refused. `--traceroute` has a separate destination and an uncaptured response path and is also refused.
- **Always refused:** anything not listed above — connection flags (`--host`, `--port`, `--ble*`), local-node-only flags (`--info`, `--nodes`, `--export-config`, `--tunnel`), interactive ones (`--listen`, `--reply`), diagnostics (`--debug`, `--seriallog`), `--sendtext`, `--configure`, `--qr*`, `--ota-update`, `--lockdown-*`, and anything a future mtjk adds until it is classified here. Refusal is the default; the allow lists are the complete exception.

## Destination rules

`--dest` is required on every command and must name a **specific remote node**:

- `^local`, `^all`, broadcast, and the relay's own node are refused — admin commands can never act on the node running the relay.
- The node must already be in the relay's node database (`!nodes` shows it). Node ids are normalized to `!xxxxxxxx` form; `!a4bf9d0c`, `0xa4bf9d0c`, and decimal forms are all accepted.

## Behavior

- One command runs at a time; a second command while one is in flight gets an immediate busy reply. Cancellation of a Matrix event handler does not release the single-command guard until the blocking radio operation finishes.
- The command is acknowledged with ⏳, then ✅ or ❌ with the exit code and captured **plain-text** output (truncated to `max_output_chars`). The remote node's text is not interpreted as HTML.
- The public mtjk API applies one monotonic `timeout` budget (default 120 s here), including its per-interface command lock, connection readiness, TX queue capacity, node polling, and request-scoped waits; any `--timeout` argument is translated into this API parameter instead of passed to the CLI parser. Blocking backend I/O is still subject to backend limits. Only command-owned response handlers and waits are retired; other relay requests are preserved. `--channel-fetch-attempts` must be at least 1.
- Every invocation is audited with sender, a SHA-256 fingerprint of the command, and outcome; raw arguments are not logged because they can contain join URLs or other credentials.

## Requirements on the remote node

Remote admin traffic rides the admin channel: the first enabled channel literally named `admin`, else channel 0, resolved from the relay node's channel table. The remote node must share that channel and PSK, and firmware 2.5+ requires an admin session passkey — mtjk requests it automatically, and if the remote node withholds admin rights the command fails with a timeout or NAK error in the reply. mtjk selects PKI encryption or the shared admin-channel transport according to the node and channel settings. Command completion uses acknowledgments or correlated admin responses as required by the selected verb.

## Safety properties

The relay's shared mesh connection is never closed by an admin command, and admin dispatch runs off the event loop in a worker thread, so relaying continues while a command waits for its remote ACK. An active plugin without `admin_room` logs a warning and stays inert.

## Development dependency and release transition

While mtjk's new public APIs are settling, MMRelay uses `mtjk @ git+https://github.com/jeremiah-k/mtjk.git@develop` in `pyproject.toml`. Recreate the MMRelay virtual environment to select it; do not install upstream `meshtastic` in the same environment because the distributions share the `meshtastic` package namespace. The branch is mutable and should not be used as a production reproducibility guarantee. Record the tested mtjk commit SHA and run the combined remote-admin/embedded-command tests. Before merging #738, release the mtjk changes, switch MMRelay to the exact published `mtjk==...` version, and rerun CI and a live remote admin ACK/readback check.

`executeCommand` completion means a supported invocation finished; it does not undo radio-side writes if a later timeout or failure occurs. A positive ACK is different from verified final device state.
