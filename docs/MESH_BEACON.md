# Mesh Beacon invitations

The `mesh_beacon` core plugin is a listening and presentation feature for firmware-native Mesh Beacons. Firmware owns RF reception and decoding; mtjk exposes decoded `MESH_BEACON_APP` packets to MMRelay. The relay keeps a small standing invitation history and turns those packets into useful Matrix affordances: readable cards, Meshtastic share URLs, equivalent `meshtastic --seturl` commands, and QR codes.

The plugin intentionally does **not** author broadcast messages, offers, target presets, target channels, intervals, or compatibility modes. Those settings are better managed by official clients or remote-admin tooling, which understand the firmware's evolving beacon configuration model. In Meshtastic-Android, for example, a broadcast target can use a `Default` channel choice represented by an unset channel index; forcing a numbered local channel is different because it carries that slot's name and key. MMRelay does not duplicate that editor.

## Enabling the listener

```yaml
plugins:
  mesh_beacon:
    active: true
    #announce: true
    #announce_repeats: false
    #relay_channel: 0
```

When the plugin starts, and again after a radio reconnect completes setup, it verifies that both the installed Meshtastic client and the connected firmware expose Mesh Beacon configuration. It then sets only `FLAG_LISTEN_ENABLED` when necessary. Every other Mesh Beacon flag and setting is preserved byte-for-byte by policy; MMRelay does not enable broadcasting or rewrite an existing broadcast configuration.

Firmware 2.8 enables Mesh Beacon listening by default, so a normal node commonly needs no write at all. A firmware build compiled without Mesh Beacon support is reported as unsupported and otherwise left alone.

`listen` is not a scanner. Mesh Beacons are zero-hop RF packets and the radio can receive only packets compatible with its current RF/channel settings. Cross-preset discovery still requires a radio to be on the transmitting preset at the time the beacon is sent.

## Matrix announcements

By default, a newly captured invitation is announced in the Matrix room mapped to the Meshtastic channel on which the packet was received. Set `relay_channel` to route every announcement to the room(s) mapped to one chosen Meshtastic channel instead. The override is Matrix routing only; it never changes the radio.

Ambient announcements contain the offered channel name, sender, optional message, advertised radio information, receive signal, and a stable short ID. They do **not** print the offered PSK. RF-provided strings are escaped before Markdown rendering.

Periodic repeats are deduplicated by sender plus offered channel, matching the useful part of the Android invitation model. The most recent packet refreshes the saved invitation, receive count, timestamp, and signal. If the invitation contents change, it can be announced again. Delivery is tracked per Matrix room, so one failed room does not suppress a later retry there. Set `announce_repeats: true` only when every periodic packet should be posted.

The last 20 actionable invitations are retained in MMRelay's local plugin data so commands still work after restart. The stored protobuf includes the advertised channel credential because it is required to reconstruct the join URL; it is not emitted into room timelines except through an explicit URL or QR command.

## Recall commands

`!beacons` and `!mesh_beacon` are aliases. Invitations are scoped to the Matrix/Meshtastic channel mapping: a room sees invitations received on its channel. When `relay_channel` is configured, the room mapped to that channel acts as the central inbox and can see all captured invitations.

- `!beacons` — list standing invitations with stable IDs, sender, receive count, and last-seen age.
- `!beacons show ID` — show decoded metadata without exposing the channel credential.
- `!beacons url ID` — render a Meshtastic share URL and the equivalent `meshtastic --seturl '<url>'` command.
- `!beacons qr ID` — post the same URL as a QR image for review/import in a Meshtastic client.
- `!beacons dismiss ID` — remove one saved invitation.
- `!beacons clear` — clear invitations visible from the current room.

A numeric list position can be used instead of the stable ID, but IDs remain valid as the list is reordered by newer receptions.

## Join URL behavior

The URL contains the exact advertised `ChannelSettings`, with position sharing forced off before export. When the beacon advertises enough radio information, MMRelay includes a fresh LoRa configuration using Meshtastic's normal join defaults (`use_preset`, hop limit 3, transmit enabled), then applies the advertised region, preset, and frequency slot when present. If a beacon omits region or preset, MMRelay uses the receiver's radio settings captured when the invitation arrived as the fallback.

This mirrors the safety property of Android's switch flow: do not copy stale bandwidth, spreading factor, coding rate, frequency override, or other RF pins from the relay's current mesh into a new invitation. If a safe full radio configuration cannot be reconstructed, the URL remains channel-only rather than inventing missing RF values.

`meshtastic --seturl` has replacement semantics: it can replace the node's channel list and, when the URL carries LoRa configuration, retune the radio. QR import in an official client gives the operator a review step. Treat Mesh Beacon invitations as untrusted input: they are unsigned RF advertisements, not authenticated administrative instructions.

## What gets ignored

Message-only Mesh Beacons are consumed but are not retained as join invitations because they cannot produce a channel URL or QR code. Malformed decoded payloads are logged and consumed without entering the standing history. Beacons originating from the relay's own node are ignored.

MMRelay does not attempt to infer or repair a broadcaster's target configuration. In particular, it does not translate a local channel slot into Android's `Default` target sentinel, configure cross-preset targets, or change broadcast timing. Those operations remain outside this plugin's scope.
