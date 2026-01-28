from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import time
from typing import Any, Awaitable, Callable

import meshtastic  # type: ignore[import-untyped]

import mmrelay.meshtastic_utils as meshtastic_utils
from mmrelay.constants.network import CONNECTION_TYPE_BLE
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
        """
        Initialize a MeshtasticBackend instance.

        Parameters:
            connect_fn (Callable[..., Any] | None): Optional function used to establish and return a Meshtastic client. If not provided, defaults to meshtastic_utils.connect_meshtastic.
            to_thread (Callable[..., Awaitable[Any]] | None): Optional helper to run blocking callables in a thread (e.g., asyncio.to_thread). If not provided, defaults to asyncio.to_thread.

        The constructor also initializes internal state used by the backend: the client reference, the user message callback placeholder, and a flag tracking whether a callback subscription has been registered.
        """
        self._connect_fn = connect_fn or meshtastic_utils.connect_meshtastic
        self._to_thread = to_thread or asyncio.to_thread
        self._client: Any | None = None
        self._message_callback: Callable[[RadioMessage], None] | None = None
        self._callback_registered = False

    @property
    def backend_name(self) -> str:
        """
        Provide the backend identifier used to distinguish this radio backend.

        Returns:
            The string "meshtastic" identifying this backend.
        """
        return "meshtastic"

    async def connect(self, config: dict[str, Any]) -> bool:
        """
        Establishes a Meshtastic client from the provided configuration and stores it for use.

        Parameters:
            config (dict[str, Any]): Application configuration; may include a "meshtastic" mapping with an optional "connection_type" key that influences how the connection is performed.

        Returns:
            bool: `true` if a Meshtastic client was created and stored, `false` otherwise.

        Side effects:
            Sets self._client and meshtastic_utils.meshtastic_client to the connected client on success, or clears them on failure.
        """
        connection_type = None
        meshtastic_cfg = config.get("meshtastic")
        if isinstance(meshtastic_cfg, dict):
            connection_type = meshtastic_cfg.get("connection_type")

        use_thread = True
        if (
            connection_type == CONNECTION_TYPE_BLE
            and self._connect_fn is meshtastic_utils.connect_meshtastic
        ):
            # Avoid nested thread/event-loop usage during BLE initialization.
            use_thread = False

        maybe_client = (
            self._to_thread(self._connect_fn, passed_config=config)
            if use_thread
            else self._connect_fn(passed_config=config)
        )
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
        """
        Close the active Meshtastic client connection with a guarded shutdown.

        If a Meshtastic client is present (either local or global), attempts a best-effort shutdown that enforces a 10-second timeout to avoid blocking application exit. Performs BLE-specific disconnect steps when the BLE interface is active, clears internal and global client/interface references, and logs warnings or errors if shutdown times out or fails.
        """
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

                    If a BLE interface is active, perform an explicit BLE disconnect to release the adapter; otherwise call the client's close method. Clears meshtastic_utils.meshtastic_client and self._client, and clears meshtastic_utils.meshtastic_iface when a BLE disconnect is performed. Does nothing if no client is present.
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
                except Exception:
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
        """
        Determine whether the Meshtastic backend currently has an active connection.

        Checks a global reconnecting flag and the configured client; if no client exists or a reconnect is in progress, reports not connected. If the client lacks an `is_connected` attribute, the function treats the client as connected; if `is_connected` is present and callable it is invoked, otherwise its truthiness is used.

        Returns:
            bool: `True` if the backend is considered connected, `False` otherwise.
        """
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
        return bool(is_conn)

    async def send_message(
        self,
        text: str,
        channel: int | None = None,
        destination_id: int | None = None,
        reply_to_id: int | str | None = None,
    ) -> Any:
        """
        Send a text message over the Meshtastic connection.

        Parameters:
            text (str): Message text to send.
            channel (int | None): Channel index to send on; defaults to 0 when omitted.
            destination_id (int | None): Destination node id; when omitted messages are broadcast.
            reply_to_id (int | str | None): If provided, send as a reply to this message id. A numeric string will be converted to an integer.

        Returns:
            Any: The underlying Meshtastic send result (e.g., acknowledgement or message object), or `None` if no client is available or an error occurred.
        """
        interface = self._client or meshtastic_utils.meshtastic_client
        if interface is None:
            backend_logger.error("No Meshtastic interface available for sending")
            return None

        try:
            # Use reply function if this is a reply
            if reply_to_id is not None:
                try:
                    reply_id_val = (
                        int(reply_to_id)
                        if isinstance(reply_to_id, str)
                        else reply_to_id
                    )
                except (ValueError, TypeError):
                    backend_logger.warning(
                        "Invalid reply_to_id '%s', sending as a regular message.",
                        reply_to_id,
                    )
                    # Fall through to send as regular message
                else:
                    # Import send_text_reply here to avoid circular imports
                    from mmrelay.meshtastic_utils import send_text_reply

                    return send_text_reply(
                        interface,
                        text=text,
                        reply_id=reply_id_val,
                        destinationId=destination_id or meshtastic.BROADCAST_ADDR,
                        channelIndex=channel if channel is not None else 0,
                    )

            # Regular message without reply (or fallback from invalid reply_to_id)
            send_kwargs: dict[str, Any] = {
                "channelIndex": channel if channel is not None else 0
            }
            if destination_id is not None:
                send_kwargs["destinationId"] = destination_id
            return interface.sendText(text, **send_kwargs)
        except Exception as e:
            backend_logger.error(
                f"Error sending message via Meshtastic backend: {e}", exc_info=True
            )
            return None

    def register_message_callback(
        self,
        callback: Callable[[RadioMessage], None],
    ) -> None:
        """
        Register a callback to receive converted RadioMessage objects for incoming Meshtastic packets.

        Subscribes to Meshtastic messages (once) and converts incoming packets into RadioMessage instances before invoking the provided callback.

        Parameters:
            callback (Callable[[RadioMessage], None]): Function called with each converted RadioMessage.
        """
        self._message_callback = callback

        if self._callback_registered:
            backend_logger.debug("Callback already registered, skipping")
            return

        # Subscribe to Meshtastic messages (pubsub supports multiple subscribers)
        from pubsub import pub  # type: ignore[import-untyped]

        # Wrapper to convert Meshtastic packet to RadioMessage
        def _packet_to_radio_message(packet: dict[str, Any], interface: Any) -> None:
            """
            Convert a Meshtastic receive packet into a RadioMessage and invoke the registered message callback.

            Processes the provided Meshtastic `packet` (expected to contain keys like `decoded`, `fromId`/`from`, `to`, `id`, `channel`) and the given `interface`, extracts message text (or a portnum-derived label), sender id/name, timestamp, channel, direct/destination status, message id, reply id, and meshnet name, constructs a RadioMessage with backend="meshtastic", and calls the outer-registered callback with that RadioMessage. The function returns immediately if no callback is registered or if `packet["decoded"]` is None.
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
                portnum_name = (
                    meshtastic_utils._get_portnum_name(portnum)
                    if portnum
                    else "unknown"
                )
                text = f"[{portnum_name}]"

            # Extract sender info
            from_id = packet.get("fromId") or packet.get("from", "")
            # Convert to string for slicing (from_id can be int), but pass original
            # to _get_node_display_name which accepts int | str
            from_id_str = str(from_id) if from_id else ""
            sender_name = meshtastic_utils._get_node_display_name(
                from_id,
                interface,
                fallback=f"Node {from_id_str[:8] if from_id_str else 'Unknown'}",
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
                    meshtastic_utils.config or {}, "meshnet_name", ""
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
                    int(to_id) if to_id and to_id != meshtastic.BROADCAST_ADDR else None
                ),
                message_id=message_id,
                reply_to_id=reply_to_id,
            )

            # Call user callback
            try:
                callback(radio_message)
            except Exception:
                backend_logger.exception("Error in radio message callback")

        # Subscribe to Meshtastic messages
        pub.subscribe(_packet_to_radio_message, "meshtastic.receive")
        self._callback_registered = True
        backend_logger.debug("Registered message callback for Meshtastic backend")

    def get_message_delay(self, config: dict[str, Any], default: float) -> float:
        """
        Retrieve configured Meshtastic message delay.

        Parameters:
            config (dict[str, Any]): Application configuration; may contain a "meshtastic" section with a "message_delay" value.
            default (float): Fallback delay in seconds used when configuration does not specify "meshtastic.message_delay".

        Returns:
            float: Message delay in seconds from configuration or provided default.
        """
        delay = config.get("meshtastic", {}).get("message_delay", default)
        if not isinstance(delay, (int, float)):
            return default
        return float(delay)

    def get_nodes(self) -> dict[str, Any]:
        """
        Retrieve the list of nodes known to the Meshtastic backend.

        Returns:
            dict: A dictionary of nodes, where keys are node identifiers and values are node objects.
                Node objects contain a "user" key with "id", "longName", and "shortName" fields.
        """
        client = self._client or meshtastic_utils.meshtastic_client
        if client and hasattr(client, "nodes"):
            return client.nodes
        return {}

    def get_client(self) -> Any:
        """
        Return the active Meshtastic client instance used by this backend.

        Returns:
            The Meshtastic client attached to this backend if present, otherwise the global meshtastic_utils.meshtastic_client; may be `None` when no client is available.
        """
        return self._client or meshtastic_utils.meshtastic_client
