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


def run(command: list[str], env: dict[str, str], *, expected_exit: int = 0) -> str:
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
    if result.returncode != expected_exit:
        raise RuntimeError(
            f"Expected exit {expected_exit}, got {result.returncode}: {command}"
        )
    return result.stdout


def verify_auth_shortcuts(source: Path, group: Path, env: dict[str, str]) -> None:
    """Read actual installed links and require one entry per login method."""
    if (group / "Setup Authentication.lnk").exists():
        raise RuntimeError("Historical authentication shortcut was not removed")
    if not (group / "unrelated.txt").exists():
        raise RuntimeError("Shortcut cleanup removed an unrelated file")
    # dict(os.environ) loses Windows' case-insensitive environment lookup.
    windows_env = {key.upper(): value for key, value in env.items()}
    powershell = (
        Path(windows_env["SYSTEMROOT"])
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    quoted = str(group).replace("'", "''")
    script = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        "$shell = New-Object -ComObject WScript.Shell; "
        f"@(Get-ChildItem -LiteralPath '{quoted}' -Filter '*.lnk' | ForEach-Object {{ "
        "$link = $shell.CreateShortcut($_.FullName); "
        "@{Name=$_.BaseName; Target=$link.TargetPath; Arguments=$link.Arguments} "
        "}) | ConvertTo-Json -Compress"
    )
    links = json.loads(
        run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script], env)
    )
    # A single .lnk serializes as one object because the pipeline unrolls
    # the array before ConvertTo-Json sees it.
    if isinstance(links, dict):
        links = [links]
    # COM expands 8.3 paths (for example RUNNER~1); compare filesystem identities.
    expected_target = (source / "setup-auth.bat").resolve()
    auth = [link for link in links if Path(link["Target"]).resolve() == expected_target]
    expected = {"MM Relay Setup Authentication": "", "MM Relay OAuth Login": "--oauth"}
    if len(auth) != 2 or {link["Name"]: link["Arguments"] for link in auth} != expected:
        raise RuntimeError(
            "Installed authentication shortcuts are missing or duplicated"
        )


def verify(installer: Path) -> None:
    target = Path(platformdirs.user_data_dir("mmrelay", appauthor=False))
    # The --version smoke check can create empty scaffolding. Never overwrite
    # actual state if this check is mistakenly run outside a disposable runner.
    if any(files for _, _, files in os.walk(target)):
        raise RuntimeError(f"Windows smoke test requires an empty data home: {target}")
    env = dict(os.environ)
    for key in ("MMRELAY_HOME", "MMRELAY_BASE_DIR", "MMRELAY_DATA_DIR"):
        env.pop(key, None)
    programs = (
        Path(env["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    )
    with (
        tempfile.TemporaryDirectory(prefix="MM Relay upgrade ") as temporary,
        tempfile.TemporaryDirectory(
            prefix="MMRelay shortcut smoke ", dir=programs
        ) as menu,
    ):
        source = Path(temporary) / "Installed Relay"
        group = Path(menu)
        (group / "Setup Authentication.lnk").write_bytes(b"legacy shortcut fixture")
        (group / "unrelated.txt").write_text("preserve", encoding="utf-8")
        install_command = [
            str(installer),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            f"/DIR={source}",
            f"/GROUP={group.name}",
        ]
        run(
            install_command,
            env,
        )
        verify_auth_shortcuts(source, group, env)
        executable = source / "mmrelay.exe"
        if not executable.is_file():
            raise RuntimeError("Installer did not create mmrelay.exe")
        # Exercise the real frozen OAuth import/lock path without a live account.
        rejected = run(
            [
                str(executable),
                "--home",
                str(Path(temporary) / "oauth-home"),
                "auth",
                "login",
                "--oauth",
                "--homeserver",
                "http://127.0.0.1",
            ],
            env,
            expected_exit=1,
        )
        if "OAuth authentication failed:" not in rejected or "HTTPS" not in rejected:
            raise RuntimeError("Frozen OAuth validation did not reject insecure HTTP")
        probe = [str(executable), "migrate", "--to-default-home", "--check-needed"]
        run(probe, env, expected_exit=1)  # Fresh installs must not offer migration.
        for launcher in ("mmrelay.bat", "setup-auth.bat", "logout.bat"):
            if "--home" in (source / launcher).read_text(encoding="utf-8"):
                raise RuntimeError(
                    f"{launcher} pins data to the installation directory"
                )
        if not (source / "migrate-data.bat").is_file():
            raise RuntimeError("Guided migration launcher is missing")
        logout = (source / "logout.bat").read_text(encoding="utf-8")
        if logout.count("auth logout %*") != 1:
            raise RuntimeError("Logout launcher must run the destructive action once")
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
        legacy = source / "data" / "meshtastic.sqlite"
        legacy.parent.mkdir()
        with closing(sqlite3.connect(legacy)) as connection:
            connection.execute("CREATE TABLE probe(value INTEGER)")
            connection.execute("INSERT INTO probe VALUES (7)")
            connection.commit()
        leftovers = [legacy]
        for suffix in ("-wal", "-shm"):
            sidecar = legacy.with_name(legacy.name + suffix)
            sidecar.write_bytes(b"legacy sidecar")
            leftovers.append(sidecar)
        for path in leftovers:
            os.utime(path, (1, 1))
        plugin_data = source / "plugins" / "core" / "weather" / "data"
        plugin_data.mkdir(parents=True)
        (plugin_data / "state.json").write_text("{}", encoding="utf-8")
        custom = source / "plugins" / "custom"
        custom.mkdir()
        (custom / "standalone.py").write_text("VALUE = 42\n", encoding="utf-8")
        run(probe, env)  # The installed executable discovers this upgrade home.
        overridden = dict(env, MMRELAY_HOME=str(source))
        run(probe, overridden, expected_exit=1)
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
        run(install_command, env)
        verify_auth_shortcuts(source, group, env)
        if any(path.exists() for path in leftovers):
            raise RuntimeError("Obsolete database groups remain in the installer home")
        if not list((target / ".migration_backups").glob("legacy-databases-*")):
            raise RuntimeError("Legacy database archive is missing")
        run(probe, env, expected_exit=1)  # The offer disappears after relocation.
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
        # The guided shortcut must also finish cleanup after a completed move.
        legacy.write_bytes((target / "database" / "meshtastic.sqlite").read_bytes())
        run(probe, env, expected_exit=1)  # Do not offer another home relocation.
        run([str(executable), "migrate", "--to-default-home", "--dry-run"], env)
        if not legacy.exists():
            raise RuntimeError("Cleanup preview removed the legacy database")
        run([str(executable), "migrate", "--to-default-home"], env)
        if legacy.exists():
            raise RuntimeError("Repeated guided migration skipped database cleanup")
        run([str(executable), "migrate", "--to-default-home"], env)
    print("Installed Windows upgrade smoke test passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    args = parser.parse_args()
    verify(args.installer.resolve())


if __name__ == "__main__":
    main()
