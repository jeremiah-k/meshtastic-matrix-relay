# What's New in MMRelay 1.5.0 (release candidate)

**Not a release announcement.** This document describes the proposed 1.5.0 branch;
verify the published artifact, version tag, and current deployment image before
upgrading. MMRelay 1.4.1 remains the last release supporting Python 3.11.

## Matrix interoperability

The core relay adopts MEDRE 0.1 canonical Matrix ingress and outbound rendering,
while retaining its current nio/E2EE session, plugins, storage, Matrix reply
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
- Longnames and shortnames travel through generic sender attribution; explicitly
  public source facts carry portnum and meshnet label through Matrix, LXMF, and
  XMPP. Reactions require an exact nonempty key; packet ID zero remains valid.
- New outbound `medre.envelope.source_transport_id` carries the native
  Meshtastic node ID when available, letting Matrix-side consumers relate
  events before a long name is known. On Meshtastic firmware 2.8 and newer,
  node IDs are advertised openly on the mesh and are regenerated together
  with the node's keys, so they are neither private nor a stable identity;
  on older firmware they remain stable identifiers.
- Canonical envelope metadata is descriptive provenance, not proof of
  authenticated Meshtastic radio authorship, Matrix-room authorization,
  successful delivery, or user identity.

## Distribution and deployment

The release-candidate source manifest currently pins unpublished MEDRE core
and the Matrix adapter to immutable Git commits, for coordinated testing. **These must be repinned to the merged freeze commits and the lock
regenerated before tagging**. A wheel with direct-URL `Requires-Dist` cannot
be uploaded to PyPI; the publishing workflow skips such uploads. Choose
between GitHub/source distribution with exact Git pins and a public indexed
release with versioned MEDRE packages. Test installs from the intended
channel, not from an editable developer checkout.

The chart version, Helm default image tag, and Kustomize image tag track the
1.5.0 release branch. Do not deploy these tags until a matching image exists.
For production, prefer immutable image digests.

## Release validation gates

Before publishing: finish the three MEDRE schema freezes and compatibility
manifest; qualify the exact dependency pair and final release artifacts; rerun full Python 3.12–3.15 CI, Windows installation, live
Matrix/E2EE, reply/emote/reaction and multi-meshnet round trips, plugin
interference, radio reconnects, and clean install/upgrade/rollback drills.
