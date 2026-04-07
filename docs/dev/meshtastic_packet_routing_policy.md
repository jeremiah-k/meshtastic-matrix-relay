# Meshtastic Inbound Packet Routing Policy

## Document Status

- **Phase**: Phase 1 + Phase 2 + follow-up hardening
- **Date**: 2026-04-07
- **Scope**: Meshtastic -> Matrix inbound routing (`on_meshtastic_message`)
- **Primary issue**: text-bearing non-chat portnums (example: `RANGE_TEST_APP`) are being relayed as normal chat

---

## 1. Problem Summary

Current inbound logic relays to Matrix chat when a packet has `decoded.text`, even when
the packet `portnum` is not chat semantics.

Observed failure mode:

- `RANGE_TEST_APP` packets with `decoded.text` and mapped channel are relayed into normal
  Matrix rooms.
- Matrix clients then suppress or ignore these messages, creating noisy/incorrect relay behavior.

Architecturally, this is a routing-policy issue, not a stale-packet/backlog filter issue.

---

## 2. Goals and Non-Goals

### Goals

1. Make Matrix chat relay **portnum-driven** rather than `if text` driven.
2. Preserve plugin extensibility: non-chat packets should still be available to plugins.
3. Keep behavior backward compatible for normal text chat (`TEXT_MESSAGE_APP`).
4. Keep Phase 1 config surface minimal (no config churn), but leave a clean path for Phase 2 operator overrides.

### Non-Goals (Phase 1)

- No broad config schema expansion.
- No reaction/reply semantic expansion to non-text-chat portnums.
- No diagnostic-room routing yet.
- No changes to plugin loader or plugin base interfaces.

---

## 3. Decision: Central Packet Routing Policy

Introduce a dedicated Meshtastic packet routing policy layer:

- **RELAY**: eligible for normal Matrix room relay (still subject to existing DM/plugin/channel checks)
- **PLUGIN_ONLY**: send through plugin pipeline, skip Matrix chat relay
- **DROP**: reserved action for future policy extensions (not required for initial behavior change)

Default Phase 1 policy:

| Portnum                       | Default action                                                         | Notes                               |
| ----------------------------- | ---------------------------------------------------------------------- | ----------------------------------- |
| `TEXT_MESSAGE_APP`            | `RELAY`                                                                | Existing chat behavior preserved    |
| `DETECTION_SENSOR_APP`        | `RELAY` when `meshtastic.detection_sensor` is true, else `PLUGIN_ONLY` | Moves existing gate into policy     |
| `RANGE_TEST_APP`              | `PLUGIN_ONLY`                                                          | Fixes current leak into Matrix chat |
| Unknown text-bearing portnums | `PLUGIN_ONLY`                                                          | Safe default for future apps        |

This provides a chat allowlist posture while keeping plugin visibility broad.

---

## 4. Target Execution Flow (`on_meshtastic_message`)

For packets where `decoded.text` exists:

1. Classify packet using centralized routing policy.
2. If action is `DROP`, return immediately.
3. Resolve sender names and build `formatted_message`.
4. Run plugin pipeline (plugins still receive `formatted_message`, `longname`, `meshnet_name`).
5. Branch by policy action:
   - `PLUGIN_ONLY`: return (no Matrix relay).
   - `RELAY`: apply channel deduction/mapping checks, then relay to Matrix.

For packets with no text:

- Keep existing non-text plugin path unchanged.

Important behavior guarantee:

- `PLUGIN_ONLY` text packets do **not** require a channel/mapped room to reach plugins.
- Channel deduction/mapping is a relay-only concern.

---

## 5. Why This Direction

### Why not blacklist?

Blacklist behavior is reactive: unknown text-bearing app traffic leaks into chat until someone
adds another deny rule.

### Why not one-off `RANGE_TEST_APP` config flag?

One-off flags do not scale and accumulate policy debt.

### Why chat allowlist + plugin visibility split?

- Human chat rooms stay clean by default.
- Plugin ecosystem keeps full observability/control.
- Future behavior can be tuned per portnum without redesigning event flow.

