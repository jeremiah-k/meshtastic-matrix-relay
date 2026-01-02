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
    """Get the current command syntax for a given command key.

    Args:
        command_key (str): The command key (e.g., 'generate_config')

    Returns:
        str: The current command syntax (e.g., 'mmrelay config generate')

    Raises:
        KeyError: If the command key is not found in the registry
    """
    if command_key not in CLI_COMMANDS:
        raise KeyError(f"Unknown CLI command key: {command_key}")
    return CLI_COMMANDS[command_key]


def get_deprecation_warning(old_flag: str) -> str:
    """
    Return a user-facing deprecation warning for a deprecated CLI flag.

    Looks up a replacement command for the given deprecated flag in DEPRECATED_COMMANDS.
    If a replacement exists, the returned message suggests the full new command (resolved
    via get_command). Otherwise it returns a generic guidance message pointing the user
    to `mmrelay --help`.

    Parameters:
        old_flag (str): Deprecated flag (e.g., '--generate-config').

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
    Return a concise suggestion message that tells the user which CLI command to run.

    Parameters:
        command_key (str): Key used to look up the full CLI command in the registry.
        purpose (str): Short phrase describing why to run the command (should start with "to", e.g. "to validate your configuration").

    Returns:
        str: Formatted suggestion like "Run '<command>' {purpose}."
    """
    command = get_command(command_key)
    return f"Run '{command}' {purpose}."


def require_command(command_key: str, purpose: str) -> str:
    """
    Return a user-facing requirement message that instructs running a registered CLI command.

    Parameters:
        command_key (str): Key used to look up the command in the CLI registry.
        purpose (str): Short purpose phrase (typically begins with "to"), e.g. "to generate a sample configuration file".

    Returns:
        str: Formatted message like "Please run '<full command>' {purpose}."

    Raises:
        KeyError: If `command_key` is not found in the command registry.
    """
    command = get_command(command_key)
    return f"Please run '{command}' {purpose}."


def retry_command(command_key: str, context: str = "") -> str:
    """
    Return a user-facing retry message instructing the user to run the given CLI command again.

    Parameters:
        command_key (str): Key from CLI_COMMANDS that identifies the command to show.
        context (str): Optional trailing context to append to the message (e.g., "after fixing X").

    Returns:
        str: Formatted message, either "Try running '<command>' again." or "Try running '<command>' again {context}."
    """
    command = get_command(command_key)
    if context:
        return f"Try running '{command}' again {context}."
    else:
        return f"Try running '{command}' again."


def validate_command(command_key: str, purpose: str) -> str:
    """
    Return a user-facing validation message that references a registered CLI command.

    command_key should be a key from the module's command registry (e.g. "check_config"); purpose is a short phrase describing the validation action (e.g. "to validate your configuration"). Returns a string like: "Use '<full-command>' {purpose}."
    """
    command = get_command(command_key)
    return f"Use '{command}' {purpose}."


# Common message templates for frequently used commands
def msg_suggest_generate_config() -> str:
    """
    Return a standardized user-facing suggestion to generate a sample configuration file.

    This message references the configured "generate_config" CLI command and is suitable for prompts and help text.

    Returns:
        str: A sentence instructing the user to run the generate-config command to generate a sample configuration file (e.g., "Run 'mmrelay config generate' to generate a sample configuration file.").
    """
    return suggest_command("generate_config", "to generate a sample configuration file")


def msg_suggest_check_config() -> str:
    """
    Return a standardized suggestion prompting the user to validate their configuration.

    This helper builds the user-visible message that tells users how to validate their config (e.g. by running the configured "check_config" CLI command).

    Returns:
        str: A full sentence suggesting the user run the config validation command.
    """
    return validate_command("check_config", "to validate your configuration")


def msg_require_auth_login() -> str:
    """
    Return a standard instruction asking the user to run the authentication command.

    This produces a formatted message that tells the user to run the configured "auth_login" CLI command
    to set up credentials.json or to add a Matrix section to config.yaml.

    Returns:
        str: A user-facing instruction string.
    """
    return require_command(
        "auth_login", "to set up credentials.json, or add matrix section to config.yaml"
    )


def msg_retry_auth_login() -> str:
    """Standard message suggesting auth retry."""
    return retry_command("auth_login")


def msg_run_auth_login() -> str:
    """
    Return a user-facing message that instructs running the auth login command to (re)generate credentials.

    The message prompts the user to run the authentication/login command again so new credentials (including a device_id) are created.

    Returns:
        str: Formatted instruction string for running the auth login command.
    """
    return msg_regenerate_credentials()


