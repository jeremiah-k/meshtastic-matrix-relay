"""Verify an installed frozen executable's guided Windows home migration."""

import argparse
import json
import os
import sqlite3
import subprocess  # nosec B404 - invoke only the installer and executable under test
import sys
import tempfile
from contextlib import closing
from pathlib import Path

import platformdirs


def run(command: list[str], env: dict[str, str]) -> str:
    result = subprocess.run(  # nosec B603 - explicit test artifact paths, no shell
        command,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=180,
        check=False,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    result.check_returncode()
    return result.stdout


def verify(installer: Path) -> None:
    target = Path(platformdirs.user_data_dir("mmrelay", appauthor=False))
    # The --version smoke check can create empty scaffolding. Never overwrite
    # actual state if this check is mistakenly run outside a disposable runner.
    if any(files for _, _, files in os.walk(target)):
        raise RuntimeError(f"Windows smoke test requires an empty data home: {target}")
    env = dict(os.environ)
    for key in ("MMRELAY_HOME", "MMRELAY_BASE_DIR", "MMRELAY_DATA_DIR"):
        env.pop(key, None)
    with tempfile.TemporaryDirectory(prefix="MM Relay upgrade ") as temporary:
        source = Path(temporary) / "Installed Relay"
        run(
            [
                str(installer),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                f"/DIR={source}",
            ],
            env,
        )
        executable = source / "mmrelay.exe"
        if not executable.is_file():
            raise RuntimeError("Installer did not create mmrelay.exe")
        for launcher in ("mmrelay.bat", "setup-auth.bat", "logout.bat"):
            if "--home" in (source / launcher).read_text(encoding="utf-8"):
                raise RuntimeError(
                    f"{launcher} pins data to the installation directory"
                )
        if not (source / "migrate-data.bat").is_file():
            raise RuntimeError("Guided migration launcher is missing")
        (source / "config.yaml").write_text("meshtastic: {}\n", encoding="utf-8")
        matrix = source / "matrix"
        matrix.mkdir()
        (matrix / "credentials.json").write_text(
            json.dumps(
                {
                    "homeserver": "https://example.org",
                    "access_token": "test-token",
                }
            ),
            encoding="utf-8",
        )
        database = source / "database" / "meshtastic.sqlite"
        database.parent.mkdir()
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE probe(value INTEGER)")
            connection.execute("INSERT INTO probe VALUES (42)")
            connection.commit()
        plugin_data = source / "plugins" / "core" / "weather" / "data"
        plugin_data.mkdir(parents=True)
        (plugin_data / "state.json").write_text("{}", encoding="utf-8")
        custom = source / "plugins" / "custom"
        custom.mkdir()
        (custom / "standalone.py").write_text("VALUE = 42\n", encoding="utf-8")
        preview = run(
            [str(executable), "migrate", "--to-default-home", "--dry-run"], env
        )
        if str(target) not in preview or not (source / "config.yaml").is_file():
            raise RuntimeError(
                "Preview did not preserve the source or show its destination"
            )
        if (target / "matrix" / "credentials.json").exists():
            raise RuntimeError("Preview modified the destination")
        run([str(executable), "migrate", "--to-default-home"], env)
        if not (target / "config.yaml").is_file() or (source / "config.yaml").exists():
            raise RuntimeError("Configuration was not relocated")
        if not (target / "matrix" / "credentials.json").is_file():
            raise RuntimeError("Matrix credentials were not relocated")
        if not (
            target / "plugins" / "core" / "weather" / "data" / "state.json"
        ).is_file():
            raise RuntimeError("Core plugin state was not relocated")
        if not (target / "plugins" / "custom" / "standalone.py").is_file():
            raise RuntimeError("Standalone custom plugin was not relocated")
        with closing(
            sqlite3.connect(target / "database" / "meshtastic.sqlite")
        ) as connection:
            if connection.execute("SELECT value FROM probe").fetchone() != (42,):
                raise RuntimeError("Database contents changed during relocation")
        run([str(executable), "verify-migration"], env)
        run([str(executable), "migrate", "--to-default-home"], env)
    print("Installed Windows upgrade smoke test passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    args = parser.parse_args()
    verify(args.installer.resolve())


if __name__ == "__main__":
    main()
