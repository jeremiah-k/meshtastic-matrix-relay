"""Drain owned OAuth work before propagating cancellation."""

from __future__ import annotations

import asyncio
from typing import TypeVar

_Result = TypeVar("_Result")


async def finish_task(task: asyncio.Task[_Result]) -> _Result:
    """Join a task through repeated cancellation without cancelling its work."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result
