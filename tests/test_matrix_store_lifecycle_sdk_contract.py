"""Real nio crypto-store lifecycle at MMRelay's client boundary.

Ports the no-homeserver crypto-store lifecycle contract from the MEDRE
windows-e2ee-contract recipe: a real AsyncClient built with MMRelay's
production constructor arguments persists its Olm identity across a
close/reopen cycle, and closing the client closes the crypto database.
The real SDK runs in an isolated interpreter because the in-process
test suite mocks ``nio``.

On platforms without fcntl, mindroom-nio 1.1.x store databases cannot
open at all (POSIX file lease); the contract pins that failure mode too,
so the windows-latest CI matrix asserts the exact SDK behavior of each
arm of the pinned version split instead of assuming one outcome.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from tests._matrix_sdk_contract_helpers import run_sdk_script, skip_unless_e2ee_ready

# Kept module-level so tests/test_matrix_sdk_contract_enforcement.py can
# monkeypatch the runner per contract module.
_run_sdk_script = run_sdk_script


def _skip_unless_e2ee_ready() -> None:
    """Skip on capability: the e2e extra with an enabled crypto backend must be installed."""
    skip_unless_e2ee_ready(_run_sdk_script)


# Mirrors the store-lifecycle script's own exit code for the pinned
# "store backend cannot open without fcntl" SDK contract.
_STORE_LEASE_UNAVAILABLE_EXIT = 42

# Mirrors the constructor arguments MMRelay's
# ``mmrelay.matrix.auth._initialize_matrix_client`` passes for an E2EE
# client (store_path + encryption_enabled AsyncClientConfig). The pinned
# nio client's ``close()`` releases the HTTP session but leaves the
# crypto database open; the client owner closes the store, so this
# script closes it explicitly and pins that seam, then proves the Olm
# identity persists across a close/reopen cycle.
#
# mindroom-nio 1.1.x wraps the store database in a POSIX file lease
# (fcntl). On platforms without fcntl the store cannot open at all, so
# the script also pins that outcome: it exits 42 (STORE_LEASE_UNAVAILABLE)
# when ``restore_login`` fails with the fcntl lease error while fcntl is
# unimportable. Exit 0 remains the full-lifecycle contract, including on
# lease-free nio builds (0.40.0) running without fcntl.
_STORE_LIFECYCLE_SCRIPT = """
import asyncio
import sys

from nio import AsyncClient, AsyncClientConfig
from nio.exceptions import LocalProtocolError

HOMESERVER = "https://example.org"
USER_ID = "@relay:example.org"
DEVICE_ID = "CONTRACT"

STORE_LEASE_UNAVAILABLE = 42

fcntl_importable = False
try:
    import fcntl  # noqa: F401
    fcntl_importable = True
except ImportError:
    pass


def make_client(store_path):
    config = AsyncClientConfig(
        max_limit_exceeded=0,
        max_timeouts=0,
        store_sync_tokens=True,
        encryption_enabled=True,
    )
    if hasattr(config, "replace_rotated_device_keys"):
        try:
            config.replace_rotated_device_keys = True
        except (AttributeError, TypeError):
            pass
    return AsyncClient(
        homeserver=HOMESERVER,
        user=USER_ID,
        device_id=DEVICE_ID,
        store_path=str(store_path),
        config=config,
    )


async def close_owned_client(client):
    await client.close()
    database = getattr(getattr(client, "store", None), "database", None)
    if database is not None and not database.is_closed():
        database.close()


def restore_login_or_pin_lease_error(client):
    try:
        client.restore_login(USER_ID, DEVICE_ID, "contract-access-token")
    except LocalProtocolError as error:
        if fcntl_importable or "fcntl" not in str(error):
            raise
        print(f"STORE-BACKEND-UNOPENABLE: {error}")
        raise SystemExit(STORE_LEASE_UNAVAILABLE)


async def main(store_path):
    client = make_client(store_path)
    try:
        restore_login_or_pin_lease_error(client)
        identity_keys = dict(client.olm.account.identity_keys)
        assert identity_keys["ed25519"] and identity_keys["curve25519"]
        database = client.store.database
        assert not database.is_closed()
    finally:
        await close_owned_client(client)
    assert database is not None and database.is_closed(), (
        "MMRelay released the client with its crypto database open"
    )

    reopened = make_client(store_path)
    try:
        restore_login_or_pin_lease_error(reopened)
        assert dict(reopened.olm.account.identity_keys) == identity_keys, (
            "crypto store did not preserve the Olm identity across restart"
        )
        reopened_database = reopened.store.database
    finally:
        await close_owned_client(reopened)
    assert reopened_database.is_closed()


raise SystemExit(asyncio.run(main(sys.argv[1])) or 0)
"""


def test_client_close_closes_real_crypto_database(tmp_path: Path) -> None:
    """A production-shaped E2EE client persists its identity and closes its store."""
    _skip_unless_e2ee_ready()

    result = _run_sdk_script(_STORE_LIFECYCLE_SCRIPT, str(tmp_path))

    output = result.stderr or result.stdout
    if result.returncode == _STORE_LEASE_UNAVAILABLE_EXIT:
        # Pinned SDK contract for lease-backed stores (mindroom-nio 1.1.x):
        # without fcntl the store cannot open at all. This outcome is only
        # legitimate on platforms where fcntl does not exist.
        assert (
            importlib.util.find_spec("fcntl") is None
        ), "restore_login hit the fcntl lease error although fcntl is importable"
        assert "STORE-BACKEND-UNOPENABLE" in result.stdout, output
        return
    assert result.returncode == 0, output
