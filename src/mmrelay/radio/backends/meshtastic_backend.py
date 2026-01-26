from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import time
from typing import Any, Awaitable, Callable

from pubsub import pub  # type: ignore[import-untyped]

import mmrelay.meshtastic_utils as meshtastic_utils
from mmrelay.log_utils import get_logger
from mmrelay.radio.base_backend import BaseRadioBackend
from mmrelay.radio.message import RadioMessage

meshtastic_logger = meshtastic_utils.logger
backend_logger = get_logger(name="MeshtasticBackend")


class MeshtasticBackend(BaseRadioBackend):
    """
    Meshtastic backend adapter using existing meshtastic_utils helpers.

    Wraps Meshtastic connection, message sending, and callbacks
    to provide a standardized radio backend interface.
    """

    def __init__(
        self,
        connect_fn: Callable[..., Any] | None = None,
        to_thread: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._connect_fn = connect_fn or meshtastic_utils.connect_meshtastic
        self._to_thread = to_thread or asyncio.to_thread
        self._client: Any | None = None
        self._message_callback: Callable[[RadioMessage], None] | None = None
        self._callback_registered = False

    @property
    def backend_name(self) -> str:
        return "meshtastic"

    async def connect(self, config: dict[str, Any]) -> bool:
        maybe_client = self._to_thread(self._connect_fn, passed_config=config)
        client = (
            await maybe_client if inspect.isawaitable(maybe_client) else maybe_client
        )
        if client is None:
            self._client = None
            meshtastic_utils.meshtastic_client = None
            return False
        self._client = client
        meshtastic_utils.meshtastic_client = client
        return True

    async def disconnect(self) -> None:
        client = self._client or meshtastic_utils.meshtastic_client
        if not client:
            return

        def _close_meshtastic_client() -> None:
            """
            Close the Meshtastic client connection with timeout protection.
            """
            if not (self._client or meshtastic_utils.meshtastic_client):
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

                    If a BLE interface is the active client, perform an explicit BLE disconnect to release
                    the adapter; a plain close() can leave BlueZ stuck.
                    Clears meshtastic_utils.meshtastic_client (and meshtastic_utils.meshtastic_iface when applicable).
                    Does nothing if no client is present.
                    """
                    client_ref = self._client or meshtastic_utils.meshtastic_client
                    if client_ref:
                        if client_ref is meshtastic_utils.meshtastic_iface:
                            # BLE shutdown needs an explicit disconnect to release
                            # the adapter; a plain close() can leave BlueZ stuck.
                            meshtastic_utils._disconnect_ble_interface(
                                meshtastic_utils.meshtastic_iface,
                                reason="shutdown",
                            )
                            meshtastic_utils.meshtastic_iface = None
                        else:
                            client_ref.close()
                        meshtastic_utils.meshtastic_client = None
                        self._client = None

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
        client = self._client or meshtastic_utils.meshtastic_client
        if client is None:
            return False
        is_conn: Any = getattr(client, "is_connected", None)
        if is_conn is None:
            return True
        # Handle both callable methods and bool attributes
        if callable(is_conn):
            return bool(is_conn())
        else:
            # Attribute is a value, convert to bool
            return (
                bool(is_conn)
                if not isinstance(is_conn, bool)
                else bool(is_conn()) if callable(is_conn) else True
            )

    async def send_message(
        self,
        text: str,
        channel: int | None = None,
        destination_id: int | None = None,
        reply_to_id: int | str | None = None,
    ) -> Any:
        """
        Send a message via Meshtastic backend.

        Uses send_text_reply() if reply_to_id is provided, otherwise
        uses the interface's sendText() method.
        """
        interface = self._client or meshtastic_utils.meshtastic_client
        if interface is None:
            backend_logger.error("No Meshtastic interface available for sending")
            return None

        # Use reply function if this is a reply
        if reply_to_id is not None:
            # Import send_text_reply here to avoid circular imports
            from mmrelay.meshtastic_utils import send_text_reply

            return send_text_reply(
                interface,
                text=text,
                reply_id=(
                    int(reply_to_id) if isinstance(reply_to_id, str) else reply_to_id
                ),
                destinationId=destination_id or meshtastic.BROADCAST_ADDR,
                channelIndex=channel if channel is not None else 0,
            )
        else:
            # Regular message without reply
            return interface.sendText(
                text, channelIndex=channel if channel is not None else 0
            )

    def register_message_callback(
        self,
        callback: Callable[[RadioMessage], None],
    ) -> None:
        """
        Register a callback to be invoked when Meshtastic messages are received.

        Wraps the existing pubsub mechanism and converts Meshtastic packets
        to RadioMessage objects before calling the user callback.
        """
        self._message_callback = callback

        if self._callback_registered:
            backend_logger.debug("Callback already registered, skipping")
            return

        # Subscribe to Meshtastic messages if not already subscribed
        if not meshtastic_utils.subscribed_to_messages:
            from pubsub import pub  # type: ignore[import-untyped]

            # Wrapper to convert Meshtastic packet to RadioMessage
            def _packet_to_radio_message(
                packet: dict[str, Any], interface: Any
            ) -> None:
                """
                Convert incoming Meshtastic packet to RadioMessage and invoke callback.

                Extracts relevant fields from the Meshtastic packet and creates
                a RadioMessage object for the callback.
                """
                if callback is None:
                    return

                decoded = packet.get("decoded", {})
                if decoded is None:
                    return

                # Extract text content
                text = decoded.get("text", "")
                if not text and "portnum" in decoded:
                    # Non-text message, create description
                    portnum = decoded.get("portnum")
                    from mmrelay.constants.messages import _get_portnum_name

                    portnum_name = _get_portnum_name(portnum) if portnum else "unknown"
                    text = f"[{portnum_name}]"

                # Extract sender info
                from_id = packet.get("fromId") or packet.get("from", "")
                sender_name = meshtastic_utils._get_node_display_name(
                    from_id,
                    interface,
                    fallback=f"Node {from_id[:8] if from_id else 'Unknown'}",
                )

                # Extract message ID and reply ID
                message_id = packet.get("id")
                reply_to_id = decoded.get("replyId")

                # Determine if this is a direct message
                to_id = packet.get("to")
                is_direct = bool(to_id and to_id != meshtastic.BROADCAST_ADDR)

                # Extract channel
                channel = packet.get("channel")

                # Get meshnet name from config
                from mmrelay.config import get_meshtastic_config_value

                meshnet_name = (
                    get_meshtastic_config_value(
                        meshtastic_utils.config, "meshnet_name", ""
                    )
                    or "default"
                )

                # Create RadioMessage
                radio_message = RadioMessage(
                    text=text,
                    sender_id=str(from_id) if from_id else "unknown",
                    sender_name=sender_name,
                    timestamp=time.time(),
                    backend="meshtastic",
                    meshnet_name=meshnet_name,
                    channel=channel,
                    is_direct_message=is_direct,
                    destination_id=(
                        int(to_id)
                        if to_id and to_id != meshtastic.BROADCAST_ADDR
                        else None
                    ),
                    message_id=message_id,
                    reply_to_id=reply_to_id,
                )

                # Call user callback
                try:
                    callback(radio_message)
                except Exception as e:
                    backend_logger.exception(f"Error in radio message callback: {e}")

            # Subscribe to Meshtastic messages
            pub.subscribe(_packet_to_radio_message, "meshtastic.receive")
            meshtastic_utils.subscribed_to_messages = True
            self._callback_registered = True
            backend_logger.debug("Registered message callback for Meshtastic backend")

    def get_message_delay(self, config: dict[str, Any], default: float) -> float:
        return config.get("meshtastic", {}).get("message_delay", default)

    def get_client(self) -> Any:
        return self._client or meshtastic_utils.meshtastic_client
