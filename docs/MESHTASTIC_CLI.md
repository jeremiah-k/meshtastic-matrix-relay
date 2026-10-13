# Meshtastic CLI plugin

The `meshtastic_cli` core plugin lets authorized Matrix users run mtjk admin commands against **remote** mesh nodes from one designated Matrix room. Commands are parsed and dispatched by mtjk's own CLI machinery, in-process, over the relay's existing Meshtastic connection — there is no second connection to the node, and the feature works whether the relay is attached over serial, TCP, or BLE.

## Enabling

Create a Matrix room for admin work, invite the bot, and configure — the
relay joins the room at startup and when invited, so an invite sent while the
relay is down is picked up at the next start:

```yaml
plugins:
  meshtastic_cli:
    active: true
    admin_room: "!room_id:matrix.org"
    #power_level: 50
    #allow_destructive: false
    #timeout: 120
    #max_output_chars: 2000
```

`admin_room` must be a **Matrix room ID**, not an alias (beginning `#`). Room
IDs are opaque: rooms at version 12 and newer have domain-less IDs (the
create event's ID under a `!` sigil), while older rooms carry a server suffix
(`!opaque:server`). Copy the ID exactly as your client shows it — never
append or strip a suffix — the value must byte-match the room's actual ID.
Aliases are not stable authorization identifiers, and an invalid value leaves
the plugin inert.

The admin room does **not** go in `matrix_rooms`. Plugin-owned rooms never relay to the mesh, so ops chatter stays in Matrix. Messages are still readable by the bot when the room is encrypted and E2EE is enabled.

## Authorization

Authorization is the admin room's own `m.room.power_levels`: a sender must hold at least `power_level` (default 50) **in that room**. This is why the plugin uses a designated room rather than DMs — a DM grants its creator power level 100 automatically, so power levels inside a DM carry no authorization meaning. Senders below the threshold get a refusal and a ⛔ reaction; the attempt is logged.

Each command checks fresh room state through the relay's existing Matrix client; it never opens another Matrix session to authorize a command. Removing a sender's power-level override revokes access even if nio's room cache retains the removed entry. If the client is unavailable or the room-state fetch fails or exceeds ten seconds, execution is refused. This authorization wait is separate from the mtjk command timeout, which starts only after authorization. Help remains available without authorization.

Rooms at version 12 and newer grant their **creator** intrinsic, undemotable power that no users-map entry can express — v12 even rejects power-level events listing the creator, so an empty `users` map is normal there (Element shows the same rule as its "Creator" badge). The plugin authorizes the room creator accordingly; for everyone else the power-levels event governs.

## Command surface

`!meshtastic --dest <node> <mtjk flags>` — for example:

- `!meshtastic --dest !a4bf9d0c --get lora.region`
- `!meshtastic --dest !a4bf9d0c --reboot`
- `!meshtastic --dest !a4bf9d0c --request-connection-status`
- `!meshtastic --dest !a4bf9d0c --set-owner "Fire Lookout"`

`!meshtastic help` lists the usage and the effective settings.

Remote administration runs through mtjk's public `executeCommand` API on the relay's existing connection. The embedded API is versioned and exposes a finite subset of the CLI. MMRelay applies a second, narrower policy: only explicitly approved remote admin actions are accepted, and a command not supported by the installed embedded API is refused before any radio action.

- **Read-only (allowed by default):** `--get`, `--get-ui-config`, `--request-connection-status`, `--get-canned-message`, `--get-ringtone`
- **Routine admin (allowed by default):** `--set`, `--reboot`, `--set-owner`, `--set-owner-short`, `--set-time`, `--set-is-unmessageable`, canned-message/ringtone writes, `--ch-add`, `--ch-set`, `--ch-enable`, `--ch-disable`, `--setlat/--setlon/--setalt`, `--remove-position`, favorite/ignore/mute toggles, `--remove-node`, `--backup-preferences`, and `--pos-fields` (reads without operands, writes with operands); modifiers `--ack`, `--ch-index`, and `--channel-fetch-attempts`. `--dry-run` is accepted only with `--set` when the installed mtjk embedded API advertises the option; otherwise it is refused rather than emulated. Place flags before the values of multi-value arguments like `--set` — mtjk's parser treats `--set owner --dry-run` as setting the owner to the literal string `--dry-run`, so MMRelay refuses a line whose flags were swallowed as values instead of silently running a different command.
- **Destructive (only with `allow_destructive: true`, and only when supported by mtjk's embedded capabilities):** `--shutdown`, `--factory-reset`, `--factory-reset-device`, `--reset-nodedb`, `--delete-file`, `--restore-preferences`, `--remove-backup-preferences`, `--enter-dfu`, `--reboot-ota`, `--set-ham`, `--ch-preset` (and the deprecated `--ch-*slow/fast/mod/turbo` shorthands), `--ch-del`, `--ch-set-url`/`--seturl`, `--ch-add-url`, `--gpio-wrb`, `--send-input-event` (with its input companions)
- **Separate or disabled operations:** The new embedded API captures `--get-canned-message` and `--get-ringtone` results, so those are now allowed. `--device-metadata`, `--gpio-rd`, `--request-telemetry`, and `--request-position` remain outside MMRelay's narrower administration policy even where mtjk supports them; the policy does not expand automatically when mtjk adds commands.
- **Local or separate-destination commands:** `--key-verify` operates on the relay radio even with a remote `--dest`, so all key-verification flags are refused. `--traceroute` has a separate destination and an uncaptured response path and is also refused.
- **Always refused:** anything not listed above — connection flags (`--host`, `--port`, `--ble*`), local-node-only flags (`--info`, `--nodes`, `--export-config`, `--tunnel`), interactive ones (`--listen`, `--reply`), diagnostics (`--debug`, `--seriallog`), `--sendtext`, `--configure`, `--qr*`, `--ota-update`, `--lockdown-*`, and anything a future mtjk adds until it is classified here. Refusal is the default; the allow lists are the complete exception.

Generic setters also enforce the destructive tier: writes to the `lora`, `security`, `bluetooth`, `network`, `mqtt`, and `serial` sections, and `--ch-set psk`, `name`, or `role` require `allow_destructive: true`. This covers pairing credentials, WiFi/MQTT credentials, and serial-console configuration. The gate uses mtjk's own normalization, including leading underscores, camelCase names, and whitespace around dotted components. Other routine writes remain allowed; `allow_destructive: false` is not a read-only mode.

## Destination rules

`--dest` is required on every command and must name a **specific remote node**:

- `^local`, `^all`, broadcast, and the relay's own node are refused — admin commands can never act on the node running the relay.
- The node must already be in the relay's node database (`!nodes` shows it). Node ids are normalized to `!xxxxxxxx` form; `!a4bf9d0c`, `0xa4bf9d0c`, and decimal forms are all accepted.

## Behavior

- One command runs at a time; a second command while one is in flight gets an immediate busy reply. Cancellation of a Matrix event handler does not release the single-command guard until the blocking radio operation finishes.
- The command is acknowledged with ⏳, then ✅ or ❌ with the exit code and captured **plain-text** output (truncated to `max_output_chars`). The remote node's text is not interpreted as HTML.
- Replies use the same unverified-device policy as relay messages and reactions, so encrypted rooms can receive help, refusals, and command output when members have unverified devices.
- The public mtjk API applies one monotonic `timeout` budget (default 120 s here), including its per-interface command lock, connection readiness, TX queue capacity, node polling, and request-scoped waits; any `--timeout` argument is translated into this API parameter instead of passed to the CLI parser. Blocking backend I/O is still subject to backend limits. Only command-owned response handlers and waits are retired; other relay requests are preserved. `--channel-fetch-attempts` must be at least 1.
- Every invocation is audited with sender, a SHA-256 fingerprint of the command, and outcome; raw arguments are not logged because they can contain join URLs or other credentials.

## Requirements on the remote node

Remote admin traffic rides the admin channel: the first enabled channel literally named `admin`, else channel 0, resolved from the relay node's channel table; the remote node must share that channel. Admin requests are PKI-encrypted on the wire — a firmware 2.5+ node accepts them only from senders whose public key appears in its admin key list — and firmware 2.5+ requires an admin session passkey, which mtjk requests automatically; if the remote node withholds admin rights the command fails with a timeout or NAK error in the reply. Command completion uses acknowledgments or correlated admin responses as required by the selected verb.

## Safety properties

The minimum configured power level must be positive. Zero, negative, and invalid values fall back to 50, so `power_level: 0` cannot accidentally authorize every ordinary room member.

If backend transport I/O never returns, the plugin remains busy until the relay process is restarted. Releasing the guard while its worker is running would permit overlapping radio commands, so handler cancellation does not bypass it. A cancelled handler drains its worker but can omit the final Matrix reply.

The relay's shared mesh connection is never closed by an admin command, and admin dispatch runs off the event loop in a worker thread, so relaying continues while a command waits for its remote ACK. An active plugin without `admin_room` logs a warning and stays inert.

A failed startup join for a plugin-owned room is logged without stopping the mapped relay or preventing other plugin rooms from joining. Invite handling can retry when the bot is invited later.

## Development dependency

MMRelay pins the exact published mtjk release in `pyproject.toml`; the Meshtastic CLI plugin requires the embedded command API (version 1) and request-correlated origin-router rejection handling that the pinned release ships. Recreate the environment with `uv sync --locked --extra dev --extra test --extra e2e` to select the pin; do not install upstream `meshtastic` in the same environment because the distributions share the `meshtastic` package namespace. Record the tested mtjk release whenever the pin changes, and run the combined Meshtastic CLI/embedded-command tests.

`executeCommand` completion means a supported invocation finished; it does not undo radio-side writes if a later timeout or failure occurs. A positive ACK is different from verified final device state.

The admin room uses the shared Matrix room-ID validation used by configuration checks. Leading or trailing whitespace is rejected rather than normalized, so startup joins, invite ownership, and command authorization all use the exact configured ID. Remote destination checks use mtjk's detached queryNodes snapshot; the plugin does not acquire library-internal database locks.
