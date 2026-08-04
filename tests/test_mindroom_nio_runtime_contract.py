"""Installed-provider contract tests for MMRelay's Matrix E2EE dependency."""

from __future__ import annotations

import subprocess  # nosec B404 - runs contract check script with hardcoded args in isolated mode
import sys
import textwrap
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from tests.constants import (
    MINDROOM_BACKFILL_LIMITED_TIMELINES_DEFAULT,
    MINDROOM_BACKFILL_PERSIST_RECOVERY_DEFAULT,
)

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"


def _single_mindroom_requirement(
    dependencies: list[str], *, expected_extras: frozenset[str]
) -> Requirement:
    """
    Find the single `mindroom-nio` requirement matching the requested extras.

    Parameters:
        dependencies (list[str]): Dependency requirement strings to inspect.
        expected_extras (frozenset[str]): Extras that the matching requirement must specify.

    Returns:
        Requirement: The matching `mindroom-nio` requirement.
    """
    matches: list[Requirement] = []
    for dependency in dependencies:
        requirement = Requirement(dependency)
        if (
            canonicalize_name(requirement.name) == "mindroom-nio"
            and frozenset(requirement.extras) == expected_extras
        ):
            matches.append(requirement)

    assert len(matches) == 1, matches
    return matches[0]


def _exact_pin(requirement: Requirement) -> str:
    """Return an exact version pin and reject ranges or environment markers."""
    specifiers = list(requirement.specifier)
    assert requirement.marker is None
    assert len(specifiers) == 1, specifiers
    specifier = specifiers[0]
    assert specifier.operator == "=="
    return specifier.version


def _mindroom_pin() -> str:
    """
    Determine the pinned version shared by the base and E2EE `mindroom-nio` dependencies.

    Returns:
        str: The shared pinned `mindroom-nio` version.
    """
    with PYPROJECT.open("rb") as handle:
        project = tomllib.load(handle)["project"]

    base_requirement = _single_mindroom_requirement(
        project["dependencies"], expected_extras=frozenset()
    )
    e2e_requirement = _single_mindroom_requirement(
        project["optional-dependencies"]["e2e"],
        expected_extras=frozenset({"e2e"}),
    )

    base_version = _exact_pin(base_requirement)
    e2e_version = _exact_pin(e2e_requirement)
    assert e2e_version == base_version
    return base_version


def test_installed_mindroom_nio_exposes_mmrelay_e2ee_contract() -> None:
    """Exercise the real provider outside MMRelay's in-process dependency mocks."""

    expected_version = _mindroom_pin()
    script = textwrap.dedent(f"""
        from importlib import metadata
        from inspect import Parameter, iscoroutinefunction, signature

        import vodozemac
        from nio import AsyncClient, AsyncClientConfig
        from nio.api import Api
        from nio.crypto import ENCRYPTION_ENABLED
        from nio.crypto.cross_signing import CrossSigningIdentity
        from nio.store import MatrixStore, SqliteStore

        assert metadata.version("mindroom-nio") == {expected_version!r}
        try:
            metadata.version("matrix-nio")
        except metadata.PackageNotFoundError:
            pass
        else:
            raise AssertionError("matrix-nio and mindroom-nio must not be co-installed")

        assert ENCRYPTION_ENABLED is True
        assert vodozemac.__name__ == "vodozemac"
        assert SqliteStore is not None
        assert MatrixStore.store_version == 7

        default_config = AsyncClientConfig()
        assert default_config.replace_rotated_device_keys is False
        assert (
            default_config.backfill_limited_timelines
            is {MINDROOM_BACKFILL_LIMITED_TIMELINES_DEFAULT!r}
        )
        assert (
            default_config.backfill_persist_recovery
            is {MINDROOM_BACKFILL_PERSIST_RECOVERY_DEFAULT!r}
        )
        assert default_config.backfill_sliding_seed_rooms == 1000

        config = AsyncClientConfig(
            encryption_enabled=True,
            store_sync_tokens=True,
            replace_rotated_device_keys=True,
        )
        assert config.encryption_enabled is True
        assert config.store_sync_tokens is True
        assert config.replace_rotated_device_keys is True

        ensure_parameters = signature(AsyncClient.ensure_cross_signing).parameters
        password = ensure_parameters["password"]
        assert password.kind in (Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY)
        assert password.default is None

        send_parameters = signature(AsyncClient.send).parameters
        for parameter in ("method", "path", "data", "headers"):
            assert parameter in send_parameters, parameter

        assert isinstance(AsyncClient.cross_signing_identity, property)
        assert iscoroutinefunction(AsyncClient.close)
        for capability in ("ensure_cross_signing", "stop_sync_forever"):
            assert callable(getattr(AsyncClient, capability, None)), capability

        identity = CrossSigningIdentity.generate("@bot:example.org")
        self_signing = identity.self_signing_key_payload()
        signature_b64 = self_signing["signatures"][identity.user_id][
            f"ed25519:{{identity.master_public_key}}"
        ]
        unsigned = {{
            key: value
            for key, value in self_signing.items()
            if key not in ("signatures", "unsigned")
        }}
        message = Api.to_canonical_json(unsigned).encode("utf-8")
        public_key = vodozemac.Ed25519PublicKey.from_base64(
            identity.master_public_key
        )
        signature_value = vodozemac.Ed25519Signature.from_base64(signature_b64)
        public_key.verify_signature(message, signature_value)
        """)

    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )  # nosec B603

    assert result.returncode == 0, result.stderr or result.stdout
