import asyncio
import importlib
import os
import ssl
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from nio import SyncError, ToDeviceError, ToDeviceResponse

import mmrelay.matrix_utils as matrix_utils_module
from mmrelay.constants.app import CREDENTIALS_FILENAME
from mmrelay.constants.domain import MATRIX_EVENT_TYPE_ROOM_MESSAGE
from mmrelay.constants.formats import (
    MATRIX_SUPPRESS_KEY,
)
from mmrelay.matrix_utils import (
    ImageUploadError,
    NioLocalTransportError,
    NioRemoteTransportError,
    _can_auto_create_credentials,
    _create_mapping_info,
    _extract_localpart_from_mxid,
    _get_detailed_matrix_error_message,
    _get_msgs_to_keep_config,
    _handle_detection_sensor_packet,
    _is_room_alias,
    _iter_room_alias_entries,
    _normalize_bot_user_id,
    _update_room_id_in_mapping,
    connect_matrix,
    get_displayname,
    get_user_display_name,
    login_matrix_bot,
    matrix_relay,
    on_decryption_failure,
    on_room_member,
    send_image,
    send_room_image,
    strip_quoted_lines,
    truncate_message,
    upload_image,
    validate_prefix_format,
)

# Test constants for login_matrix_bot tests (avoids Ruff S106 warnings)
TEST_HOMESERVER = "https://matrix.org"
TEST_USERNAME = "user"
TEST_PASSWORD = "pass"
TEST_FULL_MXID = "@user:matrix.org"

# Matrix utility function tests - converted from unittest.TestCase to standalone pytest functions


# Async Matrix function tests - converted from unittest.TestCase to standalone pytest functions


@pytest.fixture
def matrix_config():
    """Test configuration for Matrix functions."""
    return {
        "matrix": {
            "homeserver": "https://matrix.example.com",
            "bot_user_id": "@bot:example.com",
            "password": "test_password",
            "rooms": {"!test:example.com": "Test Room"},
        },
        "meshtastic": {},
    }
    assert result == expected


# Async Matrix function tests - converted from unittest.TestCase to standalone pytest functions


@pytest.fixture
def matrix_config():
    """Test configuration for Matrix functions."""
    return {
        "matrix": {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:matrix.org",
            "prefix_enabled": True,
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    # Note: whoami() is no longer called in the new E2EE implementation

    # Note: device_id remains None for legacy config without E2EE


@patch("mmrelay.matrix_utils.matrix_client", None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
@patch("mmrelay.matrix_utils.login_matrix_bot")
@patch("mmrelay.matrix_utils.async_load_credentials", new_callable=AsyncMock)
async def test_connect_matrix_alias_resolution_exception(
    mock_load_credentials, mock_login_bot, _mock_logger, mock_async_client
):
    """
    Test that connect_matrix handles alias resolution exceptions gracefully.
    """
    with patch("mmrelay.matrix_utils._create_ssl_context") as mock_ssl_context:
        # Mock SSL context creation
        mock_ssl_context.return_value = MagicMock()

        # Mock login_matrix_bot to return True (successful automatic login)
        mock_login_bot.return_value = True

        # Mock load_credentials to return valid credentials
        mock_load_credentials.return_value = {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "user_id": "@test:matrix.org",
            "device_id": "test_device_id",
        }

        # Mock the AsyncClient instance
        mock_client_instance = MagicMock()
        mock_client_instance.rooms = {}

        # Create proper async mock methods
        async def mock_whoami():
            """
            Simulate a Matrix client's `whoami()` response for tests.

            Returns:
                unittest.mock.MagicMock: Mock object with a `device_id` attribute set to "test_device_id".
            """
            return MagicMock(device_id="test_device_id")

        async def mock_sync(*_args, **_kwargs):
            """
            Return a new unittest.mock.MagicMock instance each time the coroutine is awaited.

            Returns:
                unittest.mock.MagicMock: A fresh MagicMock suitable as a mocked async client's `sync`-like result in tests.
            """
            return MagicMock()

        async def mock_get_displayname(*_args, **_kwargs):
            """
            Return a MagicMock representing a user's display name for asynchronous tests.

            Returns:
                MagicMock: with a 'displayname' attribute set to 'Test Bot'.
            """
            return MagicMock(displayname="Test Bot")

        # Create a mock for room_resolve_alias that raises an exception
        mock_room_resolve_alias = MagicMock()

        async def mock_room_resolve_alias_impl(_alias):
            """
            Mock async implementation that simulates a network failure when resolving a Matrix room alias.

            Parameters:
                _alias (str): The room alias to resolve (ignored by this mock).

            Raises:
                OSError: Always raised to simulate a network error during alias resolution.
            """
            raise OSError("Simulated network failure")

        mock_room_resolve_alias.side_effect = mock_room_resolve_alias_impl

        mock_client_instance.whoami = mock_whoami
        mock_client_instance.sync = mock_sync
        mock_client_instance.get_displayname = mock_get_displayname
        mock_client_instance.room_resolve_alias = mock_room_resolve_alias

        # Mock async close method for cleanup
        async def mock_close():
            """Simulate async client close."""
            pass

        mock_client_instance.close = mock_close
        mock_async_client.return_value = mock_client_instance

        # Create config with room aliases
        config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "bot_user_id": "@test:matrix.org",
                "password": "test_password",
            },
            "matrix_rooms": [{"id": "#error:matrix.org", "meshtastic_channel": 1}],
        }

        result = await connect_matrix(config)

        # Verify client was created
        mock_async_client.assert_called_once()
        assert result == mock_client_instance

        # Verify alias resolution was called
        mock_client_instance.room_resolve_alias.assert_called_once_with(
            "#error:matrix.org"
        )

        # Verify exception was logged
        _mock_logger.exception.assert_called_with(
            "Error resolving alias #error:matrix.org"
        )

        # Verify config was not modified (still contains alias)
        assert config["matrix_rooms"][0]["id"] == "#error:matrix.org"


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.os.makedirs")
@patch("mmrelay.matrix_utils.os.listdir")
@patch("mmrelay.matrix_utils.os.path.exists")
@patch("mmrelay.matrix_utils.os.path.isfile")
@patch("builtins.open")
@patch("mmrelay.config.json.load")
@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils._create_ssl_context")
@patch("mmrelay.matrix_utils.matrix_client", None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_connect_matrix_missing_device_id_uses_direct_assignment(
    _mock_logger,
    mock_async_client,
    mock_ssl_context,
    mock_save_credentials,
    mock_json_load,
    _mock_open,
    _mock_isfile,
    _mock_exists,
    _mock_listdir,
    _mock_makedirs,
    monkeypatch,
):
    """
    When credentials are missing device_id, the client should discover it via whoami
    and then restore the session using the discovered device_id.
    """
    _mock_exists.return_value = True
    _mock_isfile.return_value = True
    mock_json_load.return_value = {
        "homeserver": "https://matrix.example.org",
        "user_id": "@bot:example.org",
        "access_token": "test_token",
    }
    _mock_listdir.return_value = []
    mock_ssl_context.return_value = MagicMock()

    mock_client_instance = MagicMock()
    mock_client_instance.rooms = {}

    async def mock_sync(*_args, **_kwargs):
        """
        Create and return a MagicMock to simulate a sync operation result.

        Any positional and keyword arguments are accepted and ignored.

        Returns:
            MagicMock: A new MagicMock instance representing the mocked sync result.
        """
        return MagicMock()

    def mock_restore_login(user_id, device_id, access_token):
        """
        Set the mocked Matrix client's login state by assigning user, device, and token attributes.

        Parameters:
            user_id (str): Matrix user ID to set on the mock client.
            device_id (str): Device ID to set on the mock client.
            access_token (str): Access token to set on the mock client.
        """
        mock_client_instance.access_token = access_token
        mock_client_instance.user_id = user_id
        mock_client_instance.device_id = device_id

    discovered_device_id = "DISCOVERED_DEVICE"

    mock_client_instance.sync = AsyncMock(side_effect=mock_sync)
    mock_client_instance.restore_login = MagicMock(side_effect=mock_restore_login)
    mock_client_instance.whoami = AsyncMock(
        return_value=SimpleNamespace(device_id=discovered_device_id)
    )
    mock_client_instance.should_upload_keys = False
    mock_client_instance.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    mock_async_client.return_value = mock_client_instance
    # Minimal config needed for matrix_rooms
    monkeypatch.setattr(
        "mmrelay.matrix_utils.config",
        {"matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}]},
        raising=False,
    )

    client = await connect_matrix()

    assert client is mock_client_instance
    # restore_login should use the discovered device_id from whoami
    mock_client_instance.restore_login.assert_called_once_with(
        user_id="@bot:example.org",
        device_id=discovered_device_id,
        access_token="test_token",
    )
    # Access token should still be set via restore_login
    assert mock_client_instance.access_token == "test_token"
    assert mock_client_instance.user_id == "@bot:example.org"
    assert mock_client_instance.device_id == discovered_device_id
    mock_save_credentials.assert_called_once()
    call_args = mock_save_credentials.call_args
    assert call_args[0][0] == {
        "homeserver": "https://matrix.example.org",
        "user_id": "@bot:example.org",
        "access_token": "test_token",
        "device_id": discovered_device_id,
    }
    assert call_args[1]["credentials_path"].endswith(CREDENTIALS_FILENAME)


@pytest.mark.asyncio
async def test_connect_matrix_sync_timeout_closes_client(monkeypatch):
    """Initial sync timeout should close the client and raise ConnectionError."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_client.close = AsyncMock()
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    # Capture AsyncClient ssl argument for separate test
    def fake_async_client(*_args, **_kwargs):
        """
        Provide a preconfigured mock Matrix client for use in tests.

        Ignores all positional and keyword arguments and always returns the shared test mock client.

        Returns:
            mock_client: The preconfigured mock Matrix client instance used by tests.
        """
        return mock_client

    monkeypatch.setattr("mmrelay.matrix_utils.AsyncClient", fake_async_client)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.config",
        {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        },
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.MATRIX_INITIAL_SYNC_MAX_ATTEMPTS", 3, raising=False
    )
    mock_sleep = AsyncMock()
    monkeypatch.setattr("mmrelay.matrix_utils.asyncio.sleep", mock_sleep)

    with pytest.raises(ConnectionError):
        await connect_matrix()

    mock_client.close.assert_awaited_once()
    assert mock_client.sync.await_count == 3
    assert [call.args[0] for call in mock_sleep.await_args_list] == [
        matrix_utils_module.MATRIX_SYNC_RETRY_DELAY_SECS,
        min(
            matrix_utils_module.MATRIX_SYNC_RETRY_DELAY_SECS * 2.0,
            matrix_utils_module.MATRIX_INITIAL_SYNC_RETRY_MAX_DELAY_SECS,
        ),
    ]
    import mmrelay.matrix_utils as mx

    assert mx.matrix_client is None


@pytest.mark.asyncio
async def test_connect_matrix_sync_timeout_retry_then_success(monkeypatch):
    """A transient initial-sync timeout should retry and succeed without failing startup."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(
        side_effect=[asyncio.TimeoutError(), SimpleNamespace()]
    )
    mock_client.close = AsyncMock()
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_async_client(*_args, **_kwargs):
        return mock_client

    monkeypatch.setattr("mmrelay.matrix_utils.AsyncClient", fake_async_client)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.config",
        {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        },
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.MATRIX_INITIAL_SYNC_MAX_ATTEMPTS", 0, raising=False
    )
    mock_sleep = AsyncMock()
    monkeypatch.setattr("mmrelay.matrix_utils.asyncio.sleep", mock_sleep)

    client = await connect_matrix()

    assert client is mock_client
    assert mock_client.sync.await_count == 2
    mock_sleep.assert_awaited_once_with(
        matrix_utils_module.MATRIX_SYNC_RETRY_DELAY_SECS
    )


@pytest.mark.asyncio
async def test_connect_matrix_uses_ssl_context_object(monkeypatch):
    """Ensure AsyncClient receives the actual SSLContext object, not a bool."""
    ssl_ctx = object()
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.should_upload_keys = False
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.close = AsyncMock()

    client_calls = []

    def fake_async_client(*_args, **_kwargs):
        """
        Create a fake async Matrix client for tests that records the passed SSL value and returns a predefined mock client.

        Parameters:
            *_args: Ignored positional arguments.
            **_kwargs: Keyword arguments; the `ssl` key, if present, is recorded into `client_calls`.

        Returns:
            mock_client: The predefined mock client object used by tests.
        """
        client_calls.append(_kwargs.get("ssl"))
        return mock_client

    monkeypatch.setattr("mmrelay.matrix_utils.AsyncClient", fake_async_client)
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: ssl_ctx, raising=False
    )
    # Stub helpers to avoid extra work
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    monkeypatch.setattr(
        "mmrelay.matrix_utils.config",
        {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        },
        raising=False,
    )

    client = await connect_matrix()

    assert client is mock_client
    assert client_calls and client_calls[0] is ssl_ctx


