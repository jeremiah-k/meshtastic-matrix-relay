"""Tests for Matrix client construction policy."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

import mmrelay.matrix.client_config as client_config
import mmrelay.matrix_utils as matrix_utils


@dataclass(frozen=True)
class _MindroomConfig:
    store_sync_tokens: bool = False
    encryption_enabled: bool = False
    max_limit_exceeded: int = 10
    max_timeouts: int = 3
    replace_rotated_device_keys: bool = False


class _MutableNonDataclassConfig:
    def __init__(
        self,
        *,
        store_sync_tokens: bool = False,
        encryption_enabled: bool = False,
        max_limit_exceeded: int = 10,
        max_timeouts: int = 3,
    ) -> None:
        self.store_sync_tokens = store_sync_tokens
        self.encryption_enabled = encryption_enabled
        self.max_limit_exceeded = max_limit_exceeded
        self.max_timeouts = max_timeouts
        self.replace_rotated_device_keys = False


@dataclass(frozen=True)
class _LegacyConfig:
    store_sync_tokens: bool = False
    encryption_enabled: bool = False
    max_limit_exceeded: int = 10
    max_timeouts: int = 3


def test_e2ee_config_enables_rotated_device_key_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_config, "AsyncClientConfig", _MindroomConfig)

    config = matrix_utils.build_matrix_client_config(
        e2ee_enabled=True,
        max_limit_exceeded=0,
        max_timeouts=0,
    )

    assert config.encryption_enabled is True
    assert config.store_sync_tokens is True
    assert config.replace_rotated_device_keys is True
    assert config.max_limit_exceeded == 0
    assert config.max_timeouts == 0


def test_non_dataclass_provider_capability_is_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        client_config,
        "AsyncClientConfig",
        _MutableNonDataclassConfig,
    )

    config = matrix_utils.build_matrix_client_config(e2ee_enabled=True)

    assert config.replace_rotated_device_keys is True
    assert config.encryption_enabled is True


def test_plaintext_config_preserves_rotated_device_key_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_config, "AsyncClientConfig", _MindroomConfig)

    config = matrix_utils.build_matrix_client_config(e2ee_enabled=False)

    assert config.encryption_enabled is False
    assert config.replace_rotated_device_keys is False


def test_legacy_provider_config_remains_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_config, "AsyncClientConfig", _LegacyConfig)

    config = matrix_utils.build_matrix_client_config(e2ee_enabled=True)

    assert config.encryption_enabled is True
    assert config.store_sync_tokens is True
    assert not hasattr(config, "replace_rotated_device_keys")


def test_retry_limits_must_be_provided_together() -> None:
    with pytest.raises(ValueError, match="retry limits must be provided together"):
        matrix_utils.build_matrix_client_config(
            e2ee_enabled=True,
            max_limit_exceeded=0,
        )
