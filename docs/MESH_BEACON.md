# Firmware-native Mesh Beacons

The `mesh_beacon` core plugin configures the node's Mesh Beacon module on startup
and after reconnection. Firmware performs broadcasts and temporary radio changes;
MMRelay does not construct or decode beacon packets. Changes to packet payloads
therefore do not require a second beacon implementation in the relay.

The plugin is disabled by default. It requires firmware that reports the
`mesh_beacon` module and a client schema with indexed broadcast targets. Earlier
firmware continues to work with MMRelay; enabling this plugin on it logs a
configuration error and leaves the radio configuration untouched.

```yaml
plugins:
  mesh_beacon:
    active: true
    broadcast: true
    listen: true
    legacy_split: true
    message: "Join our mesh"
    interval_seconds: 3600
    offer_channel_index: null
    targets:
      - preset: "MEDIUM_FAST"
        channel_index: 0
      - preset: "SHORT_FAST"
        channel_index: 1
```

Configure those channel slots on the node first and choose presets valid for its
region. At least one target must differ from the node's running preset. A maximum
of four targets is supported, each with an explicit `preset` and `channel_index`.
The node must use a standard preset, an automatically derived frequency slot
(`lora.channel_num: 0`), and no frequency override. All targets use its current
region. Duplicate preset/channel pairs are rejected.
Secondary target channels need explicit PSKs, and blank secondary targets are
refused because older firmware redirects them to a different channel. Legacy
per-channel frequency slots are also refused: newer firmware uses target slot
fields instead.

`offer_channel_index: null` advertises the running preset and region without
sharing a channel key. Selecting an index explicitly advertises that channel's
name and PSK to recipients of the beacon. Use it only for a channel you intend to
share. A secondary channel offered this way needs an explicit PSK: its inherited
primary key cannot be conveyed by a blank PSK. Names are limited to 11 UTF-8
bytes and keys to 32 bytes, matching the firmware's channel storage.
Messages and offered channel names must not contain NUL characters, which
firmware string handling would truncate.

Compatibility uses the shared configuration fields in the initial and newer
frequency-slot schemas, with a 60-byte UTF-8 message limit. Intervals range from
3600 to 2147483 seconds so milliseconds fit older firmware's signed timer.
The firmware's optional `legacy_split` flag sends human-readable text separately
for older receiving nodes; leaving it or `listen` out preserves its current value.

Pinned-slot offers, per-target frequency slots, and per-target regions are not
supported by this plugin. A newer client descriptor alone cannot establish that
the connected firmware implements a new field. The plugin refuses pinned node
settings and unrecognized target options instead of silently advertising a
different mesh. On newer schemas it clears any stale offer frequency slot when
configuring its default-slot policy, and recreates targets without slot pins.
Supporting pinned slots requires an agreed firmware capability boundary and
radio validation once that firmware is available.

Protobuf preserves unknown fields received from firmware. If the client cannot
name every field in an existing beacon policy, broadcasting configuration is
refused until its schema is upgraded. Otherwise an invisible slot pin could be
sent back with a changed offer. Disabling broadcasting remains possible and
preserves those unknown fields.

Setting `broadcast: false` clears the broadcast bit while preserving broadcast
settings; optional listen and compatibility flags can still be changed. Matching
configuration causes no write. If a transport write raises an error, the plugin
restores the cached configuration; this does not undo a write already accepted
by the device or confirm a firmware acknowledgement.
