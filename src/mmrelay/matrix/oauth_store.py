"""Protected, atomic OAuth records with nonblocking cross-process ownership."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, BinaryIO, TypeVar

from mmrelay.matrix.oauth import OAuthError, OAuthSession
from mmrelay.matrix.oauth_async import finish_task

_Result = TypeVar("_Result")
_MAX_RECORD_BYTES = 65536


async def _finish_io(function: Callable[[], _Result]) -> _Result:
    """Finish filesystem work before exposing cancellation to its caller."""
    task = asyncio.create_task(asyncio.to_thread(function))
    return await finish_task(task)


class OAuthStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._lock_path = self.path.with_name(self.path.name + ".oauth.lock")

    def _acquire(self) -> BinaryIO:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self._lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        stream = os.fdopen(descriptor, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                if self._lock_path.stat().st_size == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return stream
        except OSError:
            stream.close()
            raise OAuthError(
                "OAuth credentials are in use by another operation."
            ) from None

    @asynccontextmanager
    async def locked(self) -> AsyncIterator[OAuthStore]:
        task = asyncio.create_task(asyncio.to_thread(self._acquire))
        try:
            stream = await finish_task(task)
        except asyncio.CancelledError:
            stream = task.result()
            await _finish_io(stream.close)
            raise
        try:
            yield self
        finally:
            await _finish_io(stream.close)

    def load_sync(self) -> dict[str, Any] | None:
        """Read a bounded record; synchronous CLI dispatch need not start a loop."""
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(_MAX_RECORD_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(raw) > _MAX_RECORD_BYTES:
            raise OAuthError("OAuth credentials exceed the size limit.")
        try:
            record = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise OAuthError("OAuth credentials are not valid JSON.") from None
        if not isinstance(record, dict):
            raise OAuthError("OAuth credentials must be a JSON object.")
        return record

    async def load(self) -> dict[str, Any] | None:
        return await _finish_io(self.load_sync)

    async def save(self, session: OAuthSession) -> None:
        def write() -> None:
            record = json.dumps(session.credentials(), indent=2).encode("utf-8")
            if len(record) > _MAX_RECORD_BYTES:
                raise OAuthError("OAuth credentials exceed the size limit.")
            descriptor, name = tempfile.mkstemp(
                prefix=".oauth-credentials-", dir=self.path.parent
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(record)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, self.path)
                if os.name != "nt":
                    directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
            finally:
                try:
                    os.unlink(name)
                except FileNotFoundError:
                    pass

        await _finish_io(write)

    async def remove(self) -> None:
        await _finish_io(lambda: self.path.unlink(missing_ok=True))
