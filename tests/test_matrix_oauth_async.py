"""Cancellation-draining contracts for the OAuth task joiner."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from mmrelay.matrix.oauth_async import finish_task


async def _failing_after(release: asyncio.Event) -> Any:
    await release.wait()
    raise ValueError("task failed")


async def _succeeding_after(release: asyncio.Event) -> str:
    await release.wait()
    return "too late"


@pytest.mark.asyncio
async def test_task_result_is_returned() -> None:
    release = asyncio.Event()
    task = asyncio.create_task(_succeeding_after(release))
    release.set()

    assert await finish_task(task) == "too late"


@pytest.mark.asyncio
async def test_task_failure_propagates_without_caller_cancellation() -> None:
    release = asyncio.Event()
    task = asyncio.create_task(_failing_after(release))
    release.set()

    with pytest.raises(ValueError, match="task failed"):
        await finish_task(task)


@pytest.mark.asyncio
async def test_recorded_cancellation_beats_a_late_task_failure() -> None:
    release = asyncio.Event()
    task = asyncio.create_task(_failing_after(release))
    outer = asyncio.create_task(finish_task(task))
    for _ in range(4):
        await asyncio.sleep(0)
    outer.cancel()
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await outer


@pytest.mark.asyncio
async def test_recorded_cancellation_beats_task_success() -> None:
    release = asyncio.Event()
    task = asyncio.create_task(_succeeding_after(release))
    outer = asyncio.create_task(finish_task(task))
    for _ in range(4):
        await asyncio.sleep(0)
    outer.cancel()
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await outer
