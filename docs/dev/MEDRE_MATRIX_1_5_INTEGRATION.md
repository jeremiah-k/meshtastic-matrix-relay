# MMRelay 1.5 / MEDRE 0.1 Matrix event contract

## Interoperability goal

MEDRE and MMRelay share Matrix rooms. This boundary makes MMRelay use
MEDRE's standardized event contract for those shared rooms instead of its
legacy hand-rolled body and reply parsing. MMRelay keeps its own Matrix
session and transport; the shared room is the interoperability surface.

## Approved release completion requirements

The implementation follows the approved canonical-only contract below.
Runtime qualification and final package publication remain required before tagging:

- Emit and consume canonical MEDRE peer metadata without flat `meshtastic_*`
  content fields or a legacy compatibility mode.
- Accept peer metadata assertions only from the relay itself or exact bot MXIDs
  in a global allowlist. Ordinary human text still relays; untrusted metadata
  cannot supply source identity, routing, body overrides, or relation targets.
  No wildcard, trust-on-first-use, or room power-level rule substitutes for the
  allowlist. Signed-event support is designed in MEDRE first for a later version.
- Deliver the authored visible/canonical body to the radio. Remove the
  `meshtastic_text` override; require an exact structured key for reactions.
  Untargeted authored emotes remain ordinary messages.
- Preserve source identity, packet ID zero, scoped reply/reaction references,
  and sender attribution through generic canonical fields. Deliver source-owned
  facts such as Meshtastic portnum through a bounded, validated transport-neutral
  projection, with Matrix and non-Matrix tests proving preservation.
- Publish stable core and adapter packages independently, and use index-resolvable
  dependencies for release artifacts. Keep explicit Git branches, tags, commits,
  forks, and editable checkouts supported for development. Official channel and
  saved custom-source selection is a package-management implementation task.

The coordinated MEDRE and Matrix schemas exclude `interop.mmrelay` from
Matrix-native metadata. Runtime tests validate source-fact delivery and inbound
authorization against the proposed source trees; live shared-room, E2EE, hardware,
and release artifact qualification remain release gates.

## Canonical outbound source facts (implementation slice)

The radio-to-Matrix core relay emits **no flat `meshtastic_*` content keys**.
It constructs MEDRE `CanonicalEvent` with the authored body, packet ID
(including zero) as a scoped `source_native_ref`, explicit reply/reaction
relations, and the native radio node as `source_transport_id`. Public radio
source facts use only `metadata.native.data["meshtastic"]["public_facts"]`
in the source event. MEDRE projects those bounded values into the generic
`medre.metadata.bridge_context.source.native_facts` object; its namespace is
`meshtastic`, with `portnum` and an optional `meshnet_label`. Sender longname
and shortname use `bridge_context.source.sender_name` and
`sender_short_name` through a generic `RenderingContext.source_attribution`.
The Matrix adapter remains the sole event renderer and existing nio transport
continues to own send, E2EE, and durable message mapping.

