#!/usr/bin/env python3
"""
Test suite for run_main() wrapper, argument handling, and banner printing.

Tests the non-async entry path: configuration loading, log-level override,
banner printing, error handling, KeyboardInterrupt, and argument variants.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mmrelay.constants.network import CONNECTION_TYPE_SERIAL
from mmrelay.main import print_banner, run_main
from tests._test_main_helpers import (
    _close_coro_if_possible,
    _mock_run_with_exception,
    _mock_run_with_keyboard_interrupt,
)


@pytest.fixture(autouse=True)
def reset_banner_state():
    import mmrelay.main

    mmrelay.main._banner_printed = False
    yield
    mmrelay.main._banner_printed = False


# =============================================================================
# Banner tests
# =============================================================================


def test_print_banner():
    """
    Tests that the banner is printed exactly once and includes the version information in the log output.
    """
    with patch("mmrelay.main.logger") as mock_logger:
        print_banner()

        # Should print banner with version
        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args[0][0]
        assert "Starting MMRelay" in call_args
        assert "version " in call_args  # Version should be included


def test_print_banner_only_once():
    """Test that banner is only printed once."""
    with patch("mmrelay.main.logger") as mock_logger:
        print_banner()
        print_banner()  # Second call

        # Should only be called once
        assert mock_logger.info.call_count == 1


# =============================================================================
# run_main basic tests
# =============================================================================


@patch("asyncio.run")
@patch("mmrelay.config.load_config")
@patch("mmrelay.config.set_config")
@patch("mmrelay.log_utils.configure_component_debug_logging")
@patch("mmrelay.main.print_banner")
def test_run_main(
    mock_print_banner,
    mock_configure_debug,
    _mock_set_config,
    mock_load_config,
    mock_asyncio_run,
    mock_config,
):
    """
    Test that `run_main` executes the full startup sequence and returns 0 on success.

    Verifies that configuration is loaded and set, logging level is overridden by arguments, the banner is printed, debug logging is configured, the main async function is run, and the function returns 0 to indicate successful execution.
    """
    # Mock arguments
    mock_args = MagicMock()
    mock_args.log_level = "debug"

    # Mock config loading
    mock_load_config.return_value = mock_config

    # Mock asyncio.run with coroutine cleanup to prevent warnings
    mock_asyncio_run.side_effect = _close_coro_if_possible

    result = run_main(mock_args)

    # Verify configuration was loaded and set
    mock_load_config.assert_called_once_with(args=mock_args)

    # Verify banner was printed
    mock_print_banner.assert_called_once()

    # Verify component debug logging was configured
    mock_configure_debug.assert_called_once()

    # Verify asyncio.run was called
    mock_asyncio_run.assert_called_once()

    # Should return 0 for success
    assert result == 0


@patch("mmrelay.config.load_config")
@patch("asyncio.run")
def test_run_main_exception_handling(mock_asyncio_run, mock_load_config, mock_config):
    """
    Verify that run_main returns 1 when an exception is raised during asynchronous execution.
    """
    # Mock config loading
    mock_load_config.return_value = mock_config

    # Mock asyncio.run with coroutine cleanup and exception
    mock_asyncio_run.side_effect = _mock_run_with_exception

    mock_args = MagicMock()
    mock_args.log_level = None
    result = run_main(mock_args)

    # Should return 1 for error
    assert result == 1


@patch("mmrelay.config.load_config")
@patch("asyncio.run")
def test_run_main_keyboard_interrupt(mock_asyncio_run, mock_load_config, mock_config):
    """
    Verifies that run_main returns 0 when a KeyboardInterrupt is raised during execution, ensuring graceful shutdown behavior.
    """
    # Mock config loading
    mock_load_config.return_value = mock_config

    # Mock asyncio.run with coroutine cleanup and KeyboardInterrupt
    mock_asyncio_run.side_effect = _mock_run_with_keyboard_interrupt

    mock_args = MagicMock()
    mock_args.log_level = None
    result = run_main(mock_args)

    # Should return 0 for graceful shutdown
    assert result == 0


@patch("asyncio.run")
@patch("mmrelay.config.load_config")
@patch("mmrelay.config.set_config")
@patch("mmrelay.log_utils.configure_component_debug_logging")
@patch("mmrelay.main.print_banner")
def test_run_main_success(
    mock_print_banner,
    _mock_configure_logging,
    _mock_set_config,
    mock_load_config,
    mock_asyncio_run,
):
    """
    Test that `run_main` completes successfully with valid configuration and arguments.

    Verifies that the banner is printed, configuration is loaded, and the main asynchronous function is executed, resulting in a return value of 0.
    """
    # Mock configuration
    mock_config = {
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
        "matrix_rooms": [{"id": "!room:matrix.org"}],
    }
    mock_load_config.return_value = mock_config

    # Mock asyncio.run with coroutine cleanup to prevent warnings
    mock_asyncio_run.side_effect = _close_coro_if_possible

    # Mock args
    mock_args = MagicMock()
    mock_args.data_dir = None
    mock_args.log_level = None

    result = run_main(mock_args)

    assert result == 0
    mock_print_banner.assert_called_once()
    mock_load_config.assert_called_once_with(args=mock_args)
    mock_asyncio_run.assert_called_once()


@patch("asyncio.run")
@patch("mmrelay.config.set_config")
@patch("mmrelay.config.load_config")
@patch("mmrelay.config.load_credentials")
@patch("mmrelay.main.print_banner")
def test_run_main_missing_config_keys(
    _mock_print_banner,
    mock_load_credentials,
    mock_load_config,
    _mock_set_config,
    mock_asyncio_run,
):
    """
    Verify run_main returns 1 when the loaded configuration is missing required keys.

    Sets up a minimal incomplete config (only matrix.homeserver) and ensures run_main detects the missing fields and returns a non-zero exit code. Uses the coroutine cleanup helper for asyncio.run to avoid ResourceWarnings.
    """
    mock_load_credentials.return_value = None
    # Mock incomplete configuration
    mock_config = {"matrix": {"homeserver": "https://matrix.org"}}  # Missing keys
    mock_load_config.return_value = mock_config

    # Mock asyncio.run with coroutine cleanup to prevent warnings
    mock_asyncio_run.side_effect = _close_coro_if_possible

    mock_args = MagicMock()
    mock_args.data_dir = None
    mock_args.log_level = None

    result = run_main(mock_args)

    assert result == 1  # Should return error code


@patch("asyncio.run")
@patch("mmrelay.config.load_config")
@patch("mmrelay.config.set_config")
@patch("mmrelay.log_utils.configure_component_debug_logging")
@patch("mmrelay.main.print_banner")
def test_run_main_keyboard_interrupt_with_args(
    _mock_print_banner,
    _mock_configure_logging,
    _mock_set_config,
    mock_load_config,
    mock_asyncio_run,
):
    """
    Test that `run_main` returns 0 when a `KeyboardInterrupt` occurs during execution with command-line arguments.

    Ensures the application exits gracefully with a success code when interrupted by the user, even if arguments are provided.
    """
    mock_config = {
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
        "matrix_rooms": [{"id": "!room:matrix.org"}],
    }
    mock_load_config.return_value = mock_config

    # Mock asyncio.run with coroutine cleanup and KeyboardInterrupt
    mock_asyncio_run.side_effect = _mock_run_with_keyboard_interrupt

    mock_args = MagicMock()
    mock_args.data_dir = None
    mock_args.log_level = None

    result = run_main(mock_args)

    assert result == 0  # Should return success on keyboard interrupt


@patch("asyncio.run")
@patch("mmrelay.config.load_config")
@patch("mmrelay.config.set_config")
@patch("mmrelay.log_utils.configure_component_debug_logging")
@patch("mmrelay.main.print_banner")
def test_run_main_exception(
    _mock_print_banner,
    _mock_configure_logging,
    _mock_set_config,
    mock_load_config,
    mock_asyncio_run,
):
    """
    Test that run_main returns 1 when a general exception is raised during asynchronous execution.
    """
    mock_config = {
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
        "matrix_rooms": [{"id": "!room:matrix.org"}],
    }
    mock_load_config.return_value = mock_config

    # Mock asyncio.run with coroutine cleanup and exception
    mock_asyncio_run.side_effect = _mock_run_with_exception

    mock_args = MagicMock()
    mock_args.data_dir = None
    mock_args.log_level = None

    result = run_main(mock_args)

    assert result == 1  # Should return error code


@patch("asyncio.run")
@patch("mmrelay.config.load_config")
@patch("mmrelay.config.set_config")
@patch("mmrelay.log_utils.configure_component_debug_logging")
@patch("mmrelay.main.print_banner")
def test_run_main_with_data_dir(
    _mock_print_banner,
    _mock_configure_logging,
    _mock_set_config,
    mock_load_config,
    mock_asyncio_run,
):
    """
    Test that run_main returns success when args includes data_dir.

    This verifies run_main executes successfully when passed args.data_dir (processing of
    `--data-dir` is performed by the CLI layer before calling run_main, so run_main does not
    modify or create the directory). Uses a minimal valid config and a mocked asyncio.run
    to avoid running the real event loop.
    """

    mock_config = {
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
        "matrix_rooms": [{"id": "!room:matrix.org"}],
    }
    mock_load_config.return_value = mock_config

    # Mock asyncio.run with coroutine cleanup to prevent warnings
    mock_asyncio_run.side_effect = _close_coro_if_possible

    # Use a simple custom data directory path
    custom_data_dir = "/home/user/test_custom_data"

    mock_args = MagicMock()
    mock_args.data_dir = custom_data_dir
    mock_args.log_level = None

    result = run_main(mock_args)

    assert result == 0
    # run_main() no longer processes --data-dir (that's handled in cli.py)
    # Just verify it runs successfully


@patch("asyncio.run", spec=True)
@patch("mmrelay.config.load_config", spec=True)
@patch("mmrelay.config.set_config", spec=True)
@patch("mmrelay.log_utils.configure_component_debug_logging", spec=True)
@patch("mmrelay.main.print_banner", spec=True)
def test_run_main_with_log_level(
    _mock_print_banner,
    _mock_configure_logging,
    _mock_set_config,
    mock_load_config,
    mock_asyncio_run,
):
    """
    Test that run_main applies a custom log level from arguments and completes successfully.

    Ensures that when a log level is specified in the arguments, it overrides the logging level in the configuration, and run_main returns 0 to indicate successful execution.
    """
    mock_config = {
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
        "matrix_rooms": [{"id": "!room:matrix.org"}],
    }
    mock_load_config.return_value = mock_config

    # Mock asyncio.run with coroutine cleanup to prevent warnings
    mock_asyncio_run.side_effect = _close_coro_if_possible

    mock_args = MagicMock()
    mock_args.data_dir = None
    mock_args.log_level = "DEBUG"

    result = run_main(mock_args)

    assert result == 0
    # Check that log level was set in config
    assert mock_config["logging"]["level"] == "DEBUG"


@patch("mmrelay.main.print_banner")
@patch("mmrelay.config.load_config")
@patch("mmrelay.config.load_credentials")
@patch("mmrelay.config.set_config")
@patch("mmrelay.log_utils.configure_component_debug_logging")
def test_run_main_with_credentials_json(
    _mock_configure_logging,
    _mock_set_config,
    mock_load_credentials,
    mock_load_config,
    _mock_print_banner,
):
    """
    Test run_main with credentials.json present (different required keys).

    When credentials.json provides matrix authentication, the matrix.homeserver
    key is not required in config.yaml.
    """
    mock_config = {
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }
    mock_load_config.return_value = mock_config
    mock_load_credentials.return_value = {"access_token": "test_token"}

    mock_args = MagicMock()
    mock_args.data_dir = None
    mock_args.log_level = None

    with patch("asyncio.run") as mock_asyncio_run:
        mock_asyncio_run.side_effect = _close_coro_if_possible
        result = run_main(mock_args)

        assert result == 0
        mock_asyncio_run.assert_called_once()


@patch("mmrelay.main.print_banner")
@patch("mmrelay.config.load_config")
@patch("mmrelay.config.load_credentials")
@patch("mmrelay.main.get_legacy_env_vars")
@patch("mmrelay.main.get_legacy_dirs")
@patch("mmrelay.main.get_home_dir")
@patch("mmrelay.config.get_log_dir")
@patch("mmrelay.config.os.makedirs")
def test_run_main_legacy_layout_warning(
    mock_makedirs,
    mock_get_log_dir,
    mock_get_home_dir,
    mock_get_legacy_dirs,
    mock_get_legacy_env_vars,
    mock_load_credentials,
    mock_load_config,
    _mock_print_banner,
):
    """Test that warning messages are logged when legacy layout is enabled."""
    mock_config = {
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }
    mock_load_config.return_value = mock_config
    mock_load_credentials.return_value = None
    mock_get_home_dir.return_value = Path("/test/home/dir")
    mock_get_legacy_dirs.return_value = [Path("/test/legacy/dir")]
    mock_get_legacy_env_vars.return_value = ["MMRELAY_DATA_DIR"]
    mock_get_log_dir.return_value = "/test/log/dir"

    mock_args = MagicMock()
    mock_args.data_dir = None
    mock_args.log_level = None

    mock_rich_logger = MagicMock()
    mock_rich_logger.info = MagicMock()

    with (
        patch("asyncio.run") as mock_asyncio_run,
        patch("mmrelay.main.get_logger", return_value=mock_rich_logger),
    ):
        mock_asyncio_run.side_effect = _close_coro_if_possible
        result = run_main(mock_args)

    assert result == 0
    mock_rich_logger.warning.assert_any_call(
        "Legacy data layout detected (MMRELAY_HOME=%s, legacy_env_vars=%s, legacy_dirs=%s). This layout is deprecated and will be removed in a future release.",
        "/test/home/dir",
        "MMRELAY_DATA_DIR",
        "/test/legacy/dir",
    )
    mock_rich_logger.warning.assert_any_call(
        "To migrate to the new layout, see docs/DOCKER.md: Migrating to the New Layout."
    )
