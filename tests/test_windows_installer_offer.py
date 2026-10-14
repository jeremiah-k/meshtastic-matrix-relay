"""Keep the installer migration offer conditional and explicitly opt-in."""

from pathlib import Path

import pytest

from scripts.ci import smoke_windows_upgrade


@pytest.mark.parametrize("duplicate", [False, True])
@pytest.mark.parametrize("root_key", ["SystemRoot", "SYSTEMROOT", "systemroot"])
def test_installed_auth_link_check_rejects_duplicate_entries(
    tmp_path, monkeypatch, duplicate, root_key
):
    import json

    source = tmp_path / "app"
    group = tmp_path / "menu"
    group.mkdir()
    (group / "unrelated.txt").write_text("preserve", encoding="utf-8")
    links = [
        {
            "Name": "MM Relay Setup Authentication",
            "Target": str(source / "setup-auth.bat"),
            "Arguments": "",
        },
        {
            "Name": "MM Relay OAuth Login",
            "Target": str(source / "setup-auth.bat"),
            "Arguments": "--oauth",
        },
    ]
    if duplicate:
        links.append(links[0])
    monkeypatch.setattr(
        smoke_windows_upgrade, "run", lambda *a, **kw: json.dumps(links)
    )
    if duplicate:
        with pytest.raises(RuntimeError, match="duplicated"):
            smoke_windows_upgrade.verify_auth_shortcuts(
                source, group, {root_key: str(tmp_path)}
            )
    else:
        smoke_windows_upgrade.verify_auth_shortcuts(
            source, group, {root_key: str(tmp_path)}
        )


@pytest.mark.parametrize("single_object", [False, True])
def test_installed_auth_link_check_reports_missing_auth_entries(
    tmp_path, monkeypatch, single_object
):
    import json

    group = tmp_path / "menu"
    group.mkdir()
    (group / "unrelated.txt").write_text("preserve", encoding="utf-8")
    link = {"Name": "Other", "Target": str(tmp_path / "other.exe"), "Arguments": ""}
    result = link if single_object else []
    monkeypatch.setattr(
        smoke_windows_upgrade, "run", lambda *a, **kw: json.dumps(result)
    )
    with pytest.raises(RuntimeError, match="missing or duplicated"):
        smoke_windows_upgrade.verify_auth_shortcuts(
            tmp_path / "app", group, {"SystemRoot": str(tmp_path)}
        )


def test_installed_auth_link_check_accepts_equivalent_directory_aliases(
    tmp_path, monkeypatch
):
    import json

    actual = tmp_path / "actual-app"
    actual.mkdir()
    (actual / "setup-auth.bat").write_text("@echo off\n", encoding="utf-8")
    alias = tmp_path / "app-alias"
    alias.symlink_to(actual, target_is_directory=True)
    group = tmp_path / "menu"
    group.mkdir()
    (group / "unrelated.txt").write_text("preserve", encoding="utf-8")
    links = [
        {"Name": name, "Target": str(actual / "setup-auth.bat"), "Arguments": arguments}
        for name, arguments in (
            ("MM Relay Setup Authentication", ""),
            ("MM Relay OAuth Login", "--oauth"),
        )
    ]
    monkeypatch.setattr(
        smoke_windows_upgrade, "run", lambda *a, **kw: json.dumps(links)
    )

    smoke_windows_upgrade.verify_auth_shortcuts(
        alias, group, {"SYSTEMROOT": str(tmp_path)}
    )


def test_auth_shortcuts_keep_one_password_entry_and_explicit_oauth_entry():
    source = (Path(__file__).parents[1] / "scripts" / "mmrelay.iss").read_text(
        encoding="utf-8"
    )
    icons = source.split("[Icons]\n", 1)[1].split("[Run]", 1)[0].splitlines()
    auth = [line for line in icons if 'Filename: "{app}\\setup-auth.bat"' in line]
    assert len(auth) == 2
    password = next(line for line in auth if "MM Relay Setup Authentication" in line)
    oauth = next(line for line in auth if "MM Relay OAuth Login" in line)
    assert "Parameters:" not in password
    assert 'Parameters: "--oauth"' in oauth
    assert 'WorkingDir: "{app}"' in oauth
    cleanup = source.split("[InstallDelete]\n", 1)[1].split("[Icons]", 1)[0]
    assert 'Type: files; Name: "{group}\\Setup Authentication.lnk"' in cleanup
    assert "*" not in cleanup


def test_declining_config_generation_skips_only_configuration_pages():
    source = (Path(__file__).parents[1] / "scripts" / "mmrelay.iss").read_text(
        encoding="utf-8"
    )
    callback = source.split("function ShouldSkipPage", 1)[1].split("end;", 1)[0]
    for page in ("MeshtasticPage", "MatrixPage", "MatrixMeshtasticPage", "OptionsPage"):
        assert f"PageID = {page}.ID" in callback
    assert "Not OverwriteConfig.Values[0]" in callback
    assert "PageID = wpFinished" not in callback


def test_migration_offer_is_conditional_unchecked_and_precedes_launch():
    source = (Path(__file__).parents[1] / "scripts" / "mmrelay.iss").read_text(
        encoding="utf-8"
    )
    entries = source.split("[Run]\n", 1)[1].split("[Code]", 1)[0].splitlines()
    migration = next(line for line in entries if "migrate-data.bat" in line)
    launch = next(line for line in entries if "mmrelay.bat" in line)
    assert "Check: ShouldOfferDataMigration" in migration
    assert "postinstall skipifsilent unchecked" in migration
    assert "nowait" not in migration and "shellexec" not in migration
    assert entries.index(migration) < entries.index(launch)
    assert "if WizardSilent or not MigrationOfferReady then" in source
    assert "if CurStep = ssPostInstall then" in source
    assert "migrate --to-default-home --check-needed" in source
