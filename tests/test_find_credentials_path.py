"""
Test for _find_credentials_json_path() to ensure legacy search works.

This is a minimal deterministic test to verify the fix for the indentation bug
that made the legacy credentials loop unreachable.
"""

from pathlib import Path

import pytest

from mmrelay.cli import _find_credentials_json_path


def test_legacy_credentials_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Test that credentials.json in a legacy root are found when HOME creds don't exist.

    This test verifies the fix for the indentation bug that made the legacy
    loop unreachable. The search order should be:
    1) Explicit path (if provided)
    2) Config-adjacent (only if config_path provided)
    3) HOME credentials
    4) Legacy roots

    When HOME credentials are missing, it should fall through to search legacy.
    """
    home = tmp_path / "home"
    legacy = tmp_path / "legacy"
    home.mkdir()
    legacy.mkdir()

    legacy_creds = legacy / "credentials.json"
    legacy_creds.write_text(
        '{"homeserver": "https://matrix.org", "access_token": "test"}'
    )

    monkeypatch.setattr("mmrelay.config.get_explicit_credentials_path", lambda _c: None)
    monkeypatch.setattr(
        "mmrelay.config.get_credentials_search_paths",
        lambda **_kwargs: [
            str(home / "credentials.json"),
            str(legacy / "credentials.json"),
        ],
    )

    result = _find_credentials_json_path(None)

    assert result == str(legacy_creds)
