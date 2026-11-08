# MMRelay 1.5 / MEDRE 0.1 Matrix event contract

## Interoperability goal

MEDRE and MMRelay share Matrix rooms. This boundary makes MMRelay use
MEDRE's standardized event contract for those shared rooms instead of its
legacy hand-rolled body and reply parsing. MMRelay keeps its own Matrix
session and transport; the shared room is the interoperability surface.

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
- The canonical `payload["body"]` and `relations` are authoritative for
  eligible ordinary Matrix text and explicit replies at relay ingress. A Matrix thread
  compatibility fallback is **not** an authored reply, and the reply fallback
  quote is stripped from the canonical body even when reply interactions are
  disabled because it is presentation metadata rather than authored text.
  Original nio objects remain available to legacy MMRelay plugin callbacks and
  mesh relay helpers; MMRelay-specific wire fields remain in the incoming
  content for its relay features. Mesh reactions still follow MMRelay's
  existing queue contract, and native reaction classification still uses
  MMRelay's existing handler.
- A codec failure is isolated at the boundary: MMRelay logs the exception and
  falls back to the legacy nio body and reply parse so an unexpected Matrix
  event shape cannot drop a relay message.
- Meshtastic→Matrix **core relay outbound** now constructs an immutable MEDRE
  `CanonicalEvent`, uses `MatrixRenderer` and validates its closed
  `MatrixOutboundOperation` before the existing nio `room_send` path. MMRelay
  still owns transport acknowledgements, stable retry transaction IDs, E2EE
  policy, database message mapping, and rendering of its historical Markdown,
  Matrix reply fallback, and `meshtastic_*` wire presentation fields.
- For radio reactions, the exact reaction symbol is carried as
  `meshtastic_reaction_key`, independent of the human-readable emote body. MMRelay
  consumes this field preferentially on Matrix ingress; older relays without it
  still use the historic emote-text parser. The
  canonical event is `message.reacted` with a radio-native target reference;
  no Matrix `m.reaction` annotation or cross-room identity is invented. Normal
  radio replies carry a Matrix-native target scoped to the destination room;
  missing historical quote context never fabricates a target.
- A non-message Matrix render, altered authored body, or lost reply relation
  **fails closed** before the SDK send. The original event's canonical body
  and explicit reply/reaction relationship remain the semantics that MEDRE
  decodes on the other side of a shared room. Multiline reply quotes prefix
  every original line and cannot truncate the authored reply at a blank line.
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

## Git-install pins and distribution caveat

The current `pyproject.toml` contains two immutable direct Git references:

- MEDRE core at `0fd9f754c91a249a2daea2bf6d10e6e5fec7f976`, the pushed
  0.1.0 schema-freeze commit.
- `medre-adapter-matrix` at the requested
  `241e9590035bb18750527c3ea1f02d26f28acfb5`; its schema-freeze follow-up
  changes tests and documentation only.

The adapter declares `medre>=0.1.0,<0.2.0` (not published yet at this
snapshot), so **the adapter Git pin alone is not a complete install**. The
core pin is needed until there is an index source satisfying that requirement.
The core pin is the pushed schema-freeze commit; repin it to the merged core
main commit before tagging MMRelay 1.5.0. The adapter declares the E2EE extra
in its base dependencies, so an MMRelay 1.5 base install includes the Matrix
crypto backend; preserving a crypto-free base footprint requires a separate
adapter metadata split.

**Release blocker for PyPI:** PyPI rejects distributions that declare direct
URLs under `Requires-Dist`. Thus these `pyproject.toml` pins support source or
GitHub releases and `pip install` from the repository, **not a PyPI-uploaded
MMRelay 1.5.0 wheel**. For a PyPI release, MEDRE and the adapter must first
be available through an index that pip can resolve, and MMRelay must use
index-resolvable requirements rather than Git URLs. Moving a Git pin to an
optional extra does not remove that upload restriction. If the first 1.5.0
release is source/GitHub-only, document that channel restriction explicitly.

## Pre-tag gates

1. Merge the MEDRE core/Matrix/Meshtastic schema freezes and rerun the
   adapter's native-namespace JSON Schema tests. Repin the core dependency to
   the merged core main commit; the Matrix pin stays at the requested SHA
   because its schema-freeze follow-up is tests/docs only. These release
   branches do not tag or publish any package.
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
5. Decide the 1.5.0 distribution channel before a tag; a PyPI upload with
   direct Git `Requires-Dist` entries will be rejected.

The **core radio-to-Matrix outbound content renderer** is now MEDRE-backed,
without moving delivery acknowledgements, identity, edit/delete authorization,
plugin send APIs, or Matrix upload sessions into the MEDRE runtime. All four
schema-freeze PRs must be merged before MMRelay 1.5.0 is tagged. The pinned
adapter commit supplies the renderer API without depending on its unmerged
schema-freeze test/doc additions.