Source facts are **untrusted remote claims**. MMRelay authorizes them only when
the observed Matrix sender exactly matches a global `matrix.trusted_relays`
entry (or the relay's own identity). Self echoes are suppressed by the relay.
The setting defaults to an empty list and configuration validation rejects
wildcards and malformed entries. For example:

```yaml
matrix:
  trusted_relays:
    - "@peer-relay:example.org"
```

The codec-normalized envelope and bridge context must agree on version, event
identity, source adapter, sender, channel, native reference, and relation types.
Invalid or unauthorized peer claims are ignored; ordinary authored text still
relays. No room power level or trust-on-first-use rule grants metadata authority.

The same bounded v1 `source.native_facts` context is supported by the LXMF
fields and XMPP stanza extension; other adapters do not automatically promise
lossless metadata exchange unless they explicitly support an embedding path.

## Decisions

- **Matrix adapter only** in MMRelay's base dependencies. MEDRE and MMRelay
  share Matrix rooms through this canonical event contract;
  `medre-adapter-matrix` owns Matrix ingress, native metadata and relation
  decoding.
  The Meshtastic adapter is for MEDRE to own a radio session; MMRelay already
  owns one through `mtjk`. Installing it here would introduce a competing
  transport lifecycle and does not improve Matrix event normalization.
- MMRelay continues to own the `mindroom-nio` Matrix client, authentication,
  E2EE/device stores, plugin callbacks, bridging rules, SQLite message map,
  and outbound Meshtastic queue. No MEDRE runtime/daemon or second Matrix
  session is started. `canonicalize_matrix_event` is an SDK-to-schema boundary for eligible
  mapped, non-suppressed relay messages.
- The visible canonical `payload["body"]` is authoritative. Flat
  `meshtastic_*` fields never supply names, routing, relation targets, or a
  hidden body override. Native Matrix reply fallback quoting is stripped once;
  authored quote lines remain. A thread without a reply marker cannot strip
  authored quotes. Explicit replies within threads retain their target; a
  compatibility parent marked `is_falling_back: true` is not an exact reply.
  Native Matrix relations take precedence over optional peer relation hints.
  Plugins receive the original nio event and the canonical text.
- A codec failure logs the exception and falls back to the native Matrix body
  and native relation parser, retaining the same prohibition on flat peer fields.
- Meshtastic→Matrix **core relay outbound** now constructs an immutable MEDRE
  `CanonicalEvent`, uses `MatrixRenderer` and validates its closed
  `MatrixOutboundOperation` before the existing nio `room_send` path. MMRelay
  still owns transport acknowledgements, stable retry transaction IDs, E2EE
  policy, database message mapping, and its Markdown and Matrix reply fallback
  presentation; no legacy flat `meshtastic_*` fields are emitted.
- For radio reactions, the exact reaction symbol is carried as
  an explicit `EventRelation.key`, independent of the human-readable emote
  body. Allowlisted peer relations require an explicit nonempty key and a
  scoped target. The canonical event is `message.reacted` with a radio-native target reference;
  no Matrix `m.reaction` annotation or cross-room identity is invented. Normal
  radio replies carry a Matrix-native target scoped to the destination room;
  unmapped radio replies retain their radio-native target in the generic context
  when reply interactions are enabled, without inventing a native Matrix relation. A missing historical quote preview
  never changes the authored body.
  Radio reply packet ID `0` is a valid target for both replies and reactions,
  including classification in the radio ingress handler.
- A non-message Matrix render, altered authored body, or lost reply relation
  **fails closed** before the SDK send. The provenance envelope must carry an
  integer version `1` and match the event's identity, kind, source adapter,
  source transport identity, native source reference, source channel, and
  relation types. The original event's canonical body
  and explicit reply/reaction relationship remain the semantics that MEDRE
  decodes on the other side of a shared room. Multiline reply quotes prefix
  every original line, including the attribution label, and cannot truncate
  the authored reply at a blank line. CRLF and lone CR line breaks in _stored
  quote previews only_ are normalized to LF and quoted line-by-line in both
  plain-text and HTML fallback; the canonical authored reply is not rewritten.
- Native plugin-owned `room_send` content and separately uploaded Matrix media
  are not reclassified as radio-originated canonical events. They retain their
  existing direct-send contract; this patch intentionally does not impersonate
  MEDRE attachment retention, edit/delete authorization, or plugin authorship.
- The canonical event has `schema_version = 1`. Its native Matrix namespace is
  `metadata.native.data["matrix"]` with independent `schema_version = 1`.
  MMRelay does not invent per-event E2EE provenance from the room's encrypted
  status, nor copy crypto keys/IVs into canonical metadata. The adapter owns
  the native schema; MEDRE core owns the canonical envelope.
- No MEDRE pre-0.1 compatibility readers/migrations will be introduced. The
  first _tagged_ 0.1.0 core and adapter releases establish the support
  baseline. MMRelay's existing historical message-map and E2EE-store behavior
  remains independent of MEDRE's experimental database formats.

## Trust and provenance boundaries

- Radio direct messages, including replies and reactions to mapped public
  messages, stay out of Matrix room delivery. Plugin handling remains available.

- Matrix members can provide arbitrary peer claims. Only exact globally
  allowlisted observed MXIDs may supply source names and public routing facts.
  Native Matrix relations remain native user actions. Replies and reactions
  require message mappings in the actual room; peer hints must also match the
  adapter and channel of the target namespace. Remote radio reaction IDs are
  never rebound to unrelated local packet IDs.
- `source.native_facts` are bounded, adapter-selected **claims**, not
  independently verified truth. Structural validation cannot authenticate
  their contents. Only a trusted observed MXID may supply peer metadata, and
  even trusted claims do not replace local relation-binding evidence.
- Same-mesh multi-relay deployments require verified peer allowlists and
  deployment-level loop qualification. An untrusted or pre-1.5 peer's meshnet
  claim (including legacy flat keys) never authorizes either forwarding or
  suppression. Do not assume this release prevents loops among untrusted
  relays on the same radio mesh.
- When a Meshtastic node ID is supplied to the outbound renderer,
  `medre.envelope.source_transport_id` embeds it in Matrix content. On
  Meshtastic firmware 2.8 and newer, node IDs are advertised openly on the
  mesh and derive from the node's public key. They remain linkable until the
  key changes; publishing them to Matrix rooms can correlate activity across
  transports, including in public rooms. Older firmware's identifiers also
  have this privacy consideration. Never treat radio node IDs or wire-envelope
  provenance as cryptographic proof of authorship.
- A Matrix emote with a radio packet ID but no eligible radio/Matrix
  reaction target is a normal authored message, not an empty reaction.
  Only explicit valid targets are relayed as reactions; packet ID zero is
  valid when a genuine target exists.

## Git-install pins and distribution caveat

The candidate `pyproject.toml` and `uv.lock` pin the coordinated test pair:

- MEDRE core at `a289675a51760c69e95466ce6fc8b5475f52bfa2`.
- Matrix adapter at `0b93dec40954f0253d2107ff6f7c9f7c932c7880`.

These immutable Git commits include bounded source facts, generic attribution,
and explicit canonical reaction keys. Repin to the final merged release pair
and repeat qualification before tagging; a development pin is not a release
signoff.

The adapter declares `medre>=0.1.0,<0.2.0` (not published yet at this
snapshot), so **the adapter Git pin alone is not a complete install**. The
core pin is needed until there is an index source satisfying that requirement.
The dependency regression test accepts immutable 40-hex Git references from
the canonical repositories or exact indexed version pins, so publishing the
MEDRE packages does not require weakening reproducibility. Every deliberate
repin still requires runtime compatibility qualification and a matching
lockfile update; a dependency's syntax alone is not proof of compatibility.
Repin both packages to their merged freeze commits before tagging MMRelay
1.5.0. Once those commits are published to PyPI, select their exact indexed
versions and regenerate the lockfile for a PyPI release. The adapter declares the E2EE extra
in its base dependencies, so an MMRelay 1.5 base install includes the Matrix
crypto backend; preserving a crypto-free base footprint requires a separate
adapter metadata split.

**Release blocker for PyPI:** PyPI rejects distributions that declare direct
URLs under `Requires-Dist`. Thus these `pyproject.toml` pins support GitHub
releases and `pip install` from the repository, **not a PyPI-uploaded MMRelay
1.5.0 wheel**. The `pypi-publish.yml` workflow checks _base and optional-extra_
requirements on the tagged tree and skips its upload action while direct
references remain; it still builds the release artifact for inspection. The
guard parses every requirement with the PEP 508 parser and permits an upload
only when no direct references remain. Malformed requirement groups fail the
preflight. An eligible manifest still needs the clean installation check below.

For a PyPI release, MEDRE and the adapter must first be available through an
index that pip can resolve, and MMRelay must use
index-resolvable requirements rather than Git URLs. Moving a Git pin to an
optional extra does not remove that upload restriction. Git/source installs
remain supported alongside stable indexed releases; source selection belongs to
deployment/development configuration rather than published direct requirements.

## Pre-tag gates

1. Merge the MEDRE core/Matrix/Meshtastic schema freezes and rerun the
   adapter's native-namespace JSON Schema tests. Repin both the core and Matrix
   adapter to their merged freeze commits: the adapter changes the native
   provenance projection as well as its tests and documentation. These release
   branches do not tag or publish any package.
   After the adapter merges, update MEDRE core's authoritative
   `.github/adapter-compatibility.json` to the landed adapter commits and run
   its ancestry and compatibility checks. The daily pin-drift canary expects
   those landed heads. Qualify MMRelay against this exact core/adapter pair.
2. Regenerate `uv.lock` **online** from the final MMRelay dependency list and
   confirm `uv lock --check`; this branch's lock resolves the two Git pins and
   adds `msgspec` and `termaid`. Regenerate it again after any pin change.
3. Run the complete MMRelay 3.12–3.15 CI matrix, Matrix E2EE/device recovery,
   plus live Matrix text/emote/reaction/reply and multiline quote roundtrips,
   mixed meshnet and plugin dispatch. Specifically verify Medre decodes the
   emitted `medre.envelope` and structured radio reaction key, while MMRelay's
   message-map writes still happen only after confirmed SDK sends.
4. Validate `pip install .` from a clean Python 3.12 environment, including
   its VCS-auth/network prerequisites, and validate package metadata with
   `python -m build` and `twine check`.
5. Execute the approved hybrid distribution policy before a tag: stable PyPI
   artifacts use index-resolvable requirements, with Git/fork/local source
   selections retained for development. A PyPI upload with direct Git
   `Requires-Dist` entries will be rejected.
   For PyPI, publish MEDRE core first, then its adapters (which depend on core),
   then replace MMRelay's Git references with the exact published versions.
   Inspect each wheel and sdist, verify its frozen schema bundle and manifest,
   and test a clean install using the index rather than local source overrides.

The **core radio-to-Matrix outbound content renderer** is MEDRE-backed and
now emits canonical metadata rather than legacy flat keys,
without moving delivery acknowledgements, identity, edit/delete authorization,
plugin send APIs, or Matrix upload sessions into the MEDRE runtime. The three
schema-freeze PRs and this MMRelay adoption PR must be merged before MMRelay
1.5.0 is tagged. The current adapter pin supplies the renderer API, but the
corrected native provenance projection requires the merged adapter freeze.

## Native radio reaction keys

A native radio tapback must supply a nonempty text key. Empty or malformed
keys are dropped instead of substituted with a warning symbol. The exact text
key, including Unicode selectors and skin-tone modifiers, becomes the
canonical relation key independently of its human-readable preview.

## Source channel identity

The radio packet's logical channel travels as canonical source_channel_id and
scopes both the source packet reference and radio reply/reaction targets.
The meshnet display label remains a separate public fact and origin label.
Callers without radio channel observations use the numeric primary channel
(`"0"`) as the source scope. A meshnet label never substitutes for a numeric
channel ID. Numeric IDs use ASCII decimal digits without leading zeros, so
references cannot split across alternative spellings of the same channel.
