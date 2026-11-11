"""Windows OAuth ownership releases byte-range locks before closing handles."""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

from mmrelay.matrix.oauth_store import OAuthStore


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["success", "error", "cancel", "cancel_acquisition"]
)
@pytest.mark.parametrize("unlock_fails", [False, True])
async def test_windows_region_is_unlocked_on_every_exit(
    tmp_path, monkeypatch, outcome, unlock_fails
):
    import mmrelay.matrix.oauth_store as module

    calls = []

    def locking(fd, mode, size):
        calls.append((fd, mode, os.lseek(fd, 0, os.SEEK_CUR), size))
        os.fstat(fd)
        if mode == 2 and unlock_fails:
            raise OSError("unlock unavailable")

    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setitem(
        sys.modules, "msvcrt", SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=locking)
    )
    finish = module.finish_task
    first = True

    async def cancel_after_acquisition(task):
        nonlocal first
        result = await finish(task)
        if first:
            first = False
            raise asyncio.CancelledError
        return result

    if outcome == "cancel_acquisition":
        monkeypatch.setattr(module, "finish_task", cancel_after_acquisition)

    async def operation():
        async with OAuthStore(tmp_path / "credentials.json").locked():
            if outcome == "error":
                raise ValueError("protected operation")
            if outcome == "cancel":
                raise asyncio.CancelledError
            assert outcome != "cancel_acquisition"

    if outcome == "success":
        await operation()
    else:
        with pytest.raises(
            ValueError if outcome == "error" else asyncio.CancelledError
        ):
            await operation()
    assert [(mode, offset, size) for _, mode, offset, size in calls] == [
        (1, 0, 1),
        (2, 0, 1),
    ]
    with pytest.raises(OSError):
        os.fstat(calls[0][0])
