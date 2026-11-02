# mindroom-nio 1.x compatibility

MMRelay uses mindroom-nio 1.3.0 on Python 3.12+. Both the base requirement and
the E2EE extra select the same provider. No environment should install multiple
packages that own the `nio` import namespace.

The Python 3.12 floor retires the 0.40.0 install path. Python 3.11 hosts can
stay on MMRelay 1.4.1, the last release that installs there. The 0.40.0
provider remains only as the previous-store fixture in CI; it is not an install
target for MMRelay.

## Compatibility boundary

The [upstream changelog](https://github.com/mindroom-ai/mindroom-nio/blob/1.3.0/CHANGELOG.md)
describes a breaking durability cutover and a Python 3.12 minimum. MMRelay uses
ordinary AsyncClient callbacks and Classic sync; it does not enable the 0.40
recovery/admission APIs removed by the 1.x cutover. The 0.40 limited-timeline
recovery options are absent from the 1.x client configuration.

MMRelay keeps ordinary client behavior. It does not attach `nio.durable`,
convert the relay to batch consumption, acknowledge SDK batches, or claim durable
event admission. That work would need an application-level design for committing
relay state before SDK acknowledgement, including duplicates, outbound radio
effects, restart, and cancellation. A dependency upgrade alone cannot provide
exactly-once relaying or lossless downtime recovery.

| Boundary                        | Treatment                                                                                                                |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Python and package installation | Select mindroom-nio 1.3.0 for Python 3.12+; keep base and E2EE constraints aligned.                                      |
| Matrix login and transport      | Keep ordinary restore_login and HTTP request APIs; exercise the real installed provider.                                 |
| Sync and event callbacks        | Keep Classic sync and async callback ordering; do not mix durable and ordinary transports.                               |
| Crypto and identity             | Reopen ordinary stores with the same account/device, trusted peers, sync cursor, and cross-signing public keys.          |
| Signature repair                | Retain the provider's device-signature upload hook and existing fail-closed identity policy.                             |
| Native OAuth                    | Keep the auth feature separate; validate its transport adapter against the supported provider before combining branches. |

## Automated checks

The main test matrix covers Python 3.12, 3.13, 3.14, and 3.15. It verifies
dependency consistency and the selected SDK, then runs the full suite. Real-SDK
contract tests use clean subprocesses because the existing unit-test fixtures
replace nio with doubles.

Contracts cover provider/backend detection, the immutable client configuration,
restored-session request headers, plaintext message responses, ordered async
callbacks, cursor handling, and preservation of ordinary crypto identities and
device trust across restart.
They also decrypt pending Olm and Megolm messages with sessions saved before
restart, including the 0.40.0-to-1.3.0 store upgrade.

The Python 3.12 CI lane also creates a separate 0.40.0 interpreter. A disposable
store created there is reopened by 1.3.0 and checked for identity/trust/cursor
preservation. Locally, point `MMRELAY_PREVIOUS_SDK_PYTHON` at a 0.40.0 environment
to run this contract. Its interpreter entry-point path must be preserved rather
than resolving the virtualenv's symlink to system Python.

```bash
MMRELAY_PREVIOUS_SDK_PYTHON=/path/to/previous/venv/bin/python \
  python -m pytest tests/test_matrix_sdk_contracts.py -v -W error --timeout=60
```

These checks use generated test identities, not real homeserver credentials.
The upgrade contract establishes only the tested ordinary-store scenario; it
does not validate pending recovery obligations, every historical store format,
or downgrade safety.

## Release validation

- Exercise password login, encrypted send/receive, key sharing, device-key
  rotation, restart, and shutdown against real homeservers.
- Exercise native OAuth refresh and revocation when combining the auth feature.
- Validate Docker, Windows plaintext deployments, and all supported Python
  versions through CI and deployment smoke tests.
- Back up the complete Matrix directory before testing an existing account.
  Do not reset identities or replace stores to work around an upgrade failure.
- Decide separately whether durable batch ingestion belongs in MMRelay; do not
  enable it as an incidental SDK-upgrade step.

No live homeserver or hardware validation is implied by this guide. General
release readiness and rollback guarantees remain unproven.

## Qualify a candidate SDK commit

The tests workflow accepts an optional full commit SHA from
`jeremiah-k/mindroom-nio` without changing MMRelay's release dependency pins:

```bash
gh workflow run test-and-coverage.yml --ref MMRELAY_BRANCH -f sdk_ref=FULL_SDK_COMMIT_SHA
```

The supported Python lanes install that source within constraints exported from
the locked MMRelay test environment, verify installed-source provenance, and run
the normal package-consistency and test checks. The SDK's package version must
satisfy the applicable declared dependency pin. The exact SDK commit appears in
the job summary. A dispatch without `sdk_ref` uses the released pins.

Record the MMRelay and SDK commits with qualification results. A successful
candidate dispatch qualifies that source combination; it does not publish a
release, change deployment sources, or prove recovery of lost signing keys.
