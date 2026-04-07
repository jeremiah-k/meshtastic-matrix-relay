# Meshtastic Inbound Packet Routing Policy

## Document Status

- **Phase**: Groundwork + Phase 1 implementation target
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

1. Resolve channel as today.
2. Resolve sender names and build `formatted_message` as today.
3. Classify packet using centralized routing policy.
4. Run plugin pipeline (plugins still receive `formatted_message`, `longname`, `meshnet_name`).
5. Branch by policy action:
   - `PLUGIN_ONLY`: log and return (no Matrix relay).
   - `RELAY`: continue current DM/plugin/channel-mapped checks, then relay.
   - `DROP`: return.

For packets with no text:

- Keep existing non-text plugin path unchanged.

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

### Phase 1 (this groundwork)

- Add a dedicated packet routing helper module (Meshtastic subpackage).
- Integrate it into `on_meshtastic_message` text path.
- Move detection sensor gate from inline event logic into routing policy.
- Add focused regression tests for routing outcomes.

### Phase 2 (optional, after validating defaults)

- Add compact operator overrides under a small routing config surface.
- Keep defaults unchanged for backward compatibility.
- Consider optional `DROP` routing and/or diagnostic room routing only if real use cases emerge.

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

Run only targeted pytest module(s) for this change set.

---

## 9. Handoff Notes for Follow-On Agents

If continuing this work:

1. Keep routing policy decisions in one helper module; do not re-scatter checks in `events.py`.
2. Preserve plugin argument shape for compatibility.
3. Avoid introducing a large config schema in the same bugfix PR.
4. Keep logs explicit when skipping Matrix relay due to policy classification.
5. Add only targeted tests for changed behavior; leave full-suite validation to CI.
