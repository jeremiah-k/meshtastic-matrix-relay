#!/usr/bin/env bash
# =============================================================================
# MMRelay Remote Admin (remote_admin plugin) integration harness
# =============================================================================
#
# Exercises the remote_admin plugin end to end against real meshtasticd nodes
# and a real Synapse homeserver:
#
#   Architecture
#     meshtasticd relay (bridge net, UDP multicast) <- MMRelay <- admin room
#     meshtasticd peer  (bridge net, UDP multicast)      remote admin target
#
#   Profiles
#     Default: force simradio with -s and require a named
#     PKI_FAILED Matrix reply within five seconds of a remote command.
#     Positive: run run-mmrelay-remote-admin-success-integration.sh. Select
#     Lora.Module: sim through native YAML without -s, preserving PKI. Provision
#     fixed private-key fixtures, authorize the relay's derived public key on
#     the peer, and require the peer's hop limit 5 (the relay's is 2).
#
#   Both profiles use separate bridge-network namespaces, a configured LoRa
#   region, matching channels, and network.enabled_protocols=UDP_BROADCAST.
#   Complete all node discovery before MMRelay owns the single-client TCP API.
#   The positive profile leaves legacy admin disabled and cannot pass on a NAK.
#
#   Test scenarios
#     0. Bot identity/auth established
#     1. Bot accepts an invite for a plugin-owned, unmapped admin room
#     2. !admin help is served in the admin room
#     3. Sender below the power-level threshold is refused
#     4. Authorized sender's command executes and returns an exit code
#     5. Match separate replies to back-to-back commands
#     6. Refuse local/broadcast/own-node/unknown destinations and unsupported flags
#     7. Verify the peer in the relay's node database before MMRelay starts
#     8. Require the profile's remote --get outcome through its exact Matrix reply
#
#   Firmware 2.7.26 beta and 2.8.2 daily disable PKC origination under -s.
#   Packet signing does not bypass that restriction or authorize admin access.
#   PKI requires the relay's public key in the target's security.admin_key list.
#   No profile downgrades encryption automatically after a rejection.
#   See docs/dev/REMOTE_ADMIN_INTEGRATION.md for setup and pinned images.
#
#   Environment Variables
#     MESHTASTICD_IMAGE: meshtasticd image (default meshtastic/meshtasticd:latest)
#     SYNAPSE_IMAGE: Synapse image (default matrixdotorg/synapse:latest)
#     PYTHON_BIN: python interpreter that has mmrelay installed (required)
#     MMRELAY_LOG_ON_SUCCESS: always show relay logs (default false)
#     MATRIX_EVENT_TIMEOUT_SECONDS: per-assertion Matrix poll timeout
#     ADMIN_POWER_LEVEL: power level the authorized sender must hold (default 50)
#     RA_ADMIN_REQUIRE_MESH: fail instead of skipping scenario 7/8 (default false)
#     RA_ADMIN_REQUIRE_SUCCESS: require a peer value over PKI (default false)
# =============================================================================

set -euo pipefail

# =============================================================================
# Configuration
# =============================================================================

PYTHON_BIN="${PYTHON_BIN:?PYTHON_BIN must point at a python with mmrelay installed}"
MESHTASTICD_IMAGE="${MESHTASTICD_IMAGE:-meshtastic/meshtasticd:latest}"
# Native reboot uses execv(argv[0]); supply an absolute executable path.
MESHTASTICD_BINARY="${MESHTASTICD_BINARY:-/usr/bin/meshtasticd}"
SYNAPSE_IMAGE="${SYNAPSE_IMAGE:-matrixdotorg/synapse:latest}"
MMRELAY_LOG_ON_SUCCESS="${MMRELAY_LOG_ON_SUCCESS:-false}"
MATRIX_EVENT_TIMEOUT_SECONDS="${MATRIX_EVENT_TIMEOUT_SECONDS:-75}"
ADMIN_POWER_LEVEL="${ADMIN_POWER_LEVEL:-50}"
RA_ADMIN_REQUIRE_MESH="${RA_ADMIN_REQUIRE_MESH:-false}"
RA_ADMIN_REQUIRE_SUCCESS="${RA_ADMIN_REQUIRE_SUCCESS:-false}"
# Executor timeout written into the relay config. Test 8's reply window must
# strictly exceed this: the executor's bounded wait fires at this many seconds
# and only then replies, so an equal test window misses the reply by design.
RA_PLUGIN_TIMEOUT_SECONDS="${RA_PLUGIN_TIMEOUT_SECONDS:-30}"

