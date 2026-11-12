"""Config check rejects ambiguous relay identities before authentication checks."""

from types import SimpleNamespace


def test_config_check_reports_invalid_peer_allowlist(tmp_path, monkeypatch, capsys):
    from mmrelay import cli

    path = tmp_path / "config.yaml"
    path.write_text("matrix:\n  trusted_relays: ['*']\n")
    monkeypatch.setattr(cli, "get_config_paths", lambda _: [str(path)])
    monkeypatch.setattr(cli, "apply_env_config_overrides", lambda config: config)
    assert cli.check_config(SimpleNamespace(allow_missing_matrix_auth=False)) is False
    assert (
        "matrix.trusted_relays must be a list of exact Matrix user IDs"
        in capsys.readouterr().out
    )


def test_config_check_preserves_mapping_error_before_peer_validation(
    tmp_path, monkeypatch, capsys
):
    """An invalid matrix section should retain the established CLI diagnostic."""
    from mmrelay import cli

    path = tmp_path / "config.yaml"
    path.write_text("matrix: []\n", encoding="utf-8")
    monkeypatch.setattr(cli, "get_config_paths", lambda _: [str(path)])
    monkeypatch.setattr(cli, "apply_env_config_overrides", lambda config: config)
    assert cli.check_config(SimpleNamespace(allow_missing_matrix_auth=False)) is False
    assert "'matrix' section must be a mapping (YAML object)" in capsys.readouterr().out
