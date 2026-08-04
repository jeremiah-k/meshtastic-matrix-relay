"""Small daemon-thread executor for blocking operations that may never return."""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Executor, Future
from typing import Any, Callable

_WorkItem = tuple[Future[Any], Callable[..., Any], tuple[Any, ...], dict[str, Any]]


class DaemonThreadExecutor(Executor):
    """Execute submitted callables on daemon worker threads.

    ``ThreadPoolExecutor`` deliberately uses non-daemon workers and CPython waits
    for those workers during interpreter shutdown. That is desirable for normal
    jobs, but not for best-effort BLE calls into BlueZ/DBus that can wedge below
    Python and never return. This executor keeps the familiar ``Future`` API while
    ensuring a stuck worker cannot prevent the relay process from exiting.

    The implementation intentionally uses only public ``concurrent.futures``
    primitives so it does not depend on private CPython thread-pool internals.
    """

    def __init__(
        self, max_workers: int = 1, *, thread_name_prefix: str = "daemon"
    ) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be greater than 0")
        self._max_workers = max_workers
        self._thread_name_prefix = thread_name_prefix
        self._work_queue: queue.Queue[_WorkItem | None] = queue.Queue()
        self._threads: set[threading.Thread] = set()
        self._lock = threading.Lock()
        self._shutdown = False
        self._thread_counter = 0

    def submit(  # type: ignore[override]
        self,
        fn: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Future[Any]:
        with self._lock:
            if self._shutdown:
                raise RuntimeError("cannot schedule new futures after shutdown")
            future: Future[Any] = Future()
            self._work_queue.put((future, fn, args, kwargs))
            self._start_worker_if_needed_locked()
            return future

    def _start_worker_if_needed_locked(self) -> None:
        alive_threads = {thread for thread in self._threads if thread.is_alive()}
        self._threads = alive_threads
        if len(alive_threads) >= self._max_workers:
            return

        self._thread_counter += 1
        worker = threading.Thread(
            target=self._worker,
            name=f"{self._thread_name_prefix}_{self._thread_counter}",
            daemon=True,
        )
        self._threads.add(worker)
        worker.start()

    def _worker(self) -> None:
        current = threading.current_thread()
        try:
            while True:
                work_item = self._work_queue.get()
                if work_item is None:
                    return

                future, fn, args, kwargs = work_item
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    result = fn(*args, **kwargs)
                except BaseException as exc:  # Future must preserve task failures.
                    future.set_exception(exc)
                else:
                    future.set_result(result)
        finally:
            with self._lock:
                self._threads.discard(current)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._lock:
            self._shutdown = True
            threads = list(self._threads)

            if cancel_futures:
                while True:
                    try:
                        work_item = self._work_queue.get_nowait()
                    except queue.Empty:
                        break
                    if work_item is not None:
                        work_item[0].cancel()

            # Queue an exit signal for every currently live worker on every call.
            # A repeated shutdown(cancel_futures=True) may have drained sentinels
            # left by an earlier non-waiting shutdown.
            for _ in threads:
                self._work_queue.put(None)

        if wait:
            for thread in threads:
                thread.join()
