# What's New in MMRelay 1.5.0 (release candidate)

**Not a release announcement.** This document describes the proposed 1.5.0 branch;
verify the published artifact, version tag, and current deployment image before
upgrading. MMRelay 1.4.1 remains the last release supporting Python 3.11.

## Matrix interoperability

The core relay adopts MEDRE 0.1 canonical Matrix ingress and outbound rendering,
while retaining its current nio/E2EE session, plugins, storage, Matrix reply
fallback, and Meshtastic transport. It does not start a second Matrix or
Meshtastic client. Plugin-authored messages and media uploads retain their
existing direct-send paths. A failed ingress codec falls back to legacy parsing;
invalid outbound renderer operations fail closed instead of being sent.

## Compatibility and privacy

- **Python 3.12 or later is required.** Preserve E2EE key stores and message
  mappings during upgrades. Test a backup and roll-back strategy before switching.
- MMRelay maintains existing `meshtastic_*` wire-field compatibility. In mapped
  rooms, `meshtastic_text` may override the visible Matrix body for the
  Meshtastic payload, and sender-provided `meshtastic_longname` and
  `meshtastic_meshnet` may affect prefixing and routing. These fields are
  **not authenticated merely because they appear in Matrix content**. Operators
  should restrict mapped-room senders accordingly; a strict relay-identity
  trust policy has not been introduced in this release candidate.
- New outbound `medre.envelope.source_transport_id` exposes the native
  Meshtastic node ID when available; in publicly readable or unencrypted
  Matrix rooms this is a stable, linkable radio identity. Packet/native
  references can also reveal correlation information. **No opt-out is
  implemented yet.** Decide whether this disclosure is acceptable before
  releasing 1.5.0 to users.
- Canonical envelope metadata is descriptive provenance, not proof of
  authenticated Meshtastic radio authorship, Matrix-room authorization,
  successful delivery, or user identity.

## Distribution and deployment

The release-candidate source manifest currently pins unpublished MEDRE core
and the Matrix adapter to immutable Git commits, including older pre-freeze
revisions. **These must be repinned to the merged freeze commits and the lock
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
manifest; decide the trust and node-identity disclosure policies; repin and
relock MMRelay; rerun full Python 3.12–3.15 CI, Windows installation, live
Matrix/E2EE, reply/emote/reaction and multi-meshnet round trips, plugin
interference, radio reconnects, and clean install/upgrade/rollback drills.
