import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from nio import SyncError  # noqa: E402

import mmrelay.matrix_utils as matrix_utils_module
from mmrelay.constants.app import CREDENTIALS_FILENAME
from mmrelay.matrix_utils import (
    NioLocalTransportError,
    connect_matrix,
)


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.matrix_client", None)
@patch("mmrelay.matrix_utils.logger")
@patch(
    "mmrelay.matrix_utils.login_matrix_bot", new_callable=AsyncMock, return_value=True
)
@patch("mmrelay.matrix_utils.async_load_credentials", new_callable=AsyncMock)
async def test_connect_matrix_auto_login_reload_json_decode_error(
    mock_load_credentials, mock_login_bot, mock_logger
):
    mock_load_credentials.side_effect = json.JSONDecodeError("test", "", 0)

    config = {
        "matrix": {
            "homeserver": "https://matrix.org",
            "bot_user_id": "@test:matrix.org",
            "password": "test_password",
        },
        "matrix_rooms": [],
    }

    with (
        patch("mmrelay.matrix_utils.get_credentials_path", side_effect=OSError("test")),
        patch("mmrelay.matrix_utils.get_explicit_credentials_path", return_value=None),
    ):
        result = await connect_matrix(config)

    assert result is None
    assert any(
        "Failed to reload newly created credentials" in call.args[0]
        for call in mock_logger.error.call_args_list
    )


# ===================================================================
# Migrated tests from test_matrix_utils.py monolith
# ===================================================================


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
async def test_connect_matrix_sync_error_closes_client(monkeypatch):
    """If initial sync returns an error response, the client should close and raise."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    error_response = SyncError("sync failed")
    mock_client.sync = AsyncMock(return_value=error_response)
    mock_client.close = AsyncMock()
    mock_client.should_upload_keys = False
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

    with pytest.raises(ConnectionError):
        await connect_matrix()

    mock_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_matrix_sync_error_close_failure_logs():
    """Sync error handling should ignore close failures and still raise."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    error_response = SyncError("sync failed")
    mock_client.sync = AsyncMock(return_value=error_response)
    mock_client.close = AsyncMock(side_effect=NioLocalTransportError("close failed"))
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

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
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock()),
        patch("mmrelay.matrix_utils.os.path.isfile", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        with pytest.raises(ConnectionError):
            await connect_matrix(config)

    assert mock_client.close.await_count == 1
    assert any(
        call.args[:2]
        == ("Ignoring error while closing client after %s", "connect_matrix setup")
        for call in mock_logger.debug.call_args_list
    )


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


async def test_connect_matrix_whoami_failure_warns(monkeypatch):
    """whoami failures should warn and continue without a device_id."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.whoami = AsyncMock(side_effect=OSError("fail"))
    mock_client.close = AsyncMock()

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
