from unittest.mock import MagicMock, patch

import pytest

from mmrelay.matrix_utils import bot_command


class TestBotCommand:
    """Test class for bot command detection functionality."""

    @pytest.fixture(autouse=True)
    def mock_bot_globals(self):
        """
        Provide a pytest fixture that patches the module-level bot identifiers for tests in this class.
        
        Patches mmrelay.matrix_utils.bot_user_id to "@bot:matrix.org" and
        mmrelay.matrix_utils.bot_user_name to "Bot" for the duration of each test, then yields
        control to the test. Intended for use as an autouse fixture within the test class.
        """
        with (
            patch("mmrelay.matrix_utils.bot_user_id", "@bot:matrix.org"),
            patch("mmrelay.matrix_utils.bot_user_name", "Bot"),
        ):
            yield

    def test_direct_mention(self):
        """
        Tests that a message starting with the bot command triggers correct command detection.
        """
        mock_event = MagicMock()
        mock_event.body = "!help"
        mock_event.source = {"content": {"formatted_body": "!help"}}

        result = bot_command("help", mock_event)
        assert result

    def test_direct_mention_require_mention_false(self):
        """
        Tests that a message starting with the bot command works when require_mention=False.
        """
        mock_event = MagicMock()
        mock_event.body = "!help"
        mock_event.source = {"content": {"formatted_body": "!help"}}

        result = bot_command("help", mock_event, require_mention=False)
        assert result

    def test_direct_mention_require_mention_true(self):
        """
        Verifies that a plain command without a bot mention is not recognized when mentions are required.

        This test constructs a mock event with a command-like body and asserts that bot_command returns falsy when require_mention is enabled.
        """
        mock_event = MagicMock()
        mock_event.body = "!help"
        mock_event.source = {"content": {"formatted_body": "!help"}}

        result = bot_command("help", mock_event, require_mention=True)
        assert not result

    def test_no_match(self):
        """
        Test that a non-command message does not trigger bot command detection.
        """
        mock_event = MagicMock()
        mock_event.body = "regular message"
        mock_event.source = {"content": {"formatted_body": "regular message"}}

        result = bot_command("help", mock_event)
        assert not result

    def test_no_match_require_mention_true(self):
        """
        Test that a non-command message does not trigger bot command detection when require_mention=True.
        """
        mock_event = MagicMock()
        mock_event.body = "regular message"
        mock_event.source = {"content": {"formatted_body": "regular message"}}

        result = bot_command("help", mock_event, require_mention=True)
        assert not result

    def test_case_insensitive(self):
        """
        Test that bot command detection is case-insensitive by verifying a command matches regardless of letter case.
        """
        mock_event = MagicMock()
        mock_event.body = "!HELP"
        mock_event.source = {"content": {"formatted_body": "!HELP"}}

        result = bot_command("HELP", mock_event)  # Command should match case
        assert result

    def test_case_insensitive_require_mention_true(self):
        """
        Test that bot command detection fails when require_mention=True even with case-insensitive match.
        """
        mock_event = MagicMock()
        mock_event.body = "!HELP"
        mock_event.source = {"content": {"formatted_body": "!HELP"}}

        result = bot_command("HELP", mock_event, require_mention=True)
        assert not result

    def test_with_args(self):
        """
        Test that the bot command is correctly detected when followed by additional arguments.
        """
        mock_event = MagicMock()
        mock_event.body = "!help me please"
        mock_event.source = {"content": {"formatted_body": "!help me please"}}

        result = bot_command("help", mock_event)
        assert result

    def test_with_args_require_mention_true(self):
        """
        Test that the bot command fails when require_mention=True even with arguments.
        """
        mock_event = MagicMock()
        mock_event.body = "!help me please"
        mock_event.source = {"content": {"formatted_body": "!help me please"}}

        result = bot_command("help", mock_event, require_mention=True)
        assert not result

    def test_bot_mention_require_mention_true(self):
        """
        Test that a message with bot mention works when require_mention=True.
        """
        mock_event = MagicMock()
        mock_event.body = "@bot:matrix.org: !help"
        mock_event.source = {"content": {"formatted_body": "@bot:matrix.org: !help"}}

        result = bot_command("help", mock_event, require_mention=True)
        assert result

    def test_bot_mention_with_name_require_mention_true(self):
        """
        Test that a message with bot display name works when require_mention=True.
        """
        mock_event = MagicMock()
        mock_event.body = "Bot: !help"
        mock_event.source = {"content": {"formatted_body": "Bot: !help"}}

        result = bot_command("help", mock_event, require_mention=True)
        assert result

    def test_non_bot_mention_require_mention_true(self):
        """
        Test that a message mentioning another user does not trigger when require_mention=True.
        """
        mock_event = MagicMock()
        mock_event.body = "@someuser: !help"
        mock_event.source = {"content": {"formatted_body": "@someuser: !help"}}

        result = bot_command("help", mock_event, require_mention=True)
        assert not result

    def test_bot_mention_require_mention_false(self):
        """
        Test that a message with bot mention works when require_mention=False.
        """
        mock_event = MagicMock()
        mock_event.body = "@bot:matrix.org: !help"
        mock_event.source = {"content": {"formatted_body": "@bot:matrix.org: !help"}}

        result = bot_command("help", mock_event, require_mention=False)
        assert result

    def test_empty_command_returns_false(self):
        """Empty commands should never match."""
        mock_event = MagicMock()
        mock_event.body = "!help"
        mock_event.source = {"content": {"formatted_body": "!help"}}

        result = bot_command("", mock_event)
        assert result is False

    def test_bad_identifier_skips_mention_parts(self):
        """Bad bot identifiers should be ignored when building mention patterns."""

        class BadIdent:
            def __str__(self):
                """
                Raise a ValueError with message "boom".
                
                Raises:
                    ValueError: Always raised when attempting to produce the string representation.
                """
                raise ValueError("boom")

        mock_event = MagicMock()
        mock_event.body = "hello"
        mock_event.source = {"content": {"formatted_body": "hello"}}

        with (
            patch("mmrelay.matrix_utils.bot_user_id", BadIdent()),
            patch("mmrelay.matrix_utils.bot_user_name", None),
            patch("mmrelay.matrix_utils.logger") as mock_logger,
        ):
            result = bot_command("help", mock_event, require_mention=True)

        assert result is False
        mock_logger.debug.assert_called()