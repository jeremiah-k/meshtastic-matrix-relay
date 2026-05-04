import asyncio
import inspect
import math
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

import mmrelay.meshtastic_utils as facade
from mmrelay.constants.network import DEFAULT_PLUGIN_TIMEOUT_SECS

__all__ = [
    "_resolve_plugin_result",
    "_resolve_plugin_timeout",
    "_run_meshtastic_plugins",
]


def _resolve_plugin_timeout(
    cfg: dict[str, Any] | None, default: float = DEFAULT_PLUGIN_TIMEOUT_SECS
) -> float:
    """
    Resolve the plugin timeout value from the configuration.

    Reads `meshtastic.plugin_timeout` from `cfg` and returns it as a positive float. If the value is missing, cannot be converted to a number, or is not greater than 0, the provided `default` is returned and a warning is logged.

    Parameters:
        cfg (dict | None): Configuration mapping that may contain a "meshtastic" section with a "plugin_timeout" value.
        default (float): Fallback timeout in seconds used when `cfg` does not provide a valid value.

    Returns:
        float: A positive timeout in seconds.
    """

    raw_value = default
    if isinstance(cfg, dict):
        try:
            raw_value = cfg.get("meshtastic", {}).get("plugin_timeout", default)
        except AttributeError:
            raw_value = default

    try:
        if isinstance(raw_value, bool):
            raise TypeError("boolean timeout")
        timeout = float(raw_value)
        if timeout > 0 and math.isfinite(timeout):
            return timeout
    except (TypeError, ValueError, OverflowError):
        pass

    facade.logger.warning(
        "Invalid meshtastic.plugin_timeout value %r; using %.1fs fallback.",
        raw_value,
        default,
    )
    return default


def _resolve_plugin_result(
    handler_result: Any,
    plugin: Any,
    plugin_timeout: float,
    loop: asyncio.AbstractEventLoop,
) -> bool:
    """
    Resolve a plugin handler result to a boolean, handling async timeouts and bad awaitables.

    Returns True when the plugin should be treated as handled, False otherwise.
    """
    if not inspect.iscoroutine(handler_result) and not inspect.isawaitable(
        handler_result
    ):
        return bool(handler_result)

    result_future = facade._submit_coro(handler_result, loop=loop)
    if result_future is None:
        facade.logger.warning(
            "Plugin %s returned no awaitable; skipping.", plugin.plugin_name
        )
        return False
    try:
        return bool(facade._wait_for_result(result_future, plugin_timeout, loop=loop))
    except (asyncio.TimeoutError, FuturesTimeoutError) as exc:
        facade.logger.warning(
            "Plugin %s did not respond within %ss: %s",
            plugin.plugin_name,
            plugin_timeout,
            exc,
        )
        return True


def _run_meshtastic_plugins(
    *,
    packet: dict[str, Any],
    formatted_message: str | None,
    longname: str | None,
    meshnet_name: str | None,
    loop: asyncio.AbstractEventLoop,
    cfg: dict[str, Any] | None,
    use_keyword_args: bool = False,
    log_with_portnum: bool = False,
    portnum: Any | None = None,
) -> bool:
    """
    Invoke Meshtastic plugins and return True when a plugin handles the message.
    """
    from mmrelay.plugin_loader import load_plugins

    plugins = load_plugins()
    plugin_timeout = facade._resolve_plugin_timeout(
        cfg, default=DEFAULT_PLUGIN_TIMEOUT_SECS
    )

    found_matching_plugin = False
    for plugin in plugins:
        if not found_matching_plugin:
            try:
                if use_keyword_args:
                    handler_result = plugin.handle_meshtastic_message(
                        packet,
                        formatted_message=formatted_message,
                        longname=longname,
                        meshnet_name=meshnet_name,
                    )
                else:
                    handler_result = plugin.handle_meshtastic_message(
                        packet,
                        formatted_message,
                        longname,
                        meshnet_name,
                    )

                found_matching_plugin = facade._resolve_plugin_result(
                    handler_result,
                    plugin,
                    plugin_timeout,
                    loop,
                )

                if found_matching_plugin:
                    if log_with_portnum:
                        facade.logger.debug(
                            "Processed %s with plugin %s",
                            portnum,
                            plugin.plugin_name,
                        )
                    else:
                        facade.logger.debug(
                            "Processed by plugin %s", plugin.plugin_name
                        )
            except Exception:
                facade.logger.exception("Plugin %s failed", plugin.plugin_name)
                # Continue processing other plugins

    return found_matching_plugin
