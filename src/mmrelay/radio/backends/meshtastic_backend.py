from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
from typing import Any, Awaitable, Callable

import mmrelay.meshtastic_utils as meshtastic_utils
from mmrelay.radio.base_backend import BaseRadioBackend

meshtastic_logger = meshtastic_utils.logger


class MeshtasticBackend(BaseRadioBackend):
    """Meshtastic backend adapter using existing meshtastic_utils helpers."""

    def __init__(
        self,
        connect_fn: Callable[..., Any] | None = None,
        to_thread: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._connect_fn = connect_fn or meshtastic_utils.connect_meshtastic
        self._to_thread = to_thread or asyncio.to_thread

    @property
    def backend_name(self) -> str:
        return "meshtastic"

    async def connect(self, config: dict[str, Any]) -> bool:
        maybe_client = self._to_thread(self._connect_fn, passed_config=config)
        client = (
            await maybe_client if inspect.isawaitable(maybe_client) else maybe_client
        )
        if client is None:
            meshtastic_utils.meshtastic_client = None
            return False
        if client is not None and meshtastic_utils.meshtastic_client is None:
            meshtastic_utils.meshtastic_client = client
        return True

    async def disconnect(self) -> None:
        if not meshtastic_utils.meshtastic_client:
            return

        def _close_meshtastic_client() -> None:
            """
            Close the Meshtastic client connection with timeout protection.
            """
            if not meshtastic_utils.meshtastic_client:
                return

            meshtastic_logger.info("Closing Meshtastic client...")
            try:
                # Timeout wrapper to prevent infinite hanging during shutdown
                # The meshtastic library can sometimes hang indefinitely during close()
                # operations, especially with BLE connections. This timeout ensures
                # the application can shut down gracefully within 10 seconds.

                def _close_meshtastic() -> None:
                    """
                    Close and clean up the active Meshtastic client connection.

                    If a BLE interface is the active client, perform an explicit BLE disconnect to release the adapter.
                    Clears meshtastic_utils.meshtastic_client (and meshtastic_utils.meshtastic_iface when applicable).
                    Does nothing if no client is present.
                    """
                    if meshtastic_utils.meshtastic_client:
                        if (
                            meshtastic_utils.meshtastic_client
                            is meshtastic_utils.meshtastic_iface
                        ):
                            # BLE shutdown needs an explicit disconnect to release
                            # the adapter; a plain close() can leave BlueZ stuck.
                            meshtastic_utils._disconnect_ble_interface(
                                meshtastic_utils.meshtastic_iface,
                                reason="shutdown",
                            )
                            meshtastic_utils.meshtastic_iface = None
                        else:
                            meshtastic_utils.meshtastic_client.close()
                        meshtastic_utils.meshtastic_client = None

                # Avoid the context manager here: __exit__ would wait for the
                # worker thread and could block forever if BLE shutdown hangs,
                # negating the timeout protection.
                executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                future = executor.submit(_close_meshtastic)
                close_timed_out = False
                try:
                    future.result(timeout=10.0)  # 10-second timeout
                except concurrent.futures.TimeoutError:
                    close_timed_out = True
                    meshtastic_logger.warning(
                        "Meshtastic client close timed out - may cause notification errors"
                    )
                    # Best-effort cancellation; the underlying close may be
                    # stuck in BLE/DBus, but we cannot block shutdown.
                    future.cancel()
                except Exception:  # noqa: BLE001 - shutdown must keep going
                    meshtastic_logger.exception(
                        "Unexpected error during Meshtastic client close"
                    )
                else:
                    meshtastic_logger.info("Meshtastic client closed successfully")
                finally:
                    if not future.done():
                        if not close_timed_out:
                            meshtastic_logger.warning(
                                "Meshtastic client close timed out - may cause notification errors"
                            )
                        future.cancel()
                    try:
                        # Do not wait for shutdown; if close hangs we still
                        # want the process to exit promptly.
                        executor.shutdown(wait=False, cancel_futures=True)
                    except TypeError:
                        # cancel_futures is unsupported on older Python versions.
                        executor.shutdown(wait=False)
            except concurrent.futures.TimeoutError:
                meshtastic_logger.warning(
                    "Meshtastic client close timed out - forcing shutdown"
                )
            except Exception as e:
                meshtastic_logger.error(
                    f"Unexpected error during Meshtastic client close: {e}",
                    exc_info=True,
                )

        maybe_result = self._to_thread(_close_meshtastic_client)
        if inspect.isawaitable(maybe_result):
            await maybe_result

    def is_connected(self) -> bool:
        if meshtastic_utils.reconnecting:
            return False
        client = meshtastic_utils.meshtastic_client
        if client is None:
            return False
        is_conn = getattr(client, "is_connected", None)
        if is_conn is None:
            return True
        return is_conn() if callable(is_conn) else bool(is_conn)

    def get_client(self) -> Any:
        return meshtastic_utils.meshtastic_client