@pytest.mark.asyncio
async def test_connect_matrix_sync_validation_error_retries_with_invite_safe_filter():
    """ValidationError from invite events triggers invite-safe sync retry."""
    import jsonschema

    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock()
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.close = AsyncMock()

    # Set up two sync calls: first fails with ValidationError, second succeeds
    call_count = [0]

    async def mock_sync(*_args, **_kwargs):
        """
        Test helper that simulates a sync operation failing once with a ValidationError and succeeding thereafter.

        On each invocation this increments the enclosing `call_count[0]` counter. The first call raises a
        jsonschema.exceptions.ValidationError to simulate an invite-safe filtering error; subsequent calls
        return a simple success sentinel.

        Raises:
            jsonschema.exceptions.ValidationError: on the first invocation.

        Returns:
            SimpleNamespace: A success sentinel object on invocations after the first.
        """
        call_count[0] += 1
        if call_count[0] == 1:
            # First sync raises ValidationError (caught, triggers invite-safe filter)
            raise jsonschema.ValidationError(  # type: ignore[attr-defined]
                message="Invalid schema",
                path=(),
                schema_path=(),
            )
        # Second sync succeeds (with invite-safe filter)
        return SimpleNamespace()

    mock_client.sync = mock_sync

    # Set up mocks for connect_matrix
    def fake_async_client(*_args, **_kwargs):
        """
        Return the preconfigured mock Matrix client, ignoring all positional and keyword arguments.

        This helper supplies the shared mock client instance for tests that expect an async client factory.

        Returns:
            mock_client: The mock Matrix client instance used by the test suite.
        """
        return mock_client

    # Patch jsonschema.exceptions to simulate ImportError for ValidationError only
    with (
        patch("mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock()),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.os.path.isfile", return_value=False),
        patch(
            "mmrelay.matrix_utils._resolve_aliases_in_mapping",
            AsyncMock(return_value=None),
        ),
        patch(
            "mmrelay.matrix_utils._display_room_channel_mappings",
            lambda *_args, **_kwargs: None,
        ),
        patch("mmrelay.matrix_utils.AsyncClient", fake_async_client),
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    # Verify that sync was called twice (initial failed, retry with invite-safe filter)
    assert call_count[0] == 2

    # Verify logging of retry behavior
    mock_logger.warning.assert_any_call(
        "Retrying initial sync without invites to tolerate invalid invite_state payloads."
    )

    # Verify client attributes were set with invite-safe filter
    assert hasattr(mock_client, "mmrelay_sync_filter")
    assert hasattr(mock_client, "mmrelay_first_sync_filter")


@pytest.mark.asyncio
async def test_connect_matrix_sync_validation_error_retry_failure_closes_client():
    """Failed invite-safe retry should close the client and raise ConnectionError."""
    import jsonschema

    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.close = AsyncMock(side_effect=NioLocalTransportError("close failed"))

    call_count = {"count": 0}

    async def mock_sync(*_args, **_kwargs):
        """
        Simulate a sync operation that increments a shared call counter and fails with controlled exceptions.

        Increments call_count["count"] each invocation. On the first invocation raises jsonschema.exceptions.ValidationError with message "Invalid schema"; on every subsequent invocation raises NioLocalTransportError("retry failed"). Positional and keyword arguments are ignored.
        """
        call_count["count"] += 1
        if call_count["count"] == 1:
            raise jsonschema.ValidationError(  # type: ignore[attr-defined]
                message="Invalid schema",
                path=(),
                schema_path=(),
            )
        raise NioLocalTransportError("retry failed")

    mock_client.sync = mock_sync

    def fake_async_client(*_args, **_kwargs):
        """
        Return the preconfigured mock Matrix client, ignoring all positional and keyword arguments.

        This helper supplies the shared mock client instance for tests that expect an async client factory.

        Returns:
            mock_client: The mock Matrix client instance used by the test suite.
        """
        return mock_client

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.AsyncClient", fake_async_client),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock()),
        patch("mmrelay.matrix_utils.os.path.isfile", return_value=False),
    ):
        with pytest.raises(ConnectionError):
            await connect_matrix(config)

    assert call_count["count"] == 2
    assert mock_client.close.await_count == 1


@pytest.mark.asyncio
async def test_connect_matrix_uploads_keys_when_needed(monkeypatch):
    """
    Verify that the Matrix client uploads keys when the client's key-upload flag is enabled.

    Asserts that connect_matrix returns the created client and that the client's `keys_upload` coroutine is awaited exactly once when `should_upload_keys` is truthy.
    """
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.close = AsyncMock()
    type(mock_client).should_upload_keys = PropertyMock(return_value=True)
    mock_client.keys_upload = AsyncMock()
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_async_client(*_args, **_kwargs):
        """
        Provide a preconfigured mock Matrix client for use in tests.

        Ignores all positional and keyword arguments and always returns the shared test mock client.

        Returns:
            mock_client: The preconfigured mock Matrix client instance used by tests.
        """
        return mock_client

    monkeypatch.setattr("mmrelay.matrix_utils.AsyncClient", fake_async_client)
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_e2ee_status",
        lambda *_args, **_kwargs: {"overall_status": "ok"},
        raising=False,
    )

    def fake_import(name):
        """
        Return a fake module-like object used to simulate imports of nio/olm modules in tests.

        Parameters:
            name (str): Module name being imported.

        Returns:
            object: A module-like object:
              - For "nio.crypto": a SimpleNamespace with attribute `OlmDevice` set to True.
              - For "nio.store": a SimpleNamespace with attribute `SqliteStore` set to True.
              - For "olm": a MagicMock instance.
              - For any other name: a MagicMock instance.
        """
        if name == "nio.crypto":
            return SimpleNamespace(OlmDevice=True)
        if name == "nio.store":
            return SimpleNamespace(SqliteStore=True)
        if name == "olm":
            return MagicMock()
        return MagicMock()

    monkeypatch.setattr(
        "mmrelay.matrix_utils.importlib.import_module", fake_import, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_room_encryption_warnings",
        lambda *_args, **_kwargs: [],
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    monkeypatch.setattr(
        "mmrelay.matrix_utils.config",
        {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        },
        raising=False,
    )

    client = await connect_matrix()

    assert client is mock_client
    mock_client.keys_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_matrix_credentials_load_exception_uses_config(
    monkeypatch, tmp_path
):
    """Credential load errors should warn and fall back to config auth."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.close = AsyncMock()

    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    candidate_path = tmp_path / CREDENTIALS_FILENAME
    candidate_path.write_text('{"invalid": true}', encoding="utf-8")

    with (
        patch(
            "mmrelay.config.get_credentials_search_paths",
            return_value=[str(candidate_path)],
        ),
        patch(
            "mmrelay.matrix_utils.get_credentials_path",
            return_value=tmp_path / CREDENTIALS_FILENAME,
        ),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        client = await connect_matrix(config)

    assert client is mock_client
    assert any(
        "Ignoring invalid credentials file" in str(call.args[0])
        or "Ignoring credentials.json missing required keys" in str(call.args[0])
        for call in mock_logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_connect_matrix_explicit_credentials_path_is_used(tmp_path):
    """Explicit credentials_path should be expanded and used first."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.close = AsyncMock()
    mock_client.restore_login = MagicMock()

    access_value = "token"
    expanded_path = tmp_path / "explicit_credentials.json"
    expanded_path_str = str(expanded_path)
    expanded_path.write_text(
        '{"homeserver": "https://matrix.example.org", '
        f'"access_token": "{access_value}", '
        '"user_id": "@bot:example.org", '
        '"device_id": "DEVICE123"}',
        encoding="utf-8",
    )

    config = {
        "credentials_path": "~/explicit_credentials.json",
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "ignored",
            "bot_user_id": "@ignored:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.config.os.getenv", return_value=None),
        patch(
            "mmrelay.config.os.path.expanduser",
            return_value=expanded_path_str,
        ) as mock_expanduser,
        patch("mmrelay.matrix_utils.AsyncClient", lambda *_a, **_k: mock_client),
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock()),
        patch(
            "mmrelay.matrix_utils._resolve_aliases_in_mapping",
            AsyncMock(return_value=None),
        ),
        patch(
            "mmrelay.matrix_utils._display_room_channel_mappings",
            lambda *_args, **_kwargs: None,
        ),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status",
            return_value={"overall_status": "ok"},
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        client = await connect_matrix(config)

    assert client is mock_client
    mock_expanduser.assert_any_call("~/explicit_credentials.json")
    mock_client.restore_login.assert_called_once_with(
        user_id="@bot:example.org",
        device_id="DEVICE123",
        access_token=access_value,
    )


