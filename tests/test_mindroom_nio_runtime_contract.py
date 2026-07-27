"""Installed-provider contract tests for MMRelay's Matrix E2EE dependency."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"


def _mindroom_pin() -> str:
    """Return the single version used by the base and E2EE dependency sets."""
    with PYPROJECT.open("rb") as handle:
        project = tomllib.load(handle)["project"]

    dependencies = project["dependencies"]
    base_matches = [
        dependency
        for dependency in dependencies
        if isinstance(dependency, str) and dependency.startswith("mindroom-nio==")
    ]
    assert base_matches == ["mindroom-nio==0.31.0"]

    optional_dependencies = project["optional-dependencies"]
    e2e_dependencies = optional_dependencies["e2e"]
    e2e_matches = [
        dependency
        for dependency in e2e_dependencies
        if isinstance(dependency, str)
        and dependency.startswith("mindroom-nio[e2e]==")
    ]
    assert e2e_matches == ["mindroom-nio[e2e]==0.31.0"]

    base_version = base_matches[0].partition("==")[2]
    e2e_version = e2e_matches[0].partition("==")[2]
    assert base_version == e2e_version
    return base_version


def test_installed_mindroom_nio_exposes_mmrelay_e2ee_contract() -> None:
    """Exercise the real provider outside MMRelay's in-process dependency mocks."""

    expected_version = _mindroom_pin()
    script = textwrap.dedent(
        f"""
        from importlib import metadata
        from inspect import Parameter, signature

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
        assert MatrixStore.store_version == 3

        default_config = AsyncClientConfig()
        assert default_config.replace_rotated_device_keys is False
        assert default_config.backfill_limited_timelines is False
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
        """
    )

    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
