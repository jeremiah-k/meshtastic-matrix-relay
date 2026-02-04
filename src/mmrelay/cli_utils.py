"""
CLI utilities and command registry.

This module provides a centralized registry of all CLI commands to ensure
consistency across error messages, help text, and documentation. It's separate
from cli.py to avoid circular dependencies when other modules need to reference
CLI commands.

It also contains CLI-specific functions that need to interact with users
via print statements (as opposed to library functions that should only log).

Usage:
    from mmrelay.cli_utils import get_command, suggest_command, logout_matrix_bot

    # Get a command string
    cmd = get_command('generate_config')  # Returns "mmrelay config generate"

    # Generate suggestion messages
    msg = suggest_command('generate_config', 'to create a sample configuration')

    # CLI functions (can use print statements)
    result = await logout_matrix_bot(password="user_password")
"""

import asyncio
import logging
import os
import ssl
from types import ModuleType
from typing import Any, cast

# Import resolve_all_paths for unified path resolution

try:
    import certifi
except ImportError:
    certifi: ModuleType | None = None  # type: ignore[no-redef]

# Import Matrix-related modules for logout functionality
try:
    from nio import AsyncClient
    from nio.exceptions import (
        LocalProtocolError,
        LocalTransportError,
        RemoteProtocolError,
        RemoteTransportError,
    )
    from nio.responses import LoginError, LogoutError

    # Create aliases for backward compatibility
    NioLoginError = LoginError
    NioLogoutError = LogoutError
    NioLocalTransportError = LocalTransportError
    NioRemoteTransportError = RemoteTransportError
    NioLocalProtocolError = LocalProtocolError
    NioRemoteProtocolError = RemoteProtocolError
except ImportError:
    # Handle case where matrix-nio is not installed
    AsyncClient = None
    LoginError = Exception
    LogoutError = Exception
    LocalTransportError = Exception
    RemoteTransportError = Exception
    LocalProtocolError = Exception
    RemoteProtocolError = Exception
    # Create aliases for backward compatibility
    NioLoginError = Exception
    NioLogoutError = Exception
    NioLocalTransportError = Exception
    NioRemoteTransportError = Exception
    NioLocalProtocolError = Exception
    NioRemoteProtocolError = Exception

# Import mmrelay modules - avoid circular imports by importing inside functions

from mmrelay.constants.cli import (
    CLI_COMMANDS,
    DEPRECATED_COMMANDS,
)
from mmrelay.log_utils import get_logger

logger: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    """
    Lazily initializes and returns the module-level logger for this module.

    Returns:
        logging.Logger: The module-scoped logger instance.

    Raises:
        RuntimeError: If the logger cannot be initialized.
    """
    global logger
    if logger is None:
        logger = get_logger(__name__)
    if logger is None:
        raise RuntimeError("Logger must be initialized")
    return logger


def get_command(command_key: str) -> str:
    """
    Lookup the current CLI syntax for the given command key.

    Parameters:
        command_key (str): Registered command key (for example, "generate_config").

    Returns:
        str: The full CLI command syntax (for example, "mmrelay config generate").

    Raises:
        KeyError: If `command_key` is not present in the command registry.
    """
    if command_key not in CLI_COMMANDS:
        raise KeyError(f"Unknown CLI command key: {command_key}")
    return CLI_COMMANDS[command_key]


def get_deprecation_warning(old_flag: str) -> str:
    """
    Produce a deprecation warning message for a deprecated CLI flag.

    If a replacement command is known, the message suggests the full new command; otherwise it directs the user to `mmrelay --help`.

    Parameters:
        old_flag (str): Deprecated flag (for example, '--generate-config').

    Returns:
        str: Formatted deprecation warning message.
    """
    new_command_key = DEPRECATED_COMMANDS.get(old_flag)
    if new_command_key:
        new_command = get_command(new_command_key)
        return f"Warning: {old_flag} is deprecated. Use '{new_command}' instead."
    return f"Warning: {old_flag} is deprecated. Run 'mmrelay --help' to see the current commands."


def suggest_command(command_key: str, purpose: str) -> str:
    """
    Suggests the full CLI command to run for a given purpose.

    Parameters:
        command_key (str): Key used to look up the full CLI command in the registry.
        purpose (str): Short phrase describing why to run the command (e.g. "to validate your configuration").

    Returns:
        str: A string formatted as "Run '<full command>' {purpose}."
    """
    command = get_command(command_key)
    return f"Run '{command}' {purpose}."