---

## 6. Backward Compatibility Notes

Preserved:

- `TEXT_MESSAGE_APP` chat relay path.
- Reaction/reply logic remains tied to `TEXT_MESSAGE_APP` interaction semantics.
- Existing non-text plugin dispatch behavior.

Intentional behavior tightening:

- Non-chat text-bearing packets no longer relay to Matrix chat by default.
- Detection sensor disabled path becomes plugin-visible (`PLUGIN_ONLY`) instead of early return.

---

## 7. Phase Plan

### Phase 1 (complete)

- Add a dedicated packet routing helper module (Meshtastic subpackage).
- Integrate it into `on_meshtastic_message` text path.
- Move detection sensor gate from inline event logic into routing policy.
- Add focused regression tests for routing outcomes.

### Phase 2 (complete)

- Add compact operator overrides under `meshtastic.packet_routing` config section.
- `chat_portnums`: list of additional portnum names to promote to chat relay.
- `disabled_portnums`: list of portnum names to drop entirely (not relayed, not sent to plugins).
- `disabled_portnums` takes precedence over `chat_portnums` when both list the same portnum.
- Detection sensor gate still applies even when listed in `chat_portnums` (disabled detection sensor blocks relay regardless).
- Keep defaults unchanged for backward compatibility.
- Config surface accepts strings and lists under `chat_portnums` / `disabled_portnums`.
- Current resolver behavior:
  - unknown numeric / non-string entries are filtered out,
  - arbitrary non-empty strings are accepted as provided and matched by name at runtime,
  - typos in string names are not rejected by strict enum validation in this phase.

Config shape:

```yaml
meshtastic:
  packet_routing:
    chat_portnums: # Additional portnums to relay like chat
      - "RANGE_TEST_APP"
    disabled_portnums: [] # Portnums to drop entirely
```

### Future considerations

- Per-portnum diagnostic room routing only if real use cases emerge.
- Finer-grained action vocabulary if needed beyond RELAY / PLUGIN_ONLY / DROP.

### Follow-up hardening (applied)

- Routing classification is performed before relay-only channel gating in the text pipeline.
- `DROP` short-circuits early.
- `PLUGIN_ONLY` delivery reaches plugins even when packet channel cannot be deduced.
- Reaction/reply Matrix interaction paths are explicitly limited to `TEXT_MESSAGE_APP` semantics.

---

## 8. Testing Strategy (Targeted)

Primary test file: `tests/test_meshtastic_utils_message_paths.py`

Required assertions:

1. `RANGE_TEST_APP` + text + mapped channel -> plugins called, Matrix relay not called.
2. `TEXT_MESSAGE_APP` + text -> Matrix relay still called.
3. Unknown numeric portnum + text -> plugin-only, no Matrix relay.
4. `DETECTION_SENSOR_APP` + `detection_sensor=false` -> plugin-only, no Matrix relay.
5. `DETECTION_SENSOR_APP` + `detection_sensor=true` -> relay allowed.
6. Plugin-only text packets do not create Matrix relay side effects.

Phase 2 additional assertions:

1. `chat_portnums` override promotes a non-chat portnum to relay.
2. `chat_portnums` accepts a single string value (not just a list).
3. `disabled_portnums` drops a packet entirely (no relay, no plugins).
4. `disabled_portnums` does not affect TEXT_MESSAGE_APP unless explicitly listed.
5. `disabled_portnums` takes precedence over `chat_portnums`.
6. Detection sensor gate still applies even with `chat_portnums` override.

Run only targeted pytest module(s) for this change set.

---

## 9. Handoff Notes for Follow-On Agents

If continuing this work:

1. Keep routing policy decisions in one helper module; do not re-scatter checks in `events.py`.
2. Preserve plugin argument shape for compatibility.
3. Keep logs explicit when skipping Matrix relay due to policy classification.
4. Add only targeted tests for changed behavior; leave full-suite validation to CI.
5. Config overrides live under `meshtastic.packet_routing`; see `src/mmrelay/constants/config.py` for key constants.
