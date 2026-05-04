# isort: skip_file
# ruff: noqa: E402, F401, I001
# fmt: off
# Facade module with load-bearing import ordering:
# globals and constants must be defined before submodule imports.
# Globals used by submodules via facade.NAME

import asyncio
import io
import logging
import os
import ssl
import time

# Globals imported but only for facade's own use (not accessed by submodules):
import getpass
import importlib
import sys
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Optional,
    cast,
)

if TYPE_CHECKING:
    pass

from nio import (
    AsyncClient,
    AsyncClientConfig,
    DiscoveryInfoError,
    DiscoveryInfoResponse,
    MatrixRoom,
    ProfileGetDisplayNameError,
    ProfileGetDisplayNameResponse,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)

# Import InviteMemberEvent separately to avoid submodule import issues
try:
    from nio import InviteMemberEvent  # pyright: ignore[reportMissingImports]
except ImportError:
    from nio.events.invite_events import (
        InviteMemberEvent,
    )

import mmrelay.config as config_module

# Local imports
from mmrelay.cli_utils import (
    _create_ssl_context,
    msg_retry_auth_login,
)
from mmrelay.config import (
    async_load_credentials,
    get_explicit_credentials_path,
    get_meshtastic_config_value,
    save_credentials,
)
from mmrelay.constants.config import (
    DEFAULT_BROADCAST_ENABLED,
    DEFAULT_DETECTION_SENSOR,
    E2EE_KEY_REQUEST_BASE_DELAY,
    E2EE_KEY_REQUEST_MAX_ATTEMPTS,
    E2EE_KEY_REQUEST_MAX_DELAY,
    E2EE_KEY_SHARING_DELAY_SECONDS,
)
from mmrelay.constants.domain import MATRIX_EVENT_TYPE_ROOM_MESSAGE
from mmrelay.constants.formats import (
    DEFAULT_TEXT_ENCODING,
    DETECTION_SENSOR_APP,
    ENCODING_ERROR_IGNORE,
)
from mmrelay.constants.messages import (
    DEFAULT_MESSAGE_TRUNCATE_BYTES,
    MESHNET_NAME_ABBREVIATION_LENGTH,
    MESSAGE_PREVIEW_LENGTH,
    MSG_MATRIX_SYNC_FAILED,
    MSG_MATRIX_SYNC_TIMEOUT,
    MSG_MISSING_MATRIX_ROOMS,
    PORTNUM_DETECTION_SENSOR_APP,
    SHORTNAME_FALLBACK_LENGTH,
)
from mmrelay.constants.network import (
    MATRIX_CLOCK_ROLLBACK_DISABLE_MS,
    MATRIX_EARLY_SYNC_TIMEOUT,
    MATRIX_EVENT_EPOCH_FLOOR_MS,
    MATRIX_INITIAL_SYNC_MAX_ATTEMPTS,
    MATRIX_INITIAL_SYNC_RETRY_MAX_DELAY_SECS,
    MATRIX_ROOM_SEND_TIMEOUT,
    MATRIX_STALE_STARTUP_EVENT_DROP_MS,
    MATRIX_STARTUP_STALE_FILTER_WINDOW_MS,
    MATRIX_STARTUP_TIMESTAMP_TOLERANCE_MS,
    MATRIX_SYNC_OPERATION_TIMEOUT,
    MATRIX_SYNC_RETRY_DELAY_SECS,
    MATRIX_TO_DEVICE_TIMEOUT,
    MILLISECONDS_PER_SECOND,
)
from mmrelay.db_utils import (
    async_prune_message_map,
    async_store_message_map,
    get_message_map_by_matrix_event_id,
)
from mmrelay.log_utils import get_logger

# Do not import plugin_loader here to avoid circular imports
from mmrelay.meshtastic_utils import connect_meshtastic, send_text_reply

# Import meshtastic protobuf for port numbers when needed
from mmrelay.message_queue import get_message_queue, queue_message
from mmrelay.paths import get_credentials_path


# Import nio exception types with error handling for test environments.
# matrix-nio is not marked py.typed in our env; keep import-untyped for mypy --strict.
try:
    nio_exceptions = importlib.import_module("nio.exceptions")
    nio_responses = importlib.import_module("nio.responses")

    NioLocalProtocolError = nio_exceptions.LocalProtocolError
    NioLocalTransportError = nio_exceptions.LocalTransportError
    NioRemoteProtocolError = nio_exceptions.RemoteProtocolError
    NioRemoteTransportError = nio_exceptions.RemoteTransportError
    NioLoginError = nio_responses.LoginError
    NioLogoutError = nio_responses.LogoutError
