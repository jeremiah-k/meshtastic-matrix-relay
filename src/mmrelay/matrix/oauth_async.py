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
        except Exception:
            # The task failed while being drained. Once the caller was
            # cancelled, report the cancellation instead of its failure.
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    try:
        result = task.result()
    except asyncio.CancelledError:
        raise
    except Exception:
        if cancelled:
            raise asyncio.CancelledError from None
        raise
    if cancelled:
        raise asyncio.CancelledError
    return result