def msg_for_e2ee_support() -> str:
    """
    Return a user-facing instruction to run the authentication command required for E2EE support.

    Returns:
        str: A formatted message instructing the user to run the configured `auth_login` CLI command to enable end-to-end encryption (E2EE) support.
    """
    return f"For E2EE support: run '{get_command('auth_login')}'"


def msg_setup_auth() -> str:
    """
    Return a standard instruction directing the user to run the authentication setup command.

    The message is formatted as "Setup: <command>", where <command> is the current CLI syntax for the "auth_login" command resolved from the command registry.

    Returns:
        str: Formatted setup instruction pointing to the auth login CLI command.
    """
    return f"Setup: {get_command('auth_login')}"


def msg_or_run_auth_login() -> str:
    """
    Return a short suggestion offering the `auth_login` command as an alternative to setup.

    This function formats and returns a user-facing message that tells the caller to
    run the configured `auth_login` CLI command to create or set up credentials.json.

    Returns:
        str: A message of the form "or run '<command>' to set up credentials.json".
    """
    return f"or run '{get_command('auth_login')}' to set up credentials.json"


def msg_setup_authentication() -> str:
    """Standard message for authentication setup."""
    return f"Setup authentication: {get_command('auth_login')}"


def msg_regenerate_credentials() -> str:
    """
    Return a standardized instruction prompting the user to re-run the authentication command to regenerate credentials that include a `device_id`.

    Returns:
        str: Message instructing the user to run the auth login command again to produce new credentials containing a `device_id`.
    """
    return f"Please run '{get_command('auth_login')}' again to generate new credentials that include a device_id."


# Helper functions moved from matrix_utils to break circular dependency


def _create_ssl_context() -> ssl.SSLContext | None:
    """
    Create an SSLContext for Matrix client connections, preferring certifi's CA bundle when available.

    Returns:
        ssl.SSLContext | None: An SSLContext configured with certifi's CA file if certifi is present, otherwise the system default SSLContext. Returns None only if context creation fails.
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


def _cleanup_local_session_data() -> None:
    """
    Remove local Matrix session artifacts: credentials.json and any E2EE store directories.

    This cleans up the on-disk session state used by the Matrix client. It removes:
    - the credentials file at <base_dir>/credentials.json (if present), and
    - E2EE store directories: the default store dir returned by get_e2ee_store_dir()
      plus any user-configured overrides found in the loaded config under
      matrix.e2ee.store_path or matrix.encryption.store_path.

    Returns:
        bool: True if all targeted files/directories were removed successfully;
              False if any removal failed (for example due to permissions). The
              function makes a best-effort attempt and will still try all removals
              even if some fail.
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
) -> str | None:
    """
    Classify a Matrix-related exception and emit user-facing and logged messages.

    Determines whether the provided exception represents credential, network,
    server, or other errors (using known nio exception types or message inspection),
    chooses messages appropriate to the given context (verification vs non-verification),
    logs them at the specified level ("error" or "warning"), prints concise feedback
    for CLI users, and signals the exception was handled.

    Parameters:
        exception: The exception instance to classify and report.
        context: Short context string describing the operation (e.g., "Password verification",
            "Server logout"); used to select phrasing and to detect verification flows.
        log_level: Logging level to use; accepted values are "error" (default) or "warning".

    Returns:
        bool: Always returns True to indicate the exception was handled and reported.
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


async def logout_matrix_bot(password: str):
    """
    Log out the configured Matrix account (if any), verify credentials, and remove local session data.

    Performs an optional verification of the supplied Matrix password by performing a temporary login, attempts to log out the active server session (invalidating the access token), and removes local session artifacts (e.g., credentials.json and any E2EE store directories). If the stored credentials lack a user_id but include an access_token and homeserver, the function will try to fetch and persist the missing user_id before proceeding.

    Parameters:
        password (str): The Matrix account password used to verify the session before performing server logout.

    Returns:
        bool: True when local cleanup (and server logout, if attempted) completed successfully; False on failure.
        If the matrix-nio dependency is not available the function prints an error and returns False.

    Side effects:
        - May update credentials.json if the user_id is fetched.
        - Removes local session files and E2EE store directories when cleanup runs.
        - Performs network requests to the homeserver for verification and logout when credentials are complete.
    """

    # Import inside function to avoid circular imports
    from mmrelay.matrix_utils import (
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
            try:
                await temp_client.close()
            except Exception:
                # Ignore errors when closing client during logout
                pass

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
            await temp_client.close()

        # Now logout the main session
        logger.info("Logging out from Matrix server...")
        print("🚪 Logging out from Matrix server...")
        main_client = AsyncClient(homeserver, user_id, ssl=ssl_context)
        main_client.restore_login(
            user_id=user_id,
            device_id=device_id,
            access_token=access_token,
        )

        try:
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
            await main_client.close()

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