def require_command(command_key: str, purpose: str) -> str:
    """
    Compose a user-facing instruction to run a registered CLI command.

    Parameters:
        command_key (str): Key used to look up the command in the CLI registry.
        purpose (str): Short purpose phrase (typically begins with "to"), e.g. "to generate a sample configuration file".

    Returns:
        str: Instruction string like "Please run '<full command>' {purpose}."

    Raises:
        KeyError: If `command_key` is not found in the command registry.
    """
    command = get_command(command_key)
    return f"Please run '{command}' {purpose}."


def retry_command(command_key: str, context: str = "") -> str:
    """
    Produce a retry instruction telling the user to run a given CLI command again.

    Parameters:
        command_key (str): Key from CLI_COMMANDS identifying the command to show.
        context (str): Optional trailing context to append to the message (e.g., "after fixing X").

    Returns:
        str: A formatted message like "Try running '<full command>' again." or "Try running '<full command>' again {context}."
    """
    command = get_command(command_key)
    if context:
        return f"Try running '{command}' again {context}."
    else:
        return f"Try running '{command}' again."


def validate_command(command_key: str, purpose: str) -> str:
    """
    Produce a user-facing instruction that recommends running a registered CLI command for a given purpose.

    Parameters:
        command_key (str): Key from the module's command registry identifying which CLI command to reference (e.g., "check_config").
        purpose (str): Short phrase describing the validation action to append (e.g., "to validate your configuration").

    Returns:
        str: Instruction string formatted as "Use '<full-command>' {purpose}."
    """
    command = get_command(command_key)
    return f"Use '{command}' {purpose}."


# Common message templates for frequently used commands
def msg_suggest_generate_config() -> str:
    """
    Suggest running the configured generate_config CLI command to create a sample configuration file.

    Returns:
        str: Instruction string, e.g. "Run 'mmrelay config generate' to generate a sample configuration file."
    """
    return suggest_command("generate_config", "to generate a sample configuration file")


def msg_suggest_check_config() -> str:
    """
    Suggest the CLI command the user should run to validate their configuration.

    Returns:
        str: A sentence suggesting running the configured config validation command (e.g. "Run 'mmrelay config check' to validate your configuration.").
    """
    return validate_command("check_config", "to validate your configuration")


def msg_require_auth_login() -> str:
    """
    Return an instruction telling the user to run the configured authentication command.

    Returns:
        Instruction string directing the user to run the `auth_login` command to set up `credentials.json` or add a Matrix section to `config.yaml`.
    """
    return require_command(
        "auth_login", "to set up credentials.json, or add matrix section to config.yaml"
    )


def msg_retry_auth_login() -> str:
    """
    Suggest retrying the authentication login command.

    Returns:
        str: Suggestion message instructing the user to try running the auth_login command again.
    """
    return retry_command("auth_login")


def msg_run_auth_login() -> str:
    """
    Instruct the user to run the authentication/login command to regenerate credentials.

    Returns:
        str: Instruction string telling the user to run the auth login command to regenerate credentials (including a device_id).
    """
    return msg_regenerate_credentials()


def msg_for_e2ee_support() -> str:
    """
    Instructs the user to run the configured authentication command for end-to-end encryption (E2EE) support.

    Returns:
        str: Instructional message telling the user to run the configured `auth_login` CLI command for E2EE support.
    """
    return f"For E2EE support: run '{get_command('auth_login')}'"


def msg_setup_auth() -> str:
    """
    Provide a setup instruction that references the current `auth_login` CLI command.

    Returns:
        setup_instruction (str): The string "Setup: <full command>" where `<full command>` is the resolved `auth_login` command from the CLI registry.
    """
    return f"Setup: {get_command('auth_login')}"


def msg_or_run_auth_login() -> str:
    """
    Suggest running the configured `auth_login` command to set up credentials.json.

    Returns:
        A message in the form "or run '<full command>' to set up credentials.json".
    """
    return f"or run '{get_command('auth_login')}' to set up credentials.json"


