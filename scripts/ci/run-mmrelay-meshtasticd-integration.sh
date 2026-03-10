#!/usr/bin/env bash

set -euo pipefail

# =============================================================================
# Meshtasticd Integration Test for MMRelay
# =============================================================================
#
# This script tests MMRelay's ability to relay messages between two isolated mesh networks
# via a shared Matrix room. This is the core use case for MMRelay: bridging multiple meshnets.
#
# Architecture:
#   - meshtasticd relay-A (port 4403) ← MMRelay A ←─┐
#                                                    └──→ Shared Matrix Room
#   - meshtasticd relay-B (port 4404) ← MMRelay B ←─┘
#
# Test Scenarios:
#   1. Matrix user message in shared room → Mesh A + Mesh B
#   2. Injected Mesh A-origin event in shared room → remote meshnet processing in MMRelay B
#   3. Injected Mesh B-origin event in shared room → remote meshnet processing in MMRelay A
#   4. Secondary Matrix user message → Mesh A + Mesh B
#   5. Secondary Matrix user reply → both meshes as structured replies
#   6. Secondary Matrix user reaction → both meshes
#
# Environment Variables:
#   MESHTASTICD_IMAGE: Docker image for meshtasticd (default: meshtastic/meshtasticd:latest)
#   SYNAPSE_IMAGE: Docker image for Synapse (default: matrixdotorg/synapse:latest)
#   MMRELAY_LOG_ON_SUCCESS: Always show logs (default: false)
#   MESHNET_NAME_A / MESHNET_NAME_B: Meshnet labels used by each relay instance
#   MESH_CHANNEL_NAME_A / MESH_CHANNEL_NAME_B: Channel names for isolated meshnets
#   MESH_PRIMARY_PSK_A / MESH_PRIMARY_PSK_B: Primary channel keys for isolated meshnets
#   MATRIX_EVENT_TIMEOUT_SECONDS: Matrix event polling timeout per assertion
# =============================================================================

# Meshtasticd Configuration
MESHTASTICD_IMAGE="${MESHTASTICD_IMAGE:-meshtastic/meshtasticd:latest}"
MESHTASTICD_CONTAINER_A="${MESHTASTICD_CONTAINER_A:-mmrelay-ci-mesh-a}"
MESHTASTICD_CONTAINER_B="${MESHTASTICD_CONTAINER_B:-mmrelay-ci-mesh-b}"
MESHTASTICD_HOST_A="${MESHTASTICD_HOST_A:-localhost}"
MESHTASTICD_HOST_B="${MESHTASTICD_HOST_B:-localhost}"
MESHTASTICD_PORT_A="${MESHTASTICD_PORT_A:-4403}"
MESHTASTICD_PORT_B="${MESHTASTICD_PORT_B:-4404}"
MESHTASTICD_HWID_A="${MESHTASTICD_HWID_A:-11}"
MESHTASTICD_HWID_B="${MESHTASTICD_HWID_B:-22}"
MESHTASTICD_READY_TIMEOUT_SECONDS="${MESHTASTICD_READY_TIMEOUT_SECONDS:-180}"
MESH_CHANNEL_NAME_A="${MESH_CHANNEL_NAME_A:-MMRelayMeshA}"
MESH_CHANNEL_NAME_B="${MESH_CHANNEL_NAME_B:-MMRelayMeshB}"
MESH_PRIMARY_PSK_A="${MESH_PRIMARY_PSK_A:-0x00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff}"
MESH_PRIMARY_PSK_B="${MESH_PRIMARY_PSK_B:-0xffeeddccbbaa99887766554433221100ffeeddccbbaa99887766554433221100}"

# Synapse Configuration
SYNAPSE_IMAGE="${SYNAPSE_IMAGE:-matrixdotorg/synapse:latest}"
SYNAPSE_CONTAINER="${SYNAPSE_CONTAINER:-mmrelay-ci-synapse}"
SYNAPSE_PORT="${SYNAPSE_PORT:-8008}"
SYNAPSE_SERVER_NAME="${SYNAPSE_SERVER_NAME:-localhost}"
SYNAPSE_READY_TIMEOUT_SECONDS="${SYNAPSE_READY_TIMEOUT_SECONDS:-180}"

# MMRelay Configuration
MMRELAY_READY_TIMEOUT_SECONDS="${MMRELAY_READY_TIMEOUT_SECONDS:-120}"
MMRELAY_LOG_ON_SUCCESS="${MMRELAY_LOG_ON_SUCCESS:-false}"
MESHNET_NAME_A="${MESHNET_NAME_A:-Mesh A}"
MESHNET_NAME_B="${MESHNET_NAME_B:-Mesh B}"
MATRIX_EVENT_TIMEOUT_SECONDS="${MATRIX_EVENT_TIMEOUT_SECONDS:-60}"
MESSAGE_MAP_WAIT_TIMEOUT_SECONDS="${MESSAGE_MAP_WAIT_TIMEOUT_SECONDS:-60}"
PYTHON_BIN="${PYTHON_BIN:-python}"

# Artifacts and Logging - Separated by Instance
CI_ARTIFACT_DIR="${CI_ARTIFACT_DIR:-${PWD}/.ci-artifacts/meshtasticd-integration}"

# Instance A directories
INSTANCE_A_DIR="${CI_ARTIFACT_DIR}/instance-a"
INSTANCE_A_LOG_DIR="${INSTANCE_A_DIR}/logs"
INSTANCE_A_DATA_DIR="${INSTANCE_A_DIR}/data"

# Instance B directories
INSTANCE_B_DIR="${CI_ARTIFACT_DIR}/instance-b"
INSTANCE_B_LOG_DIR="${INSTANCE_B_DIR}/logs"
INSTANCE_B_DATA_DIR="${INSTANCE_B_DIR}/data"

# Shared infrastructure
SHARED_DIR="${CI_ARTIFACT_DIR}/shared"
SYNAPSE_DATA_DIR="${SHARED_DIR}/synapse"
SYNAPSE_LOG_DIR="${SHARED_DIR}/logs"

# Log files
MMRELAY_LOG_PATH_A="${INSTANCE_A_LOG_DIR}/mmrelay.log"
MMRELAY_LOG_PATH_B="${INSTANCE_B_LOG_DIR}/mmrelay.log"
MESHTASTICD_LOG_PATH_A="${INSTANCE_A_LOG_DIR}/meshtasticd.log"
MESHTASTICD_LOG_PATH_B="${INSTANCE_B_LOG_DIR}/meshtasticd.log"
SYNAPSE_LOG_PATH="${SYNAPSE_LOG_DIR}/synapse.log"

# Config and data files
MMRELAY_CONFIG_PATH_A="${INSTANCE_A_DIR}/config.yaml"
MMRELAY_CONFIG_PATH_B="${INSTANCE_B_DIR}/config.yaml"
MMRELAY_HOME_DIR_A="${INSTANCE_A_DATA_DIR}/mmrelay-home"
MMRELAY_HOME_DIR_B="${INSTANCE_B_DATA_DIR}/mmrelay-home"
MMRELAY_DB_PATH_A="${MMRELAY_HOME_DIR_A}/database/meshtastic.sqlite"
MMRELAY_DB_PATH_B="${MMRELAY_HOME_DIR_B}/database/meshtastic.sqlite"
MATRIX_RUNTIME_JSON="${SHARED_DIR}/matrix-runtime.json"

# Process tracking
MMRELAY_PID_A=""
MMRELAY_PID_B=""
LOGS_PRINTED=false
OBSERVABILITY_WRITTEN=false
SUITE_START_MS=0
CURRENT_TEST_NAME=""
CURRENT_TEST_START_MS=0
MESHTASTICD_LOG_OFFSET_A=0
MESHTASTICD_LOG_OFFSET_B=0

declare -a TEST_RESULT_NAMES=()
declare -a TEST_RESULT_STATUS=()
declare -a TEST_RESULT_DURATION_MS=()
declare -a TEST_RESULT_NOTES=()

# =============================================================================
# Utility Functions
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

print_logs_if_needed() {
	local exit_code=$1
	local print_logs=false
	if ((exit_code != 0)); then
		print_logs=true
	else
		case "${MMRELAY_LOG_ON_SUCCESS,,}" in
		1 | true | yes | on)
			print_logs=true
			;;
		*) ;;
		esac
	fi

	if [[ ${print_logs} != true || ${LOGS_PRINTED} == true ]]; then
		return
	fi

	LOGS_PRINTED=true
	echo "===== MMRelay A log ====="
	[[ -f ${MMRELAY_LOG_PATH_A} ]] && cat "${MMRELAY_LOG_PATH_A}" || true
	echo "===== MMRelay B log ====="
	[[ -f ${MMRELAY_LOG_PATH_B} ]] && cat "${MMRELAY_LOG_PATH_B}" || true
	echo "===== meshtasticd A log ====="
	[[ -f ${MESHTASTICD_LOG_PATH_A} ]] && cat "${MESHTASTICD_LOG_PATH_A}" || true
	echo "===== meshtasticd B log ====="
	[[ -f ${MESHTASTICD_LOG_PATH_B} ]] && cat "${MESHTASTICD_LOG_PATH_B}" || true
	echo "===== Synapse log ====="
	[[ -f ${SYNAPSE_LOG_PATH} ]] && cat "${SYNAPSE_LOG_PATH}" || true
}