CI_ARTIFACT_DIR="${CI_ARTIFACT_DIR:-$(pwd)/.ci-artifacts/remote-admin-integration}"
if [[ ${CI_ARTIFACT_DIR} != /* ]]; then
	CI_ARTIFACT_DIR="${PWD}/${CI_ARTIFACT_DIR}"
fi
HOST_UID="$(id -u)"
HOST_GID="$(id -g)"

# Container / network naming (bridge network is required for meshing)
NETWORK_NAME="${NETWORK_NAME:-mmrelay-ra-net}"
MESHTASTICD_CONTAINER_RELAY="${MESHTASTICD_CONTAINER_RELAY:-mmrelay-ra-mesh-relay}"
MESHTASTICD_CONTAINER_PEER="${MESHTASTICD_CONTAINER_PEER:-mmrelay-ra-mesh-peer}"
SYNAPSE_CONTAINER="${SYNAPSE_CONTAINER:-mmrelay-ra-synapse}"

MESHTASTICD_PORT_RELAY="${MESHTASTICD_PORT_RELAY:-4603}"
MESHTASTICD_PORT_PEER="${MESHTASTICD_PORT_PEER:-4605}"
MESHTASTICD_HWID_RELAY="${MESHTASTICD_HWID_RELAY:-11}"
MESHTASTICD_HWID_PEER="${MESHTASTICD_HWID_PEER:-33}"
MESHTASTICD_READY_TIMEOUT_SECONDS="${MESHTASTICD_READY_TIMEOUT_SECONDS:-180}"
MESH_PEER_VISIBLE_TIMEOUT_SECONDS="${MESH_PEER_VISIBLE_TIMEOUT_SECONDS:-90}"

MESH_CHANNEL_NAME="${MESH_CHANNEL_NAME:-admin}"
MESH_PRIMARY_PSK="${MESH_PRIMARY_PSK:-base64:qg==}"
LORA_REGION="${LORA_REGION:-US}"

SYNAPSE_SERVER_NAME="${SYNAPSE_SERVER_NAME:-localhost}"
SYNAPSE_PORT_DEC="${SYNAPSE_PORT_DEC:-18008}"
SYNAPSE_READY_TIMEOUT_SECONDS="${SYNAPSE_READY_TIMEOUT_SECONDS:-180}"
MATRIX_BASE_URL="http://localhost:${SYNAPSE_PORT_DEC}"
SYNAPSE_SHARED_SECRET="${SYNAPSE_SHARED_SECRET:-mmrelay-ra-shared-secret}"

MATRIX_BOT_LOCALPART="${MATRIX_BOT_LOCALPART:-mmrelay-rabot}"
MATRIX_BOT_PASSWORD="${MATRIX_BOT_PASSWORD:-mmrelay-rabot-password}"
MATRIX_USER_LOCALPART="${MATRIX_USER_LOCALPART:-mmrelay-rauser}"
MATRIX_USER_PASSWORD="${MATRIX_USER_PASSWORD:-mmrelay-rauser-password}"
MATRIX_LOWPRIV_LOCALPART="${MATRIX_LOWPRIV_LOCALPART:-mmrelay-ralowpriv}"
MATRIX_LOWPRIV_PASSWORD="${MATRIX_LOWPRIV_PASSWORD:-mmrelay-ralowpriv-password}"

SHARED_DIR="${CI_ARTIFACT_DIR}/shared"
INSTANCE_DIR="${CI_ARTIFACT_DIR}/instance"
SYNAPSE_DATA_DIR="${SHARED_DIR}/synapse"
MMRELAY_HOME_DIR="${INSTANCE_DIR}/mmrelay-home"
MMRELAY_CONFIG_PATH="${INSTANCE_DIR}/config.yaml"
MMRELAY_LOG_PATH="${MMRELAY_HOME_DIR}/logs/mmrelay.log"
MMRELAY_STDOUT_LOG_PATH="${INSTANCE_DIR}/mmrelay-stdout.log"
MMRELAY_DB_PATH="${MMRELAY_HOME_DIR}/database/meshtastic.sqlite"
MATRIX_RUNTIME_JSON="${SHARED_DIR}/matrix-runtime.json"
MESHTASTICD_LOG_RELAY="${INSTANCE_DIR}/meshtasticd-relay.log"
MESHTASTICD_LOG_PEER="${INSTANCE_DIR}/meshtasticd-peer.log"
SYNAPSE_LOG_PATH="${SHARED_DIR}/synapse.log"

MESH_RELAY_IP=""
MESH_PEER_IP=""
RELAY_NODE_ID=""
PEER_NODE_ID=""
ROOM_ID_ADMIN=""
MMRELAY_PID=""

# Test tracking
LOGS_PRINTED=false
OBSERVABILITY_WRITTEN=false
SUITE_START_MS=0
CURRENT_TEST_NAME=""
CURRENT_TEST_START_MS=0

declare -a TEST_RESULT_NAMES=()
declare -a TEST_RESULT_STATUS=()
declare -a TEST_RESULT_DURATION_MS=()
declare -a TEST_RESULT_NOTES=()

# =============================================================================
# Utility functions (mirroring run-mmrelay-meshtasticd-integration.sh)
# =============================================================================

require_regex() {
	local value=$1
	local pattern=$2
	local name=$3
	if [[ ! ${value} =~ ${pattern} ]]; then
		echo "Invalid ${name}: ${value}" >&2
		exit 1
	fi
}

run_or_fail() {
	local message=$1
	shift
	if ! "$@"; then
		fail_test "${message}"
	fi
}

record_test_result() {
	local status=$1
	local note="${2-}"
	local end_ms
	end_ms=$(date +%s%3N)
	local duration_ms=$((end_ms - CURRENT_TEST_START_MS))
	TEST_RESULT_NAMES+=("${CURRENT_TEST_NAME}")
	TEST_RESULT_STATUS+=("${status}")
	TEST_RESULT_DURATION_MS+=("${duration_ms}")
	TEST_RESULT_NOTES+=("${note}")
}

start_test() {
	local test_name=$1
	local test_label=$2
	CURRENT_TEST_NAME="${test_name}"
	CURRENT_TEST_START_MS=$(date +%s%3N)
	echo ""
	echo "${test_label}"
}

pass_test() {
	local note=$1
	record_test_result "PASSED" "${note}"
	echo "✓ ${CURRENT_TEST_NAME} PASSED: ${note}"
}

skip_test() {
	local note=$1
	record_test_result "SKIPPED" "${note}"
	echo "⊘ ${CURRENT_TEST_NAME} SKIPPED: ${note}"
}

fail_test() {
	local note=$1
	record_test_result "FAILED" "${note}"
	echo "✗ ${CURRENT_TEST_NAME} FAILED: ${note}" >&2
	write_observability_report
	exit 1
}

# invite_bot_to_admin_room invites the bot to the plugin-owned admin room.
# This must happen AFTER the bot is running: on_invite is driven by a live
# /sync, so an invite issued before the bot's first sync is never delivered
# as an invite event (the initial sync only reports already-joined rooms).
invite_bot_to_admin_room() {
	"${PYTHON_BIN}" - "${MATRIX_BASE_URL}" "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" "${BOT_USER_ID}" <<'PY'
import sys
import urllib.parse

import requests

base_url, token, room_id, bot_user_id = sys.argv[1:5]
quoted = urllib.parse.quote(room_id, safe="")
response = requests.post(
    f"{base_url}/_matrix/client/v3/rooms/{quoted}/invite",
    headers={"Authorization": f"Bearer {token}"},
    json={"user_id": bot_user_id},
    timeout=30,
)
if response.status_code >= 400:
    raise SystemExit(f"invite failed ({response.status_code}): {response.text}")
print(f"invited {bot_user_id} to {room_id}")
PY
}

# wait_for_relay_log_line waits until PATTERN appears in the relay stdout log.
wait_for_relay_log_line() {
	local pattern=$1
	local timeout_seconds=$2
	local deadline=$((SECONDS + timeout_seconds))
	while ((SECONDS < deadline)); do
		if grep -qi "${pattern}" "${MMRELAY_STDOUT_LOG_PATH}" 2>/dev/null; then
			return 0
		fi
		sleep 2
	done
	return 1
}

stop_process() {
	local pid=$1
	local name=$2
	if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
		echo "Stopping ${name} (PID ${pid})..."
		kill "${pid}" >/dev/null 2>&1 || true
		local deadline=$((SECONDS + 10))
		while kill -0 "${pid}" >/dev/null 2>&1 && ((SECONDS < deadline)); do
			sleep 0.2
		done
		if kill -0 "${pid}" >/dev/null 2>&1; then
			kill -KILL "${pid}" >/dev/null 2>&1 || true
		fi
		wait "${pid}" 2>/dev/null || true
	fi
}

print_logs_if_needed() {
	local exit_code=$1
	if [[ ${LOGS_PRINTED} == true ]]; then
		return
	fi
	LOGS_PRINTED=true
	if [[ ${exit_code} -ne 0 || "${MMRELAY_LOG_ON_SUCCESS}" == "true" ]]; then
		echo ""
		echo "===== MMRelay log (tail 80) ====="
		tail -n 80 "${MMRELAY_LOG_PATH}" 2>/dev/null || true
		if [[ -f "${MESHTASTICD_LOG_RELAY}" ]]; then
			echo ""
			echo "===== meshtasticd relay log (tail 40) ====="
			tail -n 40 "${MESHTASTICD_LOG_RELAY}" 2>/dev/null || true
		fi
		if [[ -f "${MESHTASTICD_LOG_PEER}" ]]; then
			echo ""
			echo "===== meshtasticd peer log (tail 40) ====="
			tail -n 40 "${MESHTASTICD_LOG_PEER}" 2>/dev/null || true
		fi
	fi
}

write_observability_report() {
	if [[ ${OBSERVABILITY_WRITTEN} == true ]]; then
		return 0
	fi
	OBSERVABILITY_WRITTEN=true

	local suite_end_ms
	suite_end_ms=$(date +%s%3N)
	local suite_duration_ms=$((suite_end_ms - SUITE_START_MS))
	local total_tests=${#TEST_RESULT_NAMES[@]}
	local passed=0
	local failed=0
	local skipped=0
	local status
	for status in "${TEST_RESULT_STATUS[@]}"; do
		case "${status}" in
		PASSED) passed=$((passed + 1)) ;;
		FAILED) failed=$((failed + 1)) ;;
		SKIPPED) skipped=$((skipped + 1)) ;;
		esac
	done

	local summary="${SHARED_DIR}/observability-summary.md"
	{
		echo "# Remote admin integration summary"
		echo ""
		echo "Suite: ${passed}/${total_tests} passed, ${failed} failed, ${skipped} skipped, ${suite_duration_ms} ms"
		echo "Relay node: ${RELAY_NODE_ID:-unknown}    Admin target: ${PEER_NODE_ID:-none}"
		echo ""
		echo "| Test | Status | Duration (ms) | Note |"
		echo "|---|---|---|---|"
		local i
		for i in "${!TEST_RESULT_NAMES[@]}"; do
			echo "| ${TEST_RESULT_NAMES[$i]} | ${TEST_RESULT_STATUS[$i]} | ${TEST_RESULT_DURATION_MS[$i]} | ${TEST_RESULT_NOTES[$i]} |"
		done
		echo ""
		if [[ ${RA_ADMIN_REQUIRE_SUCCESS} == true ]]; then
			echo "## Positive PKI round trip (scenario 8)"
			echo ""
			echo "Select the simulated radio with native YAML, preserving PKI encryption."
			echo "Provision deterministic fixture private keys and let firmware derive public keys."
			echo "Authorize only the relay's derived key on the peer; leave legacy admin disabled."
			echo "Require the peer's configured hop limit 5 and exit code 0 in the Matrix reply."
			echo "The relay's own hop limit is 2. A routing failure cannot satisfy this scenario."
		else
			echo "## Transport limitation (scenario 8)"
			echo ""
			echo "Remote \`--dest\` round trips fail at the ORIGIN router in this simradio"
			echo "environment: mtjk sends admin messages with pkiEncrypted=True, and the"
			echo "firmware refuses client-flagged PKI under \`-s\` simradio"
			echo "with \`Error=34\` (PKI_FAILED), NAKing the request before it airs."
			echo "Scenario 8 requires a Matrix error reply naming PKI_FAILED within"
			echo "five seconds. A fast-fail pass does not prove"
			echo "a remote admin round trip or target authorization."
		fi
	} >"${summary}"

	echo ""
	echo "============================================================================"
	echo "Observability Summary"
	echo "============================================================================"
	echo "Suite: ${passed}/${total_tests} passed, ${failed} failed, ${skipped} skipped, ${suite_duration_ms} ms"
	echo "Relay: ${RELAY_NODE_ID:-unknown}  Target: ${PEER_NODE_ID:-none}"
	echo "Detailed summary: ${summary}"
}

cleanup() {
	local exit_code=$?
	# Preserve the failure status while attempting every cleanup step. In an
	# EXIT trap, a bare return can inherit the original nonzero exit status.
	trap - EXIT INT TERM
	set +e
	if ((SUITE_START_MS > 0)); then
		write_observability_report
	fi
	stop_process "${MMRELAY_PID}" "MMRelay"
	if docker container inspect "${MESHTASTICD_CONTAINER_RELAY}" >/dev/null 2>&1; then
		docker logs "${MESHTASTICD_CONTAINER_RELAY}" >"${MESHTASTICD_LOG_RELAY}" 2>&1 || true
		docker rm -f "${MESHTASTICD_CONTAINER_RELAY}" >/dev/null 2>&1 || true
	fi
	if docker container inspect "${MESHTASTICD_CONTAINER_PEER}" >/dev/null 2>&1; then
		docker logs "${MESHTASTICD_CONTAINER_PEER}" >"${MESHTASTICD_LOG_PEER}" 2>&1 || true
		docker rm -f "${MESHTASTICD_CONTAINER_PEER}" >/dev/null 2>&1 || true
	fi
	if docker container inspect "${SYNAPSE_CONTAINER}" >/dev/null 2>&1; then
		docker logs "${SYNAPSE_CONTAINER}" >"${SYNAPSE_LOG_PATH}" 2>&1 || true
		docker rm -f "${SYNAPSE_CONTAINER}" >/dev/null 2>&1 || true
	fi
	if docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1; then
		docker network rm "${NETWORK_NAME}" >/dev/null 2>&1 || true
	fi
	print_logs_if_needed "${exit_code}"
	exit "${exit_code}"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# =============================================================================
# meshtasticd helpers
# =============================================================================

# wait_for_meshtasticd_ready waits until the daemon at ENDPOINT answers --info.
wait_for_meshtasticd_ready() {
	local endpoint=$1
	local container=$2
	local deadline=$((SECONDS + MESHTASTICD_READY_TIMEOUT_SECONDS))
	until "${PYTHON_BIN}" -m meshtastic --timeout 5 --host "${endpoint}" --info >/dev/null 2>&1; do
		if ! docker ps --format '{{.Names}}' | grep -Fxq "${container}"; then
			echo "${container} exited before becoming ready." >&2
			return 1
		fi
		if ((SECONDS >= deadline)); then
			echo "meshtasticd ${endpoint} not ready within ${MESHTASTICD_READY_TIMEOUT_SECONDS}s." >&2
			return 1
		fi
		sleep 2
	done
	echo "meshtasticd ${endpoint} is ready."
}

# get_local_node_id returns the node ID in !xxxxxxxx form.
get_local_node_id() {
	local endpoint=$1
	local info_output
	info_output="$("${PYTHON_BIN}" -m meshtastic --timeout 20 --host "${endpoint}" --info 2>/dev/null || true)"
	local node_id
	node_id="$(printf '%s\n' "${info_output}" | grep -Eo '![0-9a-fA-F]{8}' | head -n1 || true)"
	if [[ -z ${node_id} ]]; then
		return 1
	fi
	printf '%s\n' "${node_id,,}"
}

# meshtastic_set runs one meshtastic CLI write against ENDPOINT, retrying a
# few times. Freshly started simradio nodes occasionally drop the first admin
# exchange while their config settles, and the failure is transient.
meshtastic_set() {
	local endpoint=$1
	shift
	local attempt
	for attempt in 1 2 3; do
		# Allow the simulator to finish closing the preceding TCP API client.
		sleep 1
		if "${PYTHON_BIN}" -m meshtastic --timeout 25 --host "${endpoint}" "$@" >>"${INSTANCE_DIR}/mesh-config-cli.log" 2>&1; then
			return 0
		fi
		echo "  retrying (attempt ${attempt}/3): $*" >&2
		sleep 4
	done
	return 1
}

# apply_mesh_config applies the settings simradio meshing requires.
# Config is only read at boot, so callers must restart the node afterwards.
# Only the settings that actually gate meshing are required. Note there is
# deliberately NO device.node_info_broadcast_secs write here: the firmware
# minimum is 1 hour (Default.h min_node_info_broadcast_secs = 3600; values
# below are clamped with only a debug log, AdminModule.cpp:946), and
# non-router roles are pinned to the default at fresh-config init
# (NodeDB.cpp:1100-1102), so the write is always refused. Discovery does not
# need it: fresh nodes burst their NodeInfo ~30s after boot.
apply_mesh_config() {
	local endpoint=$1
	meshtastic_set "${endpoint}" --set "lora.region" "${LORA_REGION}" || return 1
	meshtastic_set "${endpoint}" --set "network.enabled_protocols" "UDP_BROADCAST" || return 1
	meshtastic_set "${endpoint}" --ch-set name "${MESH_CHANNEL_NAME}" --ch-index 0 || return 1
	meshtastic_set "${endpoint}" --ch-set psk "${MESH_PRIMARY_PSK}" --ch-index 0 || return 1
	return 0
}

# Fixture identities belong only to this disposable integration environment.
# Set region first, since initial region selection can regenerate the keypair.
provision_private_key() {
	local endpoint=$1
	local identity=$2
	"${PYTHON_BIN}" - "${endpoint}" "${identity}" <<'PYKEY'
import hashlib
import sys
import time

from meshtastic.errors import RequestError
from meshtastic.tcp_interface import TCPInterface

host, port = sys.argv[1].rsplit(":", 1)
# Public test fixtures, never production identities. Clamp once for firmware's
# shared X25519/XEdDSA identity; firmware derives the corresponding public key.
private_key = bytearray(hashlib.sha256(
    f"mmrelay-remote-admin-test-{sys.argv[2]}".encode()
).digest())
private_key[0] &= 0xF8
private_key[31] = (private_key[31] & 0x7F) | 0x40
with TCPInterface(hostname=host, portNumber=int(port)) as client:
    security = client.localNode.readConfig("security", timeout=10).security
    client.localNode.localConfig.security.CopyFrom(security)
    client.localNode.localConfig.security.private_key = bytes(private_key)
    client.localNode.localConfig.security.ClearField("public_key")
    # Exact staged-value verification would reject the derived public key.
    # Verify the persisted private key and derived public key after reboot.
    client.localNode.writeConfig("security")
    time.sleep(1)

time.sleep(8)
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    try:
        with TCPInterface(hostname=host, portNumber=int(port)) as client:
            security = client.localNode.readConfig("security", timeout=10).security
    except (OSError, RequestError, TimeoutError) as error:
        last_error = type(error).__name__
        time.sleep(2)
        continue
    assert security.private_key == bytes(private_key), "fixture private key was not persisted"
    assert len(security.public_key) == 32, "firmware did not derive the public key"
    assert not security.admin_channel_enabled, "legacy admin must remain disabled"
    print(f"Provisioned deterministic {sys.argv[2]} identity with firmware-derived public key.")
    break
else:
    raise SystemExit(f"Fixture key provisioning was not verified: {last_error}")
PYKEY
}

prepare_pki_peer() {
	local relay_key
	relay_key=$(
		"${PYTHON_BIN}" - "${MESH_RELAY_ENDPOINT}" <<'PY'
import sys

from meshtastic.tcp_interface import TCPInterface

host, port = sys.argv[1].rsplit(":", 1)
with TCPInterface(hostname=host, portNumber=int(port)) as client:
    security = client.localNode.readConfig("security", timeout=10).security
    assert len(security.public_key) == 32, "relay public key was not derived"
    assert client.localNode.readPreference("lora.hop_limit", timeout=10) == 2
    print("0x" + security.public_key.hex())
PY
	)
	meshtastic_set "${MESH_PEER_ENDPOINT}" --set security.admin_key "${relay_key}"
	"${PYTHON_BIN}" - "${MESH_RELAY_ENDPOINT}" "${MESH_PEER_ENDPOINT}" "${relay_key}" <<'PY'
import sys
import time

from meshtastic.protobuf import mesh_pb2
from meshtastic.tcp_interface import TCPInterface

endpoints = sys.argv[1:3]
relay_key = bytes.fromhex(sys.argv[3][2:])
peer_host, peer_port = endpoints[1].rsplit(":", 1)
with TCPInterface(hostname=peer_host, portNumber=int(peer_port)) as peer:
    security = peer.localNode.readConfig("security", timeout=10).security
    assert not security.admin_channel_enabled, "positive test must use PKI authorization"
    assert relay_key in security.admin_key, "relay key was not authorized on the peer"
    assert peer.localNode.readPreference("lora.hop_limit", timeout=10) == 5

# Both peers must hold the other's real key. Reannounce signed NodeInfo through
# the firmware's heartbeat-nonce ping, respecting its 60-second throttle.
deadline = time.monotonic() + 120
while time.monotonic() < deadline:
    snapshots = []
    for endpoint in endpoints:
        host, port = endpoint.rsplit(":", 1)
        with TCPInterface(hostname=host, portNumber=int(port)) as client:
            snapshots.append((client.localNode.nodeNum, client.nodes.copy()))
            frame = mesh_pb2.ToRadio()
            frame.heartbeat.nonce = 1
            client._send_to_radio(frame)
    ready = all(
        bool(nodes.get(f"!{other_num:08x}", {}).get("user", {}).get("publicKey"))
        for (_, nodes), (other_num, _) in zip(snapshots, reversed(snapshots), strict=True)
    )
    if ready:
        print("Both real nodes discovered the other's signed public key.")
        break
    time.sleep(3)
else:
    raise SystemExit("Both PKI peers must discover each other's public key")
PY
}

# wait_for_peer_visible polls the relay's node table until PEER_ID appears.
#
# Poll structured JSON; the display table does not guarantee node identifiers.
wait_for_peer_visible() {
	local endpoint=$1
	local peer_id=$2
	local timeout_seconds=${3:-${MESH_PEER_VISIBLE_TIMEOUT_SECONDS}}
	local deadline=$((SECONDS + timeout_seconds))
	while ((SECONDS < deadline)); do
		local nodes_json
		nodes_json="$("${PYTHON_BIN}" -m meshtastic --timeout 20 --host "${endpoint}" --nodes --json 2>/dev/null || true)"
		if [[ -n ${nodes_json} ]] && printf '%s' "${nodes_json}" | grep -Fq "\"${peer_id}\""; then
			return 0
		fi
		sleep 6
	done
	return 1
}

# =============================================================================
# Matrix helpers
# =============================================================================

matrix_send_message() {
	local access_token=$1
	local room_id=$2
	local message_text=$3
	local txn_prefix=$4
	"${PYTHON_BIN}" - "${MATRIX_BASE_URL}" "${access_token}" "${room_id}" "${message_text}" "${txn_prefix}" <<'PY'
import os
import sys
import time
import urllib.parse

import requests

base_url, access_token, room_id, message_text, txn_prefix = sys.argv[1:6]

txn_id = f"{txn_prefix}-{int(time.time() * 1000)}-{os.getpid()}"
quoted_room_id = urllib.parse.quote(room_id, safe="")
quoted_txn_id = urllib.parse.quote(txn_id, safe="")
url = (
    f"{base_url}/_matrix/client/v3/rooms/{quoted_room_id}"
    f"/send/m.room.message/{quoted_txn_id}"
)
response = requests.put(
    url,
    headers={"Authorization": f"Bearer {access_token}"},
    json={"msgtype": "m.text", "body": message_text},
    timeout=20,
)
if response.status_code >= 400:
    raise RuntimeError(f"send failed ({response.status_code}): {response.text}")
event_id = response.json().get("event_id")
if not isinstance(event_id, str) or not event_id:
    raise RuntimeError("send response missing event_id")
print(event_id)
PY
}

# Look up a command event's homeserver timestamp to exclude harness polling
# and Python process startup time from the fast-rejection latency check.
matrix_event_timestamp() {
    local access_token=$1
    local room_id=$2
    local event_id=$3
    "${PYTHON_BIN}" - "${MATRIX_BASE_URL}" "${access_token}" "${room_id}" "${event_id}" <<'PY_EVENT_TS'
import sys
import urllib.parse

import requests

base_url, token, room_id, event_id = sys.argv[1:5]
room = urllib.parse.quote(room_id, safe="")
event = urllib.parse.quote(event_id, safe="")
response = requests.get(
    f"{base_url}/_matrix/client/v3/rooms/{room}/event/{event}",
    headers={"Authorization": f"Bearer {token}"}, timeout=20,
)
response.raise_for_status()
timestamp = response.json().get("origin_server_ts")
if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
    raise SystemExit("Command event missing valid origin_server_ts")
print(timestamp)
PY_EVENT_TS
}

# matrix_wait_for_reply requires a bot reply to the exact command event whose
# body contains NEEDLE, then prints the reply event ID, bounded body, and server timestamp.
matrix_wait_for_reply() {
	local access_token=$1
	local room_id=$2
	local needle=$3
	local timeout_seconds=${4:-${MATRIX_EVENT_TIMEOUT_SECONDS}}
	local request_event_id=${5:?A command event ID is required}
	"${PYTHON_BIN}" - "${MATRIX_BASE_URL}" "${access_token}" "${room_id}" "${needle}" "${timeout_seconds}" "${request_event_id}" "${BOT_USER_ID}" <<'PY'
import sys
import time
import urllib.parse

import requests

base_url, access_token, room_id, needle, timeout_seconds = sys.argv[1:6]
timeout_seconds = int(timeout_seconds)
request_event_id, bot_user_id = sys.argv[6:8]

quoted_room_id = urllib.parse.quote(room_id, safe="")
url = f"{base_url}/_matrix/client/v3/rooms/{quoted_room_id}/messages"
params = {"dir": "b", "limit": "50"}

headers = {"Authorization": f"Bearer {access_token}"}
deadline = time.monotonic() + timeout_seconds
seen: set[str] = set()

while time.monotonic() < deadline:
    try:
        response = requests.get(url, headers=headers, params=params, timeout=20)
    except requests.RequestException:
        time.sleep(2)
        continue
    if response.status_code >= 400:
        time.sleep(2)
        continue
    for event in response.json().get("chunk", []):
        if event.get("sender") != bot_user_id:
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or event_id in seen:
            continue
        seen.add(event_id)
        content = event.get("content") or {}
        reply_to = content.get("m.relates_to", {}).get("m.in_reply_to", {}).get("event_id")
        if reply_to != request_event_id:
            continue
        body = content.get("body")
        if event.get("type") == "m.room.message" and isinstance(body, str):
            if needle in body:
                timestamp = event.get("origin_server_ts")
                if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
                    raise SystemExit("Reply missing valid origin_server_ts")
                print(event_id)
                print(body.replace("\n", " ")[:400])
                print(timestamp)
                raise SystemExit(0)
    time.sleep(2)

raise SystemExit(f"Timed out waiting for a reply containing: {needle}")
PY
}

load_json_value() {
	local key=$1
	"${PYTHON_BIN}" - "${MATRIX_RUNTIME_JSON}" "${key}" <<'PY'
import json
import sys

path, key = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
value = data[key]
if not isinstance(value, str) or not value:
    raise SystemExit(f"Missing or invalid '{key}' in {path}")
print(value)
PY
}

# =============================================================================
# Preflight
# =============================================================================

echo "Preflight checks..."
command -v docker >/dev/null 2>&1 || {
	echo "docker is required." >&2
	exit 1
}
command -v "${PYTHON_BIN}" >/dev/null 2>&1 || {
	echo "PYTHON_BIN not executable: ${PYTHON_BIN}" >&2
	exit 1
}
require_regex "${MESHTASTICD_CONTAINER_RELAY}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "MESHTASTICD_CONTAINER_RELAY"
require_regex "${MESHTASTICD_CONTAINER_PEER}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "MESHTASTICD_CONTAINER_PEER"
require_regex "${SYNAPSE_CONTAINER}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "SYNAPSE_CONTAINER"
require_regex "${NETWORK_NAME}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "NETWORK_NAME"
require_regex "${MESHTASTICD_PORT_RELAY}" '^[0-9]+$' "MESHTASTICD_PORT_RELAY"
require_regex "${MESHTASTICD_PORT_PEER}" '^[0-9]+$' "MESHTASTICD_PORT_PEER"

# The firmware rejects channel names over 11 UTF-8 bytes.
channel_bytes=$(printf '%s' "${MESH_CHANNEL_NAME}" | wc -c)
if ((channel_bytes > 11)); then
	echo "MESH_CHANNEL_NAME exceeds the 11-byte firmware limit." >&2
	exit 1
fi

SUITE_START_MS=$(date +%s%3N)
mkdir -p "${SHARED_DIR}" "${INSTANCE_DIR}"
rm -rf "${MMRELAY_HOME_DIR}" "${MMRELAY_CONFIG_PATH}" "${MMRELAY_DB_PATH}"

echo ""
echo "Pulling images..."
docker pull "${MESHTASTICD_IMAGE}" >/dev/null
docker pull "${SYNAPSE_IMAGE}" >/dev/null

# =============================================================================
# Start meshtasticd on a BRIDGE network (host networking prevents meshing)
# =============================================================================

echo ""
echo "Creating bridge network ${NETWORK_NAME}..."
docker rm -f "${MESHTASTICD_CONTAINER_RELAY}" "${MESHTASTICD_CONTAINER_PEER}" "${SYNAPSE_CONTAINER}" >/dev/null 2>&1 || true
docker network rm "${NETWORK_NAME}" >/dev/null 2>&1 || true
docker network create --driver bridge "${NETWORK_NAME}" >/dev/null

echo "Starting meshtasticd relay and peer..."
declare -a radio_args=(-s)
declare -a native_mount_args=()
if [[ ${RA_ADMIN_REQUIRE_SUCCESS} == true ]]; then
	native_config="$(cd "${INSTANCE_DIR}" && pwd)/native-pki.yaml"
	cat >"${native_config}" <<'YAML'
Lora:
  Module: sim
Config:
  EnableUDP: true
YAML
	native_mount_args=(--mount "type=bind,src=${native_config},dst=/etc/meshtasticd/pki-sim.yaml,readonly")
	radio_args=(-c /etc/meshtasticd/pki-sim.yaml)
fi
docker run -d --name "${MESHTASTICD_CONTAINER_RELAY}" --network "${NETWORK_NAME}" \
	"${native_mount_args[@]}" \
	"${MESHTASTICD_IMAGE}" \
	"${MESHTASTICD_BINARY}" "${radio_args[@]}" "--fsdir=/var/lib/meshtasticd-ra-relay" -p "${MESHTASTICD_PORT_RELAY}" \
	-h "${MESHTASTICD_HWID_RELAY}" >/dev/null
docker run -d --name "${MESHTASTICD_CONTAINER_PEER}" --network "${NETWORK_NAME}" \
	"${native_mount_args[@]}" \
	"${MESHTASTICD_IMAGE}" \
	"${MESHTASTICD_BINARY}" "${radio_args[@]}" "--fsdir=/var/lib/meshtasticd-ra-peer" -p "${MESHTASTICD_PORT_PEER}" \
	-h "${MESHTASTICD_HWID_PEER}" >/dev/null

MESH_RELAY_IP="$(docker inspect "${MESHTASTICD_CONTAINER_RELAY}" \
	--format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')"
MESH_PEER_IP="$(docker inspect "${MESHTASTICD_CONTAINER_PEER}" \
	--format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')"
echo "relay ip=${MESH_RELAY_IP}  peer ip=${MESH_PEER_IP}"
MESH_RELAY_ENDPOINT="${MESH_RELAY_IP}:${MESHTASTICD_PORT_RELAY}"
MESH_PEER_ENDPOINT="${MESH_PEER_IP}:${MESHTASTICD_PORT_PEER}"

sleep 10
run_or_fail "meshtasticd relay did not become ready" \
	wait_for_meshtasticd_ready "${MESH_RELAY_ENDPOINT}" "${MESHTASTICD_CONTAINER_RELAY}"
run_or_fail "meshtasticd peer did not become ready" \
	wait_for_meshtasticd_ready "${MESH_PEER_ENDPOINT}" "${MESHTASTICD_CONTAINER_PEER}"

echo ""
echo "Applying simradio mesh configuration (region, UDP_BROADCAST, channel)..."
if ! apply_mesh_config "${MESH_RELAY_ENDPOINT}"; then
	echo "Failed to apply mesh config to the relay node." >&2
	exit 1
fi
if ! apply_mesh_config "${MESH_PEER_ENDPOINT}"; then
	echo "Failed to apply mesh config to the peer node." >&2
	exit 1
fi

if [[ ${RA_ADMIN_REQUIRE_SUCCESS} == true ]]; then
	meshtastic_set "${MESH_RELAY_ENDPOINT}" --set lora.hop_limit 2
	meshtastic_set "${MESH_PEER_ENDPOINT}" --set lora.hop_limit 5
	provision_private_key "${MESH_RELAY_ENDPOINT}" relay
	provision_private_key "${MESH_PEER_ENDPOINT}" peer
fi

echo "Restarting nodes so the new config is read at boot..."
docker restart "${MESHTASTICD_CONTAINER_RELAY}" "${MESHTASTICD_CONTAINER_PEER}" >/dev/null
sleep 10
# Report initialization failures through the scenario summary instead of
# silently exiting under set -e before the main assertions start.
CURRENT_TEST_NAME="setup_post_restart_mesh_ready"
CURRENT_TEST_START_MS=$(date +%s%3N)
run_or_fail "meshtasticd relay was not ready after restart" \
    wait_for_meshtasticd_ready "${MESH_RELAY_ENDPOINT}" "${MESHTASTICD_CONTAINER_RELAY}"
run_or_fail "meshtasticd peer was not ready after restart" \
    wait_for_meshtasticd_ready "${MESH_PEER_ENDPOINT}" "${MESHTASTICD_CONTAINER_PEER}"
if ! RELAY_NODE_ID="$(get_local_node_id "${MESH_RELAY_ENDPOINT}")"; then
    fail_test "Could not determine relay node ID after restart"
fi
if ! PEER_NODE_ID="$(get_local_node_id "${MESH_PEER_ENDPOINT}")"; then
    fail_test "Could not determine peer node ID after restart"
fi
if [[ -z ${RELAY_NODE_ID} || -z ${PEER_NODE_ID} ]]; then
    fail_test "Missing relay or peer node ID after restart"
fi
echo "relay node=${RELAY_NODE_ID}  peer node=${PEER_NODE_ID}"

if [[ ${RA_ADMIN_REQUIRE_SUCCESS} == true ]]; then
	prepare_pki_peer
fi

# Inspect the node database before MMRelay owns the single-client TCP API.
PEER_VISIBLE_BEFORE_RELAY=false
if wait_for_peer_visible "${MESH_RELAY_ENDPOINT}" "${PEER_NODE_ID}"; then
	PEER_VISIBLE_BEFORE_RELAY=true
fi

# =============================================================================
# Synapse + Matrix users + admin room
# =============================================================================

echo ""
echo "Generating Synapse config..."
# The image writes its data dir as root before dropping privileges, so a
# pre-existing host-owned directory would still end up unwritable for the
# --user generate step. Remove any stale dir and let this run create it.
rm -rf "${SYNAPSE_DATA_DIR}"
mkdir -p "${SYNAPSE_DATA_DIR}"
docker run --rm \
	--user "${HOST_UID}:${HOST_GID}" \
	-e SYNAPSE_SERVER_NAME="${SYNAPSE_SERVER_NAME}" \
	-e SYNAPSE_REPORT_STATS=no \
	-v "${SYNAPSE_DATA_DIR}:/data" \
	"${SYNAPSE_IMAGE}" generate >/dev/null

cat >>"${SYNAPSE_DATA_DIR}/homeserver.yaml" <<YAML
registration_shared_secret: "${SYNAPSE_SHARED_SECRET}"
enable_registration: true
enable_registration_without_verification: true
rc_message:
  per_second: 25
  burst_count: 100
rc_login:
  address:
    per_second: 5
    burst_count: 30
  account:
    per_second: 5
    burst_count: 30
  failed_attempts:
    per_second: 5
    burst_count: 30
YAML

echo "Starting Synapse container..."
docker run -d \
	--name "${SYNAPSE_CONTAINER}" \
	--user "${HOST_UID}:${HOST_GID}" \
	-e SYNAPSE_SERVER_NAME="${SYNAPSE_SERVER_NAME}" \
	-e SYNAPSE_REPORT_STATS=no \
	-p "127.0.0.1:${SYNAPSE_PORT_DEC}:8008" \
	-v "${SYNAPSE_DATA_DIR}:/data" \
	"${SYNAPSE_IMAGE}" >/dev/null

echo ""
echo "Waiting for Synapse..."
deadline=$((SECONDS + 10#${SYNAPSE_READY_TIMEOUT_SECONDS}))
until "${PYTHON_BIN}" - "${MATRIX_BASE_URL}" <<'PY'
import sys
import requests

try:
    response = requests.get(f"{sys.argv[1]}/_matrix/client/versions", timeout=5)
except requests.RequestException:
    raise SystemExit(1)
raise SystemExit(0 if response.status_code == 200 else 1)
PY
do
	if ! docker ps --format '{{.Names}}' | grep -Fxq "${SYNAPSE_CONTAINER}"; then
		echo "Synapse exited before becoming ready." >&2
		exit 1
	fi
	if ((SECONDS >= deadline)); then
		echo "Synapse not ready within ${SYNAPSE_READY_TIMEOUT_SECONDS}s." >&2
		exit 1
	fi
	sleep 2
done
echo "Synapse is ready."

echo ""
echo "Creating Matrix users..."
for spec in "${MATRIX_BOT_LOCALPART}:${MATRIX_BOT_PASSWORD}" \
	"${MATRIX_USER_LOCALPART}:${MATRIX_USER_PASSWORD}" \
	"${MATRIX_LOWPRIV_LOCALPART}:${MATRIX_LOWPRIV_PASSWORD}"; do
	localpart="${spec%%:*}"
	password="${spec#*:}"
	docker exec "${SYNAPSE_CONTAINER}" register_new_matrix_user \
		-u "${localpart}" -p "${password}" -a -c /data/homeserver.yaml \
		"http://localhost:8008" >/dev/null
done

echo "Creating the admin room and setting power levels..."
"${PYTHON_BIN}" - "${MATRIX_BASE_URL}" "${SYNAPSE_SHARED_SECRET}" \
	"${MATRIX_USER_LOCALPART}" "${MATRIX_USER_PASSWORD}" \
	"${MATRIX_LOWPRIV_LOCALPART}" "${MATRIX_LOWPRIV_PASSWORD}" \
	"${MATRIX_RUNTIME_JSON}" "${ADMIN_POWER_LEVEL}" <<'PY'
import json
import sys
import time
import urllib.parse

import requests

(
    base_url,
    shared_secret,
    admin_localpart,
    admin_password,
    lowpriv_localpart,
    lowpriv_password,
    out_path,
    min_power_level,
) = sys.argv[1:9]
min_power_level = int(min_power_level)


def post(path, token=None, payload=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = requests.post(
        f"{base_url}{path}", headers=headers, json=payload or {}, timeout=30
    )
    if response.status_code >= 400:
        raise RuntimeError(f"POST {path} failed ({response.status_code}): {response.text}")
    return response.json() if response.content else {}


def login(localpart, password):
    return post(
        "/_matrix/client/v3/login",
        payload={"type": "m.login.password", "identifier": {"type": "m.id.user",
                "user": localpart}, "password": password},
    )


admin = login(admin_localpart, admin_password)
lowpriv = login(lowpriv_localpart, lowpriv_password)
admin_id = admin["user_id"]
lowpriv_id = lowpriv["user_id"]

room = post(
    "/_matrix/client/v3/createRoom",
    admin["access_token"],
    {
        "preset": "private_chat",
        "name": "MMRelay remote admin CI",
        "topic": "remote_admin plugin harness",
        "room_alias_name": f"mmrelay-ra-{int(time.time())}",
        # Both senders must be members before they can speak.
        "invite": [lowpriv_id],
    },
)
room_id = room["room_id"]

# Invite AND join on behalf of the low-privilege sender so it can actually send.
quoted = urllib.parse.quote(room_id, safe="")
resp = requests.post(
    f"{base_url}/_matrix/client/v3/rooms/{quoted}/join",
    headers={"Authorization": f"Bearer {lowpriv['access_token']}"},
    json={},
    timeout=30,
)
if resp.status_code >= 400:
    raise RuntimeError(f"lowpriv join failed ({resp.status_code}): {resp.text}")

# The authorized operator needs to be at or above the plugin threshold.
quoted = urllib.parse.quote(room_id, safe="")
resp = requests.put(
    f"{base_url}/_matrix/client/v3/rooms/{quoted}/state/m.room.power_levels/",
    headers={"Authorization": f"Bearer {admin['access_token']}"},
    json={
        "users": {admin_id: 100, lowpriv_id: 0},
        "users_default": 0,
        "state_default": 50,
        "events_default": 0,
        "ban": 50,
        "kick": 50,
        "redact": 50,
        "invite": 0,
        "events": {},
    },
    timeout=30,
)
if resp.status_code >= 400:
    raise RuntimeError(f"power_levels failed ({resp.status_code}): {resp.text}")

with open(out_path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "room_id_admin": room_id,
            "admin_user_id": admin_id,
            "admin_token": admin["access_token"],
            "lowpriv_user_id": lowpriv_id,
            "lowpriv_token": lowpriv["access_token"],
        },
        handle,
    )
print(f"admin room {room_id}")
print(f"  authorized: {admin_id} (PL 100)")
print(f"  unauthorized: {lowpriv_id} (PL 0)")
PY

ROOM_ID_ADMIN="$(load_json_value room_id_admin)"
ADMIN_TOKEN="$(load_json_value admin_token)"
LOWPRIV_TOKEN="$(load_json_value lowpriv_token)"
BOT_USER_ID="@${MATRIX_BOT_LOCALPART}:${SYNAPSE_SERVER_NAME}"

# =============================================================================
# MMRelay config + launch
# =============================================================================

cat >"${MMRELAY_CONFIG_PATH}" <<EOF_CONFIG
matrix:
  homeserver: "${MATRIX_BASE_URL}"
  bot_user_id: "@${MATRIX_BOT_LOCALPART}:${SYNAPSE_SERVER_NAME}"
  e2ee:
    enabled: false
    store_path: "${MMRELAY_HOME_DIR}/matrix/store"
# The admin room is plugin-owned, NOT a matrix_rooms entry. The key must still
# be present: MMRelay's config validation requires it, even when empty.
matrix_rooms: []
plugins:
  remote_admin:
    active: true
    admin_room: "${ROOM_ID_ADMIN}"
    power_level: ${ADMIN_POWER_LEVEL}
    timeout: ${RA_PLUGIN_TIMEOUT_SECONDS}
    max_output_chars: 2000
meshtastic:
  connection_type: tcp
  host: "${MESH_RELAY_IP}"
  port: ${MESHTASTICD_PORT_RELAY}
  meshnet_name: "RemoteAdmin CI"
  health_check:
    enabled: false
  broadcast_enabled: false
database:
  msg_map:
    msgs_to_keep: 500
    wipe_on_restart: true
logging:
  level: debug
  log_to_file: true
EOF_CONFIG

echo ""
echo "Bootstrapping MMRelay bot identity..."
"${PYTHON_BIN}" -m mmrelay.cli \
	--config "${MMRELAY_CONFIG_PATH}" \
	--home "${MMRELAY_HOME_DIR}" \
	auth login \
	--homeserver "${MATRIX_BASE_URL}" \
	--username "@${MATRIX_BOT_LOCALPART}:${SYNAPSE_SERVER_NAME}" \
	--password "${MATRIX_BOT_PASSWORD}" >/dev/null

echo ""
echo "Starting MMRelay..."
PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -m mmrelay.cli \
	--config "${MMRELAY_CONFIG_PATH}" \
	--home "${MMRELAY_HOME_DIR}" \
	--log-level debug >"${MMRELAY_STDOUT_LOG_PATH}" 2>&1 &
MMRELAY_PID=$!

echo "Waiting for the bot's first sync..."
run_or_fail "MMRelay did not complete its initial sync" \
	wait_for_relay_log_line "Initial sync completed" 90

# Invite only now: on_invite is driven by a live /sync, so an invite sent
# before the bot's first sync would never reach the handler.
echo "Inviting the bot to the plugin-owned admin room..."
run_or_fail "Failed to invite the bot to the admin room" \
	invite_bot_to_admin_room

echo "Waiting for the bot to accept the invite..."
if ! wait_for_relay_log_line "accepting invite" 90; then
	echo "Bot did not accept the plugin-owned admin-room invite." >&2
	tail -n 40 "${MMRELAY_STDOUT_LOG_PATH}" >&2 || true
	exit 1
fi

sleep 5

# =============================================================================
# Test 0: bot identity is established
# =============================================================================

start_test "test_remote_admin_bootstrap" \
	"Test 0: bot authentication and Matrix connectivity are established..."

# Cross-signing keys are only published when E2EE is enabled. This harness uses
# a plaintext admin room, so asserting the chain here would be asserting a
# feature the run deliberately does not exercise.
run_or_fail "MMRelay auth status failed" \
	"${PYTHON_BIN}" -m mmrelay.cli \
	--config "${MMRELAY_CONFIG_PATH}" \
	--home "${MMRELAY_HOME_DIR}" \
	auth status

run_or_fail "Bot is not connected to Matrix" \
	wait_for_relay_log_line "Initial sync completed" 30
pass_test "Bot authenticated and completed its initial Matrix sync"

# =============================================================================
# Test 1: bot joined the plugin-owned admin room
# =============================================================================

start_test "test_admin_room_invite_and_join" \
	"Test 1: bot accepts the invite for the plugin-owned admin room..."

joined=0
deadline=$((SECONDS + 60))
while ((SECONDS < deadline)); do
	joined_members="$(
		"${PYTHON_BIN}" - "${MATRIX_BASE_URL}" "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" <<'PY' 2>/dev/null || true
import sys
import urllib.parse

import requests

base_url, token, room_id = sys.argv[1:4]
quoted = urllib.parse.quote(room_id, safe="")
resp = requests.get(
    f"{base_url}/_matrix/client/v3/rooms/{quoted}/joined_members",
    headers={"Authorization": f"Bearer {token}"}, timeout=20,
)
if resp.status_code >= 400:
    raise SystemExit(1)
print("\n".join((resp.json().get("joined") or {}).keys()))
PY
	)"
	if printf '%s\n' "${joined_members}" | grep -Fxq "${BOT_USER_ID}"; then
		joined=1
		break
	fi
	sleep 3
done
if ((joined == 0)); then
	fail_test "Bot never joined the admin room ${ROOM_ID_ADMIN}"
fi
pass_test "Bot joined the plugin-owned admin room without a matrix_rooms entry"

# =============================================================================
# Test 2: !admin help
# =============================================================================

start_test "test_admin_help" \
	"Test 2: !admin help is served in the admin room..."

help_event_id=$(matrix_send_message "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" "!admin help" "ra-help")
if ! matrix_wait_for_reply "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" "Remote admin" "${MATRIX_EVENT_TIMEOUT_SECONDS}" "${help_event_id}" >/dev/null; then
	fail_test "No !admin help reply from the bot"
fi
pass_test "!admin help returned usage and tier information"

# =============================================================================
# Test 3: unauthorized sender is refused
# =============================================================================

start_test "test_unauthorized_sender_refused" \
	"Test 3: a sender below the power-level threshold is refused..."

lowpriv_event_id=$(matrix_send_message "${LOWPRIV_TOKEN}" "${ROOM_ID_ADMIN}" \
	"!admin --dest ${PEER_NODE_ID} --reboot" "ra-lowpriv")
if ! matrix_wait_for_reply "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" "Power level" "${MATRIX_EVENT_TIMEOUT_SECONDS}" "${lowpriv_event_id}" >/dev/null; then
	fail_test "No power-level refusal reply for an unauthorized sender"
fi
pass_test "Sender below the power-level threshold was refused"

# =============================================================================
# Test 4: an authorized command runs and the reply carries an exit code
# =============================================================================

start_test "test_authorized_command_executes" \
	"Test 4: an authorized sender's command is executed..."

# Target the relay's own node: the executor refuses admin commands against it
# by design, which exercises the full authorized path (gate -> parse -> policy
# -> destination validation -> reply). Scenario 8 checks the actual peer path.
authorized_event_id=$(matrix_send_message "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" \
	"!admin --dest ${RELAY_NODE_ID} --reboot" "ra-authorized")
if ! cmd_out="$(matrix_wait_for_reply "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" "Refused" 90 "${authorized_event_id}")"; then
	fail_test "No reply for an authorized command"
fi
if ! printf '%s' "${cmd_out}" | grep -qi "relay's own node"; then
	fail_test "Authorized command did not report the expected refusal: ${cmd_out}"
fi
pass_test "Authorized command ran through the executor and returned a refusal reply"

# =============================================================================
# Test 5: distinct replies to back-to-back commands
# =============================================================================

start_test "test_back_to_back_command_replies" \
	"Test 5: back-to-back commands receive their own replies..."

# Timeline callbacks can be delivered serially. This checks both observable
# replies; concurrent lock admission is covered by the plugin's Python tests.
busy_first_event_id=$(matrix_send_message "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" \
	"!admin --dest ${RELAY_NODE_ID} --reboot" "ra-busy-1")
busy_second_event_id=$(matrix_send_message "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" \
	"!admin --dest ${RELAY_NODE_ID} --get lora.hop_limit" "ra-busy-2")
for command_event_id in "${busy_first_event_id}" "${busy_second_event_id}"; do
	if ! matrix_wait_for_reply "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" "relay's own node" 90 "${command_event_id}" >/dev/null; then
		fail_test "A back-to-back command did not receive its own refusal reply"
	fi
done
pass_test "Both back-to-back commands received separately correlated refusal replies"

# =============================================================================
# =============================================================================
# Test 6: refusal paths
# =============================================================================

start_test "test_destination_refusals" \
	"Test 6: local, broadcast, and unknown destinations are refused..."

refusals_ok=0
for bad_dest in "^local" "^all" "!00000000"; do
	refusal_event_id=$(matrix_send_message "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" \
		"!admin --dest ${bad_dest} --reboot" "ra-refuse")
	if matrix_wait_for_reply "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" "Refused" 30 "${refusal_event_id}" >/dev/null 2>&1; then
		refusals_ok=$((refusals_ok + 1))
	fi
done
if ((refusals_ok != 3)); then
	fail_test "Only ${refusals_ok}/3 invalid destinations were refused"
fi
pass_test "${refusals_ok}/3 invalid destinations refused"

# =============================================================================
# Test 6b: local-node-only option refusal
# =============================================================================

start_test "test_local_option_refused" \
	"Test 6b: a local-node-only flag is refused for a real peer..."

# Use a real remote destination so the flag policy must reject --nodes.
capability_event_id=$(matrix_send_message "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" \
	"!admin --dest ${PEER_NODE_ID} --nodes" "ra-capability")
if cap_out="$(matrix_wait_for_reply "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" "Refused" 60 "${capability_event_id}")"; then
	pass_test "Local-node-only --nodes was refused before dispatch"
else
	fail_test "A local-node-only flag was not refused: ${cap_out}"
fi

# =============================================================================
# Test 7: mesh formation
# =============================================================================

start_test "test_mesh_formation" \
	"Test 7: the remote node appears in the relay's node database..."

if [[ ${PEER_VISIBLE_BEFORE_RELAY} == true ]]; then
	pass_test "Peer ${PEER_NODE_ID} was verified in the relay node database before MMRelay startup"
elif [[ "${RA_ADMIN_REQUIRE_MESH}" == "true" ]]; then
	fail_test "Peer ${PEER_NODE_ID} never appeared in the relay's node table"
else
	skip_test "Peer ${PEER_NODE_ID} not visible; simradio multicast did not form a mesh"
fi

# =============================================================================
# Test 8: remote --dest round trip, with an explicit positive profile
# =============================================================================

start_test "test_remote_dest_round_trip" \
	"Test 8: the remote --dest command satisfies the selected profile..."

if [[ ${PEER_VISIBLE_BEFORE_RELAY} != true ]]; then
	skip_test "Prerequisite: no peer in the node database"
else
	roundtrip_preference=lora.region
	if [[ ${RA_ADMIN_REQUIRE_SUCCESS} == true ]]; then
		roundtrip_preference=lora.hop_limit
	fi
	roundtrip_event_id=$(matrix_send_message "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" \
		"!admin --dest ${PEER_NODE_ID} --get ${roundtrip_preference}" "ra-roundtrip")
	# Require the bot's reply relation to this command, preserving the distinction
	# between a peer value and fast failure at the origin router.
	roundtrip_wait_seconds=$((RA_PLUGIN_TIMEOUT_SECONDS + 30))
	if roundtrip_out="$(matrix_wait_for_reply "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" "exit code" "${roundtrip_wait_seconds}" "${roundtrip_event_id}")"; then
		# Both times are assigned by the same Matrix homeserver, excluding
		# test harness startup and polling delays.
		if ! roundtrip_command_ts="$(matrix_event_timestamp "${ADMIN_TOKEN}" "${ROOM_ID_ADMIN}" "${roundtrip_event_id}")"; then
			fail_test "Could not read the command event's server timestamp"
		fi
		roundtrip_reply_ts="${roundtrip_out##*$'\n'}"
		if [[ ! ${roundtrip_reply_ts} =~ ^[0-9]+$ ]]; then
			fail_test "Bot reply did not provide a valid server timestamp"
		fi
		roundtrip_elapsed_ms=$((roundtrip_reply_ts - roundtrip_command_ts))
		if [[ ${RA_ADMIN_REQUIRE_SUCCESS} == true ]]; then
			if printf '%s\n' "${roundtrip_out}" | grep -qE 'lora\.hop_limit:[[:space:]]*5([^0-9]|$)' &&
				printf '%s' "${roundtrip_out}" | grep -q 'exit code 0'; then
				pass_test "Remote --get returned peer lora.hop_limit=5 through the Matrix reply"
			else
				fail_test "Expected the peer's hop limit 5 and exit code 0; got ${roundtrip_out}"
			fi
		elif printf '%s' "${roundtrip_out}" | grep -q 'PKI_FAILED' && ((roundtrip_elapsed_ms >= 0 && roundtrip_elapsed_ms < 5000)); then
			pass_test "Origin PKI_FAILED reached the Matrix reply in ${roundtrip_elapsed_ms} ms (fast-fail tier)"
		else
			fail_test "Expected PKI_FAILED within 5000 ms; got ${roundtrip_out} after ${roundtrip_elapsed_ms} ms"
		fi
	else
		fail_test "No reply within $((roundtrip_wait_seconds))s (plugin timeout ${RA_PLUGIN_TIMEOUT_SECONDS}s): the executor did not report the failed command"
	fi
fi

write_observability_report

echo ""
echo "============================================================================"
echo "All remote admin test scenarios completed."
echo "============================================================================"
echo "Artifacts written to: ${CI_ARTIFACT_DIR}"
