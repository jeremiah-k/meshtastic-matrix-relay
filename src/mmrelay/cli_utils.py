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

try:
    import certifi
except ImportError:
    certifi: ModuleType | None = None  # type: ignore[no-redef]

# Import Matrix-related modules for logout functionality
try:
    # matrix-nio is not marked py.typed; keep import-untyped for strict mypy.
    from nio import AsyncClient  # type: ignore[import-untyped]
    from nio.exceptions import (  # type: ignore[import-untyped]
        LocalProtocolError,
        LocalTransportError,
        RemoteProtocolError,
        RemoteTransportError,
    )
    from nio.responses import LoginError, LogoutError  # type: ignore[import-untyped]

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

logger = logging.getLogger(__name__)

# Command registry - single source of truth for CLI command syntax
CLI_COMMANDS = {
    # Config commands
    "generate_config": "mmrelay config generate",
    "check_config": "mmrelay config check",
    # Auth commands
    "auth_login": "mmrelay auth login",
    "auth_status": "mmrelay auth status",
    # Service commands
    "service_install": "mmrelay service install",
    # Main commands
    "start_relay": "mmrelay",
    "show_version": "mmrelay --version",
    "show_help": "mmrelay --help",
}

# Deprecation mappings - maps old flags to new command keys
DEPRECATED_COMMANDS = {
    "--generate-config": "generate_config",
    "--check-config": "check_config",
    "--install-service": "service_install",
    "--auth": "auth_login",
}


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
    Create an SSL context for Matrix client connections, preferring certifi's CA bundle when available.

    Returns:
        ssl.SSLContext | None: An SSLContext that uses certifi's CA bundle when available or the system default; `None` if context creation fails.
    """
    try:
        if certifi:
            return ssl.create_default_context(cafile=certifi.where())
        else:
            return ssl.create_default_context()
    except Exception as e:
        logger.warning(
            f"Failed to create certifi-backed SSL context, falling back to system default: {e}"
        )
        try:
            return ssl.create_default_context()
        except Exception as fallback_e:
            logger.error(f"Failed to create system default SSL context: {fallback_e}")
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

    from mmrelay.config import get_base_dir, get_e2ee_store_dir

    logger.info("Clearing local session data...")
    success = True

    # Remove credentials.json
    config_dir = get_base_dir()
    credentials_path = os.path.join(config_dir, "credentials.json")

    if os.path.exists(credentials_path):
        try:
            os.remove(credentials_path)
            logger.info(f"Removed credentials file: {credentials_path}")
        except (OSError, PermissionError) as e:
            logger.error(f"Failed to remove credentials file: {e}")
            success = False
    else:
        logger.info("No credentials file found to remove")

    # Clear E2EE store directory (default and any configured override)
    candidate_store_paths = {get_e2ee_store_dir()}
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
    except Exception as e:
        logger.debug(
            f"Could not resolve configured E2EE store path: {type(e).__name__}"
        )

    any_store_found = False
    for store_path in sorted(candidate_store_paths):
        if os.path.exists(store_path):
            any_store_found = True
            try:
                shutil.rmtree(store_path)
                logger.info(f"Removed E2EE store directory: {store_path}")
            except (OSError, PermissionError) as e:
                logger.error(
                    f"Failed to remove E2EE store directory '{store_path}': {e}"
                )
                success = False
    if not any_store_found:
        logger.info("No E2EE store directory found to remove")

    if success:
        logger.info("✅ Logout completed successfully!")
        logger.info("All Matrix sessions and local data have been cleared.")
        logger.info("Run 'mmrelay auth login' to authenticate again.")
    else:
        logger.warning("Logout completed with some errors.")
        logger.warning("Some files may not have been removed due to permission issues.")

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
    log_func = logger.error if log_level == "error" else logger.warning
    emoji = "❌" if log_level == "error" else "⚠️ "
    is_verification = "verification" in context.lower()

    # Determine error category and details
    error_category = None
    error_detail = None

    # Handle specific Matrix-nio exceptions
    if isinstance(exception, (NioLoginError, NioLogoutError)) and hasattr(
        exception, "status_code"
    ):
        if (
            hasattr(exception, "errcode") and exception.errcode == "M_FORBIDDEN"
        ) or exception.status_code == 401:
            error_category = "credentials"
        elif exception.status_code in [500, 502, 503]:
            error_category = "server"
        else:
            error_category = "other"
            error_detail = str(exception.status_code)
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
            logger.debug(f"Full error details: {exception}")
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
    Log out the configured Matrix account, verify credentials when possible, and remove local session data.

    If credentials are present the function will, when feasible, verify the provided password by performing a temporary login and request server-side logout to invalidate the stored access token. If the stored credentials are incomplete it performs a best-effort local cleanup of session artifacts (for example, credentials file and E2EE stores). All network and cleanup failures are reported; the function always attempts local cleanup regardless of server outcomes.

    Parameters:
        password (str): Matrix account password used to verify the session before attempting server logout.

    Returns:
        bool: `True` when local cleanup (and server logout, if attempted) completed successfully; `False` on failure or when the matrix-nio dependency is unavailable.
    """

    # Import inside function to avoid circular imports
    from mmrelay.matrix_utils import (  # type: ignore[attr-defined]
        MATRIX_LOGIN_TIMEOUT,
        load_credentials,
    )

    # Check if matrix-nio is available
    if AsyncClient is None:
        logger.error("Matrix-nio library not available. Cannot perform logout.")
        print("❌ Matrix-nio library not available. Cannot perform logout.")
        return False

    # Load current credentials
    credentials = load_credentials()
    if not credentials:
        logger.info("No active session found. Already logged out.")
        print("ℹ️  No active session found. Already logged out.")
        return True

    homeserver = credentials.get("homeserver")
    user_id = credentials.get("user_id")
    access_token = credentials.get("access_token")
    device_id = credentials.get("device_id")

    # If user_id is missing, try to fetch it using the access token
    if not user_id and access_token and homeserver:
        logger.info("user_id missing from credentials, attempting to fetch it...")
        print("🔍 user_id missing from credentials, attempting to fetch it...")

        temp_client = None
        try:
            # Create SSL context for the temporary client
            ssl_context = _create_ssl_context()

            # Create a temporary client to fetch user_id
            temp_client = AsyncClient(homeserver, ssl=ssl_context)
            temp_client.access_token = access_token

            # Fetch user_id using whoami
            whoami_response = await asyncio.wait_for(
                temp_client.whoami(),
                timeout=MATRIX_LOGIN_TIMEOUT,
            )

            if hasattr(whoami_response, "user_id"):
                user_id = whoami_response.user_id
                logger.info(f"Successfully fetched user_id: {user_id}")
                print(f"✅ Successfully fetched user_id: {user_id}")

                # Update credentials with the fetched user_id
                credentials["user_id"] = user_id
                from mmrelay.config import save_credentials

                save_credentials(credentials)
                logger.info("Updated credentials.json with fetched user_id")
                print("✅ Updated credentials.json with fetched user_id")
            else:
                logger.error("Failed to fetch user_id from whoami response")
                print("❌ Failed to fetch user_id from whoami response")

        except asyncio.TimeoutError:
            logger.error("Timeout while fetching user_id")
            print("❌ Timeout while fetching user_id")
        except Exception as e:
            logger.exception("Error fetching user_id")
            print(f"❌ Error fetching user_id: {e}")
        finally:
            if temp_client is not None:
                try:
                    await temp_client.close()
                except Exception:
                    # Ignore close failures but keep a trace for diagnostics.
                    logger.debug(
                        "Ignoring error while closing temporary Matrix client during logout",
                        exc_info=True,
                    )

    if not all([homeserver, user_id, access_token, device_id]):
        logger.error("Invalid credentials found. Cannot verify logout.")
        logger.info("Proceeding with local cleanup only...")
        print("⚠️  Invalid credentials found. Cannot verify logout.")
        print("Proceeding with local cleanup only...")

        # Still try to clean up local files
        success = _cleanup_local_session_data()
        if success:
            print("✅ Local cleanup completed successfully!")
        else:
            print("❌ Local cleanup completed with some errors.")
        return success

    logger.info(f"Verifying password for {user_id}...")
    print(f"🔐 Verifying password for {user_id}...")

    temp_client = None
    try:
        # Create SSL context using certifi's certificates
        ssl_context = _create_ssl_context()
        if ssl_context is None:
            logger.warning(
                "Failed to create SSL context for password verification; falling back to default system SSL"
            )

        # Create a temporary client to verify the password
        # We'll try to login with the password to verify it's correct
        temp_client = AsyncClient(homeserver, user_id, ssl=ssl_context)

        try:
            # Attempt login with the provided password
            response = await asyncio.wait_for(
                temp_client.login(password, device_name="mmrelay-logout-verify"),
                timeout=MATRIX_LOGIN_TIMEOUT,
            )

            if hasattr(response, "access_token"):
                logger.info("Password verified successfully.")
                print("✅ Password verified successfully.")

                # Immediately logout the temporary session
                await temp_client.logout()
            else:
                logger.error("Password verification failed.")
                print("❌ Password verification failed.")
                return False

        except asyncio.TimeoutError:
            logger.error(
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
                    logger.debug(
                        "Ignoring error while closing temporary Matrix client after password verification",
                        exc_info=True,
                    )

        # Now logout the main session
        logger.info("Logging out from Matrix server...")
        print("🚪 Logging out from Matrix server...")
        main_client = None
        try:
            main_client = AsyncClient(homeserver, user_id, ssl=ssl_context)
            main_client.restore_login(
                user_id=user_id,
                device_id=device_id,
                access_token=access_token,
            )

            # Logout from the server (invalidates the access token)
            logout_response = await main_client.logout()
            if hasattr(logout_response, "transport_response"):
                logger.info("Successfully logged out from Matrix server.")
                print("✅ Successfully logged out from Matrix server.")
            else:
                logger.warning(
                    "Logout response unclear, proceeding with local cleanup."
                )
                print("⚠️  Logout response unclear, proceeding with local cleanup.")
        except Exception as e:
            _handle_matrix_error(e, "Server logout", "warning")
            logger.debug(f"Logout error details: {e}")
        finally:
            if main_client is not None:
                try:
                    await main_client.close()
                except (OSError, asyncio.TimeoutError):
                    logger.debug(
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
        logger.exception("Error during logout process")
        print(f"❌ Error during logout process: {e}")
        return False
