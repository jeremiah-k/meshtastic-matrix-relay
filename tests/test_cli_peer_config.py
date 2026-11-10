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