except (ImportError, AttributeError):
    # Fallback for test environments where nio imports might fail
    class _NioStubError(Exception):
        """Stub exception for nio errors in test mode"""

        pass

    NioLoginError = _NioStubError
    NioLogoutError = _NioStubError
    NioLocalProtocolError = _NioStubError
    NioRemoteProtocolError = _NioStubError
    NioLocalTransportError = _NioStubError
    NioRemoteTransportError = _NioStubError

NIO_COMM_EXCEPTIONS: tuple[type[BaseException], ...] = (
    NioLocalProtocolError,
    NioRemoteProtocolError,
    NioLocalTransportError,
    NioRemoteTransportError,
    asyncio.TimeoutError,
)
# jsonschema is a matrix-nio dependency but keep import guarded for safety.
jsonschema: Any = None
try:
    import jsonschema as _jsonschema  # pyright: ignore[reportMissingImports]  # type: ignore[import-untyped]

    jsonschema = _jsonschema
except ImportError:  # pragma: no cover - jsonschema is expected in runtime
    pass
# Provide a concrete ValidationError type for explicit exception handling.
if jsonschema is not None:
    from jsonschema.exceptions import ValidationError as _ValidationError

    JSONSCHEMA_VALIDATION_ERROR: type[Exception] = _ValidationError
else:

    class _JsonSchemaValidationError(Exception):
        """Fallback when jsonschema is unavailable."""

    JSONSCHEMA_VALIDATION_ERROR = _JsonSchemaValidationError

# Tuple of exceptions that can be unpacked in except clauses
SYNC_RETRY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    asyncio.TimeoutError,
    *NIO_COMM_EXCEPTIONS,
    JSONSCHEMA_VALIDATION_ERROR,
)
# Exception tuple for login whoami fallback when resolving user_id post-login.
WHOAMI_USER_ID_FALLBACK_EXCEPTIONS: tuple[type[BaseException], ...] = (
    *NIO_COMM_EXCEPTIONS,
    OSError,
    AttributeError,
    TypeError,
    ValueError,
    RuntimeError,
)
# Exception tuple for login operations including SSL and OS-level errors.
LOGIN_EXCEPTIONS: tuple[type[BaseException], ...] = (
    *NIO_COMM_EXCEPTIONS,
    ssl.SSLError,
    OSError,
)

logger = get_logger(name="Matrix")


class MissingMatrixRoomsError(ValueError):
    """Raised when matrix_rooms configuration is missing."""

    def __init__(self) -> None:
        super().__init__(MSG_MISSING_MATRIX_ROOMS)


class MatrixSyncTimeoutError(ConnectionError):
    """Raised when initial Matrix sync times out."""

    def __init__(self) -> None:
        super().__init__(MSG_MATRIX_SYNC_TIMEOUT)


class MatrixSyncFailedError(ConnectionError):
    """Raised when Matrix sync fails."""

    def __init__(self) -> None:
        super().__init__(MSG_MATRIX_SYNC_FAILED)


class MatrixSyncFailedDetailsError(ConnectionError):
    """Raised when Matrix sync fails with detailed error info."""

    def __init__(self, error_type: str, error_details: str) -> None:
        super().__init__(f"{MSG_MATRIX_SYNC_FAILED}: {error_type} - {error_details}")
        self.error_type = error_type
        self.error_details = error_details


_MIME_TYPE_MAP: Dict[str, str] = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
}


# ---------------------------------------------------------------------------
# Facade-owned globals — defined BEFORE submodule imports so that submodules
# can reference facade.<name> at function-call time even during circular
# import resolution.
# ---------------------------------------------------------------------------

# Builtin accessed by submodules via facade.NAME (must be explicit for module attribute access)
input = input

# Global config variable that will be set from config.py
config = None

# These will be set in connect_matrix()
matrix_homeserver = None
matrix_rooms = None
matrix_access_token = None
bot_user_id = None
bot_user_name = None  # Detected upon logon
bot_start_time = int(
    time.time() * MILLISECONDS_PER_SECOND
)  # Timestamp when the bot starts, used to filter out old messages
bot_start_monotonic_secs = time.monotonic()


matrix_client = None

# Serialize connect_matrix startup publication and invite-state monkey patching.
_MATRIX_STARTUP_SYNC_LOCK = asyncio.Lock()


