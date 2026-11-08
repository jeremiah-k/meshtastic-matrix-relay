"""MMRelay's canonical Meshtastic-to-Matrix presentation boundary.

MEDRE owns the immutable event, native reply relationship, Matrix operation
and provenance envelope. MMRelay continues to own its existing wire fields,
Markdown display convention and optional Matrix reply fallback quote. No
MEDRE transport client or delivery pipeline is instantiated here.
"""

from __future__ import annotations

import html
import re
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode

from medre.adapters.matrix.outbound import MatrixOutboundOperation
from medre.adapters.matrix.renderer import MatrixRenderer
from medre.core.events import (
    CanonicalEvent,
    EventMetadata,
    EventRelation,
    NativeMetadata,
    NativeRef,
)
from medre.core.rendering.renderer import RenderingContext
from medre.interop.mmrelay import (
    KEY_EMOJI,
    KEY_ID,
    KEY_LONGNAME,
    KEY_MESHNET,
    KEY_PORTNUM,
    KEY_REACTION_KEY,
    KEY_REPLY_ID,
    KEY_SHORTNAME,
    KEY_TEXT,
)

_MATRIX_TARGET = "mmrelay.matrix"
_MESH_SOURCE = "mmrelay.meshtastic"
_RENDERER = MatrixRenderer()


def _formatted_message(message: str) -> str:
    """Apply MMRelay's existing safe Markdown/HTML presentation convention.

    The unmodified message is always the canonical body. Formatted HTML is a
    display hint, never a second source of truth for reply interpretation.
    """
    # Imported here, not at module level, because the mmrelay.matrix_utils
    # facade imports mmrelay.matrix.relay which imports this module; a
    # module-level import would make direct imports of this boundary fail
    # with a circular ImportError.
    import mmrelay.matrix_utils as facade

    has_html = bool(re.search(r"</?[a-zA-Z][^>]*>", message))
    safe_message, has_prefix = facade._escape_leading_prefix_for_markdown(message)
    has_markdown = bool(re.search(r"[*_`~]", message)) or has_prefix
    if has_markdown or has_html:
        try:
            import markdown
            import nh3

            return nh3.clean(
                markdown.markdown(safe_message),
                tags={
                    "b",
                    "strong",
                    "i",
                    "em",
                    "code",
                    "pre",
                    "br",
                    "blockquote",
                    "a",
                    "ul",
                    "ol",
                    "li",
                    "p",
                },
                attributes={"a": {"href"}},
            )
        except ImportError:
            pass
    return html.escape(message).replace("\n", "<br/>")


def _reply_quote(
    room_id: str,
    reply_to_event_id: str,
    bot_user_id: str | None,
    original_text: str,
    original_meshnet: str | None,
) -> tuple[str, str]:
    """Build Matrix's optional human-readable reply fallback, HTML-escaped.

    The relation itself comes exclusively from MEDRE. The fallback is only
    added when the original MMRelay message mapping supplies known context.
    """
    if not isinstance(bot_user_id, str) or not bot_user_id.startswith("@"):
        raise ValueError("A Matrix user ID is required for a reply quote")
    # Only the human-readable fallback is normalized. Never rewrite the
    # authored canonical reply body. CR and CRLF count as physical line breaks
    # to text consumers and must not terminate the quoted fallback early.
    label = (original_meshnet or "Mesh").replace("\r\n", "\n").replace("\r", "\n")
    preview_text = original_text.replace("\r\n", "\n").replace("\r", "\n")
    via_server = bot_user_id.partition(":")[2]
    via_suffix = f"?{urlencode({'via': via_server})}" if via_server else ""
    reply_link = (
        "https://matrix.to/#/"
        f"{quote(room_id, safe='!:@')}/"
        f"{quote(reply_to_event_id, safe='$:@')}{via_suffix}"
    )
    bot_link = "https://matrix.to/#/" + quote(bot_user_id, safe="@:.")
    # Matrix fallback quoting must prefix EVERY line. Otherwise an original
    # message containing blank lines creates an early quote terminator, and
    # MEDRE (or another Matrix client) treats its remainder as the new body.
    lines = f"<{bot_user_id}> [{label}]: {preview_text}".split("\n")
    quoted_text = "\n".join(f"> {line}" for line in lines)
    safe_original_html = html.escape(preview_text).replace("\n", "<br/>")
    safe_label_html = html.escape(label).replace("\n", "<br/>")
    quoted_html = (
        f'<mx-reply><blockquote><a href="{html.escape(reply_link, quote=True)}">'
        f'In reply to</a> <a href="{html.escape(bot_link, quote=True)}">'
        f"{html.escape(bot_user_id)}</a><br>"
        f"[{safe_label_html}]: {safe_original_html}"
        f"</blockquote></mx-reply>"
    )
    return quoted_text, quoted_html


