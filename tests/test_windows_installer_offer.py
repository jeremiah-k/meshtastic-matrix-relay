"""Keep the installer migration offer conditional and explicitly opt-in."""

from pathlib import Path


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
