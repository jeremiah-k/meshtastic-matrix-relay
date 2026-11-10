"""Presentation excerpts must not corrupt authored canonical Matrix messages."""

import pytest

from mmrelay.constants.messages import MESSAGE_PREVIEW_LENGTH
from mmrelay.meshtastic.reaction_preview import reaction_preview


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("> quoted\n\nreal\r\nanswer", "> quoted real answer"),
        ("> quote-only\n> second", "> quote-only > second"),
        ("alpha\tbeta\rcharlie", "alpha beta charlie"),
        ("", ""),
        ("word " * 12, ("word " * 12).strip()[:MESSAGE_PREVIEW_LENGTH] + "..."),
    ],
)
def test_reaction_preview_is_single_line_without_destroying_quotes(
    source: str, expected: str
) -> None:
    """The preview is bounded and single-line; the source is left untouched."""
    assert reaction_preview(source) == expected
    assert "\n" not in reaction_preview(source)
    assert len(reaction_preview(source)) <= MESSAGE_PREVIEW_LENGTH + 3
