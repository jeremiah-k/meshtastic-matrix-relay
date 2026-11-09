"""Lossless, one-line presentation previews for radio reactions."""

from mmrelay.constants.messages import MESSAGE_PREVIEW_LENGTH


def reaction_preview(text: str) -> str:
    """Collapse authored whitespace only; never rewrite canonical stored text."""
    one_line = " ".join(text.split())
    return one_line[:MESSAGE_PREVIEW_LENGTH] + (
        "..." if len(one_line) > MESSAGE_PREVIEW_LENGTH else ""
    )
