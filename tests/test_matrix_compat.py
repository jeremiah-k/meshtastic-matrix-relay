from types import SimpleNamespace

import pytest

from mmrelay.matrix import compat


@pytest.fixture(autouse=True)
def reset_capability_cache():
    compat.reset_matrix_capabilities_cache()
    yield
    compat.reset_matrix_capabilities_cache()


def _patch_detection(monkeypatch, distributions, modules):
    def fake_version(distribution_name):
        if distribution_name in distributions:
            return distributions[distribution_name]
        raise compat.metadata.PackageNotFoundError(distribution_name)

    def fake_import_module(module_name):
        if module_name in modules:
            module = modules[module_name]
            if isinstance(module, BaseException):
                raise module
            return module
        raise ImportError(f"No module named {module_name!r}")

    monkeypatch.setattr(compat.metadata, "version", fake_version)
    monkeypatch.setattr(compat.importlib, "import_module", fake_import_module)


def test_detects_matrix_nio_olm_backend(monkeypatch):
    _patch_detection(
        monkeypatch,
        {"matrix-nio": "0.25.2"},
        {
            "nio": SimpleNamespace(AsyncClient=type("AsyncClient", (), {})),
            "nio.crypto": SimpleNamespace(OlmDevice=object),
            "nio.store": SimpleNamespace(SqliteStore=object),
            "olm": SimpleNamespace(),
        },
    )

    capabilities = compat.detect_matrix_capabilities()

    assert capabilities.provider_distribution == "matrix-nio"
    assert capabilities.crypto_backend == "olm"
    assert capabilities.encryption_available is True
    assert capabilities.recommended_e2ee_extra == "matrix-nio[e2e]"
    assert "python-olm" in capabilities.install_hint


def test_detects_matrix_nio_without_olm(monkeypatch):
    _patch_detection(
        monkeypatch,
        {"matrix-nio": "0.25.2"},
        {
            "nio": SimpleNamespace(AsyncClient=type("AsyncClient", (), {})),
            "nio.crypto": SimpleNamespace(OlmDevice=object),
            "nio.store": SimpleNamespace(SqliteStore=object),
        },
    )

    capabilities = compat.detect_matrix_capabilities()

    assert capabilities.provider_distribution == "matrix-nio"
    assert capabilities.encryption_available is False
    assert "python-olm" in compat.format_e2ee_unavailable_message(capabilities)


def test_detects_mindroom_vodozemac_backend(monkeypatch):
    _patch_detection(
        monkeypatch,
        {"mindroom-nio": "0.25.2"},
        {
            "nio": SimpleNamespace(AsyncClient=type("AsyncClient", (), {})),
            "nio.crypto": SimpleNamespace(
                ENCRYPTION_ENABLED=True,
                OlmDevice=object,
            ),
            "nio.store": SimpleNamespace(SqliteStore=object),
            "vodozemac": SimpleNamespace(),
        },
    )

    capabilities = compat.detect_matrix_capabilities()

    assert capabilities.provider_distribution == "mindroom-nio"
    assert capabilities.crypto_backend == "vodozemac"
    assert capabilities.encryption_available is True
    assert capabilities.recommended_e2ee_extra == "mindroom-nio[e2e]"
    assert "vodozemac" in capabilities.install_hint


def test_detects_mindroom_without_vodozemac(monkeypatch):
    _patch_detection(
        monkeypatch,
        {"mindroom-nio": "0.25.2"},
        {
            "nio": SimpleNamespace(AsyncClient=type("AsyncClient", (), {})),
            "nio.crypto": SimpleNamespace(ENCRYPTION_ENABLED=False),
            "nio.store": SimpleNamespace(),
        },
    )

    capabilities = compat.detect_matrix_capabilities()

    assert capabilities.provider_distribution == "mindroom-nio"
    assert capabilities.encryption_available is False
    assert "vodozemac" in compat.format_e2ee_unavailable_message(capabilities)
    assert "python-olm" not in compat.format_e2ee_unavailable_message(capabilities)
    assert "mindroom-nio[e2e]" in compat.format_e2ee_install_command(capabilities)
    assert "alongside matrix-nio" in compat.format_e2ee_install_command(capabilities)


def test_reports_multiple_known_provider_distributions(monkeypatch):
    _patch_detection(
        monkeypatch,
        {"matrix-nio": "0.25.2", "mindroom-nio": "0.25.2"},
        {"nio": SimpleNamespace(AsyncClient=type("AsyncClient", (), {}))},
    )

    capabilities = compat.detect_matrix_capabilities()

    assert capabilities.provider_distribution == "unknown"
    assert capabilities.both_known_providers_installed is True
    assert "matrix-nio=0.25.2" in capabilities.provider_version
    assert "mindroom-nio=0.25.2" in capabilities.provider_version
    message = compat.format_e2ee_unavailable_message(capabilities)
    install_command = compat.format_e2ee_install_command(capabilities)
    assert "both installed" in message
    assert "uninstall one nio namespace owner" in message
    assert "Uninstall either matrix-nio or mindroom-nio first" in install_command