def msg_setup_authentication() -> str:
    """
    Prompt the user to run the authentication setup command.

    Returns:
        Instruction text in the form "Setup authentication: <full command>".
    """
    return f"Setup authentication: {get_command('auth_login')}"


def msg_regenerate_credentials() -> str:
    """
    Prompt the user to rerun the authentication command to regenerate credentials that include a device_id.

    Returns:
        str: Instruction message telling the user to run the `auth_login` command to produce new credentials containing a `device_id`.
    """
    return f"Please run '{get_command('auth_login')}' again to generate new credentials that include a device_id."


# Helper functions moved from matrix_utils to break circular dependency


def _create_ssl_context() -> ssl.SSLContext | None:
    """
    Create an SSLContext for Matrix client connections, preferring certifi's CA bundle when available.

    Returns:
        An `ssl.SSLContext` that uses certifi's CA bundle when available or the system default; `None` if context creation fails.
    """
    try:
        if certifi:
            return ssl.create_default_context(cafile=certifi.where())
        else:
            return ssl.create_default_context()
    except (ssl.SSLError, OSError, ValueError):
        _get_logger().warning(
            "Failed to create certifi-backed SSL context, falling back to system default",
            exc_info=True,
        )
        try:
            return ssl.create_default_context()
        except (ssl.SSLError, OSError, ValueError):
            _get_logger().error(
                "Failed to create system default SSL context", exc_info=True
            )
            return None


def _cleanup_local_session_data() -> bool:
    """
    Remove local Matrix session artifacts including credentials and E2EE store directories.

    Removes the credentials file located at the application's base directory and any
    E2EE store directories (the default store directory and any user-configured
    overrides under `matrix.e2ee.store_path` or `matrix.encryption.store_path`).
    The function makes a best-effort attempt to remove all targeted files and
    directories and continues attempting other removals even if some fail.

    Returns:
        bool: `True` if all targeted files and directories were removed successfully;
              `False` if any removal failed.
    """
    import shutil

    from mmrelay.paths import resolve_all_paths

    _get_logger().info("Clearing local session data...")

    success = True

    # Use unified path resolution for credentials

    credentials_path = resolve_all_paths()["credentials_path"]
    if os.path.exists(credentials_path):
        try:
            os.remove(credentials_path)
            _get_logger().info(f"Removed credentials file: {credentials_path}")
        except (OSError, PermissionError) as e:
            _get_logger().error(f"Failed to remove credentials file: {e}")
            success = False
    else:
        _get_logger().info("No credentials file found to remove")

    # Clear E2EE store directory (default and any configured override)
    # Skip on Windows (E2EE not supported); resolve_all_paths handles this safely
    candidate_store_paths: set[str] = set()
    try:
        paths_info = resolve_all_paths()
        store_dir = paths_info.get("store_dir")
        if store_dir and store_dir != "N/A (Windows)":
            candidate_store_paths.add(store_dir)
    except (OSError, RuntimeError) as e:
        _get_logger().debug(
            "Could not resolve E2EE store path from paths: %s", type(e).__name__
        )

    # Add any configured override from config
    try:
        from mmrelay.config import load_config

        cfg = load_config(args=None) or {}
        matrix_cfg = cfg.get("matrix", {})
        for section in ("e2ee", "encryption"):
            override = os.path.expanduser(
                matrix_cfg.get(section, {}).get("store_path", "")
            )
            if override:
                candidate_store_paths.add(override)
    except (ImportError, OSError) as e:
        _get_logger().debug(
            "Could not resolve configured E2EE store path: %s", type(e).__name__
        )

    any_store_found = False
    for store_path in sorted(candidate_store_paths):
        if os.path.exists(store_path):
            any_store_found = True
            try:
                shutil.rmtree(store_path)
                _get_logger().info(f"Removed E2EE store directory: {store_path}")
            except (OSError, PermissionError) as e:
                _get_logger().error(
                    f"Failed to remove E2EE store directory '{store_path}': {e}"
                )
                success = False
    if not any_store_found:
        _get_logger().info("No E2EE store directory found to remove")

    if success:
        _get_logger().info("✅ Logout completed successfully!")
        _get_logger().info("All Matrix sessions and local data have been cleared.")
        _get_logger().info("Run 'mmrelay auth login' to authenticate again.")
    else:
        _get_logger().warning("Logout completed with some errors.")
        _get_logger().warning(
            "Some files may not have been removed due to permission issues."
        )

    return success