count_pattern_in_file() {
	local file_path=$1
	local pattern=$2
	if [[ ! -f ${file_path} ]]; then
		echo 0
		return
	fi
	grep -F -c "${pattern}" "${file_path}" || true
}

count_pattern_in_file_since() {
	local file_path=$1
	local pattern=$2
	local start_byte=$3
	if [[ ! -f ${file_path} ]] || (($(wc -c <"${file_path}") <= start_byte)); then
		echo 0
		return
	fi
	tail -c +$((start_byte + 1)) "${file_path}" | grep -F -c "${pattern}" || true
}

count_message_map_rows() {
	local db_path=$1
	if [[ ! -f ${db_path} ]]; then
		echo 0
		return
	fi
	"${PYTHON_BIN}" - "${db_path}" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
try:
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        row = conn.execute("SELECT COUNT(*) FROM message_map").fetchone()
    finally:
        conn.close()
    print(int(row[0]) if row and row[0] is not None else 0)
except sqlite3.Error:
    print(0)
PY
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

write_observability_report() {
	if [[ ${OBSERVABILITY_WRITTEN} == true ]]; then
		return
	fi
	OBSERVABILITY_WRITTEN=true

	local suite_end_ms
	suite_end_ms=$(date +%s%3N)
	local suite_duration_ms=$((suite_end_ms - SUITE_START_MS))
	local total_tests=${#TEST_RESULT_NAMES[@]}
	local passed_tests=0
	local failed_tests=0

	local status
	for status in "${TEST_RESULT_STATUS[@]}"; do
		if [[ ${status} == "PASSED" ]]; then
			passed_tests=$((passed_tests + 1))
		else
			failed_tests=$((failed_tests + 1))
		fi
	done

	local mesh_log_a_live="${INSTANCE_A_LOG_DIR}/meshtasticd-live.log"
	local mesh_log_b_live="${INSTANCE_B_LOG_DIR}/meshtasticd-live.log"
	if docker ps -a --format '{{.Names}}' | grep -Fxq "${MESHTASTICD_CONTAINER_A}"; then
		docker logs "${MESHTASTICD_CONTAINER_A}" >"${mesh_log_a_live}" 2>&1 || true
	fi
	if docker ps -a --format '{{.Names}}' | grep -Fxq "${MESHTASTICD_CONTAINER_B}"; then
		docker logs "${MESHTASTICD_CONTAINER_B}" >"${mesh_log_b_live}" 2>&1 || true
	fi

	local relay_messages_a
	local relay_messages_b
	local relay_replies_a
	local relay_replies_b
	local relay_reactions_a
	local relay_reactions_b
	local remote_mesh_a
	local remote_mesh_b
	local relay_reconnects_a
	local relay_reconnects_b
	local message_map_a
	local message_map_b
	local force_close_a
	local force_close_b
	local incoming_api_a
	local incoming_api_b
	local lost_phone_a
	local lost_phone_b
	local stability_note_a="stable"
	local stability_note_b="stable"

	relay_messages_a=$(count_pattern_in_file "${MMRELAY_LOG_PATH_A}" "Relaying message from")
	relay_messages_b=$(count_pattern_in_file "${MMRELAY_LOG_PATH_B}" "Relaying message from")
	relay_replies_a=$(count_pattern_in_file "${MMRELAY_LOG_PATH_A}" "Relaying Matrix reply from")
	relay_replies_b=$(count_pattern_in_file "${MMRELAY_LOG_PATH_B}" "Relaying Matrix reply from")
	relay_reactions_a=$(count_pattern_in_file "${MMRELAY_LOG_PATH_A}" "Relaying reaction from")
	relay_reactions_b=$(count_pattern_in_file "${MMRELAY_LOG_PATH_B}" "Relaying reaction from")
	remote_mesh_a=$(count_pattern_in_file "${MMRELAY_LOG_PATH_A}" "Processing message from remote meshnet:")
	remote_mesh_b=$(count_pattern_in_file "${MMRELAY_LOG_PATH_B}" "Processing message from remote meshnet:")
	relay_reconnects_a=$(count_pattern_in_file "${MMRELAY_LOG_PATH_A}" "Lost connection (")
	relay_reconnects_b=$(count_pattern_in_file "${MMRELAY_LOG_PATH_B}" "Lost connection (")
	message_map_a=$(count_message_map_rows "${MMRELAY_DB_PATH_A}")
	message_map_b=$(count_message_map_rows "${MMRELAY_DB_PATH_B}")
	force_close_a=$(count_pattern_in_file_since "${mesh_log_a_live}" "Force close previous TCP connection" "${MESHTASTICD_LOG_OFFSET_A}")
	force_close_b=$(count_pattern_in_file_since "${mesh_log_b_live}" "Force close previous TCP connection" "${MESHTASTICD_LOG_OFFSET_B}")
	incoming_api_a=$(count_pattern_in_file_since "${mesh_log_a_live}" "Incoming API connection" "${MESHTASTICD_LOG_OFFSET_A}")
	incoming_api_b=$(count_pattern_in_file_since "${mesh_log_b_live}" "Incoming API connection" "${MESHTASTICD_LOG_OFFSET_B}")
	lost_phone_a=$(count_pattern_in_file_since "${mesh_log_a_live}" "Lost phone connection" "${MESHTASTICD_LOG_OFFSET_A}")
	lost_phone_b=$(count_pattern_in_file_since "${mesh_log_b_live}" "Lost phone connection" "${MESHTASTICD_LOG_OFFSET_B}")
	if ((relay_reconnects_a > 0 || force_close_a > 0 || lost_phone_a > 0)); then
		stability_note_a="connection churn observed"
	fi
	if ((relay_reconnects_b > 0 || force_close_b > 0 || lost_phone_b > 0)); then
		stability_note_b="connection churn observed"
	fi

	local summary_md="${SHARED_DIR}/observability-summary.md"
	{
		echo "## Meshtasticd CI Observability Summary"
		echo
		echo "- Suite duration: ${suite_duration_ms} ms"
		echo "- Tests: ${passed_tests}/${total_tests} passed, ${failed_tests} failed"
		echo
		echo "### Test Results"
		echo "| Test | Status | Duration (ms) | Note |"
		echo "|---|---|---:|---|"
		local idx
		for idx in "${!TEST_RESULT_NAMES[@]}"; do
			echo "| ${TEST_RESULT_NAMES[$idx]} | ${TEST_RESULT_STATUS[$idx]} | ${TEST_RESULT_DURATION_MS[$idx]} | ${TEST_RESULT_NOTES[$idx]} |"
		done
		echo
		echo "### Relay Flow Metrics"
		echo "| Metric | Instance A | Instance B |"
		echo "|---|---:|---:|"
		echo "| Relaying message from | ${relay_messages_a} | ${relay_messages_b} |"
		echo "| Relaying Matrix reply from | ${relay_replies_a} | ${relay_replies_b} |"
		echo "| Relaying reaction from | ${relay_reactions_a} | ${relay_reactions_b} |"
		echo "| Processing message from remote meshnet | ${remote_mesh_a} | ${remote_mesh_b} |"
		echo "| Lost connection (reconnect triggers) | ${relay_reconnects_a} | ${relay_reconnects_b} |"
		echo "| message_map rows | ${message_map_a} | ${message_map_b} |"
		echo
		echo "### Meshtasticd Connection Metrics"
		echo "| Metric | Node A | Node B |"
		echo "|---|---:|---:|"
		echo "| Incoming API connection | ${incoming_api_a} | ${incoming_api_b} |"
		echo "| Force close previous TCP connection | ${force_close_a} | ${force_close_b} |"
		echo "| Lost phone connection | ${lost_phone_a} | ${lost_phone_b} |"
		echo "| Stability status | ${stability_note_a} | ${stability_note_b} |"
		echo "| Metrics scope | test phase only | test phase only |"
		echo
		echo "### Artifacts"
		echo "- MMRelay A log: \`${MMRELAY_LOG_PATH_A}\`"
		echo "- MMRelay B log: \`${MMRELAY_LOG_PATH_B}\`"
		echo "- meshtasticd A live log snapshot: \`${mesh_log_a_live}\`"
		echo "- meshtasticd B live log snapshot: \`${mesh_log_b_live}\`"
		echo "- meshtasticd A final log: \`${MESHTASTICD_LOG_PATH_A}\`"
		echo "- meshtasticd B final log: \`${MESHTASTICD_LOG_PATH_B}\`"
		echo "- Synapse log: \`${SYNAPSE_LOG_PATH}\`"
	} >"${summary_md}"

	echo ""
	echo "============================================================================"
	echo "Observability Summary"
	echo "============================================================================"
	echo "Suite: ${passed_tests}/${total_tests} passed, ${failed_tests} failed, ${suite_duration_ms} ms"
	echo "Relay A: msg=${relay_messages_a} reply=${relay_replies_a} reaction=${relay_reactions_a} remote=${remote_mesh_a} reconnect=${relay_reconnects_a} map=${message_map_a}"
	echo "Relay B: msg=${relay_messages_b} reply=${relay_replies_b} reaction=${relay_reactions_b} remote=${remote_mesh_b} reconnect=${relay_reconnects_b} map=${message_map_b}"
	echo "Node A: incoming=${incoming_api_a} force-close=${force_close_a} lost-phone=${lost_phone_a} status=${stability_note_a}"
	echo "Node B: incoming=${incoming_api_b} force-close=${force_close_b} lost-phone=${lost_phone_b} status=${stability_note_b}"
	echo "Detailed summary: ${summary_md}"

	if [[ -n ${GITHUB_STEP_SUMMARY-} ]]; then
		cat "${summary_md}" >>"${GITHUB_STEP_SUMMARY}" || true
	fi
}

fail_test() {
	local note=$1
	record_test_result "FAILED" "${note}"
	echo "✗ ${CURRENT_TEST_NAME} FAILED: ${note}" >&2
	write_observability_report
	exit 1
}

cleanup() {
	local exit_code=$?
	local shutdown_timeout=10

	if ((SUITE_START_MS > 0)); then
		write_observability_report || true
	fi

	# Graceful MMRelay A shutdown
	if [[ -n ${MMRELAY_PID_A} ]] && kill -0 "${MMRELAY_PID_A}" >/dev/null 2>&1; then
		echo "Stopping MMRelay A (PID ${MMRELAY_PID_A})..."
		kill -TERM "${MMRELAY_PID_A}" 2>/dev/null || true
		for i in $(seq 1 $shutdown_timeout); do
			kill -0 "${MMRELAY_PID_A}" 2>/dev/null || break
			sleep 1
		done
		kill -0 "${MMRELAY_PID_A}" 2>/dev/null && kill -KILL "${MMRELAY_PID_A}" 2>/dev/null || true
		wait "${MMRELAY_PID_A}" 2>/dev/null || true
	fi

	# Graceful MMRelay B shutdown
	if [[ -n ${MMRELAY_PID_B} ]] && kill -0 "${MMRELAY_PID_B}" >/dev/null 2>&1; then
		echo "Stopping MMRelay B (PID ${MMRELAY_PID_B})..."
		kill -TERM "${MMRELAY_PID_B}" 2>/dev/null || true
		for i in $(seq 1 $shutdown_timeout); do
			kill -0 "${MMRELAY_PID_B}" 2>/dev/null || break
			sleep 1
		done
		kill -0 "${MMRELAY_PID_B}" 2>/dev/null && kill -KILL "${MMRELAY_PID_B}" 2>/dev/null || true
		wait "${MMRELAY_PID_B}" 2>/dev/null || true
	fi

	# Capture Docker logs before cleanup
	if docker ps -a --format '{{.Names}}' | grep -Fxq "${MESHTASTICD_CONTAINER_A}"; then
		echo "Capturing meshtasticd A logs..."
		docker logs "${MESHTASTICD_CONTAINER_A}" >"${MESHTASTICD_LOG_PATH_A}" 2>&1 || true
		docker rm -f "${MESHTASTICD_CONTAINER_A}" >/dev/null 2>&1 || true
	fi

	if docker ps -a --format '{{.Names}}' | grep -Fxq "${MESHTASTICD_CONTAINER_B}"; then
		echo "Capturing meshtasticd B logs..."
		docker logs "${MESHTASTICD_CONTAINER_B}" >"${MESHTASTICD_LOG_PATH_B}" 2>&1 || true
		docker rm -f "${MESHTASTICD_CONTAINER_B}" >/dev/null 2>&1 || true
	fi

	if docker ps -a --format '{{.Names}}' | grep -Fxq "${SYNAPSE_CONTAINER}"; then
		echo "Capturing Synapse logs..."
		docker logs "${SYNAPSE_CONTAINER}" >"${SYNAPSE_LOG_PATH}" 2>&1 || true
		docker rm -f "${SYNAPSE_CONTAINER}" >/dev/null 2>&1 || true
	fi

	print_logs_if_needed "${exit_code}"
	exit "${exit_code}"
}

wait_for_meshtasticd_ready() {
	local endpoint=$1
	local container=$2
	local deadline=$((SECONDS + 10#${MESHTASTICD_READY_TIMEOUT_SECONDS}))
	until "${PYTHON_BIN}" -m meshtastic --timeout 5 --host "${endpoint}" --info >/dev/null 2>&1; do
		if ! docker ps --format '{{.Names}}' | grep -Fxq "${container}"; then
			echo "${container} exited before becoming ready." >&2
			return 1
		fi
		if ((SECONDS >= deadline)); then
			echo "${container} did not become ready within ${MESHTASTICD_READY_TIMEOUT_SECONDS}s." >&2
			return 1
		fi
		sleep 2
	done
	echo "${container} is ready."
}

configure_mesh_channel() {
	local endpoint=$1
	local channel_name=$2
	local psk_hex=$3

	"${PYTHON_BIN}" -m meshtastic \
		--timeout 25 \
		--host "${endpoint}" \
		--ch-set name "${channel_name}" \
		--ch-index 0 >/dev/null

	"${PYTHON_BIN}" -m meshtastic \
		--timeout 25 \
		--host "${endpoint}" \
		--ch-set psk "${psk_hex}" \
		--ch-index 0 >/dev/null
}

wait_for_synapse_ready() {
	local base_url=$1
	local deadline=$((SECONDS + 10#${SYNAPSE_READY_TIMEOUT_SECONDS}))
	until "${PYTHON_BIN}" - "${base_url}" <<'PY'; do
import sys
import requests

base_url = sys.argv[1]
try:
    response = requests.get(f"{base_url}/_matrix/client/versions", timeout=5)
except requests.RequestException:
    raise SystemExit(1)

if response.status_code >= 500:
    raise SystemExit(1)

if response.status_code != 200:
    raise SystemExit(1)
PY
		if ! docker ps --format '{{.Names}}' | grep -Fxq "${SYNAPSE_CONTAINER}"; then
			echo "${SYNAPSE_CONTAINER} exited before becoming ready." >&2
			return 1
		fi
		if ((SECONDS >= deadline)); then
			echo "Synapse did not become ready within ${SYNAPSE_READY_TIMEOUT_SECONDS}s." >&2
			return 1
		fi
		sleep 2
	done
	echo "Synapse is ready."
}

wait_for_log_pattern_since() {
	local log_file=$1
	local pattern=$2
	local start_byte=$3
	local timeout_seconds=$4
	local deadline=$((SECONDS + timeout_seconds))

	while ((SECONDS < deadline)); do
		if [[ -f ${log_file} ]] && (($(wc -c <"${log_file}") > start_byte)); then
			if tail -c +$((start_byte + 1)) "${log_file}" | grep -Fq "${pattern}"; then
				return 0
			fi
		fi
		if [[ -n ${MMRELAY_PID_A} ]] && ! kill -0 "${MMRELAY_PID_A}" >/dev/null 2>&1; then
			echo "MMRelay A process exited unexpectedly while waiting for '${pattern}'." >&2
			return 1
		fi
		if [[ -n ${MMRELAY_PID_B} ]] && ! kill -0 "${MMRELAY_PID_B}" >/dev/null 2>&1; then
			echo "MMRelay B process exited unexpectedly while waiting for '${pattern}'." >&2
			return 1
		fi
		sleep 1
	done

	echo "Timed out waiting for MMRelay log pattern: ${pattern}" >&2
	return 1
}

load_json_value() {
	local key=$1
	"${PYTHON_BIN}" - "${MATRIX_RUNTIME_JSON}" "${key}" <<'PY'
import json
import sys

runtime_file = sys.argv[1]
key = sys.argv[2]
with open(runtime_file, encoding="utf-8") as f:
    data = json.load(f)

value = data.get(key)
if value is None:
    raise SystemExit(1)
print(value)
PY
}

json_extract() {
	local json_payload=$1
	local key_path=$2
	JSON_PAYLOAD="${json_payload}" "${PYTHON_BIN}" - "${key_path}" <<'PY'
import json
import os
import sys

path = sys.argv[1]
data = json.loads(os.environ["JSON_PAYLOAD"])
current = data
for segment in path.split("."):
    if not segment:
        continue
    if isinstance(current, list):
        current = current[int(segment)]
        continue
    if not isinstance(current, dict) or segment not in current:
        raise SystemExit(1)
    current = current[segment]

if current is None:
    raise SystemExit(1)

if isinstance(current, (dict, list)):
    print(json.dumps(current))
else:
    print(current)
PY
}

matrix_send_message() {
	local access_token=$1
	local room_id=$2
	local message_text=$3
	local txn_prefix=$4
	local reply_to_event_id="${5-}"
	"${PYTHON_BIN}" - "${MATRIX_BASE_URL}" "${access_token}" "${room_id}" "${message_text}" "${txn_prefix}" "${reply_to_event_id}" <<'PY'
import os
import sys
import time
import urllib.parse

import requests

base_url = sys.argv[1]
access_token = sys.argv[2]
room_id = sys.argv[3]
message_text = sys.argv[4]
txn_prefix = sys.argv[5]
reply_to_event_id = sys.argv[6]

txn_id = f"{txn_prefix}-{int(time.time() * 1000)}-{os.getpid()}"
quoted_room_id = urllib.parse.quote(room_id, safe="")
quoted_txn_id = urllib.parse.quote(txn_id, safe="")
url = (
    f"{base_url}/_matrix/client/v3/rooms/{quoted_room_id}/"
    f"send/m.room.message/{quoted_txn_id}"
)
content = {"msgtype": "m.text", "body": message_text}
if reply_to_event_id:
    content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to_event_id}}

response = requests.put(
    url,
    headers={"Authorization": f"Bearer {access_token}"},
    json=content,
    timeout=20,
)
if response.status_code >= 400:
    raise RuntimeError(
        f"Failed to send Matrix message ({response.status_code}): {response.text}"
    )

event_id = response.json().get("event_id")
if not isinstance(event_id, str) or not event_id:
    raise RuntimeError("Matrix send response missing event_id")
print(event_id)
PY
}

matrix_send_mesh_origin_message() {
	local access_token=$1
	local room_id=$2
	local message_text=$3
	local meshnet_name=$4
	local longname=$5
	local shortname=$6
	local meshtastic_id=$7
	local txn_prefix=$8
	"${PYTHON_BIN}" - "${MATRIX_BASE_URL}" "${access_token}" "${room_id}" "${message_text}" "${meshnet_name}" "${longname}" "${shortname}" "${meshtastic_id}" "${txn_prefix}" <<'PY'
import os
import sys
import time
import urllib.parse

import requests

base_url = sys.argv[1]
access_token = sys.argv[2]
room_id = sys.argv[3]
message_text = sys.argv[4]
meshnet_name = sys.argv[5]
longname = sys.argv[6]
shortname = sys.argv[7]
meshtastic_id = int(sys.argv[8])
txn_prefix = sys.argv[9]

txn_id = f"{txn_prefix}-{int(time.time() * 1000)}-{os.getpid()}"
quoted_room_id = urllib.parse.quote(room_id, safe="")
quoted_txn_id = urllib.parse.quote(txn_id, safe="")
url = (
    f"{base_url}/_matrix/client/v3/rooms/{quoted_room_id}/"
    f"send/m.room.message/{quoted_txn_id}"
)
content = {
    "msgtype": "m.text",
    "body": message_text,
    "meshtastic_text": message_text,
    "meshtastic_meshnet": meshnet_name,
    "meshtastic_longname": longname,
    "meshtastic_shortname": shortname,
    "meshtastic_id": meshtastic_id,
}
response = requests.put(
    url,
    headers={"Authorization": f"Bearer {access_token}"},
    json=content,
    timeout=20,
)
if response.status_code >= 400:
    raise RuntimeError(
        f"Failed to send injected mesh-origin message ({response.status_code}): {response.text}"
    )
event_id = response.json().get("event_id")
if not isinstance(event_id, str) or not event_id:
    raise RuntimeError("Injected mesh-origin send response missing event_id")
print(event_id)
PY
}

matrix_send_reaction() {
	local access_token=$1
	local room_id=$2
	local target_event_id=$3
	local reaction_key=$4
	local txn_prefix=$5
	"${PYTHON_BIN}" - "${MATRIX_BASE_URL}" "${access_token}" "${room_id}" "${target_event_id}" "${reaction_key}" "${txn_prefix}" <<'PY'
import os
import sys
import time
import urllib.parse

import requests

base_url = sys.argv[1]
access_token = sys.argv[2]
room_id = sys.argv[3]
target_event_id = sys.argv[4]
reaction_key = sys.argv[5]
txn_prefix = sys.argv[6]

txn_id = f"{txn_prefix}-{int(time.time() * 1000)}-{os.getpid()}"
quoted_room_id = urllib.parse.quote(room_id, safe="")
quoted_txn_id = urllib.parse.quote(txn_id, safe="")
url = (
    f"{base_url}/_matrix/client/v3/rooms/{quoted_room_id}/"
    f"send/m.reaction/{quoted_txn_id}"
)
content = {
    "m.relates_to": {
        "event_id": target_event_id,
        "rel_type": "m.annotation",
        "key": reaction_key,
    }
}
response = requests.put(
    url,
    headers={"Authorization": f"Bearer {access_token}"},
    json=content,
    timeout=20,
)
if response.status_code >= 400:
    raise RuntimeError(
        f"Failed to send Matrix reaction ({response.status_code}): {response.text}"
    )

event_id = response.json().get("event_id")
if not isinstance(event_id, str) or not event_id:
    raise RuntimeError("Matrix reaction response missing event_id")
print(event_id)
PY
}

matrix_wait_event() {
	local access_token=$1
	local room_id=$2
	local timeout_seconds=$3
	local event_type="${4-}"
	local sender="${5-}"
	local body_contains="${6-}"
	local msgtype="${7-}"
	local relates_to_event_id="${8-}"
	local event_id_filter="${9-}"
	local meshtastic_reply_id="${10-}"
	local since_token="${11-}"

	"${PYTHON_BIN}" - "${MATRIX_BASE_URL}" "${access_token}" "${room_id}" "${timeout_seconds}" "${event_type}" "${sender}" "${body_contains}" "${msgtype}" "${relates_to_event_id}" "${event_id_filter}" "${meshtastic_reply_id}" "${since_token}" <<'PY'
import json
import sys
import time

import requests

(
    base_url,
    access_token,
    room_id,
    timeout_seconds_raw,
    event_type,
    sender,
    body_contains,
    msgtype,
    relates_to_event_id,
    event_id_filter,
    meshtastic_reply_id,
    since,
) = sys.argv[1:13]

timeout_seconds = int(timeout_seconds_raw)
deadline = time.monotonic() + timeout_seconds
headers = {"Authorization": f"Bearer {access_token}"}
recent = []

def _matches(event: dict) -> bool:
    if event_type and event.get("type") != event_type:
        return False
    if sender and event.get("sender") != sender:
        return False
    if event_id_filter and event.get("event_id") != event_id_filter:
        return False

    content = event.get("content", {})
    if not isinstance(content, dict):
        return False

    if msgtype and content.get("msgtype") != msgtype:
        return False

    if body_contains:
        body = content.get("body", "")
        if not isinstance(body, str) or body_contains not in body:
            return False

    if relates_to_event_id:
        relates_to = content.get("m.relates_to") or {}
        related_event_id = None
        if isinstance(relates_to, dict):
            event_id = relates_to.get("event_id")
            if isinstance(event_id, str):
                related_event_id = event_id
            in_reply_to = relates_to.get("m.in_reply_to")
            if isinstance(in_reply_to, dict):
                reply_event_id = in_reply_to.get("event_id")
                if isinstance(reply_event_id, str):
                    related_event_id = reply_event_id
        if related_event_id != relates_to_event_id:
            return False

    if meshtastic_reply_id:
        reply_id = content.get("meshtastic_replyId")
        if str(reply_id) != meshtastic_reply_id:
            return False

    return True

while time.monotonic() < deadline:
    params = {"timeout": 5000}
    if since:
        params["since"] = since

    response = requests.get(
        f"{base_url}/_matrix/client/v3/sync",
        headers=headers,
        params=params,
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Sync failed ({response.status_code}): {response.text[:300]}"
        )

    payload = response.json()
    since = payload.get("next_batch", since)
    events = (
        payload.get("rooms", {})
        .get("join", {})
        .get(room_id, {})
        .get("timeline", {})
        .get("events", [])
    )
    for event in events:
        content = event.get("content", {})
        if isinstance(content, dict):
            recent.append(
                {
                    "event_id": event.get("event_id"),
                    "type": event.get("type"),
                    "sender": event.get("sender"),
                    "msgtype": content.get("msgtype"),
                    "body": content.get("body"),
                }
            )
            recent = recent[-15:]
        if _matches(event):
            print(json.dumps({"event": event, "next_batch": since}))
            raise SystemExit(0)

if recent:
    print("Timed out waiting for Matrix event. Recent events:", file=sys.stderr)
    print(json.dumps(recent, indent=2), file=sys.stderr)
else:
    print("Timed out waiting for Matrix event. No recent events found.", file=sys.stderr)
raise SystemExit(1)
PY
}

wait_for_message_map_meshtastic_id() {
	local db_path=$1
	local matrix_event_id=$2
	local timeout_seconds=$3
	"${PYTHON_BIN}" - "${db_path}" "${matrix_event_id}" "${timeout_seconds}" <<'PY'
import sqlite3
import sys
import time

db_path = sys.argv[1]
matrix_event_id = sys.argv[2]
timeout_seconds = int(sys.argv[3])

deadline = time.monotonic() + timeout_seconds
last_error = None
while time.monotonic() < deadline:
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            row = conn.execute(
                "SELECT meshtastic_id FROM message_map WHERE matrix_event_id=?",
                (matrix_event_id,),
            ).fetchone()
            if row and row[0] not in (None, ""):
                print(row[0])
                raise SystemExit(0)
        finally:
            conn.close()
    except sqlite3.Error as exc:  # pragma: no cover - retry loop
        last_error = str(exc)
    time.sleep(1)

if last_error:
    print(f"Last SQLite error: {last_error}", file=sys.stderr)
print(
    f"Timed out waiting for message_map row for matrix_event_id={matrix_event_id}",
    file=sys.stderr,
)
raise SystemExit(1)
PY
}

# =============================================================================
# Validation
# =============================================================================

trap cleanup EXIT

if ! command -v docker >/dev/null 2>&1; then
	echo "docker is required for meshtasticd integration tests." >&2
	exit 1
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
	echo "Python runtime '${PYTHON_BIN}' is required." >&2
	exit 1
fi

if [[ "$(uname -s)" != "Linux" ]]; then
	echo "meshtasticd integration currently requires Linux (Docker host networking)." >&2
	exit 1
fi

# Validate all configuration parameters
require_regex "${MESHTASTICD_CONTAINER_A}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "MESHTASTICD_CONTAINER_A"
require_regex "${MESHTASTICD_CONTAINER_B}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "MESHTASTICD_CONTAINER_B"
require_regex "${MESHTASTICD_IMAGE}" '^[^[:space:]]+$' "MESHTASTICD_IMAGE"
require_regex "${MESHTASTICD_HOST_A}" '^[A-Za-z0-9._-]+$' "MESHTASTICD_HOST_A"
require_regex "${MESHTASTICD_HOST_B}" '^[A-Za-z0-9._-]+$' "MESHTASTICD_HOST_B"
require_regex "${MESHTASTICD_PORT_A}" '^[0-9]+$' "MESHTASTICD_PORT_A"
require_regex "${MESHTASTICD_PORT_B}" '^[0-9]+$' "MESHTASTICD_PORT_B"
require_regex "${MESHTASTICD_HWID_A}" '^[0-9]+$' "MESHTASTICD_HWID_A"
require_regex "${MESHTASTICD_HWID_B}" '^[0-9]+$' "MESHTASTICD_HWID_B"
require_regex "${SYNAPSE_CONTAINER}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "SYNAPSE_CONTAINER"
require_regex "${SYNAPSE_IMAGE}" '^[^[:space:]]+$' "SYNAPSE_IMAGE"
require_regex "${SYNAPSE_PORT}" '^[0-9]+$' "SYNAPSE_PORT"
require_regex "${MMRELAY_READY_TIMEOUT_SECONDS}" '^[0-9]+$' "MMRELAY_READY_TIMEOUT_SECONDS"
require_regex "${MATRIX_EVENT_TIMEOUT_SECONDS}" '^[0-9]+$' "MATRIX_EVENT_TIMEOUT_SECONDS"
require_regex "${MESSAGE_MAP_WAIT_TIMEOUT_SECONDS}" '^[0-9]+$' "MESSAGE_MAP_WAIT_TIMEOUT_SECONDS"
require_regex "${MESH_CHANNEL_NAME_A}" '^[[:print:]]+$' "MESH_CHANNEL_NAME_A"
require_regex "${MESH_CHANNEL_NAME_B}" '^[[:print:]]+$' "MESH_CHANNEL_NAME_B"
require_regex "${MESH_PRIMARY_PSK_A}" '^0x[0-9A-Fa-f]{64}$' "MESH_PRIMARY_PSK_A"
require_regex "${MESH_PRIMARY_PSK_B}" '^0x[0-9A-Fa-f]{64}$' "MESH_PRIMARY_PSK_B"

# Port validation
MESHTASTICD_PORT_A_DEC=$((10#${MESHTASTICD_PORT_A}))
MESHTASTICD_PORT_B_DEC=$((10#${MESHTASTICD_PORT_B}))
SYNAPSE_PORT_DEC=$((10#${SYNAPSE_PORT}))

if ((MESHTASTICD_PORT_A_DEC < 1 || MESHTASTICD_PORT_A_DEC > 65535)); then
	echo "MESHTASTICD_PORT_A must be between 1 and 65535." >&2
	exit 1
fi
if ((MESHTASTICD_PORT_B_DEC < 1 || MESHTASTICD_PORT_B_DEC > 65535)); then
	echo "MESHTASTICD_PORT_B must be between 1 and 65535." >&2
	exit 1
fi
if ((MESHTASTICD_PORT_A_DEC == MESHTASTICD_PORT_B_DEC)); then
	echo "MESHTASTICD_PORT_A and MESHTASTICD_PORT_B must be different." >&2
	exit 1
fi
if ((SYNAPSE_PORT_DEC < 1 || SYNAPSE_PORT_DEC > 65535)); then
	echo "SYNAPSE_PORT must be between 1 and 65535." >&2
	exit 1
fi
if ((10#${MMRELAY_READY_TIMEOUT_SECONDS} <= 0)); then
	echo "MMRELAY_READY_TIMEOUT_SECONDS must be greater than zero." >&2
	exit 1
fi
if ((10#${MATRIX_EVENT_TIMEOUT_SECONDS} <= 0)); then
	echo "MATRIX_EVENT_TIMEOUT_SECONDS must be greater than zero." >&2
	exit 1
fi
if ((10#${MESSAGE_MAP_WAIT_TIMEOUT_SECONDS} <= 0)); then
	echo "MESSAGE_MAP_WAIT_TIMEOUT_SECONDS must be greater than zero." >&2
	exit 1
fi
if [[ -z ${MESHNET_NAME_A} || -z ${MESHNET_NAME_B} ]]; then
	echo "MESHNET_NAME_A and MESHNET_NAME_B must be non-empty." >&2
	exit 1
fi

MESHTASTICD_ENDPOINT_A="${MESHTASTICD_HOST_A}:${MESHTASTICD_PORT_A_DEC}"
MESHTASTICD_ENDPOINT_B="${MESHTASTICD_HOST_B}:${MESHTASTICD_PORT_B_DEC}"

# =============================================================================
# Setup
# =============================================================================

echo "Meshtasticd Integration Test - Testing MMRelay's core use case"
echo "============================================================================"
echo ""

# Clean up previous runs
if [[ -d ${CI_ARTIFACT_DIR} ]]; then
	chmod -R u+rwX "${CI_ARTIFACT_DIR}" >/dev/null 2>&1 || true
	if ! rm -rf "${CI_ARTIFACT_DIR}"; then
		docker run --rm \
			--user root \
			-v "${CI_ARTIFACT_DIR}:/work" \
			"alpine:3.22" \
			/bin/sh -c "chown -R $(id -u):$(id -g) /work" >/dev/null
		rm -rf "${CI_ARTIFACT_DIR}"
	fi
fi
mkdir -p "${CI_ARTIFACT_DIR}" "${SYNAPSE_DATA_DIR}" "${MMRELAY_HOME_DIR_A}" "${MMRELAY_HOME_DIR_B}" "${INSTANCE_A_LOG_DIR}" "${INSTANCE_B_LOG_DIR}" "${SYNAPSE_LOG_DIR}"

MATRIX_BASE_URL="http://localhost:${SYNAPSE_PORT_DEC}"

# User configuration
MATRIX_BOT_USER_A_LOCALPART="mmrelaybot-a"
MATRIX_BOT_USER_B_LOCALPART="mmrelaybot-b"
MATRIX_USER_LOCALPART="mmrelayuser"
MATRIX_USER2_LOCALPART="mmrelayuser2"
MATRIX_BOT_A_PASSWORD="mmrelay-bot-a-pass"
MATRIX_BOT_B_PASSWORD="mmrelay-bot-b-pass"
MATRIX_USER_PASSWORD="mmrelay-user-pass"
MATRIX_USER2_PASSWORD="mmrelay-user2-pass"

export MATRIX_BASE_URL
export MATRIX_BOT_USER_A_LOCALPART
export MATRIX_BOT_USER_B_LOCALPART
export MATRIX_USER_LOCALPART
export MATRIX_USER2_LOCALPART
export MATRIX_BOT_A_PASSWORD
export MATRIX_BOT_B_PASSWORD
export MATRIX_USER_PASSWORD
export MATRIX_USER2_PASSWORD

# =============================================================================
# Start Infrastructure
# =============================================================================

# Ensure clean slate
docker rm -f \
	"${MESHTASTICD_CONTAINER_A}" \
	"${MESHTASTICD_CONTAINER_B}" \
	"${SYNAPSE_CONTAINER}" >/dev/null 2>&1 || true

echo "Pulling meshtasticd image: ${MESHTASTICD_IMAGE}"
if ! docker pull "${MESHTASTICD_IMAGE}"; then
	if [[ ${MESHTASTICD_IMAGE} == "meshtastic/meshtasticd:latest" || ${MESHTASTICD_IMAGE} == "meshtastic/meshtasticd" ]]; then
		echo "Failed to pull ${MESHTASTICD_IMAGE}; retrying with meshtastic/meshtasticd:beta" >&2
		MESHTASTICD_IMAGE="meshtastic/meshtasticd:beta"
		docker pull "${MESHTASTICD_IMAGE}"
	else
		echo "Failed to pull ${MESHTASTICD_IMAGE}" >&2
		exit 1
	fi
fi

echo ""
echo "Starting meshtasticd containers..."
docker run -d \
	--name "${MESHTASTICD_CONTAINER_A}" \
	--network host \
	"${MESHTASTICD_IMAGE}" \
	meshtasticd -s --fsdir=/var/lib/meshtasticd-relay-a -p "${MESHTASTICD_PORT_A_DEC}" -h "${MESHTASTICD_HWID_A}" >/dev/null

docker run -d \
	--name "${MESHTASTICD_CONTAINER_B}" \
	--network host \
	"${MESHTASTICD_IMAGE}" \
	meshtasticd -s --fsdir=/var/lib/meshtasticd-relay-b -p "${MESHTASTICD_PORT_B_DEC}" -h "${MESHTASTICD_HWID_B}" >/dev/null

wait_for_meshtasticd_ready "${MESHTASTICD_ENDPOINT_A}" "${MESHTASTICD_CONTAINER_A}"
wait_for_meshtasticd_ready "${MESHTASTICD_ENDPOINT_B}" "${MESHTASTICD_CONTAINER_B}"

echo ""
echo "Configuring isolated meshnets (one relay node per meshnet)..."
configure_mesh_channel "${MESHTASTICD_ENDPOINT_A}" "${MESH_CHANNEL_NAME_A}" "${MESH_PRIMARY_PSK_A}"
configure_mesh_channel "${MESHTASTICD_ENDPOINT_B}" "${MESH_CHANNEL_NAME_B}" "${MESH_PRIMARY_PSK_B}"
wait_for_meshtasticd_ready "${MESHTASTICD_ENDPOINT_A}" "${MESHTASTICD_CONTAINER_A}"
wait_for_meshtasticd_ready "${MESHTASTICD_ENDPOINT_B}" "${MESHTASTICD_CONTAINER_B}"

echo ""
echo "Pulling Synapse image: ${SYNAPSE_IMAGE}"
docker pull "${SYNAPSE_IMAGE}"

echo ""
echo "Generating Synapse config..."
docker run --rm \
	--user "$(id -u):$(id -g)" \
	-e SYNAPSE_SERVER_NAME="${SYNAPSE_SERVER_NAME}" \
	-e SYNAPSE_REPORT_STATS=no \
	-v "${SYNAPSE_DATA_DIR}:/data" \
	"${SYNAPSE_IMAGE}" generate >/dev/null

cat >>"${SYNAPSE_DATA_DIR}/homeserver.yaml" <<'YAML'
registration_shared_secret: "mmrelay-ci-shared-secret"
enable_registration: true
enable_registration_without_verification: true
YAML

echo "Starting Synapse container..."
docker run -d \
	--name "${SYNAPSE_CONTAINER}" \
	--user "$(id -u):$(id -g)" \
	-e SYNAPSE_SERVER_NAME="${SYNAPSE_SERVER_NAME}" \
	-e SYNAPSE_REPORT_STATS=no \
	-p "${SYNAPSE_PORT_DEC}":8008 \
	-v "${SYNAPSE_DATA_DIR}:/data" \
	"${SYNAPSE_IMAGE}" >/dev/null

wait_for_synapse_ready "${MATRIX_BASE_URL}"

# =============================================================================
# Create Matrix Users and Room
# =============================================================================

echo ""
echo "Creating Matrix users..."
docker exec "${SYNAPSE_CONTAINER}" register_new_matrix_user \
	-u "${MATRIX_BOT_USER_A_LOCALPART}" \
	-p "${MATRIX_BOT_A_PASSWORD}" \
	-a \
	-c /data/homeserver.yaml \
	"http://localhost:8008" >/dev/null

docker exec "${SYNAPSE_CONTAINER}" register_new_matrix_user \
	-u "${MATRIX_BOT_USER_B_LOCALPART}" \
	-p "${MATRIX_BOT_B_PASSWORD}" \
	-a \
	-c /data/homeserver.yaml \
	"http://localhost:8008" >/dev/null

docker exec "${SYNAPSE_CONTAINER}" register_new_matrix_user \
	-u "${MATRIX_USER_LOCALPART}" \
	-p "${MATRIX_USER_PASSWORD}" \
	--no-admin \
	-c /data/homeserver.yaml \
	"http://localhost:8008" >/dev/null

docker exec "${SYNAPSE_CONTAINER}" register_new_matrix_user \
	-u "${MATRIX_USER2_LOCALPART}" \
	-p "${MATRIX_USER2_PASSWORD}" \
	--no-admin \
	-c /data/homeserver.yaml \
	"http://localhost:8008" >/dev/null

echo ""
echo "Preparing Matrix room and runtime credentials..."
"${PYTHON_BIN}" - <<'PY' >"${MATRIX_RUNTIME_JSON}"
import json
import os
import sys
import time
import urllib.parse

import requests

base_url = os.environ["MATRIX_BASE_URL"]
bot_a_localpart = os.environ["MATRIX_BOT_USER_A_LOCALPART"]
bot_b_localpart = os.environ["MATRIX_BOT_USER_B_LOCALPART"]
user1_localpart = os.environ["MATRIX_USER_LOCALPART"]
user2_localpart = os.environ["MATRIX_USER2_LOCALPART"]
bot_a_password = os.environ["MATRIX_BOT_A_PASSWORD"]
bot_b_password = os.environ["MATRIX_BOT_B_PASSWORD"]
user1_password = os.environ["MATRIX_USER_PASSWORD"]
user2_password = os.environ["MATRIX_USER2_PASSWORD"]

def _raise_for_status(resp: requests.Response, context: str) -> None:
    if resp.status_code >= 400:
        raise RuntimeError(
            f"{context} failed ({resp.status_code}): {resp.text.strip() or resp.reason}"
        )

def login(localpart: str, password: str) -> dict[str, str]:
    payload = {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": localpart},
        "password": password,
    }
    response = requests.post(
        f"{base_url}/_matrix/client/v3/login",
        json=payload,
        timeout=20,
    )
    _raise_for_status(response, f"login ({localpart})")
    body = response.json()
    return {
        "access_token": body["access_token"],
        "user_id": body["user_id"],
    }

def post(path: str, token: str, payload: dict) -> dict:
    response = requests.post(
        f"{base_url}{path}",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=20,
    )
    _raise_for_status(response, f"POST {path}")
    return response.json()

def create_room(user_token: str, name: str, topic: str, alias_name: str) -> str:
    room_create = post(
        "/_matrix/client/v3/createRoom",
        user_token,
        {
            "preset": "private_chat",
            "name": name,
            "topic": topic,
            "room_alias_name": alias_name,
        },
    )
    room_id = room_create.get("room_id")
    if not isinstance(room_id, str):
        raise RuntimeError(f"createRoom missing room_id for alias {alias_name}")
    return room_id

def invite_and_join(room_id: str, inviter_token: str, account: dict[str, str]) -> None:
    quoted_room_id = urllib.parse.quote(room_id, safe="")
    post(
        f"/_matrix/client/v3/rooms/{quoted_room_id}/invite",
        inviter_token,
        {"user_id": account["user_id"]},
    )
    post(
        f"/_matrix/client/v3/join/{quoted_room_id}",
        account["access_token"],
        {},
    )

bot_a = login(bot_a_localpart, bot_a_password)
bot_b = login(bot_b_localpart, bot_b_password)
user1 = login(user1_localpart, user1_password)
user2 = login(user2_localpart, user2_password)

room_suffix = int(time.time())
room_id = create_room(
    user1["access_token"],
    "MMRelay CI Integration Room",
    "Shared room for MMRelay integration tests",
    f"mmrelay-ci-integration-{room_suffix}",
)

# Shared room members: user1 + user2 + bot_a + bot_b
for account in [bot_a, bot_b, user2]:
    invite_and_join(room_id, user1["access_token"], account)

sync_response = requests.get(
    f"{base_url}/_matrix/client/v3/sync",
    headers={"Authorization": f"Bearer {user1['access_token']}"},
    params={"timeout": 0},
    timeout=20,
)
_raise_for_status(sync_response, "initial user1 sync")
initial_sync_user1 = sync_response.json()

runtime = {
    "room_id": room_id,
    "bot_a_user_id": bot_a["user_id"],
    "bot_a_access_token": bot_a["access_token"],
    "bot_b_user_id": bot_b["user_id"],
    "bot_b_access_token": bot_b["access_token"],
    "user_access_token": user1["access_token"],
    "user2_access_token": user2["access_token"],
    "user2_user_id": user2["user_id"],
    "sync_since_user": initial_sync_user1.get("next_batch", ""),
}

json.dump(runtime, fp=sys.stdout)
PY

BOT_A_USER_ID="$(load_json_value bot_a_user_id)"
BOT_A_ACCESS_TOKEN="$(load_json_value bot_a_access_token)"
BOT_B_USER_ID="$(load_json_value bot_b_user_id)"
BOT_B_ACCESS_TOKEN="$(load_json_value bot_b_access_token)"
USER_ACCESS_TOKEN="$(load_json_value user_access_token)"
USER2_ACCESS_TOKEN="$(load_json_value user2_access_token)"
USER2_USER_ID="$(load_json_value user2_user_id)"
ROOM_ID="$(load_json_value room_id)"
SYNC_SINCE_USER="$(load_json_value sync_since_user)"

export BOT_A_USER_ID
export BOT_A_ACCESS_TOKEN
export BOT_B_USER_ID
export BOT_B_ACCESS_TOKEN
export USER_ACCESS_TOKEN
export USER2_ACCESS_TOKEN
export USER2_USER_ID
export ROOM_ID
export SYNC_SINCE_USER

# =============================================================================
# Create MMRelay Configurations
# =============================================================================

echo ""
echo "Creating MMRelay configurations..."

# MMRelay A - Connects to Mesh A
cat >"${MMRELAY_CONFIG_PATH_A}" <<EOF_CONFIG
matrix:
  homeserver: "${MATRIX_BASE_URL}"
  access_token: "${BOT_A_ACCESS_TOKEN}"
  bot_user_id: "${BOT_A_USER_ID}"
matrix_rooms:
  - id: "${ROOM_ID}"
    meshtastic_channel: 0
meshtastic:
  connection_type: tcp
  host: "${MESHTASTICD_HOST_A}"
  port: ${MESHTASTICD_PORT_A_DEC}
  meshnet_name: "${MESHNET_NAME_A}"
  health_check:
    enabled: false
  broadcast_enabled: true
  message_interactions:
    reactions: true
    replies: true
database:
  msg_map:
    msgs_to_keep: 1500
    wipe_on_restart: false
logging:
  level: debug
EOF_CONFIG

# MMRelay B - Connects to Mesh B
cat >"${MMRELAY_CONFIG_PATH_B}" <<EOF_CONFIG
matrix:
  homeserver: "${MATRIX_BASE_URL}"
  access_token: "${BOT_B_ACCESS_TOKEN}"
  bot_user_id: "${BOT_B_USER_ID}"
matrix_rooms:
  - id: "${ROOM_ID}"
    meshtastic_channel: 0
meshtastic:
  connection_type: tcp
  host: "${MESHTASTICD_HOST_B}"
  port: ${MESHTASTICD_PORT_B_DEC}
  meshnet_name: "${MESHNET_NAME_B}"
  health_check:
    enabled: false
  broadcast_enabled: true
  message_interactions:
    reactions: true
    replies: true
database:
  msg_map:
    msgs_to_keep: 1500
    wipe_on_restart: false
logging:
  level: debug
EOF_CONFIG

# =============================================================================
# Start MMRelay Instances
# =============================================================================

echo ""
echo "Starting MMRelay A (connected to Mesh A)..."
PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -m mmrelay.cli \
	--config "${MMRELAY_CONFIG_PATH_A}" \
	--home "${MMRELAY_HOME_DIR_A}" \
	--log-level debug >"${MMRELAY_LOG_PATH_A}" 2>&1 &
MMRELAY_PID_A=$!

startup_offset_a=0
wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_A}" \
	"Listening for inbound Matrix messages..." \
	"${startup_offset_a}" \
	$((10#${MMRELAY_READY_TIMEOUT_SECONDS}))
echo "MMRelay A is ready (PID ${MMRELAY_PID_A})"

echo ""
echo "Starting MMRelay B (connected to Mesh B)..."
PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -m mmrelay.cli \
	--config "${MMRELAY_CONFIG_PATH_B}" \
	--home "${MMRELAY_HOME_DIR_B}" \
	--log-level debug >"${MMRELAY_LOG_PATH_B}" 2>&1 &
MMRELAY_PID_B=$!

startup_offset_b=0
wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"Listening for inbound Matrix messages..." \
	"${startup_offset_b}" \
	$((10#${MMRELAY_READY_TIMEOUT_SECONDS}))
echo "MMRelay B is ready (PID ${MMRELAY_PID_B})"

# Capture meshtasticd log baselines so churn metrics reflect test-phase behavior only.
mesh_log_baseline_a="${INSTANCE_A_LOG_DIR}/meshtasticd-pre-suite.log"
mesh_log_baseline_b="${INSTANCE_B_LOG_DIR}/meshtasticd-pre-suite.log"
if docker ps -a --format '{{.Names}}' | grep -Fxq "${MESHTASTICD_CONTAINER_A}"; then
	docker logs "${MESHTASTICD_CONTAINER_A}" >"${mesh_log_baseline_a}" 2>&1 || true
	if [[ -f ${mesh_log_baseline_a} ]]; then
		MESHTASTICD_LOG_OFFSET_A=$(wc -c <"${mesh_log_baseline_a}")
	fi
fi
if docker ps -a --format '{{.Names}}' | grep -Fxq "${MESHTASTICD_CONTAINER_B}"; then
	docker logs "${MESHTASTICD_CONTAINER_B}" >"${mesh_log_baseline_b}" 2>&1 || true
	if [[ -f ${mesh_log_baseline_b} ]]; then
		MESHTASTICD_LOG_OFFSET_B=$(wc -c <"${mesh_log_baseline_b}")
	fi
fi

# =============================================================================
# Test Scenarios
# =============================================================================

echo ""
echo "============================================================================"
echo "Running Test Scenarios"
echo "============================================================================"

SUITE_START_MS=$(date +%s%3N)
SYNC_CURSOR_USER1="${SYNC_SINCE_USER}"

# Test 1: Matrix user message in shared room → Mesh A + Mesh B
MATRIX_TO_SHARED_TEXT="MMRELAY_CI_M2SHARED_$(date +%s)_${RANDOM}"
log_offset_before_m2shared_a=$(wc -c <"${MMRELAY_LOG_PATH_A}")
log_offset_before_m2shared_b=$(wc -c <"${MMRELAY_LOG_PATH_B}")

start_test "Test 1" "Test 1: Matrix user message in shared room → Mesh A + Mesh B..."
matrix_send_message \
	"${USER_ACCESS_TOKEN}" \
	"${ROOM_ID}" \
	"${MATRIX_TO_SHARED_TEXT}" \
	"mmrelay-ci-m2shared" >/dev/null
if ! wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_A}" \
	"Relaying message from" \
	"${log_offset_before_m2shared_a}" \
	45; then
	fail_test "Message was not relayed to Mesh A"
fi
if ! wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"Relaying message from" \
	"${log_offset_before_m2shared_b}" \
	45; then
	fail_test "Message was not relayed to Mesh B"
fi
pass_test "Matrix user message relayed to both meshes"

# Test 2: Injected Mesh A-origin event in Matrix → remote meshnet processing in MMRelay B
#
# Keep one API client per meshtasticd node (the relay itself) to avoid transport churn.
# We inject the same mesh-origin Matrix event shape that MMRelay publishes so we still
# exercise MMRelay's remote-meshnet processing path end-to-end through Matrix.
MESH_A_TO_MATRIX_TEXT="MMRELAY_CI_A2M_$(date +%s)_${RANDOM}"
MESH_A_SIM_ID=$((2000000000 + RANDOM))
log_offset_before_a2m_b=$(wc -c <"${MMRELAY_LOG_PATH_B}")

start_test "Test 2" "Test 2: Injected Mesh A-origin event in Matrix → remote processing in Mesh B relay..."
MESH_A_MATRIX_EVENT_ID="$(
	matrix_send_mesh_origin_message \
		"${BOT_A_ACCESS_TOKEN}" \
		"${ROOM_ID}" \
		"${MESH_A_TO_MATRIX_TEXT}" \
		"${MESHNET_NAME_A}" \
		"CI Field Node A" \
		"CFA" \
		"${MESH_A_SIM_ID}" \
		"mmrelay-ci-sim-a2m"
)"
mesh_a_event_json="$(
	matrix_wait_event \
		"${USER_ACCESS_TOKEN}" \
		"${ROOM_ID}" \
		"${MATRIX_EVENT_TIMEOUT_SECONDS}" \
		"m.room.message" \
		"${BOT_A_USER_ID}" \
		"${MESH_A_TO_MATRIX_TEXT}" \
		"m.text" \
		"" \
		"${MESH_A_MATRIX_EVENT_ID}" \
		"" \
		"${SYNC_CURSOR_USER1}"
)"
SYNC_CURSOR_USER1="$(json_extract "${mesh_a_event_json}" "next_batch")"
if ! wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"Processing message from remote meshnet:" \
	"${log_offset_before_a2m_b}" \
	60; then
	fail_test "MMRelay B did not process injected remote mesh event from ${MESHNET_NAME_A}"
fi
pass_test "Injected Mesh A-origin event reached Matrix and processed in Mesh B relay"

# Test 3: Injected Mesh B-origin event in Matrix → remote meshnet processing in MMRelay A
MESH_B_TO_MATRIX_TEXT="MMRELAY_CI_B2M_$(date +%s)_${RANDOM}"
MESH_B_SIM_ID=$((2100000000 + RANDOM))
log_offset_before_b2m_a=$(wc -c <"${MMRELAY_LOG_PATH_A}")

start_test "Test 3" "Test 3: Injected Mesh B-origin event in Matrix → remote processing in Mesh A relay..."
MESH_B_MATRIX_EVENT_ID="$(
	matrix_send_mesh_origin_message \
		"${BOT_B_ACCESS_TOKEN}" \
		"${ROOM_ID}" \
		"${MESH_B_TO_MATRIX_TEXT}" \
		"${MESHNET_NAME_B}" \
		"CI Field Node B" \
		"CFB" \
		"${MESH_B_SIM_ID}" \
		"mmrelay-ci-sim-b2m"
)"
mesh_b_event_json="$(
	matrix_wait_event \
		"${USER_ACCESS_TOKEN}" \
		"${ROOM_ID}" \
		"${MATRIX_EVENT_TIMEOUT_SECONDS}" \
		"m.room.message" \
		"${BOT_B_USER_ID}" \
		"${MESH_B_TO_MATRIX_TEXT}" \
		"m.text" \
		"" \
		"${MESH_B_MATRIX_EVENT_ID}" \
		"" \
		"${SYNC_CURSOR_USER1}"
)"
SYNC_CURSOR_USER1="$(json_extract "${mesh_b_event_json}" "next_batch")"
if ! wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_A}" \
	"Processing message from remote meshnet:" \
	"${log_offset_before_b2m_a}" \
	60; then
	fail_test "MMRelay A did not process injected remote mesh event from ${MESHNET_NAME_B}"
fi
pass_test "Injected Mesh B-origin event reached Matrix and processed in Mesh A relay"

# Test 4: Secondary Matrix user message → Mesh A + Mesh B
MATRIX_USER2_TEXT="MMRELAY_CI_U2_MSG_$(date +%s)_${RANDOM}"
log_offset_before_u2msg_a=$(wc -c <"${MMRELAY_LOG_PATH_A}")
log_offset_before_u2msg_b=$(wc -c <"${MMRELAY_LOG_PATH_B}")

start_test "Test 4" "Test 4: Secondary Matrix user message → Mesh A + Mesh B..."
MATRIX_USER2_EVENT_ID="$(
	matrix_send_message \
		"${USER2_ACCESS_TOKEN}" \
		"${ROOM_ID}" \
		"${MATRIX_USER2_TEXT}" \
		"mmrelay-ci-u2-msg"
)"
if ! wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_A}" \
	"Relaying message from" \
	"${log_offset_before_u2msg_a}" \
	45; then
	fail_test "Secondary user message did not relay to Mesh A"
fi
if ! wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"Relaying message from" \
	"${log_offset_before_u2msg_b}" \
	45; then
	fail_test "Secondary user message did not relay to Mesh B"
fi
pass_test "Secondary user message relayed to both meshes"

start_test "Test 5" "Test 5: Secondary user reply to prior Matrix event → both meshes..."

# Ensure both relay instances have persisted the mapping for the replied-to event.
# Without this gate, a fast reply can race ahead of DB persistence and be treated
# as a plain message instead of a Meshtastic reply.
if ! wait_for_message_map_meshtastic_id \
	"${MMRELAY_DB_PATH_A}" \
	"${MATRIX_USER2_EVENT_ID}" \
	"${MESSAGE_MAP_WAIT_TIMEOUT_SECONDS}" >/dev/null; then
	fail_test "Timed out waiting for message_map row in instance A for replied-to Matrix event"
fi
if ! wait_for_message_map_meshtastic_id \
	"${MMRELAY_DB_PATH_B}" \
	"${MATRIX_USER2_EVENT_ID}" \
	"${MESSAGE_MAP_WAIT_TIMEOUT_SECONDS}" >/dev/null; then
	fail_test "Timed out waiting for message_map row in instance B for replied-to Matrix event"
fi

# Test 5: Secondary Matrix user reply to prior Matrix event → both meshes as structured replies
MATRIX_USER2_REPLY_TEXT="MMRELAY_CI_U2_REPLY_$(date +%s)_${RANDOM}"
log_offset_before_u2reply_a=$(wc -c <"${MMRELAY_LOG_PATH_A}")
log_offset_before_u2reply_b=$(wc -c <"${MMRELAY_LOG_PATH_B}")
MATRIX_USER2_REPLY_EVENT_ID="$(
	matrix_send_message \
		"${USER2_ACCESS_TOKEN}" \
		"${ROOM_ID}" \
		"${MATRIX_USER2_REPLY_TEXT}" \
		"mmrelay-ci-u2-reply" \
		"${MATRIX_USER2_EVENT_ID}"
)"
reply_event_json="$(
	matrix_wait_event \
		"${USER_ACCESS_TOKEN}" \
		"${ROOM_ID}" \
		"${MATRIX_EVENT_TIMEOUT_SECONDS}" \
		"m.room.message" \
		"${USER2_USER_ID}" \
		"" \
		"m.text" \
		"${MATRIX_USER2_EVENT_ID}" \
		"${MATRIX_USER2_REPLY_EVENT_ID}" \
		"" \
		"${SYNC_CURSOR_USER1}"
)"
SYNC_CURSOR_USER1="$(json_extract "${reply_event_json}" "next_batch")"
if ! wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_A}" \
	"Relaying Matrix reply from" \
	"${log_offset_before_u2reply_a}" \
	60; then
	fail_test "Reply did not relay to Mesh A"
fi
if ! wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"Relaying Matrix reply from" \
	"${log_offset_before_u2reply_b}" \
	60; then
	fail_test "Reply did not relay to Mesh B"
fi
pass_test "Secondary user reply relayed to both meshes"

# Test 6: Secondary Matrix user reaction to prior Matrix event → both meshes
MATRIX_USER2_REACTION_KEY="👍"
log_offset_before_u2react_a=$(wc -c <"${MMRELAY_LOG_PATH_A}")
log_offset_before_u2react_b=$(wc -c <"${MMRELAY_LOG_PATH_B}")

start_test "Test 6" "Test 6: Secondary user reaction to prior Matrix event → both meshes..."
MATRIX_USER2_REACTION_EVENT_ID="$(
	matrix_send_reaction \
		"${USER2_ACCESS_TOKEN}" \
		"${ROOM_ID}" \
		"${MATRIX_USER2_EVENT_ID}" \
		"${MATRIX_USER2_REACTION_KEY}" \
		"mmrelay-ci-u2-react"
)"
reaction_event_json="$(
	matrix_wait_event \
		"${USER_ACCESS_TOKEN}" \
		"${ROOM_ID}" \
		"${MATRIX_EVENT_TIMEOUT_SECONDS}" \
		"m.reaction" \
		"${USER2_USER_ID}" \
		"" \
		"" \
		"${MATRIX_USER2_EVENT_ID}" \
		"${MATRIX_USER2_REACTION_EVENT_ID}" \
		"" \
		"${SYNC_CURSOR_USER1}"
)"
SYNC_CURSOR_USER1="$(json_extract "${reaction_event_json}" "next_batch")"
if ! wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_A}" \
	"Relaying reaction from" \
	"${log_offset_before_u2react_a}" \
	60; then
	fail_test "Reaction did not relay to Mesh A"
fi
if ! wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"Relaying reaction from" \
	"${log_offset_before_u2react_b}" \
	60; then
	fail_test "Reaction did not relay to Mesh B"
fi
pass_test "Secondary user reaction relayed to both meshes"
write_observability_report

# =============================================================================
# Success
# =============================================================================

echo ""
echo "============================================================================"
echo "All test scenarios passed!"
echo "============================================================================"
echo ""
echo "Meshtasticd integration tests completed successfully."
echo "Artifacts written to: ${CI_ARTIFACT_DIR}"
