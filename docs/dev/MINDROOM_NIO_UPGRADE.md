# mindroom-nio 1.x compatibility draft

This draft uses mindroom-nio 1.1.2 on Python 3.12+ and retains 0.40.0 on Python
3.11. Both the base requirement and the E2EE extra select the same provider.
MMRelay's Python minimum remains 3.11. No environment should install multiple
packages that own the `nio` import namespace.

## Compatibility boundary

The [upstream changelog](https://github.com/mindroom-ai/mindroom-nio/blob/1.1.2/CHANGELOG.md)
describes a breaking durability cutover and a Python 3.12 minimum. MMRelay uses
ordinary AsyncClient callbacks and Classic sync; it does not enable the 0.40
recovery/admission APIs removed by the 1.x cutover. Its client configuration also
leaves 0.40's limited-timeline recovery option disabled.

This draft keeps ordinary client behavior. It does not attach `nio.durable`,
convert the relay to batch consumption, acknowledge SDK batches, or claim durable
event admission. That work would need an application-level design for committing
relay state before SDK acknowledgement, including duplicates, outbound radio
effects, restart, and cancellation. A dependency upgrade alone cannot provide
exactly-once relaying or lossless downtime recovery.

| Boundary                        | Draft treatment                                                                                                     |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Python and package installation | Select 0.40.0 for 3.11 and 1.1.2 for 3.12+; keep base and E2EE constraints aligned.                                 |
| Matrix login and transport      | Keep ordinary restore_login and HTTP request APIs; exercise the real installed provider.                            |
| Sync and event callbacks        | Keep Classic sync and async callback ordering; do not mix durable and ordinary transports.                          |
| Crypto and identity             | Reopen ordinary stores with the same account/device, trusted peers, sync cursor, and cross-signing public keys.     |
| Signature repair                | Retain the provider's device-signature upload hook and existing fail-closed identity policy.                        |
| Native OAuth                    | Keep the auth feature separate; validate its transport adapter against both SDK versions before combining branches. |

## Automated checks

The main test matrix already covers Python 3.11, 3.12, 3.13, and 3.14. It verifies
dependency consistency and the selected SDK, then runs the full suite. Real-SDK
contract tests use clean subprocesses because the existing unit-test fixtures
replace nio with doubles.

Contracts cover provider/backend detection, the immutable client configuration,
restored-session request headers, plaintext message responses, ordered async
callbacks, cursor handling, and preservation of ordinary crypto identities and
device trust across restart.
They also decrypt pending Olm and Megolm messages with sessions saved before
restart, including the 0.40.0-to-1.1.2 store upgrade.

The Python 3.12 CI lane also creates a separate 0.40.0 interpreter. A disposable
store created there is reopened by 1.1.2 and checked for identity/trust/cursor
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

## Work before merging

- Exercise password login, encrypted send/receive, key sharing, device-key
  rotation, restart, and shutdown against real homeservers.
- Exercise native OAuth refresh and revocation when combining the auth feature.
- Validate Docker, Windows plaintext deployments, and all supported Python
  versions through CI and deployment smoke tests.
- Back up the complete Matrix directory before testing an existing account.
  Do not reset identities or replace stores to work around an upgrade failure.
- Decide separately whether durable batch ingestion belongs in MMRelay; do not
  enable it as an incidental SDK-upgrade step.

No live homeserver or hardware validation is implied by this draft. General
release readiness and rollback guarantees remain unproven.