@pytest.mark.asyncio
async def test_connect_matrix_ignores_config_access_token_when_credentials_present(
    monkeypatch,
):
    """Credentials should take precedence over config access_token."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.close = AsyncMock()

    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "config_token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=True),
        patch("mmrelay.matrix_utils.os.path.isfile", return_value=True),
        patch("builtins.open", new_callable=MagicMock),
        patch(
            "mmrelay.config.json.load",
            return_value={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "access_token": "creds_token",
                "device_id": "DEV",
            },
        ),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        client = await connect_matrix(config)

    assert client is mock_client
    mock_logger.info.assert_any_call(
        "NOTE: Ignoring Matrix login details in config.yaml in favor of credentials.json"
    )


@pytest.mark.asyncio
async def test_connect_matrix_auto_login_load_credentials_failure(monkeypatch):
    """Automatic login should return None if new credentials cannot be loaded."""
    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "bot_user_id": "@bot:example.org",
            "password": "secret",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.login_matrix_bot", return_value=True),
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new=AsyncMock(return_value=None),
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await connect_matrix(config)

    assert result is None
    mock_logger.error.assert_called_with("Failed to load newly created credentials")


@pytest.mark.asyncio
async def test_connect_matrix_auto_login_failure(monkeypatch):
    """Automatic login failures should return None."""
    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "bot_user_id": "@bot:example.org",
            "password": "secret",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.login_matrix_bot", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await connect_matrix(config)

    assert result is None
    assert any(
        "Automatic login failed" in call.args[0]
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_connect_matrix_missing_matrix_section_returns_none():
    """Missing matrix config should log and return None."""
    config = {"matrix_rooms": []}

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await connect_matrix(config)

    assert result is None
    mock_logger.error.assert_any_call(
        "No Matrix authentication available. Neither credentials.json nor matrix section in config found."
    )


@pytest.mark.asyncio
async def test_connect_matrix_missing_required_fields_returns_none():
    """Missing required fields in matrix section should return None."""
    config = {
        "matrix": {"homeserver": "https://example.org", "bot_user_id": "@bot:example"},
        "matrix_rooms": [],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await connect_matrix(config)

    assert result is None
    assert any(
        "Matrix section is missing required field"
        in " ".join(str(arg) for arg in call.args)
        and "access_token" in " ".join(str(arg) for arg in call.args)
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_connect_matrix_missing_matrix_rooms_raises():
    """Missing matrix_rooms should raise ValueError."""
    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
        }
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        pytest.raises(ValueError),
    ):
        await connect_matrix(config)


@pytest.mark.asyncio
async def test_connect_matrix_e2ee_windows_disables(monkeypatch):
    """E2EE should be disabled on Windows platforms."""
    import mmrelay.matrix_utils as mx

    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    monkeypatch.setattr("mmrelay.matrix_utils.sys.platform", "win32", raising=False)
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr("mmrelay.matrix_utils.AsyncClientConfig", MagicMock())
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
            "encryption": {"enabled": True},
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        await connect_matrix(config)

    _, kwargs = mx.AsyncClientConfig.call_args  # type: ignore[attr-defined]
    assert kwargs["encryption_enabled"] is False


@pytest.mark.asyncio
async def test_connect_matrix_e2ee_store_path_from_config(monkeypatch):
    """Configured E2EE store_path should be expanded and created."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_import(name):
        if name == "nio.crypto":
            return SimpleNamespace(OlmDevice=True)
        if name == "nio.store":
            return SimpleNamespace(SqliteStore=True)
        if name == "olm":
            return MagicMock()
        return MagicMock()

    monkeypatch.setattr("mmrelay.matrix_utils.sys.platform", "linux", raising=False)
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.importlib.import_module", fake_import, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    store_path = os.path.expanduser("~/mmrelay-store")
    client_calls = []

    def fake_async_client(*_args, **_kwargs):
        client_calls.append(_kwargs)
        return mock_client

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        fake_async_client,
        raising=False,
    )
    with (
        patch("mmrelay.matrix_utils.os.makedirs"),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True, "store_path": store_path},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    assert client_calls
    assert client_calls[0]["store_path"] == store_path


