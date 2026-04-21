from unittest.mock import MagicMock, patch

import pytest

from mmrelay.matrix_utils import _parse_matrix_message_command, bot_command

BOT_MXID = "@testbot:example.org"
OTHER_MXID = "@relay:example.com"


def _make_event(body: str, formatted_body: str | None = None) -> MagicMock:
    """Create a Matrix event-shaped mock with optional formatted_body."""
    event = MagicMock()
    event.body = body
    content: dict[str, str] = {}
    if formatted_body is not None:
        content["formatted_body"] = formatted_body
    event.source = {"content": content}
    return event


class TestMatrixCommandParser:
    """Command parser tests intentionally use non-real MXIDs."""

    @pytest.fixture(autouse=True)
    def patch_bot_identity(self):
        with (
            patch("mmrelay.matrix_utils.bot_user_id", BOT_MXID),
            patch("mmrelay.matrix_utils.bot_user_name", "ForxRelay"),
        ):
            yield

    def test_parser_matches_supported_mxid_with_whitespace_separator(self):
        event = _make_event(f"{BOT_MXID} !map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is not None
        assert parsed.command == "map"
        assert parsed.args == ""

    def test_parser_matches_supported_mxid_with_colon_separator(self):
        event = _make_event(f"{BOT_MXID}: !map 42")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is not None
        assert parsed.command == "map"
        assert parsed.args == "42"

    def test_parser_rejects_spaced_semicolon_prefix(self):
        event = _make_event(f"{BOT_MXID} ; !map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is None

    def test_parser_rejects_compact_mxid_and_command(self):
        event = _make_event(f"{BOT_MXID}!map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is None

    def test_parser_rejects_display_name_prefix(self):
        event = _make_event("ForxRelay !map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is None

    def test_parser_rejects_display_name_colon_prefix(self):
        event = _make_event("ForxRelay: !map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is None

    def test_parser_rejects_arbitrary_prose_before_command(self):
        event = _make_event("I like !map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=False)
        assert parsed is None

    def test_parser_requires_mxid_mention_when_enabled(self):
        event = _make_event("!map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is None

    def test_parser_allows_bare_command_when_mentions_not_required(self):
        event = _make_event("!map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=False)
        assert parsed is not None
        assert parsed.command == "map"
        assert parsed.args == ""

    def test_parser_rejects_other_mxid_when_mentions_required(self):
        event = _make_event(f"{OTHER_MXID}: !map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is None

    def test_parser_checks_normalized_formatted_body(self):
        event = _make_event(
            "not a command",
            formatted_body=f"<a>{BOT_MXID}</a>: <strong>!MaP</strong> now",
        )
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is not None
        assert parsed.command == "map"
        assert parsed.args == "now"

    def test_bot_command_wrapper_matches_supported_mxid(self):
        event = _make_event(f"{BOT_MXID}: !HELP")
        assert bot_command("help", event, require_mention=True)

    def test_bot_command_wrapper_rejects_display_name(self):
        event = _make_event("ForxRelay: !help")
        assert not bot_command("help", event, require_mention=True)

    def test_bot_command_empty_command_returns_false(self):
        event = _make_event("!help")
        assert bot_command("", event) is False

    def test_bad_mxid_identifier_is_ignored(self):
        """Broken bot MXID stringification should fail closed."""

        class BadIdent:
            def __str__(self):
                raise ValueError("boom")

        event = _make_event("!help")
        with (
            patch("mmrelay.matrix_utils.bot_user_id", BadIdent()),
            patch("mmrelay.matrix_utils.logger") as mock_logger,
        ):
            parsed = _parse_matrix_message_command(
                event, ("help",), require_mention=True
            )
        assert parsed is None
        mock_logger.debug.assert_called()
