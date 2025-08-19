"""
Tests for CLI utilities and command registry.

This module tests the centralized CLI command registry and utility functions
that provide consistent command references across the application.
"""

import pytest
from mmrelay.cli_utils import (
    CLI_COMMANDS,
    DEPRECATED_COMMANDS,
    get_command,
    get_deprecation_warning,
    suggest_command,
    require_command,
    retry_command,
    validate_command,
    msg_suggest_generate_config,
    msg_suggest_check_config,
    msg_require_auth_login,
    msg_retry_auth_login,
    msg_run_auth_login,
    msg_for_e2ee_support,
    msg_setup_auth,
    msg_or_run_auth_login,
    msg_setup_authentication,
    msg_regenerate_credentials,
)


class TestCommandRegistry:
    """Test the CLI command registry constants."""

    def test_cli_commands_structure(self):
        """Test that CLI_COMMANDS has expected structure and commands."""
        assert isinstance(CLI_COMMANDS, dict)
        assert len(CLI_COMMANDS) > 0
        
        # Test key commands exist
        expected_commands = [
            "generate_config",
            "check_config", 
            "auth_login",
            "auth_status",
            "service_install",
            "start_relay",
            "show_version",
            "show_help"
        ]
        
        for cmd in expected_commands:
            assert cmd in CLI_COMMANDS
            assert isinstance(CLI_COMMANDS[cmd], str)
            assert len(CLI_COMMANDS[cmd]) > 0

    def test_deprecated_commands_structure(self):
        """Test that DEPRECATED_COMMANDS maps old flags to new command keys."""
        assert isinstance(DEPRECATED_COMMANDS, dict)
        
        # Test expected deprecated mappings
        expected_mappings = {
            "--generate-config": "generate_config",
            "--check-config": "check_config",
            "--install-service": "service_install",
            "--auth": "auth_login",
        }
        
        for old_flag, new_key in expected_mappings.items():
            assert old_flag in DEPRECATED_COMMANDS
            assert DEPRECATED_COMMANDS[old_flag] == new_key
            # Ensure the new key exists in CLI_COMMANDS
            assert new_key in CLI_COMMANDS


class TestGetCommand:
    """Test the get_command function."""

    def test_get_command_valid_keys(self):
        """Test get_command returns correct commands for valid keys."""
        assert get_command("generate_config") == "mmrelay config generate"
        assert get_command("check_config") == "mmrelay config check"
        assert get_command("auth_login") == "mmrelay auth login"
        assert get_command("start_relay") == "mmrelay"

    def test_get_command_invalid_key(self):
        """Test get_command raises KeyError for invalid keys."""
        with pytest.raises(KeyError, match="Unknown CLI command key: invalid_key"):
            get_command("invalid_key")

    def test_get_command_empty_key(self):
        """Test get_command raises KeyError for empty key."""
        with pytest.raises(KeyError):
            get_command("")


class TestGetDeprecationWarning:
    """Test the get_deprecation_warning function."""

    def test_deprecation_warning_with_replacement(self):
        """Test deprecation warning for flags with known replacements."""
        warning = get_deprecation_warning("--generate-config")
        assert "Warning: --generate-config is deprecated" in warning
        assert "mmrelay config generate" in warning

    def test_deprecation_warning_without_replacement(self):
        """Test deprecation warning for unknown deprecated flags."""
        warning = get_deprecation_warning("--unknown-flag")
        assert "Warning: --unknown-flag is deprecated" in warning
        assert "mmrelay --help" in warning

    def test_deprecation_warning_empty_flag(self):
        """Test deprecation warning for empty flag."""
        warning = get_deprecation_warning("")
        assert "Warning:  is deprecated" in warning
        assert "mmrelay --help" in warning


class TestSuggestCommand:
    """Test the suggest_command function."""

    def test_suggest_command_basic(self):
        """Test suggest_command formats messages correctly."""
        result = suggest_command("generate_config", "to create a sample configuration")
        assert result == "Run 'mmrelay config generate' to create a sample configuration."

    def test_suggest_command_different_purposes(self):
        """Test suggest_command with different purposes."""
        result = suggest_command("check_config", "to validate settings")
        assert result == "Run 'mmrelay config check' to validate settings."

    def test_suggest_command_invalid_key(self):
        """Test suggest_command raises KeyError for invalid command key."""
        with pytest.raises(KeyError):
            suggest_command("invalid_key", "to do something")