# CLI-specific functions (can use print statements for user interaction)


def _handle_matrix_error(
    exception: Exception, context: str, log_level: str = "error"
) -> bool:
    """
    Classify a Matrix-related exception, log and print an appropriate user-facing message, and mark it handled.

    Parameters:
        exception (Exception): The exception to classify and report.
        context (str): Short description of the operation (e.g., "Password verification"); used to tailor message phrasing and detect verification flows.
        log_level (str): Logging level to use; either "error" or "warning".

    Returns:
        bool: `True` indicating the exception was handled and reported.
    """
    logger = _get_logger()
    log_func = logger.error if log_level == "error" else logger.warning
    emoji = "❌" if log_level == "error" else "⚠️ "
    is_verification = "verification" in context.lower()

    # Determine error category and details
    error_category = None
    error_detail = None

    # Handle specific Matrix-nio exceptions
    if isinstance(exception, (NioLoginError, NioLogoutError)):
        errcode = getattr(exception, "errcode", None)
        status_code = getattr(exception, "status_code", None)
        if errcode == "M_FORBIDDEN" or status_code == 401:
            error_category = "credentials"
        elif status_code in [
            500,
            502,
            503,
        ]:
            error_category = "server"
        else:
            error_category = "other"
            error_detail = str(status_code)
    # Handle network/transport exceptions
    elif isinstance(
        exception,
        (
            NioLocalTransportError,
            NioRemoteTransportError,
            NioLocalProtocolError,
            NioRemoteProtocolError,
        ),
    ):
        error_category = "network"
    else:
        # Fallback to string matching for unknown exceptions
        error_msg = str(exception).lower()
        if "forbidden" in error_msg or "401" in error_msg:
            error_category = "credentials"
        elif (
            "network" in error_msg
            or "connection" in error_msg
            or "timeout" in error_msg
        ):
            error_category = "network"
        elif (
            "server" in error_msg
            or "500" in error_msg
            or "502" in error_msg
            or "503" in error_msg
        ):
            error_category = "server"
        else:
            error_category = "other"
            error_detail = type(exception).__name__

    # Generate appropriate messages based on category and context
    if error_category == "credentials":
        if is_verification:
            log_func(f"{context} failed: Invalid credentials.")
            log_func("Please check your username and password.")
            print(f"{emoji} {context} failed: Invalid credentials.")
            print("Please check your username and password.")
        else:
            log_func(
                f"{context} failed due to invalid token (already logged out?), proceeding with local cleanup."
            )
            print(
                f"{emoji} {context} failed due to invalid token (already logged out?), proceeding with local cleanup."
            )
    elif error_category == "network":
        if is_verification:
            log_func(f"{context} failed: Network connection error.")
            log_func(
                "Please check your internet connection and Matrix server availability."
            )
            print(f"{emoji} {context} failed: Network connection error.")
            print(
                "Please check your internet connection and Matrix server availability."
            )
        else:
            log_func(
                f"{context} failed due to network issues, proceeding with local cleanup."
            )
            print(
                f"{emoji} {context} failed due to network issues, proceeding with local cleanup."
            )
    elif error_category == "server":
        if is_verification:
            log_func(f"{context} failed: Matrix server error.")
            log_func(
                "Please try again later or contact your Matrix server administrator."
            )
            print(f"{emoji} {context} failed: Matrix server error.")
            print("Please try again later or contact your Matrix server administrator.")
        else:
            log_func(
                f"{context} failed due to server error, proceeding with local cleanup."
            )
            print(
                f"{emoji} {context} failed due to server error, proceeding with local cleanup."
            )
    else:  # error_category == "other"
        if is_verification:
            log_func(f"{context} failed: {error_detail or 'Unknown error'}")
            _get_logger().debug(f"Full error details: {exception}")
            print(f"{emoji} {context} failed: {error_detail or 'Unknown error'}")
        else:
            log_func(
                f"{context} failed ({error_detail or 'Unknown error'}), proceeding with local cleanup."
            )
            print(
                f"{emoji} {context} failed ({error_detail or 'Unknown error'}), proceeding with local cleanup."
            )

    return True


