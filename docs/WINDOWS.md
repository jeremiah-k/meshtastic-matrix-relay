# Windows installation

For most Windows users, start with a **new installation**; migration is only
relevant if you have data from an older installer. Docker deployment follows
the [Docker Guide](DOCKER.md) and does not use the native Windows paths below.

## Install and run

With Python 3.11 or newer, install the native command in an isolated environment
using either [pipx](https://pipx.pypa.io/stable/) or
[uv](https://docs.astral.sh/uv/):

```powershell
pipx install mmrelay
# Or:
uv tool install mmrelay

mmrelay config generate
mmrelay auth login
mmrelay
```

To upgrade, use `pipx upgrade mmrelay` or `uv tool upgrade mmrelay`, matching
your installer. For direct device access, configure the serial, TCP, or BLE
connection in `config.yaml`. Matrix E2EE is not supported by the native Windows
build; see the [E2EE Guide](E2EE.md) for supported platforms.

The optional packaged Windows installer provides `mmrelay.exe` and Start-menu
shortcuts without requiring a separate Python installation. Its default install
is per-user, with application files under the selected installation directory
and writable data under `%LOCALAPPDATA%\mmrelay`.

Use `mmrelay paths` to see the actual configuration and data directories;
`mmrelay config edit` opens the active configuration. If the installer generates
a configuration, supply a full Matrix user ID such as `@relay:example.org` and
a Meshtastic channel number from 0 to 7. Leaving the installer password blank
allows authentication separately with `mmrelay auth login`. An existing
configuration is backed up before intentional replacement.

## Matrix authentication

Stop the relay before changing authentication. **MM Relay Setup Authentication**
keeps the existing password-login flow. **MM Relay OAuth Login** starts the
experimental native OAuth device flow: enter the homeserver, open the displayed
HTTPS address in a browser, and approve the displayed code. Keep the command
window open until credentials are saved. The shortcut does not ask for a password
or open a browser automatically; approval can be completed on another trusted
device.

OAuth is available on Windows independently of E2EE, which remains unsupported.
The homeserver must support native device authorization and public-client
registration; this does not make every SSO server compatible. Existing credentials
are not replaced automatically. See the [OAuth guide](MATRIX_OAUTH.md) for server
requirements, session changes, renewal, and revocation.

From PowerShell in the installation directory, the equivalent command is:

```powershell
.\mmrelay.exe auth login --oauth
```

Both authentication shortcuts use the active data home. Upgrades remove the
historical **Setup Authentication** alias in the selected Start-menu group;
password login and OAuth each have one distinctly named entry.

## Custom plugins in the packaged executable

The frozen executable includes a private Python runtime for installing plugin
dependencies. Community plugins still require the normal opt-in, an explicit
repository reference, and `install_requirements: true`; Git must be on PATH
to clone a community repository. Installed dependencies live beneath
`plugins/deps/<Python-ABI>-<platform>/` in the selected data home and are kept
separate across Python runtime upgrades.

Frozen installation requires compatible prebuilt Windows wheels; dependencies
available only as source distributions may require a native pipx/uv setup with
build tooling instead. Third-party plugins run trusted code inside MMRelay;
the included Python runtime is **not** a sandbox.

## Existing installer-directory data (only if applicable)

Older Windows installations may keep data next to `mmrelay.exe`, or under the
historical nested AppData home. Upgrades **do not move that data automatically**;
MMRelay continues to discover the existing home. If everything is working, no
migration is required merely because a newer installer is installed.

When a discovered older home can be relocated into an empty default data home, the installer's Finish page offers **Review and migrate existing MMRelay data**. This option is unchecked and opens the preview-and-confirm flow before any selected relay launch. It is absent on fresh installs, after migration, with explicit home overrides, or when the destination contains data. Silent installs do not offer or run migration. An applicable home also gets one startup reminder; nothing moves automatically.

If you _choose_ to relocate an older home:

1. Stop MMRelay and make an independent backup of the complete existing home.
2. Check the effective source with `mmrelay paths`.
3. Open **MM Relay Migrate Data** from the Start menu (or run `migrate-data.bat`), review the preview, and confirm only if both paths are correct.
4. Start MMRelay, inspect `mmrelay paths`, and verify configuration, Matrix credentials, database, and plugins before deleting old backups.

The guided operation refuses explicit home overrides, running instances, and
occupied destinations; it retains a snapshot under `.migration_backups`.
After the migration steps succeed, redundant legacy SQLite databases and their
sidecars are copied into `.migration_backups/legacy-databases-*`, byte-verified,
and removed from the live legacy directories. The active database is preserved,
and unrelated files are left alone. The preview lists these archive operations.
Configuration with custom absolute paths needs manual review. For unusual old
layouts, recovery, and CLI migration options, consult the
[historical migration reference](MIGRATION_1.3.md#windows-specific-notes).
