import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from mmrelay.matrix import compat


class _FakeSqliteStore:
    """Minimal constructible stand-in so the openability probe succeeds."""

    def __init__(self, user_id: str, device_id: str, store_path: str) -> None:
        self.store_path = store_path


@pytest.fixture(autouse=True)
def reset_capability_cache():
    compat.reset_matrix_capabilities_cache()
    yield
    compat.reset_matrix_capabilities_cache()


def _patch_detection(
    monkeypatch: pytest.MonkeyPatch,
    distributions: dict[str, str | None],
    modules: dict[str, object],
) -> None:
    def fake_version(distribution_name: str) -> str:
        version = distributions.get(distribution_name)
        if version is not None:
            return version
        raise compat.metadata.PackageNotFoundError(distribution_name)

    def fake_import_module(module_name: str) -> object:
        if module_name in modules:
            module = modules[module_name]
            if isinstance(module, BaseException):
                raise module
            return module
        raise ImportError(f"No module named {module_name!r}")

    monkeypatch.setattr(compat.metadata, "version", fake_version)
    monkeypatch.setattr(compat.importlib, "import_module", fake_import_module)


def _read_pyproject_deps() -> dict[str, object]:
    """Read pyproject.toml and extract dependencies and optional-dependencies."""
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)
    result = data.get("project", {})
    return result if isinstance(result, dict) else {}


def test_default_dependency_is_mindroom_not_matrix_nio():
    project = _read_pyproject_deps()
    deps = project.get("dependencies", [])
    assert any("mindroom-nio" in dep for dep in deps)
    assert not any("matrix-nio" in dep for dep in deps)


def test_only_e2e_extra_for_matrix_providers():
    project = _read_pyproject_deps()
    extras = project.get("optional-dependencies", {})
    for name, deps in extras.items():
        if name == "e2e":
            continue
        for dep in deps:
            assert (
                "matrix-nio" not in dep
            ), f"extra {name} unexpectedly contains matrix-nio: {dep}"
            assert (
                "mindroom-nio" not in dep
            ), f"extra {name} unexpectedly contains mindroom-nio: {dep}"


def test_e2e_extra_points_to_mindroom():
    extras = _read_pyproject_deps().get("optional-dependencies", {})
    e2e_deps = extras.get("e2e", [])
    assert any("mindroom-nio[e2e]" in dep for dep in e2e_deps)


def test_no_extra_installs_both_providers_with_base():
    project = _read_pyproject_deps()
    base_deps = project.get("dependencies", [])
    extras = project.get("optional-dependencies", {})

    # For every optional extra, evaluate combined dependencies as base_deps + extra_deps
    for extra_name, extra_deps in extras.items():
        combined = base_deps + extra_deps
        has_matrix = any("matrix-nio" in dep for dep in combined)
        has_mindroom = any("mindroom-nio" in dep for dep in combined)
        assert not (
            has_matrix and has_mindroom
        ), f"extra '{extra_name}' combined deps contain both matrix-nio and mindroom-nio"


def test_both_providers_installed_disables_e2ee_even_with_crypto(monkeypatch):
    _patch_detection(
        monkeypatch,
        {"matrix-nio": "0.25.2", "mindroom-nio": "0.25.2"},
        {
            "nio": SimpleNamespace(
                AsyncClient=type("AsyncClient", (), {}),
                crypto=SimpleNamespace(
                    ENCRYPTION_ENABLED=True,
                    OlmDevice=object,
                ),
                api=SimpleNamespace(Api=type("Api_dummy", (), {})),
                store=SimpleNamespace(SqliteStore=_FakeSqliteStore),
            ),
            "nio.api": SimpleNamespace(Api=type("Api_dummy", (), {})),
            "nio.crypto": SimpleNamespace(
                ENCRYPTION_ENABLED=True,
                OlmDevice=object,
            ),
            "nio.store": SimpleNamespace(SqliteStore=_FakeSqliteStore),
            "olm": SimpleNamespace(),
            "vodozemac": SimpleNamespace(),
        },
    )

    capabilities = compat.detect_matrix_capabilities()

    assert capabilities.both_known_providers_installed is True
    assert capabilities.encryption_available is False
    assert capabilities.crypto_backend != "olm"
    assert capabilities.crypto_backend != "vodozemac"


