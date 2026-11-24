# Sharing Rooms with Other Relays

Several relays can bridge different radio meshes into the same Matrix room:
two MMRelay instances each bridging their own mesh, or an MMRelay instance
alongside [MEDRE](https://github.com/jeremiah-k/medre), a separate bridge
project that can also serve the room. MMRelay instances configure trusted
peer Matrix accounts; MEDRE instead allowlists rooms and treats embedded
relay provenance as untrusted. One deployment rule reduces echo-loop risk,
but trust settings do not provide distributed loop prevention. This page covers shared rooms generally; see the
[MEDRE side of the setup](https://github.com/jeremiah-k/medre/blob/main/docs/ops/shared-rooms-with-mmrelay.md)
for what a MEDRE operator configures.

## How shared rooms work

MMRelay uses MEDRE's message format for radio messages it posts to Matrix,
which is what lets MMRelay and MEDRE share a room. The original visible body is retained, but a second relay may add its own
visible prefix rather than stripping the first one. For example,
`[node/mesh]: [sender/origin]: message` has two expected attribution layers;
this is not necessarily a loop. The needed MEDRE packages install with
MMRelay. Reply and reaction correlation depends on the receiving transport's
supported relation bindings, not an arbitrary cross-transport guarantee.

## Trust every relay in the room

Every MMRelay instance sharing a Matrix room should run 1.5.0 or later
and trust every other relay in it. This is a **deployment requirement** in
this guide, not a runtime version check that prevents older instances from
joining the room. Configure `matrix.trusted_relays` on **each**
relay with the exact Matrix user ID of **every other** relay bot in that
room. For two instances, configure both directions:

On relay A (`@relay-a:example.org`):

```yaml
matrix:
  trusted_relays:
    - "@relay-b:example.org"
```

On relay B (`@relay-b:example.org`):

```yaml
matrix:
  trusted_relays:
    - "@relay-a:example.org"
```

To share the room with a MEDRE bridge instead of (or in addition to) another
MMRelay instance, list the MEDRE bot's Matrix user ID the same way. The list
is empty by default, and entries must match exactly — no wildcards, no
partial names. `mmrelay config check` rejects malformed entries.

Verify each account before trusting it: use the observed Matrix sender ID in
the drop warning (see below) to check the peer account.

## Keep one relay per radio mesh

Avoid message loops: do not connect two relays to the same Meshtastic mesh
and Matrix room unless you have separately tested how loops are prevented in
your topology. Mutual trust is required for shared-room peer handling, **but
it is not a general-purpose loop-prevention mechanism**; incorrect room or
radio topology can still cause repeated traffic.

As a plain rule: keep one relay per radio mesh. Two relays attached to the
same mesh while sharing a room relay each other's messages back until the
mesh and the room flood. With one relay per mesh — each bridge attached to
its own mesh, meeting only in shared rooms — the direct same-mesh echo cycle
is avoided. This topology does not guarantee exactly-once transmission or
eliminate every cross-instance loop or duplicated retry.

MMRelay also drops a trusted peer message whose reported meshnet matches its
own local meshnet, so a same-mesh echo cannot relay through it twice.

## Warnings you may see

- A message that claims to come from a relay but whose sender is not listed
  is **dropped**, with a log warning naming the sender's Matrix account.
- A listed sender whose relay message is malformed is dropped too, with a
  separate warning. Check the peer relay's version rather than looking for a
  bypass.

Warnings repeat at most once every five minutes **per sender and rejection
reason** in a running process; further drops are logged at DEBUG. The
rejection logs omit message bodies and raw envelopes. Every rejected claim
is dropped, whether or not it generates another warning.

If you see one of these warnings, use the **observed Matrix sender ID** in
the log to check the peer account before updating `matrix.trusted_relays`.

## What relays and what drops

Ordinary Matrix messages **without** the reserved MEDRE claim continue to
relay as before, including messages from human users. A user who sends a
MEDRE claim is subject to the same trust check as a peer bot. A trusted peer
message addressed to the local relay's own meshnet is dropped instead of
relayed back onto that mesh.

**Do not mix older relay versions into a shared-room deployment.** Upgrade
every participating instance and configure mutual trust before using the
room for cross-mesh relaying.

## Privacy notes

Relay events may carry stable Meshtastic node IDs in Matrix event metadata.
People and clients with access to the room can correlate those IDs across
Matrix rooms and radio meshes, even if the IDs are also broadcast over
radio. Treat them as privacy-relevant. Trusting a relay's Matrix bot account
does **not** authenticate its claimed radio sender, verify that a displayed
radio name is genuine, or prove delivery to a radio node.

## Moving an account between MMRelay and MEDRE

MMRelay and the MEDRE Matrix adapter use the same Matrix SDK and the same
store layout, so an MMRelay E2EE store is not foreign material to a MEDRE
installation or vice versa. If you are moving a bot account from an MMRelay
installation to MEDRE, MEDRE documents how to import the existing
account-signing file in
[sharing a room with MMRelay](https://github.com/jeremiah-k/medre/blob/main/docs/ops/shared-rooms-with-mmrelay.md#migrating-a-matrix-account-from-an-mmrelay-installation).
Keep tokens and device databases in their original stores; move only the
signing file, and stop the relay before moving anything.
