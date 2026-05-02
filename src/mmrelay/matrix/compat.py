"""Compatibility detection for Matrix nio providers."""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass
from importlib import metadata
from typing import Literal

ProviderDistribution = Literal["matrix-nio", "mindroom-nio", "unknown"]
CryptoBackend = Literal["olm", "vodozemac", "unavailable", "unknown"]

_MATRIX_NIO_DIST = "matrix-nio"
_MINDROOM_NIO_DIST = "mindroom-nio"
_UNKNOWN_VERSION = "unknown"

_CAPABILITIES_CACHE: MatrixLibraryCapabilities | None = None


@dataclass(frozen=True)
class MatrixLibraryCapabilities:
    """Detected Matrix provider and crypto capabilities."""

    provider_name: str
    provider_version: str
    provider_distribution: ProviderDistribution
    crypto_backend: CryptoBackend
    encryption_available: bool
    store_available: bool
    sqlite_store_available: bool
    olm_available: bool
    vodozemac_available: bool
    nio_crypto_available: bool
    nio_crypto_encryption_enabled: bool | None
    nio_crypto_olm_device_available: bool
    recommended_e2ee_extra: str
    install_hint: str
    both_known_providers_installed: bool
    supports_stop_sync_forever: bool
    supports_thread_receipts: bool
    supports_authenticated_media: bool


def _distribution_version(distribution_name: str) -> str | None:
    try:
        return metadata.version(distribution_name)
    except metadata.PackageNotFoundError:
        return None