def test_matrix_nio_install_guidance_does_not_recommend_mmrelay_e2e(monkeypatch):
    _patch_detection(
        monkeypatch,
        {"matrix-nio": "0.25.2"},
        {
            "nio": SimpleNamespace(AsyncClient=type("AsyncClient", (), {})),
            "nio.crypto": SimpleNamespace(OlmDevice=object),
            "nio.store": SimpleNamespace(SqliteStore=_FakeSqliteStore),
        },
    )

    capabilities = compat.detect_matrix_capabilities()
    cmd = compat.format_e2ee_install_command(capabilities)

    assert "pip install 'matrix-nio[e2e]==0.25.2'" in cmd
    assert "controlled replacement" in cmd
    assert "Do not install mmrelay[e2e]" in cmd
    assert "mmrelay[e2e]" not in cmd.replace("Do not install mmrelay[e2e]", "")


def test_detect_provider_nio_fallback(monkeypatch):
    """When only a bare 'nio' module is importable (no known dist), provider should be 'nio'."""
    _patch_detection(
        monkeypatch,
        {"matrix-nio": None, "mindroom-nio": None},
        {
            "nio": SimpleNamespace(AsyncClient=type("AsyncClient", (), {})),
            "nio.crypto": None,
            "nio.store": None,
            "nio.api": None,
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.provider_name == "nio"
    assert caps.provider_distribution == "unknown"
    assert caps.encryption_available is False


def test_detect_provider_unavailable(monkeypatch):
    """When no nio module or distribution is found, provider should be 'unavailable'."""
    from typing import NoReturn

    def raise_not_found(name: str) -> NoReturn:
        raise compat.metadata.PackageNotFoundError(name)

    def raise_import_error(name: str) -> NoReturn:
        raise ImportError(f"No module named {name!r}")

    monkeypatch.setattr(compat.metadata, "version", raise_not_found)
    monkeypatch.setattr(compat.importlib, "import_module", raise_import_error)

    caps = compat.detect_matrix_capabilities()
    assert caps.provider_name == "unavailable"
    assert caps.provider_distribution == "unknown"
    assert caps.encryption_available is False


def test_e2ee_install_guidance_fallback(monkeypatch):
    """When provider is unknown and no crypto backend detected, should return generic guidance."""
    _patch_detection(
        monkeypatch,
        {"matrix-nio": None, "mindroom-nio": None},
        {
            "nio": SimpleNamespace(AsyncClient=type("AsyncClient", (), {})),
            "nio.crypto": None,
            "nio.store": None,
            "nio.api": None,
        },
    )

    caps = compat.detect_matrix_capabilities()
    cmd = compat.format_e2ee_install_command(caps)
    assert "E2EE extra" in cmd


def test_detect_matrix_capabilities_mindroom_nio_ready(monkeypatch):
    """mindroom-nio with vodozemac ready should report encryption_available=True."""
    _patch_detection(
        monkeypatch,
        {"mindroom-nio": "0.25.2"},
        {
            "nio": SimpleNamespace(
                AsyncClient=type("AsyncClient", (), {}),
                crypto=SimpleNamespace(ENCRYPTION_ENABLED=True),
                store=SimpleNamespace(SqliteStore=_FakeSqliteStore),
            ),
            "nio.crypto": SimpleNamespace(ENCRYPTION_ENABLED=True),
            "nio.store": SimpleNamespace(SqliteStore=_FakeSqliteStore),
            "nio.api": SimpleNamespace(
                Api=type(
                    "Api_dummy",
                    (),
                    {
                        "update_receipt_marker": lambda *a: None,
                        "download": lambda *a, allow_remote=None: None,
                    },
                ),
            ),
            "vodozemac": SimpleNamespace(),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.provider_distribution == "mindroom-nio"
    assert caps.crypto_backend == "vodozemac"
    assert caps.encryption_available is True


def test_detect_matrix_capabilities_mindroom_nio_partial(monkeypatch):
    """mindroom-nio with vodozemac present but not fully ready should report encryption_available=False."""
    _patch_detection(
        monkeypatch,
        {"mindroom-nio": "0.25.2"},
        {
            "nio": SimpleNamespace(
                AsyncClient=type("AsyncClient", (), {}),
                crypto=SimpleNamespace(ENCRYPTION_ENABLED=False),
                store=SimpleNamespace(SqliteStore=_FakeSqliteStore),
            ),
            "nio.crypto": SimpleNamespace(ENCRYPTION_ENABLED=False),
            "nio.store": SimpleNamespace(SqliteStore=_FakeSqliteStore),
            "nio.api": SimpleNamespace(
                Api=type(
                    "Api_dummy",
                    (),
                    {
                        "update_receipt_marker": lambda *a: None,
                        "download": lambda *a, allow_remote=None: None,
                    },
                ),
            ),
            "vodozemac": SimpleNamespace(),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.provider_distribution == "mindroom-nio"
    assert caps.crypto_backend == "vodozemac"
    assert caps.encryption_available is False


def test_detect_matrix_capabilities_mindroom_nio_no_crypto(monkeypatch):
    """mindroom-nio without vodozemac but with nio.crypto should report backend=unavailable."""
    _patch_detection(
        monkeypatch,
        {"mindroom-nio": "0.25.2"},
        {
            "nio": SimpleNamespace(
                AsyncClient=type("AsyncClient", (), {}),
                crypto=SimpleNamespace(),
                store=SimpleNamespace(SqliteStore=_FakeSqliteStore),
            ),
            "nio.crypto": SimpleNamespace(),
            "nio.store": SimpleNamespace(SqliteStore=_FakeSqliteStore),
            "nio.api": SimpleNamespace(
                Api=type(
                    "Api_dummy",
                    (),
                    {
                        "update_receipt_marker": lambda *a: None,
                        "download": lambda *a, allow_remote=None: None,
                    },
                ),
            ),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.provider_distribution == "mindroom-nio"
    assert caps.crypto_backend == "unavailable"
    assert caps.encryption_available is False


def test_detect_matrix_capabilities_mindroom_nio_nothing(monkeypatch):
    """mindroom-nio without any crypto modules should report backend=unknown."""
    _patch_detection(
        monkeypatch,
        {"mindroom-nio": "0.25.2"},
        {
            "nio": SimpleNamespace(AsyncClient=type("AsyncClient", (), {})),
            "nio.api": SimpleNamespace(
                Api=type(
                    "Api_dummy",
                    (),
                    {
                        "update_receipt_marker": lambda *a: None,
                        "download": lambda *a, allow_remote=None: None,
                    },
                ),
            ),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.provider_distribution == "mindroom-nio"
    assert caps.crypto_backend == "unknown"
    assert caps.encryption_available is False


def test_detect_matrix_capabilities_matrix_nio_ready(monkeypatch):
    """matrix-nio with olm fully ready should report encryption_available=True."""
    _patch_detection(
        monkeypatch,
        {"matrix-nio": "0.25.2"},
        {
            "nio": SimpleNamespace(
                AsyncClient=type("AsyncClient", (), {}),
                crypto=SimpleNamespace(OlmDevice=object),
                store=SimpleNamespace(SqliteStore=_FakeSqliteStore),
            ),
            "nio.crypto": SimpleNamespace(OlmDevice=object),
            "nio.store": SimpleNamespace(SqliteStore=_FakeSqliteStore),
            "nio.api": SimpleNamespace(
                Api=type(
                    "Api_dummy",
                    (),
                    {
                        "update_receipt_marker": lambda *a: None,
                        "download": lambda *a, allow_remote=None: None,
                    },
                ),
            ),
            "olm": SimpleNamespace(),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.provider_distribution == "matrix-nio"
    assert caps.crypto_backend == "olm"
    assert caps.encryption_available is True


def test_detect_matrix_capabilities_matrix_nio_unavailable(monkeypatch):
    """matrix-nio with nio.crypto but no olm/vodozemac should report backend=unavailable."""
    _patch_detection(
        monkeypatch,
        {"matrix-nio": "0.25.2"},
        {
            "nio": SimpleNamespace(
                AsyncClient=type("AsyncClient", (), {}),
                crypto=SimpleNamespace(),
                store=SimpleNamespace(SqliteStore=_FakeSqliteStore),
            ),
            "nio.crypto": SimpleNamespace(),
            "nio.store": SimpleNamespace(SqliteStore=_FakeSqliteStore),
            "nio.api": SimpleNamespace(
                Api=type(
                    "Api_dummy",
                    (),
                    {
                        "update_receipt_marker": lambda *a: None,
                        "download": lambda *a, allow_remote=None: None,
                    },
                ),
            ),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.provider_distribution == "matrix-nio"
    assert caps.crypto_backend == "unavailable"
    assert caps.encryption_available is False


def test_detect_matrix_capabilities_matrix_nio_unknown(monkeypatch):
    """matrix-nio with no nio.crypto module should report backend=unknown."""
    _patch_detection(
        monkeypatch,
        {"matrix-nio": "0.25.2"},
        {
            "nio": SimpleNamespace(AsyncClient=type("AsyncClient", (), {})),
            "nio.api": SimpleNamespace(
                Api=type(
                    "Api_dummy",
                    (),
                    {
                        "update_receipt_marker": lambda *a: None,
                        "download": lambda *a, allow_remote=None: None,
                    },
                ),
            ),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.provider_distribution == "matrix-nio"
    assert caps.crypto_backend == "unknown"
    assert caps.encryption_available is False


def test_detect_matrix_capabilities_fallthrough_vodozemac_ready(monkeypatch):
    """Unknown provider with vodozemac ready should fall through to vodozemac=True."""
    _patch_detection(
        monkeypatch,
        {"matrix-nio": None, "mindroom-nio": None},
        {
            "nio": SimpleNamespace(
                AsyncClient=type("AsyncClient", (), {}),
                crypto=SimpleNamespace(ENCRYPTION_ENABLED=True),
                store=SimpleNamespace(SqliteStore=_FakeSqliteStore),
            ),
            "nio.crypto": SimpleNamespace(ENCRYPTION_ENABLED=True),
            "nio.store": SimpleNamespace(SqliteStore=_FakeSqliteStore),
            "nio.api": SimpleNamespace(
                Api=type(
                    "Api_dummy",
                    (),
                    {
                        "update_receipt_marker": lambda *a: None,
                        "download": lambda *a, allow_remote=None: None,
                    },
                ),
            ),
            "vodozemac": SimpleNamespace(),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.crypto_backend == "vodozemac"
    assert caps.encryption_available is True


def test_detect_matrix_capabilities_fallthrough_olm_ready(monkeypatch):
    """Unknown provider with olm ready should fall through to olm=True."""
    _patch_detection(
        monkeypatch,
        {"matrix-nio": None, "mindroom-nio": None},
        {
            "nio": SimpleNamespace(
                AsyncClient=type("AsyncClient", (), {}),
                crypto=SimpleNamespace(OlmDevice=object),
                store=SimpleNamespace(SqliteStore=_FakeSqliteStore),
            ),
            "nio.crypto": SimpleNamespace(OlmDevice=object),
            "nio.store": SimpleNamespace(SqliteStore=_FakeSqliteStore),
            "nio.api": SimpleNamespace(
                Api=type(
                    "Api_dummy",
                    (),
                    {
                        "update_receipt_marker": lambda *a: None,
                        "download": lambda *a, allow_remote=None: None,
                    },
                ),
            ),
            "olm": SimpleNamespace(),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.crypto_backend == "olm"
    assert caps.encryption_available is True


def test_detect_matrix_capabilities_fallthrough_partial_vodozemac(monkeypatch):
    """Unknown provider with partial vodozemac should report encryption_available=False."""
    _patch_detection(
        monkeypatch,
        {"matrix-nio": None, "mindroom-nio": None},
        {
            "nio": SimpleNamespace(
                AsyncClient=type("AsyncClient", (), {}),
                crypto=SimpleNamespace(ENCRYPTION_ENABLED=False),
                store=SimpleNamespace(SqliteStore=_FakeSqliteStore),
            ),
            "nio.crypto": SimpleNamespace(ENCRYPTION_ENABLED=False),
            "nio.store": SimpleNamespace(SqliteStore=_FakeSqliteStore),
            "nio.api": SimpleNamespace(
                Api=type(
                    "Api_dummy",
                    (),
                    {
                        "update_receipt_marker": lambda *a: None,
                        "download": lambda *a, allow_remote=None: None,
                    },
                ),
            ),
            "vodozemac": SimpleNamespace(),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.crypto_backend == "vodozemac"
    assert caps.encryption_available is False


def test_detect_matrix_capabilities_fallthrough_partial_olm(monkeypatch):
    """Unknown provider with partial olm (no sqlite store) should report encryption_available=False."""
    _patch_detection(
        monkeypatch,
        {"matrix-nio": None, "mindroom-nio": None},
        {
            "nio": SimpleNamespace(
                AsyncClient=type("AsyncClient", (), {}),
                crypto=SimpleNamespace(OlmDevice=object),
            ),
            "nio.crypto": SimpleNamespace(OlmDevice=object),
            "nio.api": SimpleNamespace(
                Api=type(
                    "Api_dummy",
                    (),
                    {
                        "update_receipt_marker": lambda *a: None,
                        "download": lambda *a, allow_remote=None: None,
                    },
                ),
            ),
            "olm": SimpleNamespace(),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.crypto_backend == "olm"
    assert caps.encryption_available is False


def test_detect_matrix_capabilities_fallthrough_unavailable(monkeypatch):
    """Unknown provider with nio.crypto but no olm/vodozemac should report unavailable."""
    _patch_detection(
        monkeypatch,
        {"matrix-nio": None, "mindroom-nio": None},
        {
            "nio": SimpleNamespace(
                AsyncClient=type("AsyncClient", (), {}),
                crypto=SimpleNamespace(),
                store=SimpleNamespace(SqliteStore=_FakeSqliteStore),
            ),
            "nio.crypto": SimpleNamespace(),
            "nio.store": SimpleNamespace(SqliteStore=_FakeSqliteStore),
            "nio.api": SimpleNamespace(
                Api=type(
                    "Api_dummy",
                    (),
                    {
                        "update_receipt_marker": lambda *a: None,
                        "download": lambda *a, allow_remote=None: None,
                    },
                ),
            ),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.crypto_backend == "unavailable"
    assert caps.encryption_available is False


def test_detect_matrix_capabilities_fallthrough_unknown(monkeypatch):
    """Unknown provider with no crypto modules should report unknown."""
    _patch_detection(
        monkeypatch,
        {"matrix-nio": None, "mindroom-nio": None},
        {
            "nio": SimpleNamespace(AsyncClient=type("AsyncClient", (), {})),
            "nio.api": SimpleNamespace(
                Api=type(
                    "Api_dummy",
                    (),
                    {
                        "update_receipt_marker": lambda *a: None,
                        "download": lambda *a, allow_remote=None: None,
                    },
                ),
            ),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.crypto_backend == "unknown"
    assert caps.encryption_available is False


def test_supports_authenticated_media_exception(monkeypatch):
    """When inspect.signature raises TypeError/ValueError, supports_authenticated_media should be False."""

    class BadApi:
        @staticmethod
        def download(*args, **kwargs):
            pass

    monkeypatch.setattr(
        compat.inspect,
        "signature",
        lambda _: (_ for _ in ()).throw(TypeError("not callable")),
    )

    _patch_detection(
        monkeypatch,
        {"matrix-nio": "0.25.2"},
        {
            "nio": SimpleNamespace(AsyncClient=type("AsyncClient", (), {})),
            "nio.crypto": SimpleNamespace(OlmDevice=object),
            "nio.store": SimpleNamespace(SqliteStore=_FakeSqliteStore),
            "nio.api": SimpleNamespace(Api=BadApi),
            "olm": SimpleNamespace(),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.supports_authenticated_media is False


def test_format_e2ee_unavailable_message_conflict(monkeypatch):
    """format_e2ee_unavailable_message should return conflict message when both providers installed."""
    _patch_detection(
        monkeypatch,
        {"matrix-nio": "0.25.2", "mindroom-nio": "0.25.2"},
        {
            "nio": SimpleNamespace(AsyncClient=type("AsyncClient", (), {})),
            "nio.crypto": SimpleNamespace(OlmDevice=object),
            "nio.store": SimpleNamespace(SqliteStore=_FakeSqliteStore),
            "nio.api": SimpleNamespace(Api=type("Api_dummy", (), {})),
            "olm": SimpleNamespace(),
            "vodozemac": SimpleNamespace(),
        },
    )

    msg = compat.format_e2ee_unavailable_message()
    assert "both installed" in msg
    assert "matrix-nio and mindroom-nio" in msg


def test_format_e2ee_install_command_fallback(monkeypatch):
    """format_e2ee_install_command should return generic guidance for unknown providers."""
    _patch_detection(
        monkeypatch,
        {"matrix-nio": None, "mindroom-nio": None},
        {
            "nio": SimpleNamespace(AsyncClient=type("AsyncClient", (), {})),
            "nio.crypto": SimpleNamespace(),
            "nio.store": SimpleNamespace(SqliteStore=_FakeSqliteStore),
            "nio.api": SimpleNamespace(
                Api=type(
                    "Api_dummy",
                    (),
                    {
                        "update_receipt_marker": lambda *a: None,
                        "download": lambda *a, allow_remote=None: None,
                    },
                ),
            ),
        },
    )

    caps = compat.detect_matrix_capabilities()
    cmd = compat.format_e2ee_install_command(caps)
    assert "E2EE extra" in cmd


def test_format_e2ee_install_command_mindroom_nio(monkeypatch):
    """format_e2ee_install_command should return mindroom-nio guidance for mindroom-nio provider."""
    _patch_detection(
        monkeypatch,
        {"mindroom-nio": "0.25.2"},
        {
            "nio": SimpleNamespace(
                AsyncClient=type("AsyncClient", (), {}),
                crypto=SimpleNamespace(ENCRYPTION_ENABLED=True),
                store=SimpleNamespace(SqliteStore=_FakeSqliteStore),
            ),
            "nio.crypto": SimpleNamespace(ENCRYPTION_ENABLED=True),
            "nio.store": SimpleNamespace(SqliteStore=_FakeSqliteStore),
            "nio.api": SimpleNamespace(
                Api=type(
                    "Api_dummy",
                    (),
                    {
                        "update_receipt_marker": lambda *a: None,
                        "download": lambda *a, allow_remote=None: None,
                    },
                ),
            ),
            "vodozemac": SimpleNamespace(),
        },
    )

    caps = compat.detect_matrix_capabilities()
    cmd = compat.format_e2ee_install_command(caps)
    assert "mmrelay[e2e]" in cmd
    assert "pipx" in cmd


class _UnopenableSqliteStore:
    """Stand-in whose construction fails, like nio's fcntl-leased store on Windows."""

    def __init__(self, user_id: str, device_id: str, store_path: str) -> None:
        raise RuntimeError("store filesystem ownership requires fcntl support")


def _make_store_modules(store_class: object) -> dict[str, object]:
    return {
        "nio": SimpleNamespace(
            AsyncClient=type("AsyncClient", (), {}),
            crypto=SimpleNamespace(ENCRYPTION_ENABLED=True),
            store=SimpleNamespace(SqliteStore=store_class),
        ),
        "nio.crypto": SimpleNamespace(ENCRYPTION_ENABLED=True),
        "nio.store": SimpleNamespace(SqliteStore=store_class),
        "nio.api": SimpleNamespace(
            Api=type(
                "Api_dummy",
                (),
                {
                    "update_receipt_marker": lambda *a: None,
                    "download": lambda *a, allow_remote=None: None,
                },
            )
        ),
        "vodozemac": SimpleNamespace(),
    }


def test_detect_probe_rejects_unopenable_store(monkeypatch):
    """A store backend that cannot open here disables E2EE despite crypto being installed."""
    _patch_detection(
        monkeypatch,
        {"mindroom-nio": "1.1.4"},
        _make_store_modules(_UnopenableSqliteStore),
    )

    caps = compat.detect_matrix_capabilities()

    assert caps.vodozemac_available is True
    assert caps.sqlite_store_available is True
    assert caps.sqlite_store_openable is False
    assert caps.encryption_available is False
    assert caps.crypto_backend == "vodozemac"
    assert "fcntl" in caps.install_hint
    message = compat.format_e2ee_unavailable_message(caps)
    assert "cannot open on this platform" in message
    command = compat.format_e2ee_install_command(caps)
    assert "No package install enables E2EE" in command


def test_detect_probe_accepts_constructible_store(monkeypatch):
    """A store backend that opens here keeps vodozemac E2EE available."""
    _patch_detection(
        monkeypatch,
        {"mindroom-nio": "1.1.4"},
        _make_store_modules(_FakeSqliteStore),
    )

    caps = compat.detect_matrix_capabilities()

    assert caps.sqlite_store_openable is True
    assert caps.encryption_available is True


def test_probe_closes_database_handle():
    """The openability probe closes the store database it opened."""
    closed = []

    class _RecordingStore(_FakeSqliteStore):
        def __init__(self, user_id: str, device_id: str, store_path: str) -> None:
            super().__init__(user_id, device_id, store_path)
            self.database = SimpleNamespace(close=lambda: closed.append(True))

    assert compat._probe_sqlite_store_openable(
        SimpleNamespace(SqliteStore=_RecordingStore)
    )
    assert closed == [True]


def test_probe_reports_false_without_store_class():
    """Without a SqliteStore class there is nothing to open; the probe fails closed."""
    assert compat._probe_sqlite_store_openable(SimpleNamespace()) is False


def test_store_probe_is_not_run_without_crypto_candidate(monkeypatch):
    """Capability detection must not touch disk for a crypto-less nio installation."""

    probe_calls = []

    class _UnexpectedStore:
        def __init__(self, *args: object) -> None:
            probe_calls.append(args)
            raise AssertionError("store probe ran without a crypto backend")

    _patch_detection(
        monkeypatch,
        {"mindroom-nio": "1.1.4"},
        {
            "nio": SimpleNamespace(AsyncClient=object),
            "nio.crypto": SimpleNamespace(ENCRYPTION_ENABLED=False),
            "nio.store": SimpleNamespace(SqliteStore=_UnexpectedStore),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.sqlite_store_available is True
    assert caps.sqlite_store_openable is False
    assert caps.store_failure is None
    assert probe_calls == []
    assert "fcntl" not in caps.install_hint
    assert "mmrelay[e2e]" in compat.format_e2ee_install_command(caps)


def test_missing_sqlite_store_gives_dependency_guidance(monkeypatch):
    """An absent SqliteStore is not evidence of a Windows POSIX-lease failure."""
    _patch_detection(
        monkeypatch,
        {"mindroom-nio": "1.1.4"},
        {
            "nio": SimpleNamespace(AsyncClient=object),
            "nio.crypto": SimpleNamespace(ENCRYPTION_ENABLED=True),
            "nio.store": SimpleNamespace(),
            "vodozemac": SimpleNamespace(),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.sqlite_store_available is False
    assert caps.store_failure is None
    assert "fcntl" not in caps.install_hint
    assert "mmrelay[e2e]" in compat.format_e2ee_install_command(caps)


def test_generic_store_probe_error_does_not_claim_fcntl(monkeypatch):
    """A permissions failure must not be presented as a missing POSIX module."""

    class _PermissionDeniedStore:
        def __init__(self, *args: object) -> None:
            raise PermissionError("Cannot write to store directory")

    _patch_detection(
        monkeypatch,
        {"mindroom-nio": "1.1.4"},
        {
            "nio": SimpleNamespace(AsyncClient=object),
            "nio.crypto": SimpleNamespace(ENCRYPTION_ENABLED=True),
            "nio.store": SimpleNamespace(SqliteStore=_PermissionDeniedStore),
            "vodozemac": SimpleNamespace(),
        },
    )

    caps = compat.detect_matrix_capabilities()
    assert caps.sqlite_store_openable is False
    assert caps.store_failure == "other"
    assert "fcntl" not in caps.install_hint
    assert "filesystem permissions" in compat.format_e2ee_install_command(caps)
    assert "cannot open" in compat.format_e2ee_unavailable_message(caps)