async def render_matrix_relay_content(
    *,
    room_id: str,
    message: str,
    longname: str,
    shortname: str,
    meshnet_name: str,
    portnum: int,
    meshtastic_id: int | None = None,
    meshtastic_replyId: int | None = None,
    meshtastic_text: str | None = None,
    emote: bool = False,
    emoji: bool = False,
    reply_to_event_id: str | None = None,
    reaction_key: str | None = None,
    reply_preview: tuple[str, str | None] | None = None,
    bot_user_id: str | None = None,
    source_node_id: str | None = None,
) -> dict[str, Any]:
    """Render one canonical radio message into a validated Matrix send operation.

    Reply relations use a **Matrix native** target reference scoped to this
    exact room, not a MEDRE canonical ID. The typed operation must remain a
    room message; a missing or malformed result fails closed before sending.
    Legacy Meshtastic wire fields retain their original types for existing
    MMRelay clients, independently of MEDRE's immutable metadata projection.
    When provided, ``source_node_id`` carries the native Meshtastic identity;
    display names are only a fallback for callers without that identity.
    """
    wire_fields: dict[str, object] = {
        KEY_LONGNAME: longname,
        KEY_SHORTNAME: shortname,
        KEY_MESHNET: meshnet_name,
        KEY_PORTNUM: portnum,
    }
    if meshtastic_id is not None:
        wire_fields[KEY_ID] = meshtastic_id
    if meshtastic_replyId is not None:
        wire_fields[KEY_REPLY_ID] = meshtastic_replyId
    if meshtastic_text is not None:
        wire_fields[KEY_TEXT] = meshtastic_text
    if emoji:
        wire_fields[KEY_EMOJI] = 1

    is_reaction = emoji and meshtastic_replyId is not None
    reaction_symbol = reaction_key.strip() if isinstance(reaction_key, str) else ""
    if is_reaction and reply_to_event_id:
        raise ValueError("Radio reactions cannot also carry a Matrix reply")
    if is_reaction and not reaction_symbol:
        raise ValueError("Meshtastic reaction requires an explicit reaction_key")
    if is_reaction:
        wire_fields[KEY_REACTION_KEY] = reaction_symbol

    relations: tuple[EventRelation, ...] = ()
    if is_reaction:
        # This is a RADIO reaction, not a native Matrix annotation. There is
        # no authorized Matrix target ID; MEDRE will render an emote fallback.
        relations = (
            EventRelation(
                relation_type="reaction",
                target_event_id=None,
                target_native_ref=NativeRef(
                    adapter=_MESH_SOURCE,
                    native_channel_id=meshnet_name,
                    native_message_id=str(meshtastic_replyId),
                ),
                key=reaction_symbol,
                fallback_text=meshtastic_text,
                metadata={"meshtastic_reply_id": str(meshtastic_replyId)},
            ),
        )
    elif reply_to_event_id:
        relations = (
            EventRelation(
                relation_type="reply",
                target_event_id=None,
                target_native_ref=NativeRef(
                    adapter=_MATRIX_TARGET,
                    native_channel_id=room_id,
                    native_message_id=reply_to_event_id,
                ),
                key=None,
                fallback_text=None,
            ),
        )

    event = CanonicalEvent(
        event_id=str(uuid.uuid4()),
        event_kind="message.reacted" if is_reaction else "message.created",
        schema_version=1,
        timestamp=datetime.now(UTC),
        source_adapter=_MESH_SOURCE,
        # An actual native node ID is the transport identity. Shortname is
        # only a backwards-compatible fallback for callers without a packet.
        source_transport_id=(
            source_node_id
            if isinstance(source_node_id, str) and source_node_id
            else shortname or longname or "mesh"
        ),
        source_channel_id=meshnet_name,
        parent_event_id=None,
        lineage=(),
        relations=relations,
        payload={"body": message, "msgtype": "m.emote" if emote else "m.text"},
        metadata=EventMetadata(
            native=NativeMetadata(data={"interop": {"mmrelay": wire_fields}})
        ),
        source_native_ref=(
            NativeRef(
                adapter=_MESH_SOURCE,
                native_channel_id=meshnet_name,
                native_message_id=str(meshtastic_id),
            )
            if meshtastic_id is not None
            else None
        ),
    )
    result = await _RENDERER.render(
        event,
        RenderingContext(
            delivery_strategy="direct",
            target_adapter=_MATRIX_TARGET,
            target_channel=room_id,
            target_platform="matrix",
            source_origin_label="",  # MMRelay already owns its message prefixes
        ),
    )
    operation = MatrixOutboundOperation.from_payload(result.payload)
    if (
        operation is None
        or operation.kind != "send_event"
        or operation.event_type != "m.room.message"
        or not isinstance(operation.content, dict)
    ):
        raise ValueError("MEDRE returned a non-message Matrix operation")
    content: dict[str, Any] = dict(operation.content)
    if not is_reaction and content.get("body") != message:
        raise ValueError("MEDRE Matrix renderer changed the authored message body")
    if reply_to_event_id and content.get("m.relates_to") != {
        "m.in_reply_to": {"event_id": reply_to_event_id}
    }:
        raise ValueError("MEDRE Matrix renderer lost the native reply relation")
    if not reply_to_event_id and "m.relates_to" in content:
        raise ValueError("MEDRE Matrix renderer invented a native relation")
    medre_fields = content.get("medre")
    envelope = medre_fields.get("envelope") if isinstance(medre_fields, dict) else None
    native_ref = event.source_native_ref
    expected_native_ref = (
        {
            "adapter": native_ref.adapter,
            "native_channel_id": native_ref.native_channel_id,
            "native_message_id": native_ref.native_message_id,
            "native_thread_id": native_ref.native_thread_id,
        }
        if native_ref is not None
        else None
    )
    if (
        not isinstance(envelope, dict)
        or type(envelope.get("schema_version")) is not int
        or envelope.get("schema_version") != 1
        or envelope.get("canonical_event_id") != event.event_id
        or envelope.get("source_adapter") != _MESH_SOURCE
        or envelope.get("source_transport_id") != event.source_transport_id
        or envelope.get("source_native_ref") != expected_native_ref
        or envelope.get("source_channel") != event.source_channel_id
        or envelope.get("event_kind") != event.event_kind
        or envelope.get("relation_info")
        != ",".join(sorted({relation.relation_type for relation in relations}))
    ):
        raise ValueError("MEDRE Matrix renderer lost canonical v1 provenance")

    # The pinned MEDRE 0.1 renderer defaults to m.text. MMRelay's radio emote
    # fallback is a presentation choice, never an edit/reaction mutation.
    content["msgtype"] = "m.emote" if emote or is_reaction else "m.text"
    if is_reaction:
        # MEDRE owns the reaction relation and provenance; MMRelay retains its
        # established human-readable radio reaction presentation in this room.
        content["body"] = message
    content.update(wire_fields)
    content["format"] = "org.matrix.custom.html"
    formatted_body = _formatted_message(message)
    if reply_to_event_id and reply_preview is not None:
        try:
            original_text, original_meshnet = reply_preview
            quoted_text, quoted_html = _reply_quote(
                room_id,
                reply_to_event_id,
                bot_user_id,
                original_text,
                original_meshnet,
            )
        except ValueError:
            # Lazy facade import; see _formatted_message for the rationale.
            import mmrelay.matrix_utils as facade

            facade.logger.warning(
                "Cannot quote Matrix reply %s without a valid bot user ID",
                reply_to_event_id,
            )
        else:
            content["body"] = f"{quoted_text}\n\n{message}"
            formatted_body = quoted_html + formatted_body
    content["formatted_body"] = formatted_body
    # Re-check the fully projected wire operation, not just MEDRE's initial
    # envelope; the closed operation is internal and never reaches Matrix.
    MatrixOutboundOperation.send_event("m.room.message", content)
    return content