# ---------------------------------------------------------------------------
# Thin live wrappers for config / e2ee_utils helpers.
#
# These MUST be wrapper functions, not direct imported aliases, so that
# monkeypatching the original source modules (mmrelay.config.* or
# mmrelay.e2ee_utils.*) is still observable by code that routes through
# facade.*.  A direct alias freezes the function object at import time;
# a wrapper performs a live lookup on every call.
# ---------------------------------------------------------------------------


def is_e2ee_enabled(*args: Any, **kwargs: Any) -> Any:
    return config_module.is_e2ee_enabled(*args, **kwargs)


def get_e2ee_store_dir(*args: Any, **kwargs: Any) -> Any:
    return config_module.get_e2ee_store_dir(*args, **kwargs)


def get_e2ee_status(*args: Any, **kwargs: Any) -> Any:
    from mmrelay import e2ee_utils as _e2ee_utils

    return _e2ee_utils.get_e2ee_status(*args, **kwargs)


def get_room_encryption_warnings(*args: Any, **kwargs: Any) -> Any:
    from mmrelay import e2ee_utils as _e2ee_utils

    return _e2ee_utils.get_room_encryption_warnings(*args, **kwargs)


def get_e2ee_error_message(*args: Any, **kwargs: Any) -> Any:
    from mmrelay import e2ee_utils as _e2ee_utils

    return _e2ee_utils.get_e2ee_error_message(*args, **kwargs)


@dataclass
class MatrixAuthInfo:
    homeserver: str
    access_token: str
    user_id: str
    device_id: Optional[str]
    credentials: dict[str, Any] | None
    credentials_path: str | None


# ---------------------------------------------------------------------------
# Submodule imports — after all facade-owned globals so circular-import
# resolution never sees a partially-initialized module.
#
# NOTE ON CIRCULAR IMPORTS: The circular imports between submodules
# (credentials.py, sync_bootstrap.py, etc.) and this facade are handled
# by the facade module pattern. Submodules access shared state via
# `import mmrelay.matrix_utils as facade` and facade.NAME references.
# This does not affect normal operation when importing through proper
# entry points (like mmrelay.cli). The architecture is correct as-is.
# ---------------------------------------------------------------------------

from mmrelay.matrix.room_mapping import (
    _create_mapping_info,
    _display_room_channel_mappings,
    _extract_localpart_from_mxid,
    _get_valid_device_id,
    _is_room_alias,
    _is_room_mapped,
    _iter_room_alias_entries,
    _resolve_aliases_in_mapping,
    _update_room_id_in_mapping,
)
from mmrelay.matrix.prefixes import (
    _add_truncated_vars,
    _can_auto_create_credentials,
    _escape_leading_prefix_for_markdown,
    _first_nonblank_str,
    _get_detailed_matrix_error_message,
    _get_msgs_to_keep_config,
    _normalize_bot_user_id,
    get_interaction_settings,
    get_matrix_prefix,
    get_meshtastic_prefix,
    message_storage_enabled,
    validate_prefix_format,
)
from mmrelay.matrix.command_bridge import (
    _connect_meshtastic,
    _estimate_clock_rollback_ms,
    _get_meshtastic_interface_and_channel,
    _handle_detection_sensor_packet,
    _parse_matrix_message_command,
    _refresh_bot_start_timestamps,
    bot_command,
    get_displayname,
)
from mmrelay.matrix.credentials import (
    _missing_credentials_keys,
    _resolve_and_load_credentials,
    _resolve_credentials_save_path,
)
from mmrelay.matrix.auth import (
    _close_matrix_client_after_failure,
    _configure_e2ee,
    _initialize_matrix_client,
    _maybe_upload_e2ee_keys,
    _perform_matrix_login,
)
from mmrelay.matrix.sync_bootstrap import (
    _perform_initial_sync,
    _post_sync_setup,
    connect_matrix,
    join_matrix_room,
    login_matrix_bot,
)
from mmrelay.matrix.relay import (
    _get_e2ee_error_message,
    _retry_backoff_delay,
    _send_matrix_message_with_retry,
    matrix_relay,
)
from mmrelay.matrix.replies import (
    format_reply_message,
    get_user_display_name,
    handle_matrix_reply,
    send_reply_to_meshtastic,
    strip_quoted_lines,
    truncate_message,
)
from mmrelay.matrix.media import (
    ImageUploadError,
    send_image,
    send_room_image,
    upload_image,
)
from mmrelay.matrix.events import (
    on_decryption_failure,
    on_invite,
    on_room_member,
    on_room_message,
)
