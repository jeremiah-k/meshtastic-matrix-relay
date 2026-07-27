"""Matrix client configuration shared by authentication and startup flows."""

from __future__ import annotations

from dataclasses import replace

from nio import AsyncClientConfig

from mmrelay.log_utils import get_logger

__all__ = ["build_matrix_client_config"]

logger = get_logger(name="Matrix")


def build_matrix_client_config(
    *,
    e2ee_enabled: bool,
    max_limit_exceeded: int | None = None,
    max_timeouts: int | None = None,
) -> AsyncClientConfig:
    """
    Build a Matrix client configuration with MMRelay's E2EE trust policy.
    
    Parameters:
        e2ee_enabled (bool): Whether to enable end-to-end encryption.
        max_limit_exceeded (int | None): Maximum limit-exceeded retries.
        max_timeouts (int | None): Maximum timeout retries; must be provided
            together with max_limit_exceeded.
    
    Returns:
        AsyncClientConfig: The configured Matrix client settings.
    
    Raises:
        ValueError: If only one retry limit is provided.
    """
    if (max_limit_exceeded is None) != (max_timeouts is None):
        raise ValueError("Matrix retry limits must be provided together")

    if max_limit_exceeded is None:
        config = AsyncClientConfig(
            store_sync_tokens=True,
            encryption_enabled=e2ee_enabled,
        )
    else:
        config = AsyncClientConfig(
            max_limit_exceeded=max_limit_exceeded,
            max_timeouts=max_timeouts,
            store_sync_tokens=True,
            encryption_enabled=e2ee_enabled,
        )

    if e2ee_enabled and hasattr(config, "replace_rotated_device_keys"):
        try:
            setattr(config, "replace_rotated_device_keys", True)
        except (AttributeError, TypeError):
            # Preserve compatibility with immutable dataclass-style providers
            # without using dataclass internals as the capability check.
            try:
                config = replace(config, replace_rotated_device_keys=True)
            except (TypeError, ValueError):
                logger.warning(
                    "Matrix provider exposes rotated device-key recovery but its "
                    "client configuration could not be updated"
                )
                return config
        logger.debug(
            "Enabled rotated Matrix device-key recovery for MMRelay's TOFU E2EE policy"
        )
    return config
