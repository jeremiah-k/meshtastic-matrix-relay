from pathlib import Path
from types import SimpleNamespace

import pytest

from mmrelay.matrix import compat


@pytest.fixture(autouse=True)
def reset_capability_cache():
    compat.reset_matrix_capabilities_cache()
    yield
    compat.reset_matrix_capabilities_cache()


def _patch_detection(
    monkeypatch: pytest.MonkeyPatch,
    distributions: dict[str, str],
    modules: dict[str, object],
) -> None:
    def fake_version(distribution_name: str) -> str:
        if distribution_name in distributions:
            return distributions[distribution_name]
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
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

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
                store=SimpleNamespace(SqliteStore=object),
            ),
            "nio.api": SimpleNamespace(Api=type("Api_dummy", (), {})),
            "nio.crypto": SimpleNamespace(
                ENCRYPTION_ENABLED=True,
                OlmDevice=object,
            ),
            "nio.store": SimpleNamespace(SqliteStore=object),
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
            "nio.store": SimpleNamespace(SqliteStore=object),
        },
    )

    capabilities = compat.detect_matrix_capabilities()
    cmd = compat.format_e2ee_install_command(capabilities)

    assert "pip install 'matrix-nio[e2e]==0.25.2'" in cmd
    assert "controlled replacement" in cmd
    assert "Do not install mmrelay[e2e]" in cmd
    assert "mmrelay[e2e]" not in cmd.replace("Do not install mmrelay[e2e]", "")
