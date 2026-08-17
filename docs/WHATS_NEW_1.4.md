# What's New in 1.4.0

MMRelay 1.4.0 is primarily a reliability, encryption, and platform-baseline
release. It raises the minimum Python version to 3.11 and incorporates the BLE,
Matrix E2EE, and logging hardening developed since 1.3.8.

## Before upgrading

1. Upgrade the runtime to Python 3.11 or newer.
2. If you still use pre-1.3 file locations or legacy `MMRELAY_*_DIR`
   environment variables, run `mmrelay migrate --dry-run`, then `mmrelay
   migrate` and `mmrelay verify-migration`.
3. Back up the Matrix E2EE store, credentials, and cross-signing sidecar
   together before changing authentication or storage.
4. For containers or Kubernetes, update the image tag to `1.4.0` after the
   release image is available.

## Python 3.11 minimum

Matplotlib 3.11 requires Python 3.11 or newer. MMRelay uses Matplotlib for
telemetry graphs, so retaining Python 3.10 in package metadata would advertise
an environment that cannot install the required dependency stack.

Python 3.10 systems can remain on the final MMRelay 1.3.x release. Recreate
virtual environments or pipx installations after upgrading Python so compiled
packages are installed for the new interpreter.

## Matrix E2EE and device identity

MMRelay now uses mindroom-nio 0.40 and verifies the bot device's server-visible
cross-signing chain rather than treating local state alone as proof of trust.
The relay can create or reuse its bot-account cross-signing identity and sign
its own Matrix device. It does not verify other users or link Matrix identity
to Meshtastic identity.

Normal startup fails closed when the homeserver already has a cross-signing
identity but the matching local private sidecar is unavailable. It does not
silently rotate the account trust root. If the sidecar is irrecoverably lost,
`mmrelay auth login --reset-cross-signing` provides an explicit,
password-authenticated recovery path. That reset replaces the account's
cross-signing identity, so other Matrix clients may require verification again.
See [E2EE.md](E2EE.md) before using it.

## BLE and Meshtastic reliability

The Meshtastic dependency is the mtjk fork, currently pinned to the tested
release in `pyproject.toml`. The 1.4 work includes additional relay-side BLE
shutdown protection, daemonized worker handling, stale-work cleanup, and probe
timing that waits for startup/reconnect stabilization before sending metadata
traffic.

These relay safeguards complement mtjk's own BLE lifecycle and reconnect
handling; they do not replace it.

## Logging reliability

File logging now uses one shared rotating handler with consistent path
expansion and refresh behavior. Component loggers are reattached when logging
configuration is refreshed, and path-reporting commands agree with the file
actually being written.

## Final legacy-layout migration window

The previously announced removal of v1.3 legacy-layout compatibility is deferred
one release. **The 1.4 release series is the final bridge release that ships the
legacy path fallbacks and `migrate`/`verify-migration` tooling.**

This is intentional: users upgrading directly from MMRelay 1.2 or older should
not have to install an intermediate 1.3 build merely to move their data. Finish
migration during 1.4. Legacy layout compatibility and migration commands are
planned for removal in 1.5.

See [MIGRATION_1.3.md](MIGRATION_1.3.md) for the migration procedure.

## Packaging and dependency updates

The release also rolls forward the supported dependency and CI stack, including
Python 3.11-3.14 testing, mindroom-nio 0.40, recent mtjk releases, and current
pinned GitHub Actions/container dependencies.

Release workflows validate that the release tag matches `project.version`
before publishing package, container, or Windows installer artifacts, preventing
an automated post-release version bump from racing release builds.

## Maintainer notes

- Package metadata, runtime checks, type checking, Windows guidance, and CI use
  the same Python 3.11 minimum.
- The Python 3.10 source-checkout TOML fallback is gone in favor of Python
  3.11's standard-library `tomllib`.
- Large plugin/CLI/connection refactors are intentionally deferred unless they
  fix a demonstrated release-blocking defect.