def _module_available(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
    except ImportError:
        return False
    return True


def _import_optional(module_name: str) -> object | None:
    try:
        return importlib.import_module(module_name)
    except ImportError:
        return None


def _detect_provider() -> tuple[str, str, ProviderDistribution, bool]:
    matrix_version = _distribution_version(_MATRIX_NIO_DIST)
    mindroom_version = _distribution_version(_MINDROOM_NIO_DIST)
    both_installed = matrix_version is not None and mindroom_version is not None

    if mindroom_version is not None and matrix_version is None:
        return "mindroom-nio", mindroom_version, "mindroom-nio", both_installed
    if matrix_version is not None and mindroom_version is None:
        return "matrix-nio", matrix_version, "matrix-nio", both_installed
    if mindroom_version is not None and matrix_version is not None:
        return (
            "multiple nio providers installed",
            f"matrix-nio={matrix_version}, mindroom-nio={mindroom_version}",
            "unknown",
            both_installed,
        )
    if _module_available("nio"):
        return "nio", _UNKNOWN_VERSION, "unknown", both_installed
    return "unavailable", _UNKNOWN_VERSION, "unknown", both_installed


def _e2ee_install_guidance(
    provider_distribution: ProviderDistribution,
    crypto_backend: CryptoBackend,
    both_known_providers_installed: bool,
) -> tuple[str, str]:
    if both_known_providers_installed:
        return (
            "one nio provider E2EE extra",
            "matrix-nio and mindroom-nio are both installed; uninstall one nio namespace owner before enabling E2EE",
        )
    if provider_distribution == "mindroom-nio" or crypto_backend == "vodozemac":
        extra = "mindroom-nio[e2e]"
        return (
            extra,
            f"install {extra} / vodozemac as a replacement provider, not alongside matrix-nio",
        )
    if provider_distribution == "matrix-nio" or crypto_backend == "olm":
        extra = "matrix-nio[e2e]"
        return extra, f"install {extra} / python-olm"
    return (
        "nio provider E2EE extra",
        "install the active nio provider's E2EE extra; no usable nio crypto backend was detected",
    )


def detect_matrix_capabilities() -> MatrixLibraryCapabilities:
    """Detect Matrix nio provider and optional E2EE capabilities."""

    provider_name, provider_version, provider_distribution, both_installed = (
        _detect_provider()
    )

    olm_available = _module_available("olm")
    vodozemac_available = _module_available("vodozemac")

    nio_crypto = _import_optional("nio.crypto")
    nio_store = _import_optional("nio.store")
    nio_client = _import_optional("nio")
    nio_api = _import_optional("nio.api")

    nio_crypto_available = nio_crypto is not None
    nio_crypto_olm_device_available = bool(
        nio_crypto is not None and hasattr(nio_crypto, "OlmDevice")
    )
    encryption_enabled_value = (
        getattr(nio_crypto, "ENCRYPTION_ENABLED", None)
        if nio_crypto is not None
        else None
    )
    nio_crypto_encryption_enabled = (
        encryption_enabled_value is True
        if encryption_enabled_value is not None
        else None
    )

    store_available = nio_store is not None
    sqlite_store_available = bool(
        nio_store is not None and hasattr(nio_store, "SqliteStore")
    )

    legacy_olm_ready = (
        olm_available and nio_crypto_olm_device_available and sqlite_store_available
    )
    vodozemac_ready = (
        vodozemac_available
        and nio_crypto_encryption_enabled is True
        and sqlite_store_available
    )

    if both_installed:
        crypto_backend: CryptoBackend = "unavailable"
        encryption_available = False
    elif provider_distribution == "mindroom-nio":
        if vodozemac_ready:
            crypto_backend = "vodozemac"
            encryption_available = True
        elif vodozemac_available or nio_crypto_encryption_enabled is True:
            crypto_backend = "vodozemac"
            encryption_available = False
        elif nio_crypto_available:
            crypto_backend = "unavailable"
            encryption_available = False
        else:
            crypto_backend = "unknown"
            encryption_available = False
    elif provider_distribution == "matrix-nio":
        if legacy_olm_ready:
            crypto_backend = "olm"
            encryption_available = True
        elif olm_available or nio_crypto_olm_device_available:
            crypto_backend = "olm"
            encryption_available = False
        elif nio_crypto_available:
            crypto_backend = "unavailable"
            encryption_available = False
        else:
            crypto_backend = "unknown"
            encryption_available = False
    elif vodozemac_ready:
        crypto_backend = "vodozemac"
        encryption_available = True
    elif legacy_olm_ready:
        crypto_backend = "olm"
        encryption_available = True
    elif vodozemac_available or nio_crypto_encryption_enabled is True:
        crypto_backend = "vodozemac"
        encryption_available = False
    elif olm_available or nio_crypto_olm_device_available:
        crypto_backend = "olm"
        encryption_available = False
    elif nio_crypto_available:
        crypto_backend = "unavailable"
        encryption_available = False
    else:
        crypto_backend = "unknown"
        encryption_available = False
    recommended_e2ee_extra, install_hint = _e2ee_install_guidance(
        provider_distribution, crypto_backend, both_installed
    )

    supports_stop_sync_forever = bool(
        nio_client is not None
        and hasattr(getattr(nio_client, "AsyncClient", object), "stop_sync_forever")
    )
    supports_thread_receipts = bool(
        nio_api is not None
        and hasattr(getattr(nio_api, "Api", object), "update_receipt_marker")
    )
    download = (
        getattr(getattr(nio_api, "Api", object), "download", None)
        if nio_api is not None
        else None
    )
    try:
        supports_authenticated_media = bool(
            download is not None
            and "allow_remote" in inspect.signature(download).parameters
        )
    except (TypeError, ValueError):
        supports_authenticated_media = False

    return MatrixLibraryCapabilities(
        provider_name=provider_name,
        provider_version=provider_version,
        provider_distribution=provider_distribution,
        crypto_backend=crypto_backend,
        encryption_available=encryption_available,
        store_available=store_available,
        sqlite_store_available=sqlite_store_available,
        olm_available=olm_available,
        vodozemac_available=vodozemac_available,
        nio_crypto_available=nio_crypto_available,
        nio_crypto_encryption_enabled=nio_crypto_encryption_enabled,
        nio_crypto_olm_device_available=nio_crypto_olm_device_available,
        recommended_e2ee_extra=recommended_e2ee_extra,
        install_hint=install_hint,
        both_known_providers_installed=both_installed,
        supports_stop_sync_forever=supports_stop_sync_forever,
        supports_thread_receipts=supports_thread_receipts,
        supports_authenticated_media=supports_authenticated_media,
    )


def get_matrix_capabilities() -> MatrixLibraryCapabilities:
    """Return cached Matrix capabilities for normal runtime checks."""

    global _CAPABILITIES_CACHE
    if _CAPABILITIES_CACHE is None:
        _CAPABILITIES_CACHE = detect_matrix_capabilities()
    return _CAPABILITIES_CACHE


def reset_matrix_capabilities_cache() -> None:
    """Reset cached Matrix capabilities, primarily for tests."""

    global _CAPABILITIES_CACHE
    _CAPABILITIES_CACHE = None


def format_e2ee_unavailable_message(
    capabilities: MatrixLibraryCapabilities | None = None,
) -> str:
    """Return a provider-aware E2EE dependency message."""

    detected = capabilities or get_matrix_capabilities()
    details = (
        f"provider={detected.provider_name} "
        f"version={detected.provider_version} "
        f"crypto_backend={detected.crypto_backend}"
    )
    return f"E2EE dependencies not installed ({detected.install_hint}; {details})"


def format_e2ee_install_command(
    capabilities: MatrixLibraryCapabilities | None = None,
) -> str:
    """Return actionable install guidance for the detected provider."""

    detected = capabilities or get_matrix_capabilities()
    if detected.both_known_providers_installed:
        return (
            "Uninstall either matrix-nio or mindroom-nio first; both provide the "
            "nio namespace and should not be installed together."
        )
    if detected.provider_distribution == "mindroom-nio":
        return "pipx install 'mmrelay[e2e]' (installs mindroom-nio[e2e] / vodozemac)"
    if detected.provider_distribution == "matrix-nio":
        return (
            "Install matrix-nio E2EE in a controlled replacement environment: "
            "pip install 'matrix-nio[e2e]==0.25.2'. "
            "Do not install mmrelay[e2e] (it uses mindroom-nio)."
        )
    return f"Install the active nio provider's E2EE extra ({detected.recommended_e2ee_extra})."