async def logout_matrix_bot(password: str) -> bool:
    """
    Log out the configured Matrix account, optionally verify credentials, and remove local session data.

    Performs a best-effort server-side logout if full credentials are available (verifying the provided password when possible) and always attempts to remove local session artifacts such as credentials and E2EE stores.

    Parameters:
        password (str): Matrix account password used to verify the session before attempting server logout.

    Returns:
        bool: `True` when local cleanup (and server logout, if attempted) completed successfully; `False` otherwise.
    """

    # Import inside function to avoid circular imports
    from mmrelay.matrix_utils import (  # type: ignore[attr-defined]
        MATRIX_LOGIN_TIMEOUT,
        load_credentials,
    )

    # Check if matrix-nio is available
    if AsyncClient is None:
        _get_logger().error("Matrix-nio library not available. Cannot perform logout.")
        print("❌ Matrix-nio library not available. Cannot perform logout.")
        return False

    # Load current credentials
    credentials = load_credentials()
    if not credentials:
        _get_logger().info("No active session found. Already logged out.")
        print("ℹ️  No active session found. Already logged out.")
        return True

    homeserver = credentials.get("homeserver")
    user_id = credentials.get("user_id")
    access_token = credentials.get("access_token")
    device_id = credentials.get("device_id")

    # If user_id is missing, try to fetch it using the access token
    if not user_id and access_token and homeserver:
        _get_logger().info(
            "user_id missing from credentials, attempting to fetch it..."
        )
        print("🔍 user_id missing from credentials, attempting to fetch it...")

        temp_client = None
        try:
            # Create SSL context for the temporary client
            ssl_context = _create_ssl_context()

            # Create a temporary client to fetch user_id
            ssl_param = cast(Any, ssl_context)
            temp_client = AsyncClient(homeserver, ssl=ssl_param)
            temp_client.access_token = access_token

            # Fetch user_id using whoami
            whoami_response = await asyncio.wait_for(
                temp_client.whoami(),
                timeout=MATRIX_LOGIN_TIMEOUT,
            )

            user_id = getattr(whoami_response, "user_id", None)
            if user_id:
                _get_logger().info(f"Successfully fetched user_id: {user_id}")
                print(f"✅ Successfully fetched user_id: {user_id}")

                # Update credentials with the fetched user_id
                credentials["user_id"] = user_id
                from mmrelay.config import save_credentials

                save_credentials(credentials)
                _get_logger().info("Updated credentials.json with fetched user_id")
                print("✅ Updated credentials.json with fetched user_id")
            else:
                _get_logger().error("Failed to fetch user_id from whoami response")
                print("❌ Failed to fetch user_id from whoami response")

        except asyncio.TimeoutError:
            _get_logger().error("Timeout while fetching user_id")
            print("❌ Timeout while fetching user_id")
        except Exception as e:
            _get_logger().exception("Error fetching user_id")
            # Handle both network exceptions (when matrix-nio is installed) and
            # unexpected errors. When matrix-nio is not installed, the Nio types
            # are aliased to Exception, so guard with AsyncClient presence.
            if AsyncClient is not None and isinstance(
                e,
                (
                    NioLocalTransportError,
                    NioRemoteTransportError,
                    NioLocalProtocolError,
                    NioRemoteProtocolError,
                ),
            ):
                _get_logger().warning(f"Network error fetching user_id: {e}")
                print(f"❌ Network error fetching user_id: {e}")
            else:
                _get_logger().error("Unexpected error fetching user_id")
                print(f"❌ Unexpected error fetching user_id: {e}")
        finally:
            if temp_client is not None:
                try:
                    await temp_client.close()
                except Exception:
                    # Ignore close failures but keep a trace for diagnostics.
                    _get_logger().debug(
                        "Ignoring error while closing temporary Matrix client during logout",
                        exc_info=True,
                    )

    if not all([homeserver, user_id, access_token, device_id]):
        _get_logger().error("Invalid credentials found. Cannot verify logout.")
        _get_logger().info("Proceeding with local cleanup only...")
        print("⚠️  Invalid credentials found. Cannot verify logout.")
        print("Proceeding with local cleanup only...")

        # Still try to clean up local files
        success = _cleanup_local_session_data()
        if success:
            print("✅ Local cleanup completed successfully!")
        else:
            print("❌ Local cleanup completed with some errors.")
        return success
    assert homeserver is not None
    assert user_id is not None
    assert access_token is not None
    assert device_id is not None
    homeserver_str = cast(str, homeserver)
    user_id_str = cast(str, user_id)
    access_token_str = cast(str, access_token)
    device_id_str = cast(str, device_id)

    _get_logger().info(f"Verifying password for {user_id}...")
    print(f"🔐 Verifying password for {user_id}...")

    temp_client = None
    try:
        # Create SSL context using certifi's certificates
        ssl_context = _create_ssl_context()
        if ssl_context is None:
            _get_logger().warning(
                "Failed to create SSL context for password verification; falling back to default system SSL"
            )

        # Create a temporary client to verify the password
        # We'll try to login with the password to verify it's correct
        ssl_param = cast(Any, ssl_context)
        temp_client = AsyncClient(homeserver_str, user_id_str, ssl=ssl_param)

        try:
            # Attempt login with the provided password
            response = await asyncio.wait_for(
                temp_client.login(password, device_name="mmrelay-logout-verify"),
                timeout=MATRIX_LOGIN_TIMEOUT,
            )

            if hasattr(response, "access_token"):
                _get_logger().info("Password verified successfully.")
                print("✅ Password verified successfully.")

                # Immediately logout the temporary session
                try:
                    await asyncio.wait_for(
                        temp_client.logout(),
                        timeout=MATRIX_LOGIN_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    _get_logger().warning(
                        "Timeout during temporary session logout, continuing..."
                    )
            else:
                _get_logger().error("Password verification failed.")
                print("❌ Password verification failed.")
                return False

        except asyncio.TimeoutError:
            _get_logger().error(
                "Password verification timed out. Please check your network connection."
            )
            print(
                "❌ Password verification timed out. Please check your network connection."
            )
            return False
        except Exception as e:
            _handle_matrix_error(e, "Password verification", "error")
            return False
        finally:
            if temp_client is not None:
                try:
                    await temp_client.close()
                except (OSError, asyncio.TimeoutError):
                    # Avoid masking the original error; log for diagnostics only.
                    _get_logger().debug(
                        "Ignoring error while closing temporary Matrix client after password verification",
                        exc_info=True,
                    )

        # Now logout the main session
        _get_logger().info("Logging out from Matrix server...")
        print("🚪 Logging out from Matrix server...")
        main_client = None
        try:
            main_client = AsyncClient(homeserver_str, user_id_str, ssl=ssl_param)
            main_client.restore_login(
                user_id=user_id_str,
                device_id=device_id_str,
                access_token=access_token_str,
            )

            # Logout from the server (invalidates the access token)
            try:
                logout_response = await asyncio.wait_for(
                    main_client.logout(),
                    timeout=MATRIX_LOGIN_TIMEOUT,
                )
            except asyncio.TimeoutError:
                _get_logger().warning(
                    "Timeout during Matrix server logout, proceeding with local cleanup."
                )
                print(
                    "⚠️  Timeout during Matrix server logout, proceeding with local cleanup."
                )
            else:
                if hasattr(logout_response, "transport_response"):
                    _get_logger().info("Successfully logged out from Matrix server.")
                    print("✅ Successfully logged out from Matrix server.")
                else:
                    _get_logger().warning(
                        "Logout response unclear, proceeding with local cleanup."
                    )
                    print("⚠️  Logout response unclear, proceeding with local cleanup.")
        except Exception as e:
            _handle_matrix_error(e, "Server logout", "warning")
            _get_logger().debug(f"Logout error details: {e}")
        finally:
            if main_client is not None:
                try:
                    await main_client.close()
                except (OSError, asyncio.TimeoutError):
                    _get_logger().debug(
                        "Ignoring error while closing main Matrix client",
                        exc_info=True,
                    )

        # Clear local session data
        success = _cleanup_local_session_data()
        if success:
            print()
            print("✅ Logout completed successfully!")
            print("All Matrix sessions and local data have been cleared.")
            print("Run 'mmrelay auth login' to authenticate again.")
        else:
            print()
            print("⚠️  Logout completed with some errors.")
            print("Some files may not have been removed due to permission issues.")
        return success

    except Exception as e:
        _get_logger().exception("Error during logout process")
        print(f"❌ Error during logout process: {e}")
        return False
