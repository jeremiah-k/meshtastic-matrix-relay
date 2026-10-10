# Remote admin integration tests

These local Docker tests exercise a real Synapse homeserver, MMRelay, and two native meshtasticd nodes. They require no physical radios. The success profile requires an authorized peer's configuration value in the Matrix reply. The forced-simradio profile covers origin-router rejection latency separately.

## Prerequisites

- Docker Engine with bridge networking and permission to run containers.
- Bash, GNU `timeout`, and Python with MMRelay and its test dependencies installed.
- An mtjk build with embedded command API version 1 and request-correlated origin-router rejection handling.
- Outbound access to pull the pinned container images on the first run.

Run from the repository root. Select the interpreter used to install MMRelay. When testing an mtjk checkout, install it into that same environment:

```bash
export PYTHON_BIN="$PWD/.venv/bin/python"
uv pip install --python "$PYTHON_BIN" --no-deps -e /path/to/mtjk
```

Use immutable image digests for repeatable results. The following daily and Synapse images were exercised together:

```bash
export MESHTASTICD_IMAGE="meshtastic/meshtasticd@sha256:11c1c987eb59a2442633d45d7162705c540b03aee84aee1b7227baeafe44a9f6"
export SYNAPSE_IMAGE="matrixdotorg/synapse@sha256:f223f5fc532600801284fededefca82288fb71551bf4fcc89cd0b3363a3461cc"
```

The meshtasticd image is 2.8.2.9084439 daily. Override the digest to exercise another firmware build. MESHTASTICD_BINARY defaults to `/usr/bin/meshtasticd`; an absolute executable path is required for native firmware reboots.

## Successful PKI round trip

```bash
timeout --foreground 15m bash scripts/ci/run-mmrelay-remote-admin-success-integration.sh
```

The success wrapper selects the simulated radio using native YAML (`Lora.Module: sim`) without the `-s` option, which disables PKI origination on the tested firmware. It configures matching channels and UDP broadcast, provisions a different fixed test private key on each node, and lets firmware derive the corresponding public keys and identities. Fixture scalars are clamped for the shared X25519/XEdDSA identity. These publicly reproducible keys belong only in this disposable test environment.

The peer authorizes the relay's derived public key in security.admin_key. Legacy channel administration stays disabled. Both nodes must discover each other's public keys before MMRelay takes the firmware's single-client TCP API connection.

The relay's hop limit is 2 and the peer's is 5. Scenario 8 submits `!admin --dest <peer> --get lora.hop_limit` and requires both `lora.hop_limit: 5` and exit code 0 in the bot's reply to that exact Matrix command event. A local value, timeout, or routing error cannot pass. Missing peer discovery fails the success profile.

The remaining scenarios cover plugin-owned room invitation without a matrix_rooms mapping, help, power-level authorization, separately correlated replies to back-to-back commands, all three invalid destinations, and local-node-only flag refusal. Concurrent lock admission and embedded capability/version gating are covered by the targeted Python tests; sequential Matrix timeline delivery alone does not prove concurrent admission behavior.

## Origin-router fast failure

```bash
RA_ADMIN_REQUIRE_MESH=true timeout --foreground 15m bash scripts/ci/run-mmrelay-remote-admin-integration.sh
```

This profile launches nodes with `-s`. Firmware 2.8.2 daily and 2.7.26 beta reject client-flagged PKI admin requests locally with PKI_FAILED before transmission. Scenario 8 requires a PKI_FAILED Matrix reply to the exact command event within five seconds, with the plugin's command budget set to 30 seconds. A generic exception name or timeout fails. A fast-failure pass establishes rejection handling, not a successful remote round trip.

The 2.7.26 beta image tested for this profile is:

```bash
export MESHTASTICD_IMAGE="meshtastic/meshtasticd@sha256:23e92b1331a3a471eaef0c63cbca4365ca40b3111a9781cfdbe5a5114e5773d4"
```

## Artifacts and isolation

The success profile defaults to `.ci-artifacts/remote-admin-success-integration`; the default profile uses `.ci-artifacts/remote-admin-integration`. Each run writes a scenario summary under `shared/observability-summary.md` and captures firmware and MMRelay logs. The summary distinguishes returned peer values from routing failures.

Runtime configuration and logs can contain disposable Matrix credentials and private-key material. Share the summary rather than the complete artifact directory.

The harness removes its containers and network and stops its MMRelay process on exit. It replaces resources with the configured names at startup, so simultaneous runs must use distinct NETWORK_NAME, MESHTASTICD_CONTAINER_RELAY, MESHTASTICD_CONTAINER_PEER, SYNAPSE_CONTAINER, SYNAPSE_PORT_DEC, and CI_ARTIFACT_DIR values. Synapse binds to loopback only. Serial runs can use the defaults. MATRIX_EVENT_TIMEOUT_SECONDS and the image digests are configurable; all waits remain bounded.

These scripts run locally and do not dispatch hosted CI. Packet signing does not bypass target authorization or the forced-simradio PKI restriction. Neither profile automatically downgrades admin encryption.
