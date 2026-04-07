"""Tests for credential management functionality in mmrelay."""

from pathlib import Path
from unittest.mock import patch

import pytest

from mmrelay.cli_utils import _cleanup_local_session_data
from mmrelay.config import get_e2ee_store_dir, load_credentials, save_credentials
from mmrelay.constants.config import CONFIG_KEY_DEVICE_ID
from mmrelay.matrix_utils import _can_auto_create_credentials


@patch("mmrelay.config.os.makedirs")
def test_get_e2ee_store_dir(mock_makedirs):
    """Test E2EE store directory creation."""
    store_dir = get_e2ee_store_dir()
    assert store_dir is not None
    assert "store" in store_dir
    mock_makedirs.assert_called_once()


@patch("mmrelay.config.get_credentials_path")
@patch("mmrelay.config.os.path.exists")
@patch("mmrelay.config.open")
@patch("mmrelay.config.json.load")
def test_load_credentials_success(
    mock_json_load, _mock_open, mock_exists, mock_get_credentials_path
):
    """Test successful credentials loading."""
    mock_get_credentials_path.return_value = Path("/test/config/credentials.json")
    mock_exists.return_value = True
    mock_json_load.return_value = {
        "homeserver": "https://matrix.example.org",
        "user_id": "@bot:example.org",
        "access_token": "test_token",
        CONFIG_KEY_DEVICE_ID: "TEST_DEVICE",
    }

    credentials = load_credentials()

    assert credentials is not None
    assert credentials["homeserver"] == "https://matrix.example.org"
    assert credentials["user_id"] == "@bot:example.org"
    assert credentials["access_token"] == "test_token"
    assert credentials[CONFIG_KEY_DEVICE_ID] == "TEST_DEVICE"


@patch("mmrelay.config.get_credentials_path")
@patch("mmrelay.config.os.path.exists")
def test_load_credentials_file_not_exists(mock_exists, mock_get_credentials_path):
    """Test credentials loading when file doesn't exist."""
    mock_get_credentials_path.return_value = Path("/test/config/credentials.json")
    mock_exists.return_value = False

    credentials = load_credentials()

    assert credentials is None


@patch("mmrelay.config.get_credentials_path")
@patch("mmrelay.config.get_explicit_credentials_path", return_value=None)
@patch("mmrelay.config.open")
@patch("mmrelay.config.json.dump")
@patch("mmrelay.config.os.makedirs")
@patch("mmrelay.config.os.path.exists", return_value=True)
def test_save_credentials(
    _mock_exists,
    _mock_makedirs,
    mock_json_dump,
    _mock_open,
    _mock_get_explicit,
    mock_get_credentials_path,
):
    """
    Verify that save_credentials writes the provided credentials JSON to the resolved config directory.

    get_explicit_credentials_path is patched to return None so save_credentials falls through to
    get_credentials_path, which is mocked to return a fixed path. Asserts that the target directory
    is created, the credentials file is opened, and json.dump is called with the credentials and
    an indent of 2.
    """
    mock_get_credentials_path.return_value = Path("/test/config/credentials.json")

    test_credentials = {
        "homeserver": "https://matrix.example.org",
        "user_id": "@bot:example.org",
        "access_token": "test_token",
        CONFIG_KEY_DEVICE_ID: "TEST_DEVICE",
    }

    save_credentials(test_credentials)

    _mock_makedirs.assert_called_once_with("/test/config", exist_ok=True)
    _mock_open.assert_called_once()
    mock_json_dump.assert_called_once_with(
        test_credentials, _mock_open().__enter__(), indent=2
    )


def test_cleanup_local_session_data_success():
    """Test successful cleanup of local session data."""
    with (
        patch(
            "mmrelay.paths.resolve_all_paths",
            return_value={
                "credentials_path": "/test/config/matrix/credentials.json",
                "store_dir": "/test/config/matrix/store",
            },
        ),
        patch("mmrelay.config.load_config", return_value={}),
        patch("os.path.exists") as mock_exists,
        patch("os.remove") as mock_remove,
        patch("shutil.rmtree") as mock_rmtree,
    ):
        mock_exists.return_value = True

        result = _cleanup_local_session_data()

        assert result is True
        mock_remove.assert_called_once_with("/test/config/matrix/credentials.json")
        mock_rmtree.assert_called_once_with("/test/config/matrix/store")


def test_cleanup_local_session_data_files_not_exist():
    """Test cleanup when files don't exist."""
    with (
        patch(
            "mmrelay.paths.resolve_all_paths",
            return_value={
                "credentials_path": "/test/config/matrix/credentials.json",
                "store_dir": "/test/config/matrix/store",
            },
        ),
        patch("mmrelay.config.load_config", return_value={}),
        patch("os.path.exists", return_value=False),
    ):
        result = _cleanup_local_session_data()

        assert result is True


def test_cleanup_local_session_data_permission_error():
    """Test cleanup with permission errors."""
    with (
        patch(
            "mmrelay.paths.resolve_all_paths",
            return_value={
                "credentials_path": "/test/config/matrix/credentials.json",
                "store_dir": "/test/config/matrix/store",
            },
        ),
        patch("mmrelay.config.load_config", return_value={}),
        patch("os.path.exists", return_value=True),
        patch("os.remove", side_effect=PermissionError("Access denied")),
        patch("shutil.rmtree", side_effect=PermissionError("Access denied")),
    ):
        result = _cleanup_local_session_data()

        assert result is False


def test_can_auto_create_credentials_success():
    """Test successful detection of auto-create capability."""
    matrix_config = {
        "homeserver": "https://matrix.example.org",
        "bot_user_id": "@bot:example.org",
        "password": "test_password",
    }

    result = _can_auto_create_credentials(matrix_config)
    assert result is True


@pytest.mark.parametrize(
    "invalid_config",
    [
        pytest.param(
            {
                "homeserver": "https://matrix.example.org",
                "bot_user_id": None,
                "password": "test_password",
            },
            id="none_bot_user_id",
        ),
        pytest.param(
            {
                "homeserver": None,
                "bot_user_id": "@bot:matrix.org",
                "password": "password123",
            },
            id="none_homeserver",
        ),
        pytest.param(
            {
                "homeserver": "https://matrix.org",
                "bot_user_id": None,
                "password": "password123",
            },
            id="none_bot_user_id_alt",
        ),
    ],
)
def test_can_auto_create_credentials_with_invalid_values(invalid_config):
    """
    Test _can_auto_create_credentials returns False when values are None.
    """
    result = _can_auto_create_credentials(invalid_config)
    assert result is False


def test_can_auto_create_credentials_whitespace_values():
    """
    Test _can_auto_create_credentials returns False when values contain only whitespace.
    """
    config = {
        "homeserver": "   ",
        "bot_user_id": "@bot:matrix.org",
        "password": "password123",
    }

    result = _can_auto_create_credentials(config)
    assert result is False
