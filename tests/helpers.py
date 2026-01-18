"""
Test helper utilities shared across test modules.

This module provides reusable test utilities to avoid code duplication
and improve maintainability of the test suite.
"""

import asyncio
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class InlineExecutorLoop:
    """
    Wrap an event loop and execute run_in_executor calls inline for tests.

    This shim simulates a running asyncio event loop for synchronous executor
    execution in tests, making tests deterministic by avoiding actual threading.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Store provided event loop for use by this wrapper.

        Parameters:
            loop (asyncio.AbstractEventLoop): The underlying event loop whose attributes
                and non-overridden behavior are delegated to this shim.
        """
        self._loop = loop

    def is_running(self) -> bool:
        """
        Report whether this executor loop should be treated as running.

        Returns:
            True if loop is considered running, False otherwise.
        """
        return True

    def run_in_executor(
        self, _executor: Any, func: Callable[..., T], *args: Any
    ) -> asyncio.Future[T]:
        """
        Execute a callable synchronously and return a Future resolved with its outcome.

        Parameters:
            _executor: Ignored executor placeholder (kept for compatibility with
                loop.run_in_executor signature).
            func (Callable[..., T]): The function to execute.
            *args: Positional arguments to pass to `func`.

        Returns:
            asyncio.Future[T]: A Future that contains `func`'s return value or
                exception raised by `func`.

        Notes:
            Any exception raised by `func` will be set on the returned Future,
            matching the semantics of loop.run_in_executor.
        """
        fut = self._loop.create_future()
        try:
            result = func(*args)
        except Exception as exc:
            fut.set_exception(exc)
        else:
            fut.set_result(result)
        return fut

    def __getattr__(self, name: str) -> Any:
        """
        Delegate attribute access to wrapped event loop.

        Parameters:
            name (str): Attribute name being accessed on this wrapper.

        Returns:
            The attribute value from underlying event loop corresponding to `name`.

        Raises:
            AttributeError: If attribute does not exist on the wrapped loop.
        """
        return getattr(self._loop, name)


def inline_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """
    Run given callable synchronously in the current thread and return its result.

    This helper is used in tests to replace asyncio.to_thread, avoiding
    actual threading for deterministic test behavior.

    Parameters:
        func (Callable[..., Any]): The function to execute.
        *args: Positional arguments to pass to `func`.
        **kwargs: Keyword arguments to pass to `func`.

    Returns:
        Any: The return value produced by calling `func(*args, **kwargs)`.
    """
    return func(*args, **kwargs)
