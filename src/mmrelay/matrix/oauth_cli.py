"""Operator-facing native OAuth login and passwordless revocation."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

from mmrelay.matrix.oauth import DevicePrompt, OAuthClient, OAuthError, OAuthSession
from mmrelay.matrix.oauth_async import finish_task
from mmrelay.matrix.oauth_store import OAuthStore


class _ConfigPathArgsView:
    """Config-path view of parsed arguments for the config helpers' protocol."""

    config: str | None

    def __init__(self, args: argparse.Namespace) -> None:
        self.config: str | None = getattr(args, "config", None)


def credential_store(
    args: argparse.Namespace, config: dict[str, Any] | None = None
) -> OAuthStore:
    """Use the same ordered credentials locations as relay startup."""
    from mmrelay.config import (
        get_config_paths,
        get_credentials_search_paths,
        get_explicit_credentials_path,
        load_config_silently,
    )
    from mmrelay.paths import get_credentials_path

    # The config helpers only read ``config`` from the parsed arguments.
    path_args = _ConfigPathArgsView(args)
    if config is None:
        config = load_config_silently(path_args)
    explicit = get_explicit_credentials_path(config)
    paths = get_credentials_search_paths(
        explicit_path=explicit, config_paths=get_config_paths(path_args)
    )
    for candidate in paths:
        if Path(candidate).exists():
            return OAuthStore(candidate)
    target = (
        Path(os.path.expandvars(explicit)).expanduser()
        if explicit
        else get_credentials_path()
    )
    explicit_directory = explicit is not None and (
        explicit.endswith(os.sep)
        or (os.altsep is not None and explicit.endswith(os.altsep))
    )
    if target.is_dir() or explicit_directory:
        target /= "credentials.json"
    return OAuthStore(target)


def display_prompt(prompt: DevicePrompt) -> None:
    print("Open this address in a browser on a trusted device:")
    print(prompt.verification_uri)
    print(f"Enter code: {prompt.user_code}")
    print("Approve only the MMRelay login you started. Do not share the code.")


async def login(
    homeserver: str,
    store: OAuthStore,
    *,
    expected_user_id: str | None = None,
    protocol: OAuthClient | None = None,
) -> OAuthSession:
    protocol = protocol if protocol is not None else OAuthClient()
    async with store.locked():
        if await store.load() is not None:
            raise OAuthError(
                "Credentials already exist. Stop the relay and log out before "
                "creating another session; in-place OAuth conversion is unsupported."
            )
        session = await protocol.authorize_device(
            homeserver, display_prompt, expected_user_id=expected_user_id
        )
        try:
            await store.save(session)
        except (OSError, OAuthError):
            # Persistence failure must not leave a usable remote login orphaned.
            try:
                await finish_task(asyncio.create_task(protocol.revoke(session)))
            except OAuthError:
                print(
                    "Could not revoke the unsaved session; revoke MMRelay in your account settings."
                )
            raise
    return session


async def logout(store: OAuthStore, protocol: OAuthClient | None = None) -> None:
    protocol = protocol if protocol is not None else OAuthClient()
    async with store.locked():
        record = await store.load()
        if record is None:
            raise OAuthError("OAuth credentials were not found.")
        await protocol.revoke(OAuthSession.parse(record))
        # Retain the identity store: revocation is not an encryption-key reset.
        await store.remove()


def handle_login(args: argparse.Namespace) -> int:
    if getattr(args, "password", None) is not None or getattr(
        args, "reset_cross_signing", False
    ):
        print("OAuth login does not accept --password or --reset-cross-signing.")
        return 1
    username = getattr(args, "username", None)
    if username is not None and (not username.startswith("@") or ":" not in username):
        print("Use a full Matrix ID with OAuth --username, such as @bot:example.com.")
        return 1
    try:
        homeserver = getattr(args, "homeserver", None)
        if homeserver is None:
            homeserver = input("Matrix homeserver HTTPS URL: ").strip()
        store = credential_store(args)
        session = asyncio.run(login(homeserver, store, expected_user_id=username))
        print(
            f"Authenticated MMRelay as {session.user_id} with device {session.device_id}."
        )
        print(f"Credentials saved: {store.path}")
        print(
            "OAuth login does not complete encryption verification. Encrypted rooms require a trusted device; see docs/MATRIX_OAUTH.md."
        )
        return 0
    except (KeyboardInterrupt, EOFError):
        print("OAuth authentication cancelled.")
        return 1
    except OAuthError as exc:
        print(f"OAuth authentication failed: {exc}")
        return 1
    except (OSError, ValueError, TypeError):
        # Non-protocol exceptions are not guaranteed to be operator-safe.
        print(
            "OAuth authentication failed. Check server support, connectivity, and credentials-file ownership; password login was not attempted."
        )
        return 1


def handle_logout(args: argparse.Namespace, store: OAuthStore) -> int:
    print(
        "Revoke the MMRelay OAuth session and remove its credentials. Encryption keys are retained."
    )
    try:
        if getattr(args, "password", None) is not None:
            print("OAuth logout does not require or accept a password.")
            return 1
        if not getattr(args, "yes", False) and not input(
            "Revoke this session? (y/N): "
        ).strip().lower().startswith("y"):
            print("Logout cancelled.")
            return 0
        asyncio.run(logout(store))
        print("OAuth session revoked; credentials removed. Encryption keys retained.")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("OAuth logout cancelled.")
        return 1
    except OAuthError as exc:
        print(f"OAuth revocation failed: {exc}")
        return 1
    except (OSError, ValueError, TypeError):
        print(
            "OAuth revocation failed. Credentials were retained unless remote revocation succeeded and local removal failed; revoke MMRelay through your account settings if necessary."
        )
        return 1


def existing_oauth_store(
    args: argparse.Namespace, config: dict[str, Any] | None = None
) -> OAuthStore | None:
    store = credential_store(args, config)
    record: dict[str, Any] | None = store.load_sync()
    return store if record is not None and record.get("auth_type") == "oauth" else None