@pytest.mark.asyncio
async def test_connect_matrix_e2ee_store_path_precedence_encryption(monkeypatch):
    """Encryption store_path should take precedence over e2ee store_path."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_import(name):
        if name == "nio.crypto":
            return SimpleNamespace(OlmDevice=True)
        if name == "nio.store":
            return SimpleNamespace(SqliteStore=True)
        if name == "olm":
            return MagicMock()
        return MagicMock()

    monkeypatch.setattr("mmrelay.matrix_utils.sys.platform", "linux", raising=False)
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.importlib.import_module", fake_import, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    encryption_path = os.path.expanduser("~/enc-store")
    e2ee_path = os.path.expanduser("~/e2ee-store")
    client_calls = []

    def fake_async_client(*_args, **_kwargs):
        client_calls.append(_kwargs)
        return mock_client

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        fake_async_client,
        raising=False,
    )
    with (
        patch("mmrelay.matrix_utils.os.makedirs"),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True, "store_path": encryption_path},
                "e2ee": {"store_path": e2ee_path},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    assert client_calls
    assert client_calls[0]["store_path"] == encryption_path


@pytest.mark.asyncio
async def test_connect_matrix_e2ee_store_path_uses_e2ee_section(monkeypatch):
    """e2ee store_path should be used when encryption store_path is absent."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_import(name):
        if name == "nio.crypto":
            return SimpleNamespace(OlmDevice=True)
        if name == "nio.store":
            return SimpleNamespace(SqliteStore=True)
        if name == "olm":
            return MagicMock()
        return MagicMock()

    monkeypatch.setattr("mmrelay.matrix_utils.sys.platform", "linux", raising=False)
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.importlib.import_module", fake_import, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    e2ee_path = os.path.expanduser("~/e2ee-store")
    client_calls = []

    def fake_async_client(*_args, **_kwargs):
        client_calls.append(_kwargs)
        return mock_client

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        fake_async_client,
        raising=False,
    )
    with (
        patch("mmrelay.matrix_utils.os.makedirs"),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "e2ee": {"enabled": True, "store_path": e2ee_path},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    assert client_calls
    assert client_calls[0]["store_path"] == e2ee_path


@pytest.mark.asyncio
async def test_connect_matrix_e2ee_store_path_default(monkeypatch, tmp_path):
    """Default store path should be used when no store_path is configured."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_import(name):
        if name == "nio.crypto":
            return SimpleNamespace(OlmDevice=True)
        if name == "nio.store":
            return SimpleNamespace(SqliteStore=True)
        if name == "olm":
            return MagicMock()
        return MagicMock()

    monkeypatch.setattr("mmrelay.matrix_utils.sys.platform", "linux", raising=False)
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.importlib.import_module", fake_import, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    from mmrelay.config import get_e2ee_store_dir as config_get_e2ee_store_dir

    default_path = str(config_get_e2ee_store_dir())
    client_calls = []

    def fake_async_client(*_args, **_kwargs):
        client_calls.append(_kwargs)
        return mock_client

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        fake_async_client,
        raising=False,
    )
    with (
        patch("mmrelay.matrix_utils.os.makedirs"),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status",
            return_value={"overall_status": "ok"},
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    assert client_calls
    assert client_calls[0]["store_path"] == default_path


@pytest.mark.asyncio
async def test_connect_matrix_whoami_missing_device_id_warns(monkeypatch):
    """Missing device_id from whoami should warn and continue."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.whoami = AsyncMock(return_value=SimpleNamespace(device_id=None))

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=True),
        patch("mmrelay.matrix_utils.os.path.isfile", return_value=True),
        patch("builtins.open", new_callable=MagicMock),
        patch(
            "mmrelay.config.json.load",
            return_value={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "access_token": "token",
            },
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        config = {"matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}]}
        await connect_matrix(config)

    mock_logger.warning.assert_any_call("whoami response did not contain device_id")


@pytest.mark.asyncio
async def test_connect_matrix_whoami_failure_warns(monkeypatch):
    """whoami failures should warn and continue without a device_id."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.whoami = AsyncMock(side_effect=NioLocalTransportError("fail"))

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_e2ee_status",
        lambda *_args, **_kwargs: {"overall_status": "ok"},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_room_encryption_warnings",
        lambda *_args, **_kwargs: [],
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=True),
        patch("mmrelay.matrix_utils.os.path.isfile", return_value=True),
        patch("builtins.open", new_callable=MagicMock),
        patch(
            "mmrelay.config.json.load",
            return_value={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "access_token": "token",
            },
        ),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        config = {"matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}]}
        await connect_matrix(config)

    assert any(
        "Failed to discover device_id via whoami" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )
    mock_logger.warning.assert_any_call(
        "E2EE may not work properly without a device_id"
    )


@pytest.mark.asyncio
async def test_connect_matrix_save_credentials_failure_warns(monkeypatch):
    """Save failures after whoami device_id discovery should warn."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.whoami = AsyncMock(return_value=SimpleNamespace(device_id="DEV"))

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_e2ee_status",
        lambda *_args, **_kwargs: {"overall_status": "ok"},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_room_encryption_warnings",
        lambda *_args, **_kwargs: [],
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=True),
        patch("mmrelay.matrix_utils.os.path.isfile", return_value=True),
        patch("builtins.open", new_callable=MagicMock),
        patch(
            "mmrelay.config.json.load",
            return_value={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "access_token": "token",
            },
        ),
        patch("mmrelay.matrix_utils.save_credentials", side_effect=OSError("boom")),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        config = {"matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}]}
        await connect_matrix(config)

    assert any(
        "Failed to persist updated session information" in call.args[0]
        for call in mock_logger.exception.call_args_list
    )


@pytest.mark.asyncio
async def test_connect_matrix_keys_upload_failure_logs(monkeypatch):
    """Key upload errors should be logged and not raise."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.close = AsyncMock()
    type(mock_client).should_upload_keys = PropertyMock(return_value=True)
    mock_client.keys_upload = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_import(name):
        if name == "nio.crypto":
            return SimpleNamespace(OlmDevice=True)
        if name == "nio.store":
            return SimpleNamespace(SqliteStore=True)
        if name == "olm":
            return MagicMock()
        return MagicMock()

    monkeypatch.setattr(
        "mmrelay.matrix_utils.importlib.import_module", fake_import, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    mock_logger = MagicMock()
    monkeypatch.setattr(
        "mmrelay.matrix_utils.logger",
        mock_logger,
        raising=False,
    )

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
            "encryption": {"enabled": True},
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        client = await connect_matrix(config)

    assert client is mock_client
    mock_logger.exception.assert_any_call(
        "Failed to upload E2EE keys. Consider regenerating credentials with: mmrelay auth login"
    )


@pytest.mark.asyncio
async def test_connect_matrix_displayname_fallbacks(monkeypatch):
    """Missing displayname should fall back to bot_user_id."""
    import mmrelay.matrix_utils as mx

    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname=None)
    )
    mock_client.close = AsyncMock()

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_e2ee_status",
        lambda *_args, **_kwargs: {"overall_status": "ok"},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_room_encryption_warnings",
        lambda *_args, **_kwargs: [],
        raising=False,
    )

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with patch("mmrelay.matrix_utils.os.path.exists", return_value=False):
        client = await connect_matrix(config)

    assert client is mock_client
    assert mx.bot_user_name == "@bot:example.org"


@pytest.mark.asyncio
async def test_connect_matrix_displayname_exception_fallback(monkeypatch):
    """Displayname lookups that error should fall back to bot_user_id."""
    import mmrelay.matrix_utils as mx

    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_client.close = AsyncMock()

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_e2ee_status",
        lambda *_args, **_kwargs: {"overall_status": "ok"},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_room_encryption_warnings",
        lambda *_args, **_kwargs: [],
        raising=False,
    )

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        client = await connect_matrix(config)

    assert client is mock_client
    assert mx.bot_user_name == "@bot:example.org"
    assert any(
        "Failed to get bot display name" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


# E2EE Configuration Tests


# E2EE Client Initialization Tests


# Verify E2EE initialization sequence was called
# Since we're using simple functions, we can't assert calls, but we can verify the client was returned
# The fact that connect_matrix completed successfully means all the async calls worked


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.os.makedirs")
@patch("mmrelay.matrix_utils.os.listdir")
@patch("mmrelay.matrix_utils.os.path.exists")
@patch("mmrelay.matrix_utils._create_ssl_context")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_connect_matrix_e2ee_store_missing_db_files_warns(
    mock_logger,
    mock_async_client,
    mock_ssl_context,
    mock_exists,
    mock_listdir,
    _mock_makedirs,
):
    """Missing E2EE store DB files should warn when E2EE is enabled."""
    mock_listdir.return_value = ["notes.txt"]

    def exists_side_effect(path):
        if path.endswith(CREDENTIALS_FILENAME):
            return False
        if path == "/test/store":
            return True
        return False

    mock_exists.side_effect = exists_side_effect
    mock_ssl_context.return_value = MagicMock()

    mock_client_instance = MagicMock()
    mock_client_instance.rooms = {}
    mock_client_instance.sync = AsyncMock(return_value=MagicMock())
    mock_client_instance.whoami = AsyncMock(
        return_value=SimpleNamespace(device_id="DEV")
    )
    mock_client_instance.should_upload_keys = False
    mock_client_instance.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_async_client.return_value = mock_client_instance

    test_config = {
        "matrix": {
            "homeserver": "https://matrix.example.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:example.org",
            "encryption": {"enabled": True, "store_path": "/test/store"},
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    mock_olm = MagicMock()
    import importlib as _importlib

    real_import_module = _importlib.import_module

    def mock_import_side_effect(module_name, *args, **kwargs):
        if module_name == "olm":
            return mock_olm
        if module_name == "nio.crypto":
            mock_crypto = MagicMock()
            mock_crypto.OlmDevice = MagicMock()
            return mock_crypto
        if module_name == "nio.store":
            mock_store = MagicMock()
            mock_store.SqliteStore = MagicMock()
            return mock_store
        return real_import_module(module_name, *args, **kwargs)

    with (
        patch("mmrelay.config.is_e2ee_enabled", return_value=True),
        patch("importlib.import_module", side_effect=mock_import_side_effect),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
        patch(
            "mmrelay.matrix_utils._resolve_aliases_in_mapping",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "mmrelay.matrix_utils._display_room_channel_mappings",
            return_value=None,
        ),
        patch("mmrelay.matrix_utils.matrix_client", None),
    ):
        await connect_matrix(test_config)

    assert any(
        "No existing E2EE store files found" in call.args[0]
        for call in mock_logger.info.call_args_list
    )


@pytest.mark.asyncio
async def test_connect_matrix_e2ee_key_sharing_delay(monkeypatch, tmp_path):
    """E2EE-enabled connections should wait for key sharing delay."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_e2ee_status",
        lambda *_args, **_kwargs: {"overall_status": "ok"},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_room_encryption_warnings",
        lambda *_args, **_kwargs: [],
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    mock_olm = MagicMock()
    import importlib as _importlib

    real_import_module = _importlib.import_module

    def mock_import_side_effect(module_name, *args, **kwargs):
        if module_name == "olm":
            return mock_olm
        if module_name == "nio.crypto":
            mock_crypto = MagicMock()
            mock_crypto.OlmDevice = MagicMock()
            return mock_crypto
        if module_name == "nio.store":
            mock_store = MagicMock()
            mock_store.SqliteStore = MagicMock()
            return mock_store
        return real_import_module(module_name, *args, **kwargs)

    sleep_mock = AsyncMock()
    with (
        patch("mmrelay.config.is_e2ee_enabled", return_value=True),
        patch("importlib.import_module", side_effect=mock_import_side_effect),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.os.makedirs"),
        patch("mmrelay.matrix_utils.os.listdir", return_value=["test.db"]),
        patch("mmrelay.matrix_utils.asyncio.sleep", sleep_mock),
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {
                    "enabled": True,
                    "store_path": str(tmp_path / "mmrelay-store"),
                },
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    sleep_mock.assert_awaited_once()


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.async_load_credentials", new_callable=AsyncMock)
@patch("mmrelay.matrix_utils._create_ssl_context")
@patch("mmrelay.matrix_utils.AsyncClient")
async def test_connect_matrix_legacy_config(
    mock_async_client, mock_ssl_context, mock_load_credentials
):
    """Test Matrix connection with legacy config (no E2EE)."""
    # No credentials.json available
    mock_load_credentials.return_value = None

    # Mock SSL context
    mock_ssl_context.return_value = MagicMock()

    # Mock AsyncClient instance
    mock_client_instance = MagicMock()
    mock_client_instance.sync = AsyncMock()
    mock_client_instance.rooms = {}
    mock_client_instance.whoami = AsyncMock()
    mock_client_instance.whoami.return_value = MagicMock(device_id="LEGACY_DEVICE")
    mock_client_instance.get_displayname = AsyncMock()
    mock_client_instance.get_displayname.return_value = MagicMock(
        displayname="Test Bot"
    )
    mock_async_client.return_value = mock_client_instance

    # Legacy config without E2EE
    test_config = {
        "matrix": {
            "homeserver": "https://matrix.example.org",
            "access_token": "legacy_token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    # Mock the global matrix_client to None to ensure fresh creation
    with patch("mmrelay.matrix_utils.matrix_client", None):
        client = await connect_matrix(test_config)

        assert client is not None
        assert client == mock_client_instance

        # Verify AsyncClient was created without E2EE
        mock_async_client.assert_called_once()
        call_args = mock_async_client.call_args
        assert "device_id" not in call_args[1]
        assert call_args[1].get("store_path") is None

        # Verify sync was called
        mock_client_instance.sync.assert_called()


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
async def test_login_matrix_bot_e2ee_store_path_created(
    _mock_ssl_context, mock_async_client, _mock_save_credentials, tmp_path
):
    """E2EE-enabled logins should create a store path."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    from mmrelay.config import get_e2ee_store_dir as config_get_e2ee_store_dir

    store_path = str(config_get_e2ee_store_dir())

    with (
        patch("mmrelay.config.load_config", return_value={"matrix": {}}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=True),
        patch("mmrelay.matrix_utils.os.makedirs") as mock_makedirs,
        patch(
            "mmrelay.matrix_utils._normalize_bot_user_id",
            return_value="@user:matrix.org",
        ),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is True
    assert any(
        call.args == (store_path,) and call.kwargs == {"exist_ok": True}
        for call in mock_makedirs.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_api_login_debug_path(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """API login debug path should parse and log request payload safely."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    class DummyApi:
        @staticmethod
        def login(user, password, device_name, device_id=None):
            import json

            return (
                "POST",
                "/login",
                json.dumps(
                    {
                        "user": user,
                        "password": password,
                        "device_name": device_name,
                        "device_id": device_id,
                    }
                ),
            )

    with (
        patch.dict("sys.modules", {"nio.api": SimpleNamespace(Api=DummyApi)}),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is True
    assert any(
        "Matrix API call details" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


@patch("mmrelay.matrix_utils.getpass.getpass")
@patch("mmrelay.matrix_utils.input")
async def test_login_matrix_bot_type_error_specific_message(mock_input, mock_getpass):
    """Type errors from matrix-nio should return False."""
    mock_input.side_effect = ["https://matrix.org", "user", "y"]
    mock_getpass.return_value = "pass"

    with (
        patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
        patch("mmrelay.cli_utils._create_ssl_context", return_value=None),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        mock_client = AsyncMock()
        mock_client.login.side_effect = TypeError(
            "'>=' not supported between instances of 'str' and 'int'"
        )
        mock_client.close = AsyncMock()
        mock_async_client.return_value = mock_client

        result = await login_matrix_bot()

    assert result is False
    assert any(
        "Matrix-nio library error during login" in call.args[0]
        for call in mock_logger.error.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_login_response_unexpected(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Unexpected login responses should return False."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token=None, status_code=None, message=None
    )
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is False
    assert any(
        "Unexpected login response" in call.args[0]
        for call in mock_logger.error.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_whoami_fallback_when_missing_user_id(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    mock_save_credentials,
):
    """Missing user_id from whoami should fall back to response user_id."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@fallback:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id=None)
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is True
    assert mock_save_credentials.call_args.args[0]["user_id"] == "@fallback:matrix.org"
    assert any(
        "whoami response did not include user_id; using login response user_id"
        in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_logout_others_warns(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Logout_others should warn that the feature is unimplemented."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=True,
        )

    assert result is True
    mock_logger.warning.assert_any_call("Logout others not yet implemented")


@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_save_credentials_failure_triggers_cleanup(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
):
    """Failures during save_credentials should trigger outer exception handling."""
    from mmrelay.matrix_utils import NioLoginError

    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock(side_effect=OSError("close-fail"))

    with (
        patch(
            "mmrelay.matrix_utils.save_credentials", side_effect=NioLoginError("fail")
        ),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is False
    mock_logger.exception.assert_any_call("Error during login")
    assert any(
        "Ignoring error during client cleanup" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_login_timeout(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
):
    """Login timeouts should log and return False."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.side_effect = asyncio.TimeoutError
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is False
    assert any(
        "Login timed out after" in call.args[0]
        for call in mock_logger.exception.call_args_list
    )
    mock_main_client.close.assert_awaited_once()


@pytest.mark.parametrize(
    "exc, expected_log",
    [
        (ConnectionError("boom"), "Network connectivity issue detected."),
        (ssl.SSLError("bad cert"), "SSL/TLS certificate issue detected."),
        (type("DNSError", (Exception,), {})("dns"), "DNS resolution failed."),
        (
            ValueError("'user_id' is a required property"),
            "Matrix server response validation failed.",
        ),
    ],
)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_login_exception_guidance(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    exc,
    expected_log,
):
    """Login exceptions should emit targeted troubleshooting guidance."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.side_effect = exc
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is False
    assert any(
        expected_log in call.args[0] for call in mock_logger.error.call_args_list
    )
    mock_main_client.close.assert_awaited_once()


@pytest.mark.parametrize(
    "status_code, message, expected_log",
    [
        (401, "M_FORBIDDEN", "Authentication failed - invalid username or password."),
        (404, "M_NOT_FOUND", "User not found or homeserver not found."),
        (429, "M_LIMIT_EXCEEDED", "Rate limited - too many login attempts."),
        (
            500,
            "server error",
            "Matrix server error - the server is experiencing issues.",
        ),
        (
            "M_FORBIDDEN",
            "Invalid username or password",
            "Authentication failed - invalid username or password.",
        ),
        (418, "teapot", "Login failed for unknown reason."),
    ],
)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_login_response_status_codes(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    status_code,
    message,
    expected_log,
):
    """Status-coded login failures should log targeted guidance."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token=None, status_code=status_code, message=message
    )
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is False
    assert any(
        expected_log in call.args[0] for call in mock_logger.error.call_args_list
    )
    mock_main_client.close.assert_awaited_once()


@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
async def test_login_matrix_bot_forbidden_with_localpart_suggests_full_mxid(
    _mock_ssl_context,
    mock_async_client,
):
    """401/M_FORBIDDEN guidance should suggest full MXID when localpart was used."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token=None, status_code=401, message="M_FORBIDDEN"
    )
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is False
    assert any(
        "retry with a full Matrix ID" in call.args[0]
        for call in mock_logger.error.call_args_list
    )
    mock_main_client.close.assert_awaited_once()


@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
async def test_login_matrix_bot_forbidden_with_full_mxid_skips_full_mxid_hint(
    _mock_ssl_context,
    mock_async_client,
):
    """Full MXID input should not log redundant full-MXID retry guidance."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token=None, status_code=401, message="M_FORBIDDEN"
    )
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_FULL_MXID,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is False
    assert not any(
        "retry with a full Matrix ID" in call.args[0]
        for call in mock_logger.error.call_args_list
    )
    mock_main_client.close.assert_awaited_once()


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_whoami_exception_uses_fallback(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    mock_save_credentials,
):
    """whoami failures should warn and fall back to response user_id."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@fallback:matrix.org"
    )
    mock_main_client.whoami.side_effect = RuntimeError("whoami failed")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is True
    assert mock_save_credentials.call_args.args[0]["user_id"] == "@fallback:matrix.org"
    assert any(
        "whoami call failed" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_whoami_oserror_uses_fallback(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    mock_save_credentials,
):
    """OSError from whoami should warn and fall back to login response user_id."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@fallback:matrix.org"
    )
    mock_main_client.whoami.side_effect = OSError("network down")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is True
    assert mock_save_credentials.call_args.args[0]["user_id"] == "@fallback:matrix.org"
    assert any(
        "whoami call failed" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
async def test_login_matrix_bot_saves_credentials_without_user_id_when_unknown(
    _mock_ssl_context,
    mock_async_client,
    mock_save_credentials,
):
    """If whoami and login response omit user_id, credentials should still save."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id=None
    )
    mock_main_client.whoami.return_value = MagicMock(user_id=None)
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is True
    saved_credentials = mock_save_credentials.call_args.args[0]
    assert "user_id" not in saved_credentials
    assert any(
        "saving credentials without user_id" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_e2ee_config_load_exception_disables_e2ee(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Config load failures should disable E2EE and skip store setup."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", side_effect=RuntimeError("boom")),
        patch("mmrelay.config.is_e2ee_enabled") as mock_is_e2ee,
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.get_e2ee_store_dir") as mock_store_dir,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is True
    mock_is_e2ee.assert_not_called()
    mock_store_dir.assert_not_called()
    assert any(
        "Could not load config for E2EE check" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )
    assert any(
        "E2EE disabled in configuration" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_no_password_warns(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Empty passwords should log a warning."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.getpass.getpass", return_value=""),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=None,
            logout_others=False,
        )

    assert result is True
    mock_logger.warning.assert_any_call("No password provided")

    @patch("mmrelay.matrix_utils.async_load_credentials", new_callable=AsyncMock)
    @patch("mmrelay.matrix_utils.save_credentials")
    @patch("mmrelay.matrix_utils.AsyncClient")
    @patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
    @patch(
        "mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org"
    )
    async def test_login_matrix_bot_credentials_load_failure_logs_debug(
        _mock_normalize,
        _mock_ssl_context,
        mock_async_client,
        _mock_save_credentials,
        mock_load_credentials,
        tmp_path,
    ):
        """Credential load errors should be logged and ignored."""
        mock_discovery_client = AsyncMock()
        mock_main_client = AsyncMock()
        mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

        mock_discovery_client.discovery_info.return_value = SimpleNamespace(
            homeserver_url="https://matrix.org"
        )
        mock_discovery_client.close = AsyncMock()
        mock_main_client.login.return_value = MagicMock(
            access_token="token", device_id="DEV", user_id="@user:matrix.org"
        )
        mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
        mock_main_client.close = AsyncMock()

        credentials_path = str(tmp_path / CREDENTIALS_FILENAME)
        mock_load_credentials.side_effect = OSError("boom")

        with (
            patch(
                "mmrelay.paths.resolve_all_paths",
                return_value={
                    "credentials_path": credentials_path,
                    "legacy_sources": [],
                },
            ),
            patch("mmrelay.matrix_utils.os.path.exists", return_value=True),
            patch("mmrelay.config.load_config", return_value={}),
            patch("mmrelay.config.is_e2ee_enabled", return_value=False),
            patch("mmrelay.matrix_utils.logger") as mock_logger,
        ):
            result = await login_matrix_bot(
                homeserver=TEST_HOMESERVER,
                username=TEST_USERNAME,
                password=TEST_PASSWORD,
                logout_others=False,
            )

        assert result is True
        assert any(
            "Could not load existing credentials" in call.args[0]
            for call in mock_logger.debug.call_args_list
        )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_api_login_debug_failure_logs(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """API debug failures should log and continue."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    class DummyApi:
        @staticmethod
        def login(*_args, **_kwargs):
            raise RuntimeError("api boom")

    with (
        patch.dict("sys.modules", {"nio.api": SimpleNamespace(Api=DummyApi)}),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is True
    assert any(
        "Failed to test API call" in call.args[0]
        for call in mock_logger.error.call_args_list
    )


# Matrix logout tests


class TestMatrixUtilityFunctions:
    def test_truncate_message_respects_utf8_boundaries(self):
        text = "hello😊"
        truncated = truncate_message(text, max_bytes=6)
        assert truncated == "hello"

    def test_strip_quoted_lines_removes_quoted_content(self):
        text = "Line one\n> quoted line\n Line two"
        result = strip_quoted_lines(text)
        assert result == "Line one Line two"

    def test_validate_prefix_format_success(self):
        is_valid, error = validate_prefix_format("{display}", {"display": "Alice"})
        assert is_valid is True
        assert error is None

    def test_validate_prefix_format_missing_key(self):
        is_valid, error = validate_prefix_format("{missing}", {"display": "Alice"})
        assert is_valid is False
        assert error is not None
        assert "missing" in error


class TestMatrixE2EEHasAttrChecks:
    """Test class for E2EE hasattr checks in matrix_utils.py"""

    @pytest.fixture
    def e2ee_config(self):
        """
        Create a minimal Matrix configuration dictionary with end-to-end encryption enabled for tests.

        The configuration contains a `matrix` section with homeserver, access token, bot user id, and `e2ee: {"enabled": True}`, and a `matrix_rooms` mapping with a sample room configured for `meshtastic_channel: 0`.

        Returns:
            dict: Test-ready Matrix configuration with E2EE enabled.
        """
        return {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
                "e2ee": {"enabled": True},
            },
            "matrix_rooms": {"!room:matrix.org": {"meshtastic_channel": 0}},
        }

    async def test_connect_matrix_hasattr_checks_success(self, e2ee_config):
        """Test hasattr checks for nio.crypto.OlmDevice and nio.store.SqliteStore when available"""
        with (
            patch("mmrelay.matrix_utils.matrix_client", None),
            patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
            patch("mmrelay.matrix_utils.logger"),
            patch("mmrelay.matrix_utils.importlib.import_module") as mock_import,
        ):
            # Mock AsyncClient instance with proper async methods
            mock_client_instance = MagicMock()
            mock_client_instance.rooms = {}
            mock_client_instance.login = AsyncMock(return_value=MagicMock())
            mock_client_instance.sync = AsyncMock(return_value=MagicMock())
            mock_client_instance.join = AsyncMock(return_value=MagicMock())
            mock_client_instance.close = AsyncMock()
            mock_client_instance.get_displayname = AsyncMock(
                return_value=MagicMock(displayname="TestBot")
            )
            mock_client_instance.keys_upload = AsyncMock()
            mock_async_client.return_value = mock_client_instance

            # Create mock modules with required attributes
            mock_olm = SimpleNamespace()
            mock_nio_crypto = SimpleNamespace(OlmDevice=MagicMock())
            mock_nio_store = SimpleNamespace(SqliteStore=MagicMock())

            def import_side_effect(name):
                """
                Return a mock module object for the specified import name to simulate E2EE dependencies in tests.

                Parameters:
                    name (str): Fully qualified module name ('olm', 'nio.crypto', or 'nio.store').

                Returns:
                    object: The mock module corresponding to the requested name.

                Raises:
                    ImportError: If the requested name is not a supported mock module.
                """
                if name == "olm":
                    return mock_olm
                elif name == "nio.crypto":
                    return mock_nio_crypto
                elif name == "nio.store":
                    return mock_nio_store
                else:
                    # For any other import, raise ImportError to simulate missing dependency
                    raise ImportError(f"No module named '{name}'")

            mock_import.side_effect = import_side_effect

            # Run the async function
            await connect_matrix(e2ee_config)

            # Verify client was created and E2EE dependencies were checked
            mock_async_client.assert_called_once()
            expected_imports = {"olm", "nio.crypto", "nio.store"}
            actual_imports = {call.args[0] for call in mock_import.call_args_list}
            assert expected_imports.issubset(actual_imports)

    async def test_connect_matrix_hasattr_checks_missing_olmdevice(self, e2ee_config):
        """Test hasattr check failure when nio.crypto.OlmDevice is missing"""
        with (
            patch("mmrelay.matrix_utils.matrix_client", None),
            patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
            patch("mmrelay.matrix_utils.logger") as mock_logger,
            patch("mmrelay.matrix_utils.importlib.import_module") as mock_import,
        ):
            # Mock AsyncClient instance with proper async methods
            mock_client_instance = MagicMock()
            mock_client_instance.rooms = {}
            mock_client_instance.login = AsyncMock(return_value=MagicMock())
            mock_client_instance.sync = AsyncMock(return_value=MagicMock())
            mock_client_instance.join = AsyncMock(return_value=MagicMock())
            mock_client_instance.close = AsyncMock()
            mock_client_instance.get_displayname = AsyncMock(
                return_value=MagicMock(displayname="TestBot")
            )
            mock_async_client.return_value = mock_client_instance

            # Create mock modules where nio.crypto lacks OlmDevice
            mock_olm = SimpleNamespace()
            mock_nio_crypto = SimpleNamespace()
            # Simulate missing OlmDevice attribute to exercise hasattr failure
            mock_nio_store = SimpleNamespace(SqliteStore=MagicMock())

            def import_side_effect(name):
                """
                Return a mock module object for the specified import name to simulate E2EE dependencies in tests.

                Parameters:
                    name (str): Fully qualified module name ('olm', 'nio.crypto', or 'nio.store').

                Returns:
                    object: The mock module corresponding to the requested name.

                Raises:
                    ImportError: If the requested name is not a supported mock module.
                """
                if name == "olm":
                    return mock_olm
                elif name == "nio.crypto":
                    return mock_nio_crypto
                elif name == "nio.store":
                    return mock_nio_store
                else:
                    # For any other import, raise ImportError to simulate missing dependency
                    raise ImportError(f"No module named '{name}'")

            mock_import.side_effect = import_side_effect

            # Run the async function
            await connect_matrix(e2ee_config)

            # Verify ImportError was logged and E2EE was disabled
            mock_logger.exception.assert_called_with("Missing E2EE dependency")
            mock_logger.error.assert_called_with(
                "Please reinstall with: pipx install 'mmrelay[e2e]'"
            )
            mock_logger.warning.assert_called_with(
                "E2EE will be disabled for this session."
            )

    async def test_connect_matrix_hasattr_checks_missing_sqlitestore(self, e2ee_config):
        """Test hasattr check failure when nio.store.SqliteStore is missing"""
        with (
            patch("mmrelay.matrix_utils.matrix_client", None),
            patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
            patch("mmrelay.matrix_utils.logger") as mock_logger,
            patch("mmrelay.matrix_utils.importlib.import_module") as mock_import,
        ):
            # Mock AsyncClient instance with proper async methods
            mock_client_instance = MagicMock()
            mock_client_instance.rooms = {}
            mock_client_instance.login = AsyncMock(return_value=MagicMock())
            mock_client_instance.sync = AsyncMock(return_value=MagicMock())
            mock_client_instance.join = AsyncMock(return_value=MagicMock())
            mock_client_instance.close = AsyncMock()
            mock_client_instance.get_displayname = AsyncMock(
                return_value=MagicMock(displayname="TestBot")
            )
            mock_async_client.return_value = mock_client_instance

            # Create mock modules where nio.store lacks SqliteStore
            mock_olm = SimpleNamespace()
            mock_nio_crypto = SimpleNamespace(OlmDevice=MagicMock())
            # Simulate missing SqliteStore attribute to exercise hasattr failure
            mock_nio_store = SimpleNamespace()

            def import_side_effect(name):
                """
                Provide a mock module for simulating E2EE dependencies during tests.

                Parameters:
                    name (str): Fully qualified module name to mock (e.g., 'olm', 'nio.crypto', or 'nio.store').

                Returns:
                    object: The mock module corresponding to the requested name.

                Raises:
                    ImportError: If the requested name is not a supported mock module.
                """
                if name == "olm":
                    return mock_olm
                elif name == "nio.crypto":
                    return mock_nio_crypto
                elif name == "nio.store":
                    return mock_nio_store
                else:
                    # For any other import, raise ImportError to simulate missing dependency
                    raise ImportError(f"No module named '{name}'")

            mock_import.side_effect = import_side_effect

            # Run the async function
            await connect_matrix(e2ee_config)

            # Verify ImportError was logged and E2EE was disabled
            mock_logger.exception.assert_called_with("Missing E2EE dependency")
            mock_logger.error.assert_called_with(
                "Please reinstall with: pipx install 'mmrelay[e2e]'"
            )
            mock_logger.warning.assert_called_with(
                "E2EE will be disabled for this session."
            )


class TestGetDetailedSyncErrorMessage:
    """Test cases for _get_detailed_matrix_error_message function."""


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


async def test_connect_matrix_e2ee_missing_nio_crypto():
    """
    Test connect_matrix handles missing nio.crypto.OlmDevice gracefully.
    """
    config = {
        "matrix": {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:matrix.org",
            "encryption": {"enabled": True},
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch("mmrelay.matrix_utils._create_ssl_context"),
        patch("mmrelay.matrix_utils.importlib.import_module") as mock_import,
    ):
        # Mock importlib to simulate missing nio.crypto
        def mock_import_side_effect(module_name):
            if module_name == "olm":
                return MagicMock()  # olm is available
            elif module_name == "nio.crypto":
                mock_crypto = MagicMock()
                mock_crypto.OlmDevice = MagicMock()
                # Remove OlmDevice attribute
                del mock_crypto.OlmDevice
                return mock_crypto
            return MagicMock()

        mock_import.side_effect = mock_import_side_effect

        # Mock AsyncClient instance
        mock_client_instance = MagicMock()
        mock_client_instance.rooms = {}

        async def mock_sync(*args, **kwargs):
            return MagicMock()

        async def mock_get_displayname(*args, **kwargs):
            return MagicMock(displayname="Test Bot")

        mock_client_instance.sync = mock_sync
        mock_client_instance.get_displayname = mock_get_displayname
        mock_async_client.return_value = mock_client_instance

        result = await connect_matrix(config)

        # Should still create client but with E2EE disabled
        assert result == mock_client_instance
        # Should log exception about missing nio.crypto.OlmDevice
        mock_logger.exception.assert_called_with("Missing E2EE dependency")


async def test_connect_matrix_e2ee_missing_sqlite_store():
    """
    Test connect_matrix handles missing nio.store.SqliteStore gracefully.
    """
    config = {
        "matrix": {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:matrix.org",
            "encryption": {"enabled": True},
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch("mmrelay.matrix_utils._create_ssl_context"),
        patch("mmrelay.matrix_utils.importlib.import_module") as mock_import,
    ):
        # Mock importlib to simulate missing nio.store.SqliteStore
        def mock_import_side_effect(module_name):
            if module_name == "olm":
                return MagicMock()  # olm is available
            elif module_name == "nio.crypto":
                mock_crypto = MagicMock()
                mock_crypto.OlmDevice = MagicMock()
                return mock_crypto
            elif module_name == "nio.store":
                mock_store = MagicMock()
                mock_store.SqliteStore = MagicMock()
                # Remove SqliteStore attribute
                del mock_store.SqliteStore
                return mock_store
            return MagicMock()

        mock_import.side_effect = mock_import_side_effect

        # Mock AsyncClient instance
        mock_client_instance = MagicMock()
        mock_client_instance.rooms = {}

        async def mock_sync(*args, **kwargs):
            return MagicMock()

        async def mock_get_displayname(*args, **kwargs):
            return MagicMock(displayname="Test Bot")

        mock_client_instance.sync = mock_sync
        mock_client_instance.get_displayname = mock_get_displayname
        mock_async_client.return_value = mock_client_instance

        result = await connect_matrix(config)

        # Should still create client but with E2EE disabled
        assert result == mock_client_instance
        # Should log exception about missing nio.store.SqliteStore
        mock_logger.exception.assert_called_with("Missing E2EE dependency")


@pytest.mark.asyncio
async def test_on_decryption_failure():
    """Test on_decryption_failure handles decryption failures with retry logic."""

    # Create mock room and event
    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # Create a response mock that satisfies isinstance(response, ToDeviceResponse)
        mock_response = MagicMock(spec=ToDeviceResponse)
        mock_client.to_device = AsyncMock(return_value=mock_response)

        # Test successful key request - should exit after first success
        await on_decryption_failure(mock_room, mock_event)

        # Verify the event was patched with room_id
        assert mock_event.room_id == "!room123:matrix.org"
        # Verify key request was created and sent (only 1 call on success)
        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        assert mock_client.to_device.await_count == 1
        mock_client.to_device.assert_awaited_once_with({"type": "m.room_key_request"})
        # Verify logging - error about decryption failure, 1 info message on success
        assert mock_logger.error.call_count == 1  # Initial decryption failure
        assert mock_logger.info.call_count == 1  # Success message
        # Verify single sleep after success with key-sharing delay.
        assert [call.args[0] for call in mock_sleep.await_args_list] == [
            matrix_utils_module.E2EE_KEY_SHARING_DELAY_SECONDS
        ]


@pytest.mark.asyncio
async def test_on_decryption_failure_without_matrix_client_logs_and_returns():
    """Test on_decryption_failure exits early when matrix_client is unavailable."""
    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"

    with (
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        await on_decryption_failure(mock_room, mock_event)

        # Initial decrypt error + unavailable matrix client error.
        assert mock_logger.error.call_count == 2
        mock_event.as_key_request.assert_not_called()
        mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_decryption_failure_missing_device_id():
    """Missing device_id should prevent key requests and log an error."""
    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = None
        mock_client.to_device = AsyncMock()

        await on_decryption_failure(mock_room, mock_event)

        mock_logger.error.assert_any_call(
            "Cannot request keys for event %s: client has no device_id",
            "$event123",
        )
        mock_client.to_device.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_decryption_failure_retry_on_exception():
    """Test on_decryption_failure retries with exponential backoff on communication exceptions."""

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        max_attempts = matrix_utils_module.E2EE_KEY_REQUEST_MAX_ATTEMPTS
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # Create a response mock that satisfies isinstance(response, ToDeviceResponse)
        mock_response = MagicMock(spec=ToDeviceResponse)
        # Fail until the final allowed attempt, then succeed.
        mock_client.to_device = AsyncMock(
            side_effect=[
                *[
                    NioRemoteTransportError("Network error")
                    for _ in range(max_attempts - 1)
                ],
                mock_response,
            ]
        )

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        # Verify all attempts were made.
        assert mock_client.to_device.await_count == max_attempts
        # Verify warnings were logged for failures before success
        assert mock_logger.warning.call_count == max_attempts - 1
        # Verify success info message (only on final successful attempt)
        assert mock_logger.info.call_count == 1
        # Compute expected sleep list: exponential backoff for first (max_attempts-1) retries, then key-sharing delay
        expected_sleep_list = []
        for attempt in range(1, max_attempts):  # attempts 1 to max_attempts-1
            delay = min(
                matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY * (2 ** (attempt - 1)),
                matrix_utils_module.E2EE_KEY_REQUEST_MAX_DELAY,
            )
            expected_sleep_list.append(delay)
        expected_sleep_list.append(matrix_utils_module.E2EE_KEY_SHARING_DELAY_SECONDS)
        assert [
            call.args[0] for call in mock_sleep.await_args_list
        ] == expected_sleep_list


@pytest.mark.asyncio
async def test_on_decryption_failure_all_retries_fail():
    """Test on_decryption_failure logs error after all retries are exhausted."""

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # All attempts fail
        mock_client.to_device = AsyncMock(
            side_effect=NioRemoteTransportError("Network error")
        )

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        # Compute expected values from constants
        max_attempts = matrix_utils_module.E2EE_KEY_REQUEST_MAX_ATTEMPTS
        # Verify all attempts were made
        assert mock_client.to_device.await_count == max_attempts
        # Verify warnings for failures before final attempt
        assert mock_logger.warning.call_count == max_attempts - 1
        # Check that the final error message was logged (f-string format)
        mock_logger.exception.assert_called_once()
        # No info messages since all requests failed
        assert mock_logger.info.call_count == 0
        # Compute expected sleep list: exponential backoff for first (max_attempts-1) retries
        # No backoff after final failure
        expected_sleep_list = []
        for attempt in range(1, max_attempts):  # attempts 1 to max_attempts-1
            delay = min(
                matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY * (2 ** (attempt - 1)),
                matrix_utils_module.E2EE_KEY_REQUEST_MAX_DELAY,
            )
            expected_sleep_list.append(delay)
        assert [
            call.args[0] for call in mock_sleep.await_args_list
        ] == expected_sleep_list


@pytest.mark.asyncio
async def test_on_decryption_failure_to_device_error():
    """Test on_decryption_failure handles ToDeviceError (server-side error) with retry."""

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # First returns ToDeviceError (server error), second returns ToDeviceResponse (success)
        mock_error = MagicMock(spec=ToDeviceError)
        mock_response = MagicMock(spec=ToDeviceResponse)
        mock_client.to_device = AsyncMock(
            side_effect=[
                mock_error,  # Server error on first attempt
                mock_response,  # Success on second attempt
            ]
        )

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        # Verify 2 attempts were made (error on first, success on second)
        assert mock_client.to_device.await_count == 2
        # Verify warning for the server error
        assert mock_logger.warning.call_count == 1
        # Verify success info message
        assert mock_logger.info.call_count == 1
        # Verify backoff after error and success key-sharing delay.
        expected_sleep_list = [
            matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY,
            matrix_utils_module.E2EE_KEY_SHARING_DELAY_SECONDS,
        ]
        assert [
            call.args[0] for call in mock_sleep.await_args_list
        ] == expected_sleep_list


@pytest.mark.asyncio
async def test_on_decryption_failure_backoff_caps_at_max_delay():
    """
    Verify exponential backoff caps at the configured maximum delay when repeated to-device failures occur.

    This test simulates repeated ToDeviceError responses from the Matrix client's to_device call and asserts that on_decryption_failure schedules retries with asyncio.sleep delays that respect E2EE_KEY_REQUEST_BASE_DELAY and do not exceed E2EE_KEY_REQUEST_MAX_DELAY (expected sleep calls: 20, then capped 30.0). It also verifies the key request is created for the configured bot user and device.
    """

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch("mmrelay.matrix_utils.E2EE_KEY_REQUEST_MAX_ATTEMPTS", 3),
        patch("mmrelay.matrix_utils.E2EE_KEY_REQUEST_BASE_DELAY", 20),
        patch("mmrelay.matrix_utils.E2EE_KEY_REQUEST_MAX_DELAY", 30.0),
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # Keep returning ToDeviceError so retries continue until max attempts.
        mock_error = MagicMock(spec=ToDeviceError)
        mock_client.to_device = AsyncMock(
            side_effect=[mock_error, mock_error, mock_error]
        )

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        assert (
            mock_client.to_device.await_count
            == matrix_utils_module.E2EE_KEY_REQUEST_MAX_ATTEMPTS
        )
        assert [call.args[0] for call in mock_sleep.await_args_list] == [
            matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY,
            min(
                matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY * 2,
                matrix_utils_module.E2EE_KEY_REQUEST_MAX_DELAY,
            ),
        ]
        # Initial decryption error + terminal retry exhaustion error.
        assert mock_logger.error.call_count == 2
        assert mock_logger.info.call_count == 0


@pytest.mark.asyncio
async def test_on_decryption_failure_unexpected_response_type():
    """Unexpected to_device response types should be logged and retried with backoff."""

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # Return an unexpected type for all attempts to exercise fallback handling.
        max_attempts = matrix_utils_module.E2EE_KEY_REQUEST_MAX_ATTEMPTS
        mock_client.to_device = AsyncMock(side_effect=[object()] * max_attempts)

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        assert mock_client.to_device.await_count == max_attempts
        # Compute expected sleep list: exponential backoff for first (max_attempts-1) retries
        # No backoff after final failure
        expected_sleep_list = []
        for attempt in range(1, max_attempts):  # attempts 1 to max_attempts-1
            delay = min(
                matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY * (2 ** (attempt - 1)),
                matrix_utils_module.E2EE_KEY_REQUEST_MAX_DELAY,
            )
            expected_sleep_list.append(delay)
        assert [
            call.args[0] for call in mock_sleep.await_args_list
        ] == expected_sleep_list
        # Warning for each unexpected response
        assert mock_logger.warning.call_count == max_attempts
        assert (
            mock_logger.error.call_count == 2
        )  # initial decryption + final retry failure


@pytest.mark.asyncio
async def test_on_decryption_failure_timeout_on_to_device():
    """Test on_decryption_failure handles asyncio.TimeoutError from the to-device wrapper."""

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
        patch("mmrelay.matrix_utils.asyncio.wait_for") as mock_wait_for,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        mock_client.to_device = AsyncMock(return_value=MagicMock(spec=ToDeviceResponse))

        async def mock_wait_for_impl(coro, timeout):
            assert timeout == matrix_utils_module.MATRIX_TO_DEVICE_TIMEOUT
            await coro
            raise asyncio.TimeoutError()

        mock_wait_for.side_effect = mock_wait_for_impl

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )

        # Compute expected values from constants
        max_attempts = matrix_utils_module.E2EE_KEY_REQUEST_MAX_ATTEMPTS
        assert mock_wait_for.await_count == max_attempts
        # Verify all attempts were made via to_device calls
        assert mock_client.to_device.await_count == max_attempts
        # Verify warnings for failures before final attempt
        assert mock_logger.warning.call_count == max_attempts - 1
        # Verify final exception was logged
        mock_logger.exception.assert_called_once()
        # No info messages since all requests failed
        assert mock_logger.info.call_count == 0
        # Compute expected sleep list: exponential backoff for first (max_attempts-1) retries
        # No backoff after final failure
        expected_sleep_list = []
        for attempt in range(1, max_attempts):  # attempts 1 to max_attempts-1
            delay = min(
                matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY * (2 ** (attempt - 1)),
                matrix_utils_module.E2EE_KEY_REQUEST_MAX_DELAY,
            )
            expected_sleep_list.append(delay)
        assert [
            call.args[0] for call in mock_sleep.await_args_list
        ] == expected_sleep_list


@pytest.mark.asyncio
async def test_on_room_member():
    """Test on_room_member handles room member events."""

    # Create mock room and event
    mock_room = MagicMock()
    mock_event = MagicMock()

    # The function just passes, so we just test it can be called
    await on_room_member(mock_room, mock_event)


class TestUncoveredMatrixUtils(unittest.TestCase):
    """Test cases for uncovered functions and edge cases in matrix_utils.py."""

    @patch("mmrelay.matrix_utils.logger")
    def test_is_room_alias_with_various_inputs(self, mock_logger):
        """Test _is_room_alias function with different input types."""

        # Test with valid alias
        self.assertTrue(_is_room_alias("#room:example.com"))

        # Test with room ID
        self.assertFalse(_is_room_alias("!room:example.com"))

        # Test with non-string types
        self.assertFalse(_is_room_alias(None))
        self.assertFalse(_is_room_alias(123))
        self.assertFalse(_is_room_alias([]))

    @patch("mmrelay.matrix_utils.logger")
    def test_iter_room_alias_entries_list_format(self, _mock_logger):
        """Test _iter_room_alias_entries with list format."""

        # Test with list of strings
        mapping = ["#room1:example.com", "#room2:example.com"]
        entries = list(_iter_room_alias_entries(mapping))

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0][0], "#room1:example.com")
        self.assertEqual(entries[1][0], "#room2:example.com")

        # Test that setters work
        entries[0][1]("!newroom:example.com")
        self.assertEqual(mapping[0], "!newroom:example.com")

    @patch("mmrelay.matrix_utils.logger")
    def test_iter_room_alias_entries_dict_format(self, _mock_logger):
        """Test _iter_room_alias_entries with dict format."""
        mapping = {
            "one": "#room1:example.com",
            "two": {"id": "#room2:example.com"},
        }
        entries = list(_iter_room_alias_entries(mapping))

        self.assertEqual(len(entries), 2)
        entries[0][1]("!new1:example.com")
        entries[1][1]("!new2:example.com")

        self.assertEqual(mapping["one"], "!new1:example.com")
        self.assertEqual(mapping["two"]["id"], "!new2:example.com")

    @patch("mmrelay.matrix_utils.logger")
    def test_can_auto_create_credentials_missing_fields(self, mock_logger):
        """Test _can_auto_create_credentials with missing fields."""
        from mmrelay.matrix_utils import _can_auto_create_credentials

        # Test missing homeserver
        config1 = {"bot_user_id": "@bot:example.com", "password": "secret123"}
        self.assertFalse(_can_auto_create_credentials(config1))

        # Test missing user_id
        config2 = {"homeserver": "https://example.com", "password": "secret123"}
        self.assertFalse(_can_auto_create_credentials(config2))

        # Test empty strings
        config3 = {
            "homeserver": "",
            "bot_user_id": "@bot:example.com",
            "password": "secret123",
        }
        self.assertFalse(_can_auto_create_credentials(config3))

    @patch("mmrelay.matrix_utils.logger")
    def test_normalize_bot_user_id_various_formats(self, mock_logger):
        """Test _normalize_bot_user_id with different input formats."""

        # Test with full MXID
        result1 = _normalize_bot_user_id("example.com", "@user:example.com")
        self.assertEqual(result1, "@user:example.com")

        # Test with localpart only
        result2 = _normalize_bot_user_id("example.com", "user")
        self.assertEqual(result2, "@user:example.com")

        # Test with already formatted ID
        result3 = _normalize_bot_user_id("example.com", "user:example.com")
        self.assertEqual(result3, "@user:example.com")

        # Test with falsy input
        result4 = _normalize_bot_user_id("example.com", "")
        self.assertEqual(result4, "")

    @patch("mmrelay.matrix_utils.logger")
    def test_normalize_bot_user_id_ipv6_and_ports(self, mock_logger):
        """Test _normalize_bot_user_id with IPv6 hosts and ports."""

        result1 = _normalize_bot_user_id("https://[2001:db8::1]:8448/path", "alice")
        self.assertEqual(result1, "@alice:[2001:db8::1]")

        result2 = _normalize_bot_user_id("example.com", "@bob:[2001:db8::1]:8448")
        self.assertEqual(result2, "@bob:[2001:db8::1]")

        result3 = _normalize_bot_user_id("[::1]:8448", "carol")
        self.assertEqual(result3, "@carol:[::1]")

    def test_extract_localpart_from_mxid(self):
        """Test _extract_localpart_from_mxid with different input formats."""

        # Test with full MXID
        result1 = _extract_localpart_from_mxid("@user:example.com")
        self.assertEqual(result1, "user")

        # Test with MXID using different server
        result2 = _extract_localpart_from_mxid("@bot:tchncs.de")
        self.assertEqual(result2, "bot")

        # Test with localpart only
        result3 = _extract_localpart_from_mxid("alice")
        self.assertEqual(result3, "alice")

        # Test with empty string
        result4 = _extract_localpart_from_mxid("")
        self.assertEqual(result4, "")

        # Test with None
        result5 = _extract_localpart_from_mxid(None)
        self.assertIsNone(result5)

        # Test with MXID containing special characters
        result6 = _extract_localpart_from_mxid("@user_123:example.com")
        self.assertEqual(result6, "user_123")

    def test_normalize_bot_user_id_preserves_existing_server_part(self):
        """Test that _normalize_bot_user_id preserves existing server part in MXID."""

        # Test with full MXID - should preserve server part
        result1 = _normalize_bot_user_id("https://matrix.tchncs.de", "@bot:tchncs.de")
        self.assertEqual(result1, "@bot:tchncs.de")

        # Test with localpart only - should use provided homeserver
        result2 = _normalize_bot_user_id("https://tchncs.de", "bot")
        self.assertEqual(result2, "@bot:tchncs.de")

        # Test with already formatted ID without @
        result3 = _normalize_bot_user_id("https://example.com", "bot:example.com")
        self.assertEqual(result3, "@bot:example.com")

    @patch("mmrelay.matrix_utils.logger")
    def test_get_detailed_matrix_error_message_bytes(self, mock_logger):
        """Test _get_detailed_matrix_error_message with bytes input."""

        # Test with valid UTF-8 bytes
        result = _get_detailed_matrix_error_message(b"Error message")
        self.assertEqual(result, "Error message")

        # Test with invalid UTF-8 bytes
        result = _get_detailed_matrix_error_message(b"\xff\xfe\xfd")
        self.assertEqual(
            result, "Network connectivity issue or server unreachable (binary data)"
        )

    @patch("mmrelay.matrix_utils.logger")
    def test_get_detailed_matrix_error_message_object_attributes(self, mock_logger):
        """Test _get_detailed_matrix_error_message with object having attributes."""

        # Test with message attribute
        mock_response = MagicMock()
        mock_response.message = "Custom error message"
        result = _get_detailed_matrix_error_message(mock_response)
        self.assertEqual(result, "Custom error message")

        # Test with status_code attribute only (no message)
        mock_response2 = MagicMock()
        mock_response2.message = None  # No message
        mock_response2.status_code = 404
        result = _get_detailed_matrix_error_message(mock_response2)
        self.assertEqual(result, "Server not found - check homeserver URL")

        # Test with status_code 429 only
        mock_response3 = MagicMock()
        mock_response3.message = None  # No message
        mock_response3.status_code = 429
        result = _get_detailed_matrix_error_message(mock_response3)
        self.assertEqual(result, "Rate limited - too many requests")

    def test_get_detailed_matrix_error_message_transport_status_non_int(self):
        """Test transport_response with non-int status_code."""

        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = None
        mock_response.transport_response = SimpleNamespace(status_code="bad")

        result = _get_detailed_matrix_error_message(mock_response)

        self.assertEqual(result, "Network connectivity issue or server unreachable")

    def test_get_detailed_matrix_error_message_attribute_error(self):
        """Test fallback for unexpected attribute errors."""

        class ExplodingResponse:
            def __getattr__(self, _name):
                raise ValueError("boom")

        result = _get_detailed_matrix_error_message(ExplodingResponse())
        self.assertEqual(
            result,
            "Unable to determine specific error - likely a network connectivity issue",
        )

    @patch("mmrelay.matrix_utils.logger")
    def test_update_room_id_in_mapping_unsupported_type(self, mock_logger):
        """Test _update_room_id_in_mapping with unsupported mapping type."""

        mapping = "not a list or dict"
        result = _update_room_id_in_mapping(
            mapping, "#old:example.com", "!new:example.com"
        )

        self.assertFalse(result)


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_broadcast_disabled():
    """Test _handle_detection_sensor_packet when broadcast is disabled."""
    config = {"meshtastic": {"broadcast_enabled": False}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    with patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config:
        mock_get_config.return_value = False  # broadcast_enabled

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        # Should not attempt to connect or send
        mock_get_config.assert_called()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_detection_disabled():
    """Test _handle_detection_sensor_packet when detection is disabled."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": False}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    with patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config:
        mock_get_config.side_effect = [
            True,
            False,
        ]  # broadcast_enabled, detection_sensor

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        # Should not attempt to connect or send
        assert mock_get_config.call_count == 2


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_connect_fail():
    """Test _handle_detection_sensor_packet when Meshtastic connection fails."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch("mmrelay.matrix_utils._connect_meshtastic") as mock_connect,
    ):
        mock_get_config.side_effect = [
            True,
            True,
        ]  # broadcast_enabled, detection_sensor
        mock_connect.return_value = None  # Connection fails

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_connect.assert_called_once()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_missing_channel():
    """Test _handle_detection_sensor_packet when meshtastic_channel is missing."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {}  # No meshtastic_channel
    full_display_name = "Test User"
    text = "Test message"

    mock_interface = MagicMock()

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch("mmrelay.matrix_utils._connect_meshtastic") as mock_connect,
    ):
        mock_get_config.side_effect = [True, True]
        mock_connect.return_value = mock_interface

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_connect.assert_called_once()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_invalid_channel():
    """Test _handle_detection_sensor_packet when meshtastic_channel is invalid."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {"meshtastic_channel": -1}  # Invalid channel
    full_display_name = "Test User"
    text = "Test message"

    mock_interface = MagicMock()

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch("mmrelay.matrix_utils._connect_meshtastic") as mock_connect,
    ):
        mock_get_config.side_effect = [True, True]
        mock_connect.return_value = mock_interface

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_connect.assert_called_once()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_success():
    """Test _handle_detection_sensor_packet successful relay."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    mock_interface = MagicMock()
    mock_queue = MagicMock()
    mock_queue.get_queue_size.return_value = 1

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch(
            "mmrelay.matrix_utils._connect_meshtastic", new_callable=AsyncMock
        ) as mock_connect,
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
        patch("mmrelay.matrix_utils.get_message_queue") as mock_get_queue,
    ):
        mock_get_config.side_effect = [True, True]
        mock_connect.return_value = mock_interface
        mock_queue_message.return_value = True
        mock_get_queue.return_value = mock_queue

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_queue_message.assert_called_once()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_queue_size_gt_one():
    """Test _handle_detection_sensor_packet logs when queue has multiple entries."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    mock_interface = MagicMock()
    mock_queue = MagicMock()
    mock_queue.get_queue_size.return_value = 3

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch(
            "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
            new_callable=AsyncMock,
        ) as mock_get_iface,
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
        patch("mmrelay.matrix_utils.get_message_queue") as mock_get_queue,
        patch("mmrelay.meshtastic_utils.logger") as mock_mesh_logger,
    ):
        mock_get_config.side_effect = [True, True]
        mock_get_iface.return_value = (mock_interface, 0)
        mock_queue_message.return_value = True
        mock_get_queue.return_value = mock_queue

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_mesh_logger.info.assert_called()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_queue_fail():
    """Test _handle_detection_sensor_packet when queue_message fails."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    mock_interface = MagicMock()

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch("mmrelay.matrix_utils._connect_meshtastic") as mock_connect,
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
    ):
        mock_get_config.side_effect = [True, True]
        mock_connect.return_value = mock_interface
        mock_queue_message.return_value = False  # Queue fails

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_queue_message.assert_called_once()


def test_matrix_utils_imports_nio_exceptions_when_available(monkeypatch):
    """Exercise the nio exception import branch for coverage."""
    import mmrelay.matrix_utils as mu

    # reload is intentional to exercise the import-time wiring of nio exceptions.

    original_values = {
        "NioLocalProtocolError": mu.NioLocalProtocolError,
        "NioLocalTransportError": mu.NioLocalTransportError,
        "NioRemoteProtocolError": mu.NioRemoteProtocolError,
        "NioRemoteTransportError": mu.NioRemoteTransportError,
        "NioLoginError": mu.NioLoginError,
        "NioLogoutError": mu.NioLogoutError,
        "NIO_COMM_EXCEPTIONS": mu.NIO_COMM_EXCEPTIONS,
        "config": mu.config,
        "matrix_client": mu.matrix_client,
        "matrix_rooms": mu.matrix_rooms,
        "bot_user_id": mu.bot_user_id,
        "matrix_access_token": mu.matrix_access_token,
        "matrix_homeserver": mu.matrix_homeserver,
        "bot_user_name": mu.bot_user_name,
        "bot_start_time": mu.bot_start_time,
    }

    exc_mod = types.ModuleType("nio.exceptions")
    resp_mod = types.ModuleType("nio.responses")

    class LocalProtocolError(Exception):
        pass

    class LocalTransportError(Exception):
        pass

    class RemoteProtocolError(Exception):
        pass

    class RemoteTransportError(Exception):
        pass

    class LoginError(Exception):
        pass

    class LogoutError(Exception):
        pass

    exc_mod.LocalProtocolError = LocalProtocolError  # type: ignore[attr-defined]
    exc_mod.LocalTransportError = LocalTransportError  # type: ignore[attr-defined]
    exc_mod.RemoteProtocolError = RemoteProtocolError  # type: ignore[attr-defined]
    exc_mod.RemoteTransportError = RemoteTransportError  # type: ignore[attr-defined]
    resp_mod.LoginError = LoginError  # type: ignore[attr-defined]
    resp_mod.LogoutError = LogoutError  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "nio.exceptions", exc_mod)
    monkeypatch.setitem(sys.modules, "nio.responses", resp_mod)
    # monkeypatch restores sys.modules entries after the test to avoid side effects.

    importlib.reload(mu)

    assert mu.NioLocalProtocolError is LocalProtocolError
    assert mu.NioLoginError is LoginError

    # Restore original exception classes so other tests using imports remain consistent.
    mu.NioLocalProtocolError = original_values["NioLocalProtocolError"]
    mu.NioLocalTransportError = original_values["NioLocalTransportError"]
    mu.NioRemoteProtocolError = original_values["NioRemoteProtocolError"]
    mu.NioRemoteTransportError = original_values["NioRemoteTransportError"]
    mu.NioLoginError = original_values["NioLoginError"]
    mu.NioLogoutError = original_values["NioLogoutError"]
    mu.NIO_COMM_EXCEPTIONS = original_values["NIO_COMM_EXCEPTIONS"]
    mu.config = original_values["config"]
    mu.matrix_client = original_values["matrix_client"]
    mu.matrix_rooms = original_values["matrix_rooms"]
    mu.bot_user_id = original_values["bot_user_id"]
    mu.matrix_access_token = original_values["matrix_access_token"]
    mu.matrix_homeserver = original_values["matrix_homeserver"]
    mu.bot_user_name = original_values["bot_user_name"]
    mu.bot_start_time = original_values["bot_start_time"]


@pytest.mark.asyncio
async def test_connect_matrix_alias_resolution_warns_when_client_falsey(monkeypatch):
    """Alias resolution should warn when the client is unavailable/truthy check fails."""
    mock_client = MagicMock()
    mock_client.__bool__.return_value = False
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.should_upload_keys = False

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context",
        lambda: MagicMock(),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
            },
            "matrix_rooms": [{"id": "#alias:example.org", "meshtastic_channel": 0}],
        }
        await connect_matrix(config)

    mock_logger.warning.assert_any_call(
        "Cannot resolve alias #alias:example.org: Matrix client is not available"
    )


@pytest.mark.asyncio
async def test_matrix_relay_logs_unexpected_exception():
    """Unexpected errors in matrix_relay should be logged and not raised."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.room_send = AsyncMock()

    config = {
        "meshtastic": {"meshnet_name": "TestMesh"},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.config", config),
        patch("mmrelay.matrix_utils.connect_matrix", return_value=mock_client),
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": False},
        ),
        patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False),
        patch(
            "mmrelay.matrix_utils.join_matrix_room",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        await matrix_relay(
            room_id="!room:matrix.org",
            message="Hello",
            longname="Alice",
            shortname="A",
            meshnet_name="TestMesh",
            portnum=1,
        )

    mock_logger.exception.assert_called_once_with(
        "Error sending radio message to matrix room !room:matrix.org"
    )
