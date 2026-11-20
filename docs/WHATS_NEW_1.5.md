# What's New in MMRelay 1.5.0 (release candidate)

**Not a release announcement.** This document describes the proposed 1.5.0 branch;
verify the published artifact, version tag, and current deployment image before
upgrading. MMRelay 1.4.1 remains the last release supporting Python 3.11.

## Matrix interoperability

The core relay adopts MEDRE canonical Matrix ingress and outbound rendering
(the published 0.2.0 packages; see Distribution), while retaining its current
nio/E2EE session, plugins, storage, Matrix reply
fallback, and Meshtastic transport. It does not start a second Matrix or
Meshtastic client. Plugin-authored messages and media uploads retain their
existing direct-send paths. A failed ingress codec falls back to native Matrix text and relation parsing;
invalid outbound renderer operations fail closed instead of being sent.

## Compatibility and privacy

- **Python 3.12 or later is required.** Preserve E2EE key stores and message
  mappings during upgrades. Test a backup and roll-back strategy before switching.
- Shared-room peers use canonical MEDRE metadata. Configure exact bot MXIDs in
  `matrix.trusted_relays` to accept their source names, mesh labels, portnums,
  and scoped relation hints. The allowlist defaults to empty and applies globally;
  it accepts no wildcards, implicit room trust, or trust-on-first-use.
  Ordinary human text remains eligible. Flat `meshtastic_*` wire fields are
  ignored, including hidden `meshtastic_text` body overrides.
  **Same-mesh relay deployment warning:** If multiple relays bridge the same
  radio mesh into the same Matrix room, configure all participating 1.5 bot
  MXIDs as trusted peers and validate loop suppression before enabling radio
  broadcasts. An untrusted peer or pre-1.5 relay is treated as ordinary Matrix
  text, including when it claims the local meshnet; that claim never grants
  suppression or authority. Do not co-deploy those configurations until an
  independent loop-prevention plan is qualified. Same-mesh multi-relay loop
  hardening remains outside this PR.
- Longnames and shortnames travel through generic sender attribution; explicitly
  public source facts carry portnum and meshnet label through Matrix, LXMF, and
  XMPP. Reactions require an exact nonempty key; packet ID zero remains valid.
- New outbound `medre.envelope.source_transport_id` carries the native
  Meshtastic node ID when available, letting Matrix-side consumers relate
  events before a long name is known. On Meshtastic firmware 2.8 and newer,
  node IDs are advertised openly on the mesh and derive from the node's public
  key. They remain linkable identifiers until that key changes. Exposing them in
  Matrix rooms (including public or unencrypted rooms) can correlate activity
  across transports even though the ID is also broadcast on the mesh. The same
  privacy consideration applies to older firmware's stable identifiers.
- Canonical envelope metadata is descriptive provenance, not proof of
  authenticated Meshtastic radio authorship, Matrix-room authorization,
  successful delivery, or user identity.

## Distribution and deployment

The source manifest pins the published MEDRE packages from PyPI:
`medre==0.2.0` and `medre-adapter-matrix==0.2.0`. Installs resolve through
the public index with ordinary versioned wheels; the release-candidate Git
pins and the direct-URL wheel restriction they carried are gone. Test
installs from the intended channel, not from an editable developer checkout.

The chart version, Helm default image tag, and Kustomize image tag still
carry the 1.4.1 values until the 1.5.0 metadata commit lands; do not deploy
release tags until a matching image exists. For production, prefer immutable
image digests.

## Candidate interoperability evidence (November 11, 2026)

The reviewed three-mesh normal-topology laboratory campaign reported six
passing scenarios, including shared-room fanout, channel parity, replies,
untrusted claims, and LXMF loopback. This supports the configured topology,
not arbitrary overlapping bridges on one radio medium. It does **not**
qualify native emoji-flagged radio tapbacks, the LXMF reply-origin counterpart,
a deployed XMPP/Prosody leg, device recovery, or final release artifacts.
The candidate Git pins have since been replaced by the published 0.2.0
packages, and hosted CI is green at the landed pins. These laboratory
results still do not substitute for qualification of the exact published
pair, the final release artifacts, and release signing.

## Release validation gates

Before publishing: qualify the exact published dependency pair
(`medre==0.2.0`, `medre-adapter-matrix==0.2.0`) and the final release
artifacts; rerun full Python 3.12–3.15 CI, Windows installation, live
Matrix/E2EE, reply/emote/reaction and multi-meshnet round trips, plugin
interference, radio reconnects, and clean install/upgrade/rollback drills.
The MEDRE schema freezes and compatibility manifest completed with the
upstream 0.2.0 release train; medre 0.2.1 (radio delivery fixes) is
published beyond this pin, and absorbing it before the tag is a release
decision that repeats the pair qualification.