class TestRequireCommand:
    """Test the require_command function."""

    def test_require_command_basic(self):
        """Test require_command formats messages correctly."""
        result = require_command("auth_login", "to set up authentication")
        assert result == "Please run 'mmrelay auth login' to set up authentication."

    def test_require_command_invalid_key(self):
        """Test require_command raises KeyError for invalid command key."""
        with pytest.raises(KeyError):
            require_command("invalid_key", "to do something")


class TestRetryCommand:
    """Test the retry_command function."""

    def test_retry_command_without_context(self):
        """Test retry_command without additional context."""
        result = retry_command("auth_login")
        assert result == "Try running 'mmrelay auth login' again."

    def test_retry_command_with_context(self):
        """Test retry_command with additional context."""
        result = retry_command("auth_login", "after fixing the configuration")
        assert result == "Try running 'mmrelay auth login' again after fixing the configuration."

    def test_retry_command_empty_context(self):
        """Test retry_command with empty context string."""
        result = retry_command("auth_login", "")
        assert result == "Try running 'mmrelay auth login' again."


class TestValidateCommand:
    """Test the validate_command function."""

    def test_validate_command_basic(self):
        """Test validate_command formats messages correctly."""
        result = validate_command("check_config", "to validate your configuration")
        assert result == "Use 'mmrelay config check' to validate your configuration."


class TestMessageTemplates:
    """Test the predefined message template functions."""

    def test_msg_suggest_generate_config(self):
        """Test msg_suggest_generate_config returns expected message."""
        result = msg_suggest_generate_config()
        assert "mmrelay config generate" in result
        assert "sample configuration file" in result

    def test_msg_suggest_check_config(self):
        """Test msg_suggest_check_config returns expected message."""
        result = msg_suggest_check_config()
        assert "mmrelay config check" in result
        assert "validate your configuration" in result

    def test_msg_require_auth_login(self):
        """Test msg_require_auth_login returns expected message."""
        result = msg_require_auth_login()
        assert "mmrelay auth login" in result
        assert "credentials.json" in result

    def test_msg_retry_auth_login(self):
        """Test msg_retry_auth_login returns expected message."""
        result = msg_retry_auth_login()
        assert "mmrelay auth login" in result
        assert "again" in result

    def test_msg_run_auth_login(self):
        """Test msg_run_auth_login returns expected message."""
        result = msg_run_auth_login()
        assert "mmrelay auth login" in result
        assert "device_id" in result

    def test_msg_for_e2ee_support(self):
        """Test msg_for_e2ee_support returns expected message."""
        result = msg_for_e2ee_support()
        assert "E2EE support" in result
        assert "mmrelay auth login" in result

    def test_msg_setup_auth(self):
        """Test msg_setup_auth returns expected message."""
        result = msg_setup_auth()
        assert "Setup:" in result
        assert "mmrelay auth login" in result

    def test_msg_or_run_auth_login(self):
        """Test msg_or_run_auth_login returns expected message."""
        result = msg_or_run_auth_login()
        assert "or run" in result
        assert "mmrelay auth login" in result
        assert "credentials.json" in result

    def test_msg_setup_authentication(self):
        """Test msg_setup_authentication returns expected message."""
        result = msg_setup_authentication()
        assert "Setup authentication" in result
        assert "mmrelay auth login" in result

    def test_msg_regenerate_credentials(self):
        """Test msg_regenerate_credentials returns expected message."""
        result = msg_regenerate_credentials()
        assert "mmrelay auth login" in result
        assert "device_id" in result
        assert "again" in result


class TestIntegration:
    """Test integration between different functions."""

    def test_all_deprecated_commands_have_valid_replacements(self):
        """Test that all deprecated commands map to valid CLI commands."""
        for old_flag, new_key in DEPRECATED_COMMANDS.items():
            # Should not raise KeyError
            command = get_command(new_key)
            assert isinstance(command, str)
            assert len(command) > 0

    def test_message_functions_use_valid_commands(self):
        """Test that all message functions reference valid commands."""
        # These should not raise KeyError
        msg_suggest_generate_config()
        msg_suggest_check_config()
        msg_require_auth_login()
        msg_retry_auth_login()
        msg_run_auth_login()
        msg_for_e2ee_support()
        msg_setup_auth()
        msg_or_run_auth_login()
        msg_setup_authentication()
        msg_regenerate_credentials()
