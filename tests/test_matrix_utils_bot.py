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
            patch("mmrelay.matrix_utils.bot_user_name", "TestRelay"),
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

    def test_parser_rejects_comma_prefix(self):
        event = _make_event(f"{BOT_MXID}, !map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is None

    def test_parser_rejects_compact_mxid_and_command(self):
        event = _make_event(f"{BOT_MXID}!map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is None

    def test_parser_accepts_display_name_prefix(self):
        event = _make_event("TestRelay !map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is not None
        assert parsed.command == "map"
        assert parsed.args == ""

    def test_parser_accepts_display_name_colon_prefix(self):
        event = _make_event("TestRelay: !map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is not None
        assert parsed.command == "map"
        assert parsed.args == ""

    def test_parser_rejects_arbitrary_prose_before_command(self):
        event = _make_event("I like !map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=False)
        assert parsed is None

    def test_parser_rejects_display_name_without_separator(self):
        event = _make_event("TestRelay!map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is None

    def test_parser_rejects_display_name_not_at_start(self):
        event = _make_event("Hello TestRelay !map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is None

    def test_parser_rejects_partial_display_name_match(self):
        event = _make_event("Forx !map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is None

    def test_display_name_case_insensitive(self):
        event = _make_event("testrelay !map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is not None
        assert parsed.command == "map"

    def test_mxid_mention_takes_precedence_over_display_name(self):
        event = _make_event(f"{BOT_MXID}: !map")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is not None
        assert parsed.command == "map"

    def test_formatted_body_mxid_wins_over_plain_body_display_name_fallback(self):
        event = _make_event(
            "TestRelay !map 123",
            formatted_body=(
                '<a href="https://matrix.to/#/%40testbot%3Aexample.org">'
                "TestRelay</a>: !map 456"
            ),
        )
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is not None
        assert parsed.command == "map"
        assert parsed.args == "456"

    def test_plain_body_mxid_wins_over_formatted_body_display_name_fallback(self):
        event = _make_event(
            f"{BOT_MXID}: !map 999",
            formatted_body="<span>TestRelay !map 321</span>",
        )
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is not None
        assert parsed.command == "map"
        assert parsed.args == "999"

    def test_display_name_fallback_skipped_when_name_not_configured(self):
        event = _make_event("TestRelay !map")
        with patch("mmrelay.matrix_utils.bot_user_name", None):
            parsed = _parse_matrix_message_command(
                event, ("map",), require_mention=True
            )
        assert parsed is None

    def test_display_name_fallback_with_args(self):
        event = _make_event("TestRelay: !map 42")
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is not None
        assert parsed.command == "map"
        assert parsed.args == "42"

    def test_unrelated_formatted_body_link_keeps_display_name_fallback(self):
        event = _make_event(
            "TestRelay !map 55",
            formatted_body=(
                '<a href="https://matrix.to/#/%40relay%3Aexample.com">'
                "TestRelay</a>: !map 777"
            ),
        )
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is not None
        assert parsed.command == "map"
        assert parsed.args == "55"

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

    def test_parser_accepts_formatted_body_mention_pill_targeting_bot_mxid(self):
        event = _make_event(
            "not a command",
            formatted_body=(
                '<a href="https://matrix.to/#/%40testbot%3Aexample.org">'
                "TestRelay</a>: <strong>!MaP</strong> now"
            ),
        )
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is not None
        assert parsed.command == "map"
        assert parsed.args == "now"

    def test_parser_prefers_real_href_over_data_href(self):
        event = _make_event(
            "not a command",
            formatted_body=(
                '<a class="mention" data-href="https://matrix.to/#/%40relay%3Aexample.com" '
                'href="https://matrix.to/#/%40testbot%3Aexample.org">TestRelay</a>: !map'
            ),
        )
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is not None
        assert parsed.command == "map"
        assert parsed.args == ""

    def test_parser_rejects_formatted_body_with_only_data_href(self):
        event = _make_event(
            "not a command",
            formatted_body=(
                '<a class="mention" data-href="https://matrix.to/#/%40testbot%3Aexample.org">'
                "TestRelay</a>: !map"
            ),
        )
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is None

    def test_parser_rejects_formatted_body_link_not_targeting_bot_mxid(self):
        event = _make_event(
            "not a command",
            formatted_body=(
                '<a href="https://matrix.to/#/%40relay%3Aexample.com">'
                "TestRelay</a>: !map"
            ),
        )
        parsed = _parse_matrix_message_command(event, ("map",), require_mention=True)
        assert parsed is None

    def test_bot_command_wrapper_matches_supported_mxid(self):
        event = _make_event(f"{BOT_MXID}: !HELP")
        assert bot_command("help", event, require_mention=True)

    def test_bot_command_wrapper_accepts_display_name(self):
        event = _make_event("TestRelay: !help")
        assert bot_command("help", event, require_mention=True)

    def test_bot_command_empty_command_returns_false(self):
        event = _make_event("!help")
        assert bot_command("", event) is False

    def test_bad_mxid_identifier_is_ignored(self):
        """Broken bot MXID stringification should fail closed."""

        class BadIdent:
            def __str__(self) -> str:
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
