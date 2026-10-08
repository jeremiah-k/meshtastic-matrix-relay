"""Atomic records and cross-process ownership of rotating secrets."""

import asyncio
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from mmrelay.matrix.oauth import OAuthError, OAuthSession
from mmrelay.matrix.oauth_store import OAuthStore
from tests.oauth_helpers import session


@pytest.mark.asyncio
async def test_store_round_trip_with_owner_only_permissions(tmp_path: Path) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    async with store.locked():
        assert await store.load() is None
        await store.save(session())
        assert OAuthSession.parse(await store.load()) == session()
    if os.name != "nt":
        assert store.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_failed_replace_preserves_complete_credentials(tmp_path: Path) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    async with store.locked():
        await store.save(session())
        with patch(
            "mmrelay.matrix.oauth_store.os.replace", side_effect=OSError("disk failure")
        ):
            with pytest.raises(OSError):
                await store.save(session(access_token="test-different"))
        assert OAuthSession.parse(await store.load()) == session()
        assert not list(tmp_path.glob(".oauth-credentials-*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_oversized_save_preserves_readable_credentials(
    tmp_path: Path, existing: bool
) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    async with store.locked():
        if existing:
            await store.save(session())
        with pytest.raises(OAuthError, match="size limit"):
            await store.save(
                session(access_token="A" * 32500, refresh_token="R" * 32500)
            )
        assert await store.load() == (session().credentials() if existing else None)
        assert not list(tmp_path.glob(".oauth-credentials-*"))


@pytest.mark.asyncio
async def test_lock_contention_and_release_after_exception(tmp_path: Path) -> None:
    first = OAuthStore(tmp_path / "credentials.json")
    second = OAuthStore(first.path)
    with pytest.raises(ValueError, match="stop"):
        async with first.locked():
            with pytest.raises(OAuthError, match="in use"):
                async with second.locked():
                    pytest.fail("Concurrent owner acquired the store")
            raise ValueError("stop")
    async with second.locked():
        await second.save(session())


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b"[]", b"not json", b"x" * 65537])
async def test_malformed_records_fail_closed(tmp_path: Path, raw: bytes) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    store.path.write_bytes(raw)
    with pytest.raises(OAuthError):
        await store.load()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.no_global_mocks
async def test_repeated_cancellation_drains_write_under_lock(tmp_path: Path) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    entered = threading.Event()
    resume = threading.Event()
    original_replace = os.replace

    def blocked_replace(source, target):
        entered.set()
        if not resume.wait(5):
            raise TimeoutError("Test write was not released")
        original_replace(source, target)

    async def write() -> None:
        async with store.locked():
            await store.save(session())

    with patch("mmrelay.matrix.oauth_store.os.replace", side_effect=blocked_replace):
        task = asyncio.create_task(write())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            with pytest.raises(OAuthError, match="in use"):
                async with OAuthStore(store.path).locked():
                    pytest.fail("Write ownership was released before completion")
        finally:
            resume.set()
            with pytest.raises(asyncio.CancelledError):
                await task
    async with OAuthStore(store.path).locked():
        assert await store.load() == session().credentials()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.no_global_mocks
async def test_repeated_cancellation_closes_acquired_lock(tmp_path: Path) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    entered = threading.Event()
    resume = threading.Event()
    original_open = os.open

    def blocked_open(path, flags, mode=0o777):
        if str(path).endswith(".oauth.lock"):
            entered.set()
            if not resume.wait(5):
                raise TimeoutError("Test acquisition was not released")
        return original_open(path, flags, mode)

    async def acquire() -> None:
        async with store.locked():
            pytest.fail("Cancelled acquisition must not enter its body")

    with patch("mmrelay.matrix.oauth_store.os.open", side_effect=blocked_open):
        task = asyncio.create_task(acquire())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            resume.set()
            with pytest.raises(asyncio.CancelledError):
                await task
    async with OAuthStore(store.path).locked():
        assert await store.load() is None


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.no_global_mocks
async def test_cancelled_acquisition_preserves_cancellation_after_lock_error(
    tmp_path: Path,
) -> None:
    """A late acquisition failure must not mask the caller's cancellation."""
    store = OAuthStore(tmp_path / "credentials.json")
    entered = threading.Event()
    release = threading.Event()

    def rejected_acquire():
        entered.set()
        if not release.wait(5):
            raise TimeoutError("Test acquisition was not released")
        raise OAuthError("Lock acquisition failed after cancellation")

    async def acquire() -> None:
        async with store.locked():
            pytest.fail("Failed lock acquisition must not enter its body")

    with patch.object(store, "_acquire", side_effect=rejected_acquire):
        task = asyncio.create_task(acquire())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(0)
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
