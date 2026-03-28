#!/usr/bin/env bash

set -euo pipefail

# =============================================================================
# Meshtasticd Integration Test for MMRelay
# =============================================================================
#
# This script tests MMRelay's ability to relay messages between two isolated mesh networks
# through Matrix using both a plaintext room and an encrypted room.
#
# Architecture:
#   - meshtasticd relay-A (port 4403) ← MMRelay A ←─┐
#                                                    ├──→ Shared plaintext Matrix room
#                                                    └──→ Shared encrypted Matrix room
#   - meshtasticd relay-B (port 4404) ← MMRelay B ←─┘
#
# Test Scenarios:
#   1. Matrix user message in plaintext room → Mesh A + Mesh B
#   2. Injected Mesh A-origin event in plaintext room → remote meshnet processing in MMRelay B
#   3. Injected Mesh B-origin event in plaintext room → remote meshnet processing in MMRelay A
#   4. E2EE Matrix user message in encrypted room → Mesh A + Mesh B
#   5. E2EE Matrix user reply in encrypted room → both meshes as structured replies
#   6. E2EE Matrix user reaction in encrypted room → both meshes
#   7. dm-rcv-basic plugin initialization (DM forwarding untested - infra limitation)
#   8. Stale name rows are pruned to match current node DB
#
# Environment Variables:
#   MESHTASTICD_IMAGE: Docker image for meshtasticd (default: meshtastic/meshtasticd:latest)
#   SYNAPSE_IMAGE: Docker image for Synapse (default: matrixdotorg/synapse:latest)
#   MMRELAY_LOG_ON_SUCCESS: Always show logs (default: false)
#   MESHNET_NAME_A / MESHNET_NAME_B: Meshnet labels used by each relay instance
#   MESH_CHANNEL_NAME_A / MESH_CHANNEL_NAME_B: Channel names for isolated meshnets
#   MESH_PRIMARY_PSK_A / MESH_PRIMARY_PSK_B: Primary channel keys for isolated meshnets
#   MATRIX_EVENT_TIMEOUT_SECONDS: Matrix event polling timeout per assertion
#   NODEDB_REFRESH_INTERVAL_SECONDS: Node-name refresh cadence in MMRelay config
#   NAME_PRUNE_WAIT_TIMEOUT_SECONDS: Timeout for stale-name prune assertions
#   MMRELAY_ALLOW_TAGGED_IMAGE_CACHE: Reuse local tag-based images instead of forcing pull
# =============================================================================

# Meshtasticd Configuration
MESHTASTICD_IMAGE="${MESHTASTICD_IMAGE:-meshtastic/meshtasticd:latest}"
MESHTASTICD_CONTAINER_A="${MESHTASTICD_CONTAINER_A:-mmrelay-ci-mesh-a}"
MESHTASTICD_CONTAINER_B="${MESHTASTICD_CONTAINER_B:-mmrelay-ci-mesh-b}"
MESHTASTICD_CONTAINER_A_PEER="${MESHTASTICD_CONTAINER_A_PEER:-mmrelay-ci-mesh-a-peer}"
MESHTASTICD_CONTAINER_B_PEER="${MESHTASTICD_CONTAINER_B_PEER:-mmrelay-ci-mesh-b-peer}"
MESHTASTICD_HOST_A="${MESHTASTICD_HOST_A:-localhost}"
MESHTASTICD_HOST_B="${MESHTASTICD_HOST_B:-localhost}"
MESHTASTICD_HOST_A_PEER="${MESHTASTICD_HOST_A_PEER:-localhost}"
MESHTASTICD_HOST_B_PEER="${MESHTASTICD_HOST_B_PEER:-localhost}"
MESHTASTICD_PORT_A="${MESHTASTICD_PORT_A:-4403}"
MESHTASTICD_PORT_B="${MESHTASTICD_PORT_B:-4404}"
MESHTASTICD_PORT_A_PEER="${MESHTASTICD_PORT_A_PEER:-4405}"
MESHTASTICD_PORT_B_PEER="${MESHTASTICD_PORT_B_PEER:-4406}"
MESHTASTICD_HWID_A="${MESHTASTICD_HWID_A:-11}"
MESHTASTICD_HWID_B="${MESHTASTICD_HWID_B:-22}"
MESHTASTICD_HWID_A_PEER="${MESHTASTICD_HWID_A_PEER:-33}"
MESHTASTICD_HWID_B_PEER="${MESHTASTICD_HWID_B_PEER:-44}"
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
NAME_PRUNE_WAIT_TIMEOUT_SECONDS="${NAME_PRUNE_WAIT_TIMEOUT_SECONDS:-75}"
NODEDB_REFRESH_INTERVAL_SECONDS="${NODEDB_REFRESH_INTERVAL_SECONDS:-5}"
PYTHON_BIN="${PYTHON_BIN:-python}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
	echo "Python runtime '${PYTHON_BIN}' is required." >&2
	exit 1
fi

# docker_pull_with_retry pulls a Docker image with exponential backoff retry
# to handle transient Docker Hub rate limiting errors.
# Usage: docker_pull_with_retry <image> [max_retries]
docker_pull_with_retry() {
	local image="${1}"
	local max_retries="${2:-3}"
	local retry=0
	local delay=5

	while [[ ${retry} -lt ${max_retries} ]]; do
		if docker pull "${image}"; then
			return 0
		fi
		retry=$((retry + 1))
		if [[ ${retry} -lt ${max_retries} ]]; then
			echo "Docker pull failed, retrying in ${delay}s (attempt ${retry}/${max_retries})..." >&2
			sleep "${delay}"
			delay=$((delay * 2))
		fi
	done
	echo "Failed to pull ${image} after ${max_retries} attempts" >&2
	return 1
}

# ensure_docker_image_available reuses a local image when present and only pulls
# when necessary.
# Usage: ensure_docker_image_available <image> [max_retries]
ensure_docker_image_available() {
	local image="${1}"
	local max_retries="${2:-3}"
	local allow_tagged_cache="${MMRELAY_ALLOW_TAGGED_IMAGE_CACHE:-false}"

	# Default behavior is to trust only immutable digest references.
	# CI cache-restore steps can opt into trusted tag reuse by setting
	# MMRELAY_ALLOW_TAGGED_IMAGE_CACHE=true for deterministic cache tags.
	if [[ ${image} == *@sha256:* || ${allow_tagged_cache} == "true" ]]; then
		if docker image inspect "${image}" >/dev/null 2>&1; then
			echo "Using cached Docker image: ${image}"
			return 0
		fi
	fi

	echo "Pulling Docker image: ${image}"
	docker_pull_with_retry "${image}" "${max_retries}"
}

# Names-table SQL identifiers loaded from app constants.
names_table_output=$(
	"${PYTHON_BIN}" - <<'PY'
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path.cwd() / "src"))
from mmrelay.constants.database import (
    NAMES_FIELD_LONGNAME,
    NAMES_FIELD_SHORTNAME,
    NAMES_TABLE_LONGNAMES,
    NAMES_TABLE_SHORTNAMES,
)

print(NAMES_TABLE_LONGNAMES)
print(NAMES_TABLE_SHORTNAMES)
print(NAMES_FIELD_LONGNAME)
print(NAMES_FIELD_SHORTNAME)
PY
) || {
	echo "Failed to load names-table constants via '${PYTHON_BIN}'." >&2
	exit 1
}
mapfile -t names_table_constants <<<"${names_table_output}"
if [[ ${#names_table_constants[@]} -ne 4 ]]; then
	echo "Failed to parse names-table constants from '${PYTHON_BIN}' output." >&2
	exit 1
fi

NAMES_TABLE_LONGNAMES="${names_table_constants[0]}"
NAMES_TABLE_SHORTNAMES="${names_table_constants[1]}"
NAMES_FIELD_LONGNAME="${names_table_constants[2]}"
NAMES_FIELD_SHORTNAME="${names_table_constants[3]}"

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
ROOM_ID_DM_A=""
ROOM_ID_DM_B=""
RELAY_NODE_ID_A=""
RELAY_NODE_ID_B=""
MESHTASTICD_ENDPOINT_A_PEER=""
MESHTASTICD_ENDPOINT_B_PEER=""

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
# require_regex validates a value against a regular expression PATTERN and exits with an error message containing NAME if the value does not match.

require_regex() {
	local value=$1
	local pattern=$2
	local name=$3
	if [[ ! ${value} =~ ${pattern} ]]; then
		echo "Invalid ${name}: ${value}" >&2
		exit 1
	fi
}

# run_with_status executes a command with errexit temporarily disabled and returns its exit status.
run_with_status() {
	set +e
	"$@"
	local status=$?
	set -e
	return "${status}"
}

# run_capture_with_status executes a command with errexit temporarily disabled, captures stdout into the named variable, and returns command status.
run_capture_with_status() {
	local output_var=$1
	shift
	local output
	set +e
	output="$("$@")"
	local status=$?
	set -e
	printf -v "${output_var}" "%s" "${output}"
	return "${status}"
}

# run_or_fail executes a command and fails the current test with failure_note if the command exits non-zero.
run_or_fail() {
	local failure_note=$1
	shift
	local status=0
	if run_with_status "$@"; then
		status=0
	else
		status=$?
	fi
	if ((status != 0)); then
		fail_test "${failure_note}"
	fi
}

# run_capture_or_fail captures command output into output_var and fails the current test with failure_note if the command exits non-zero.
run_capture_or_fail() {
	local output_var=$1
	local failure_note=$2
	shift 2
	local status=0
	if run_capture_with_status "${output_var}" "$@"; then
		status=0
	else
		status=$?
	fi
	if ((status != 0)); then
		fail_test "${failure_note}"
	fi
}

# print_logs_if_needed prints collected component logs to stdout when the test suite failed or when MMRELAY_LOG_ON_SUCCESS enables log-on-success; it avoids printing logs more than once.
# It takes a single argument: the numeric exit code used to determine whether logs should be printed.
# exit_code: numeric exit status of the test suite; logs are printed if this is non-zero or if MMRELAY_LOG_ON_SUCCESS is set to 1/true/yes/on.
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

# stop_process stops a process given its PID: sends SIGTERM, waits up to 10 seconds for it to exit, then sends SIGKILL if still running and waits to reap it. Parameters: $1 — PID of the process; $2 — human-readable name used in log messages.
stop_process() {
	local pid=$1
	local name=$2
	local shutdown_timeout=10

	if [[ -n ${pid} ]] && kill -0 "${pid}" >/dev/null 2>&1; then
		echo "Stopping ${name} (PID ${pid})..."
		kill -TERM "${pid}" 2>/dev/null || true
		for _ in $(seq 1 "${shutdown_timeout}"); do
			kill -0 "${pid}" 2>/dev/null || break
			sleep 1
		done
		kill -0 "${pid}" 2>/dev/null && kill -KILL "${pid}" 2>/dev/null || true
		wait "${pid}" 2>/dev/null || true
	fi
}

# count_pattern_in_file counts occurrences of a literal string pattern in a file and echoes the count; echoes 0 if the file does not exist.
count_pattern_in_file() {
	local file_path=$1
	local pattern=$2
	if [[ ! -f ${file_path} ]]; then
		echo 0
		return
	fi
	grep -F -c "${pattern}" "${file_path}" || true
}

# count_pattern_in_file_since counts occurrences of a literal pattern in a file starting at the given byte offset and echoes the count.
count_pattern_in_file_since() {
	local file_path=$1
	local pattern=$2
	local start_byte=$3
	if [[ ! -f ${file_path} ]]; then
		echo 0
		return
	fi
	local file_size
	file_size=$(wc -c <"${file_path}")
	if ((file_size <= start_byte)); then
		echo 0
		return
	fi
	tail -c +$((start_byte + 1)) "${file_path}" | grep -F -c "${pattern}" || true
}

# count_message_map_rows counts rows in the `message_map` table of the given SQLite database and echoes the integer count to stdout; echoes 0 if the file does not exist or an error occurs.
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

# record_test_result records a test's outcome, computes its duration since CURRENT_TEST_START_MS, and appends the test name, status, duration (milliseconds), and optional note to the global result arrays.
# Arguments:
#   status - status string for the test (e.g., "PASSED", "FAILED").
#   note   - optional brief note or context to record with the result.
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

# start_test records the start of a test by setting CURRENT_TEST_NAME and CURRENT_TEST_START_MS (epoch milliseconds) and echoes a blank line followed by the provided human-readable test label.
start_test() {
	local test_name=$1
	local test_label=$2
	CURRENT_TEST_NAME="${test_name}"
	CURRENT_TEST_START_MS=$(date +%s%3N)
	echo ""
	echo "${test_label}"
}

# pass_test records a test as passed with an optional note and prints a success message.
pass_test() {
	local note=$1
	record_test_result "PASSED" "${note}"
	echo "✓ ${CURRENT_TEST_NAME} PASSED: ${note}"
}

# write_observability_report writes an observability Markdown summary of the test suite and prints a concise report to stdout.
# It gathers test results and runtime metrics (relay flow counts, message_map rows, meshtasticd connection events), captures live meshtasticd logs, writes the summary to "${SHARED_DIR}/observability-summary.md", and appends that file to GITHUB_STEP_SUMMARY when available.
# The function is idempotent and will do nothing if the observability summary has already been written.
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
			echo "| ${TEST_RESULT_NAMES[${idx}]} | ${TEST_RESULT_STATUS[${idx}]} | ${TEST_RESULT_DURATION_MS[${idx}]} | ${TEST_RESULT_NOTES[${idx}]} |"
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

# fail_test records a failed test with a note, writes the observability report, and exits the script with status 1.
fail_test() {
	local note=$1
	record_test_result "FAILED" "${note}"
	echo "✗ ${CURRENT_TEST_NAME} FAILED: ${note}" >&2
	write_observability_report
	exit 1
}

# cleanup performs final test-suite teardown by writing the observability report (if started), stopping MMRelay processes, capturing and removing Meshtasticd and Synapse container logs, conditionally printing logs, and exiting with the original exit code.
cleanup() {
	local exit_code=$?

	if ((SUITE_START_MS > 0)); then
		run_with_status write_observability_report
	fi

	stop_process "${MMRELAY_PID_A}" "MMRelay A"
	stop_process "${MMRELAY_PID_B}" "MMRelay B"

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

	if docker ps -a --format '{{.Names}}' | grep -Fxq "${MESHTASTICD_CONTAINER_A_PEER}"; then
		docker rm -f "${MESHTASTICD_CONTAINER_A_PEER}" >/dev/null 2>&1 || true
	fi

	if docker ps -a --format '{{.Names}}' | grep -Fxq "${MESHTASTICD_CONTAINER_B_PEER}"; then
		docker rm -f "${MESHTASTICD_CONTAINER_B_PEER}" >/dev/null 2>&1 || true
	fi

	if docker ps -a --format '{{.Names}}' | grep -Fxq "${SYNAPSE_CONTAINER}"; then
		echo "Capturing Synapse logs..."
		docker logs "${SYNAPSE_CONTAINER}" >"${SYNAPSE_LOG_PATH}" 2>&1 || true
		docker rm -f "${SYNAPSE_CONTAINER}" >/dev/null 2>&1 || true
	fi

	print_logs_if_needed "${exit_code}"
	exit "${exit_code}"
}

# wait_for_meshtasticd_ready waits until the Meshtastic daemon at the given endpoint responds to the Meshtastic CLI; parameters: `endpoint` (host[:port]) and `container` (Docker container name); returns non-zero if the container exits before becoming ready or if readiness does not occur within MESHTASTICD_READY_TIMEOUT_SECONDS.
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

# configure_mesh_channel sets the Meshtastic channel name and pre-shared key (PSK) on the specified endpoint.
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

# get_local_node_id reads the connected node ID for a Meshtastic endpoint and
# returns it in !xxxxxxxx format suitable for --dest.
get_local_node_id() {
	local endpoint=$1
	local info_output
	info_output="$("${PYTHON_BIN}" -m meshtastic --timeout 15 --host "${endpoint}" --info 2>/dev/null || true)"
	local node_id
	node_id="$(printf '%s\n' "${info_output}" | grep -Eo '![0-9a-fA-F]{8}' | head -n1 || true)"
	if [[ -z ${node_id} ]]; then
		echo "Unable to determine Meshtastic node ID for endpoint ${endpoint}" >&2
		return 1
	fi
	printf '%s\n' "${node_id,,}"
}

# send_direct_mesh_message sends a direct text message to a destination node.
# Returns 0 on success, non-zero on failure. Output is captured for debugging.
send_direct_mesh_message() {
	local endpoint=$1
	local destination_id=$2
	local text=$3
	local stderr_file
	stderr_file=$(mktemp)
	"${PYTHON_BIN}" -m meshtastic \
		--timeout 20 \
		--host "${endpoint}" \
		--dest "${destination_id}" \
		--sendtext "${text}" \
		--wait-to-disconnect 2 >/dev/null 2>"${stderr_file}"
	local exit_code=$?
	if ((exit_code != 0)); then
		echo "send_direct_mesh_message failed: endpoint=${endpoint} dest=${destination_id} exit=${exit_code}" >&2
		sed 's/^/  /' "${stderr_file}" >&2
	fi
	rm -f "${stderr_file}"
	return "${exit_code}"
}

# wait_for_node_in_nodedb waits until a node with the given ID appears in the
# node DB of the specified meshtasticd endpoint. Returns 0 on success, 1 on timeout.
wait_for_node_in_nodedb() {
	local endpoint=$1
	local node_id=$2
	local timeout_seconds=${3:-30}
	local deadline=$((SECONDS + timeout_seconds))

	while ((SECONDS < deadline)); do
		local nodes_output
		nodes_output=$("${PYTHON_BIN}" -m meshtastic --timeout 10 --host "${endpoint}" --nodes 2>/dev/null || true)
		if printf '%s\n' "${nodes_output}" | grep -Fq "${node_id}"; then
			return 0
		fi
		sleep 2
	done
	echo "Timed out waiting for node ${node_id} in nodedb of ${endpoint}" >&2
	return 1
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

# wait_for_log_pattern_since waits until `pattern` appears in `log_file` after `start_byte` or until `timeout_seconds` elapse.
# It checks the file size relative to `start_byte` and searches only the new content; returns 0 if the pattern is found.
# Returns 1 and prints an error if the timeout is reached or if either MMRelay process (MMRELAY_PID_A or MMRELAY_PID_B) exits while waiting.
# Arguments:
#   $1 - path to the log file to monitor
#   $2 - literal pattern to search for (grep -F style)
#   $3 - start byte offset (search begins at byte offset +1)
#   $4 - timeout in seconds to wait before failing
wait_for_log_pattern_since() {
	local log_file=$1
	local pattern=$2
	local start_byte=$3
	local timeout_seconds=$4
	local deadline=$((SECONDS + timeout_seconds))

	while ((SECONDS < deadline)); do
		local log_size=0
		if [[ -f ${log_file} ]]; then
			log_size=$(wc -c <"${log_file}")
		fi
		if ((log_size > start_byte)); then
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

# load_json_value reads MATRIX_RUNTIME_JSON and echoes the value of the given top-level JSON key.
# key is the top-level key to extract; the function prints the value to stdout and exits with status 1 if the key is not present.
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

# json_extract extracts the JSON value specified by a dot-separated key path from the given JSON payload and echoes it; exits with status 1 if the path is missing or the value is null.
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

# write_matrix_credentials_json writes a Matrix credential JSON file to the given path containing the homeserver, user_id, access_token, and device_id.
write_matrix_credentials_json() {
	local output_path=$1
	local homeserver=$2
	local user_id=$3
	local access_token=$4
	local device_id=$5
	"${PYTHON_BIN}" - "${output_path}" "${homeserver}" "${user_id}" "${access_token}" "${device_id}" <<'PY'
import json
import pathlib
import sys

output_path = pathlib.Path(sys.argv[1])
homeserver = sys.argv[2]
user_id = sys.argv[3]
access_token = sys.argv[4]
device_id = sys.argv[5]

output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(
    json.dumps(
        {
            "homeserver": homeserver,
            "user_id": user_id,
            "access_token": access_token,
            "device_id": device_id,
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY
}

# write_e2ee_auth_state_json writes a JSON auth state file at the given path containing `access_token`, `user_id`, and `device_id`, creating parent directories as needed.
write_e2ee_auth_state_json() {
	local output_path=$1
	local access_token=$2
	local user_id=$3
	local device_id=$4
	"${PYTHON_BIN}" - "${output_path}" "${access_token}" "${user_id}" "${device_id}" <<'PY'
import json
import pathlib
import sys

output_path = pathlib.Path(sys.argv[1])
access_token = sys.argv[2]
user_id = sys.argv[3]
device_id = sys.argv[4]

output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(
    json.dumps(
        {
            "access_token": access_token,
            "user_id": user_id,
            "device_id": device_id,
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY
}

# matrix_send_message sends a message to a Matrix room (optionally as a reply) and echoes the resulting event ID.
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

# matrix_send_mesh_origin_message sends an event to a Matrix room that represents a Meshtastic-origin message and prints the created event_id.
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

# matrix_send_e2ee_message sends an end-to-end encrypted message into a Matrix room (optionally as a reply), restoring or saving local encryption auth state and printing the created event ID to stdout.
matrix_send_e2ee_message() {
	local user_id=$1
	local password=$2
	local room_id=$3
	local message_text=$4
	local store_path=$5
	local auth_state_path=$6
	local reply_to_event_id="${7-}"
	"${PYTHON_BIN}" - "${MATRIX_BASE_URL}" "${user_id}" "${password}" "${room_id}" "${message_text}" "${store_path}" "${auth_state_path}" "${reply_to_event_id}" <<'PY'
import asyncio
import json
import pathlib
import sys
import urllib.parse

from nio import AsyncClient, AsyncClientConfig, LoginError

(
    base_url,
    user_id,
    password,
    room_id,
    message_text,
    store_path_raw,
    auth_state_path_raw,
    reply_to_event_id,
) = sys.argv[1:9]

parsed = urllib.parse.urlparse(base_url)
homeserver = f"{parsed.scheme}://{parsed.netloc}"
store_path = pathlib.Path(store_path_raw)
auth_state_path = pathlib.Path(auth_state_path_raw)
store_path.mkdir(parents=True, exist_ok=True)
auth_state_path.parent.mkdir(parents=True, exist_ok=True)


async def _run() -> str:
    client = AsyncClient(
        homeserver=homeserver,
        user=user_id,
        store_path=str(store_path),
        config=AsyncClientConfig(encryption_enabled=True, store_sync_tokens=True),
    )

    try:
        restored = False
        if auth_state_path.is_file():
            try:
                saved = json.loads(auth_state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                saved = {}
            access_token = saved.get("access_token")
            saved_user = saved.get("user_id")
            device_id = saved.get("device_id")
            if (
                isinstance(access_token, str)
                and access_token
                and isinstance(saved_user, str)
                and saved_user == user_id
                and isinstance(device_id, str)
                and device_id
            ):
                client.restore_login(
                    user_id=saved_user,
                    device_id=device_id,
                    access_token=access_token,
                )
                restored = True

        if not restored:
            login_response = await client.login(
                password=password,
                device_name="mmrelay-ci-e2ee-user2",
            )
            if isinstance(login_response, LoginError):
                raise RuntimeError(
                    f"E2EE login failed ({login_response.status_code}): {login_response.message}"
                )
            auth_state_path.write_text(
                json.dumps(
                    {
                        "access_token": login_response.access_token,
                        "user_id": login_response.user_id,
                        "device_id": login_response.device_id,
                    }
                ),
                encoding="utf-8",
            )

        if client.should_upload_keys:
            await client.keys_upload()

        # Load room state and device keys before sending encrypted events.
        await client.sync(timeout=3000, full_state=True)

        content = {"msgtype": "m.text", "body": message_text}
        if reply_to_event_id:
            content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to_event_id}}

        response = await client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content=content,
            ignore_unverified_devices=True,
        )
        event_id = getattr(response, "event_id", None)
        if not isinstance(event_id, str) or not event_id:
            raise RuntimeError("Encrypted Matrix message send response missing event_id")
        return event_id
    finally:
        await client.close()


result = asyncio.run(_run())
print(result)
PY
}

# matrix_send_e2ee_reaction sends an end-to-end encrypted reaction into a Matrix room and echoes the created Matrix event ID to stdout.
# Arguments: user_id, password, room_id, target_event_id, reaction_key, store_path, auth_state_path — where store_path is the client store directory for encryption state and auth_state_path is the JSON file path used to persist/restore access token and device info.
matrix_send_e2ee_reaction() {
	local user_id=$1
	local password=$2
	local room_id=$3
	local target_event_id=$4
	local reaction_key=$5
	local store_path=$6
	local auth_state_path=$7
	"${PYTHON_BIN}" - "${MATRIX_BASE_URL}" "${user_id}" "${password}" "${room_id}" "${target_event_id}" "${reaction_key}" "${store_path}" "${auth_state_path}" <<'PY'
import asyncio
import json
import pathlib
import sys
import urllib.parse

from nio import AsyncClient, AsyncClientConfig, LoginError

(
    base_url,
    user_id,
    password,
    room_id,
    target_event_id,
    reaction_key,
    store_path_raw,
    auth_state_path_raw,
) = sys.argv[1:9]

parsed = urllib.parse.urlparse(base_url)
homeserver = f"{parsed.scheme}://{parsed.netloc}"
store_path = pathlib.Path(store_path_raw)
auth_state_path = pathlib.Path(auth_state_path_raw)
store_path.mkdir(parents=True, exist_ok=True)
auth_state_path.parent.mkdir(parents=True, exist_ok=True)


async def _run() -> str:
    client = AsyncClient(
        homeserver=homeserver,
        user=user_id,
        store_path=str(store_path),
        config=AsyncClientConfig(encryption_enabled=True, store_sync_tokens=True),
    )
    try:
        restored = False
        if auth_state_path.is_file():
            try:
                saved = json.loads(auth_state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                saved = {}
            access_token = saved.get("access_token")
            saved_user = saved.get("user_id")
            device_id = saved.get("device_id")
            if (
                isinstance(access_token, str)
                and access_token
                and isinstance(saved_user, str)
                and saved_user == user_id
                and isinstance(device_id, str)
                and device_id
            ):
                client.restore_login(
                    user_id=saved_user,
                    device_id=device_id,
                    access_token=access_token,
                )
                restored = True

        if not restored:
            login_response = await client.login(
                password=password,
                device_name="mmrelay-ci-e2ee-user2",
            )
            if isinstance(login_response, LoginError):
                raise RuntimeError(
                    f"E2EE login failed ({login_response.status_code}): {login_response.message}"
                )
            auth_state_path.write_text(
                json.dumps(
                    {
                        "access_token": login_response.access_token,
                        "user_id": login_response.user_id,
                        "device_id": login_response.device_id,
                    }
                ),
                encoding="utf-8",
            )

        if client.should_upload_keys:
            await client.keys_upload()

        await client.sync(timeout=3000, full_state=True)
        response = await client.room_send(
            room_id=room_id,
            message_type="m.reaction",
            content={
                "m.relates_to": {
                    "event_id": target_event_id,
                    "rel_type": "m.annotation",
                    "key": reaction_key,
                }
            },
            ignore_unverified_devices=True,
        )
        event_id = getattr(response, "event_id", None)
        if not isinstance(event_id, str) or not event_id:
            raise RuntimeError("Encrypted Matrix reaction send response missing event_id")
        return event_id
    finally:
        await client.close()


result = asyncio.run(_run())
print(result)
PY
}

# matrix_wait_event waits for a Matrix event in a room that matches optional filters (type, sender, content, msgtype, relation, event IDs, meshtastic reply id) within a timeout and echoes the matched event JSON plus the next_batch sync token on success; exits nonzero on timeout or sync error.
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

# matrix_wait_event_by_id waits for a specific Matrix event by ID in a room and echoes the event JSON to stdout when found; optionally validates the event sender and allowed event types and exits non-zero on timeout or on validation/fetch errors.
matrix_wait_event_by_id() {
	local access_token=$1
	local room_id=$2
	local event_id=$3
	local timeout_seconds=$4
	local expected_sender="${5-}"
	local allowed_types_csv="${6-}"

	"${PYTHON_BIN}" - "${MATRIX_BASE_URL}" "${access_token}" "${room_id}" "${event_id}" "${timeout_seconds}" "${expected_sender}" "${allowed_types_csv}" <<'PY'
import json
import sys
import time
import urllib.parse

import requests

(
    base_url,
    access_token,
    room_id,
    event_id,
    timeout_seconds_raw,
    expected_sender,
    allowed_types_csv,
) = sys.argv[1:8]

timeout_seconds = int(timeout_seconds_raw)
allowed_types = {t for t in allowed_types_csv.split(",") if t}
quoted_room_id = urllib.parse.quote(room_id, safe="")
quoted_event_id = urllib.parse.quote(event_id, safe="")
url = f"{base_url}/_matrix/client/v3/rooms/{quoted_room_id}/event/{quoted_event_id}"
headers = {"Authorization": f"Bearer {access_token}"}
deadline = time.monotonic() + timeout_seconds
last_status = None
last_body = ""

while time.monotonic() < deadline:
    try:
        response = requests.get(url, headers=headers, timeout=20)
    except requests.RequestException:
        time.sleep(1)
        continue

    last_status = response.status_code
    last_body = response.text[:300]

    if response.status_code == 200:
        event = response.json()
        event_sender = event.get("sender")
        event_type = event.get("type")

        if expected_sender and event_sender != expected_sender:
            raise RuntimeError(
                f"Event sender mismatch: expected '{expected_sender}', got '{event_sender}'"
            )
        if allowed_types and event_type not in allowed_types:
            raise RuntimeError(
                f"Event type mismatch: expected one of {sorted(allowed_types)}, got '{event_type}'"
            )

        print(json.dumps(event))
        raise SystemExit(0)

    if response.status_code in (403, 404):
        time.sleep(1)
        continue

    raise RuntimeError(
        f"Failed to fetch Matrix event by ID ({response.status_code}): {response.text[:300]}"
    )

if last_status is None:
    print(
        f"Timed out waiting for Matrix event {event_id}: request did not succeed",
        file=sys.stderr,
    )
else:
    print(
        f"Timed out waiting for Matrix event {event_id}: last status={last_status}, body={last_body}",
        file=sys.stderr,
    )
raise SystemExit(1)
PY
}

# wait_for_message_map_meshtastic_id waits for a Meshtastic ID mapped to a given Matrix event ID to appear in the message_map SQLite table, prints the Meshtastic ID to stdout on success, and exits non‑zero if the row is not found within the provided timeout.
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

# upsert_name_entry inserts or updates one row in longnames/shortnames for test setup.
upsert_name_entry() {
	local db_path=$1
	local table_name=$2
	local column_name=$3
	local meshtastic_id=$4
	local name_value=$5
	"${PYTHON_BIN}" - \
		"${db_path}" \
		"${table_name}" \
		"${column_name}" \
		"${meshtastic_id}" \
		"${name_value}" \
		"${NAMES_TABLE_LONGNAMES}" \
		"${NAMES_FIELD_LONGNAME}" \
		"${NAMES_TABLE_SHORTNAMES}" \
		"${NAMES_FIELD_SHORTNAME}" <<'PY'
import sqlite3
import sys

(
    db_path,
    table_name,
    column_name,
    meshtastic_id,
    name_value,
    longnames_table,
    longname_column,
    shortnames_table,
    shortname_column,
) = sys.argv[1:10]
allowed_columns = {
    longnames_table: longname_column,
    shortnames_table: shortname_column,
}
expected_column = allowed_columns.get(table_name)
if expected_column is None or expected_column != column_name:
    raise SystemExit(f"Invalid table/column pair: {table_name}.{column_name}")

with sqlite3.connect(db_path, timeout=5) as conn:
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute(
        f"INSERT INTO {table_name} (meshtastic_id, {column_name}) VALUES (?, ?) "
        f"ON CONFLICT(meshtastic_id) DO UPDATE SET {column_name}=excluded.{column_name}",
        (meshtastic_id, name_value),
    )
PY
}

# get_existing_name_entry returns one current meshtastic_id for a specific names table.
get_existing_name_entry() {
	local db_path=$1
	local table_name=$2
	"${PYTHON_BIN}" - \
		"${db_path}" \
		"${table_name}" \
		"${NAMES_TABLE_LONGNAMES}" \
		"${NAMES_TABLE_SHORTNAMES}" <<'PY'
import pathlib
import sqlite3
import sys

db_path, table_name, longnames_table, shortnames_table = sys.argv[1:5]

allowed_tables = {longnames_table, shortnames_table}
if table_name not in allowed_tables:
    print(f"Invalid table name: {table_name}", file=sys.stderr)
    raise SystemExit(1)
db_uri = pathlib.Path(db_path).resolve().as_uri() + "?mode=ro"
try:
    with sqlite3.connect(db_uri, uri=True, timeout=5) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        row = conn.execute(
            f"SELECT meshtastic_id FROM {table_name} "
            "WHERE meshtastic_id IS NOT NULL AND meshtastic_id != '' "
            "ORDER BY meshtastic_id LIMIT 1"
        ).fetchone()
except sqlite3.Error as exc:
    error_text = str(exc).lower()
    if (
        "no such table" in error_text
        or "database is locked" in error_text
        or "database schema is locked" in error_text
    ):
        print(f"Transient SQLite state for {table_name}, will retry: {exc}", file=sys.stderr)
        raise SystemExit(2)
    print(f"SQLite error querying {table_name}: {exc}", file=sys.stderr)
    raise SystemExit(1)

if row and row[0]:
    print(row[0])
    raise SystemExit(0)

raise SystemExit(2)
PY
}

# poll_for_existing_name_entry polls until a name entry exists, using global timeout settings.
# Sets the result variable and returns 0 on success, calls fail_test on timeout.
_fail_if_relay_not_running_during_poll() {
	local relay_pid=${1-}
	local relay_name=${2:-MMRelay}
	local relay_log_path=${3-}
	local detail=${4:-poll}
	if [[ -z ${relay_pid} ]]; then
		return 0
	fi
	if kill -0 "${relay_pid}" >/dev/null 2>&1; then
		return 0
	fi

	if [[ -n ${relay_log_path} ]] && [[ -f ${relay_log_path} ]]; then
		{
			echo "Last 20 lines from ${relay_name} log (${relay_log_path}):"
			tail -n 20 "${relay_log_path}" || true
		} >&2
	fi
	fail_test "${relay_name} process (pid=${relay_pid}) exited while waiting during ${detail}"
}

poll_for_existing_name_entry() {
	local result_var_name=$1
	local db_path=$2
	local table_name=$3
	local instance_label=$4
	local relay_pid=${5-}
	local relay_name=${6:-MMRelay}
	local relay_log_path=${7-}

	local poll_start poll_now poll_elapsed captured_value capture_status
	poll_start=$(date +%s)
	while true; do
		_fail_if_relay_not_running_during_poll \
			"${relay_pid}" \
			"${relay_name}" \
			"${relay_log_path}" \
			"precondition poll for ${table_name} in ${instance_label}"
		run_with_status run_capture_with_status \
			captured_value \
			get_existing_name_entry \
			"${db_path}" \
			"${table_name}"
		capture_status=$?
		if ((capture_status == 2)); then
			captured_value=""
		elif ((capture_status != 0)); then
			fail_test \
				"Failed reading ${table_name} in ${instance_label} while waiting for first sync"
		fi

		if [[ -n ${captured_value} ]]; then
			printf -v "${result_var_name}" "%s" "${captured_value}"
			return 0
		fi

		poll_now=$(date +%s)
		poll_elapsed=$((poll_now - poll_start))
		if [[ ${poll_elapsed} -ge ${POLL_TIMEOUT_SECONDS} ]]; then
			fail_test "Timed out waiting for existing ${table_name} row in ${instance_label} (${poll_elapsed}s)"
		fi

		sleep "${POLL_INTERVAL_SECONDS}"
	done
}

# wait_for_name_entry_state waits until a names-table row reaches expected state for an ID.
wait_for_name_entry_state() {
	local db_path=$1
	local table_name=$2
	local meshtastic_id=$3
	local timeout_seconds=$4
	local relay_pid=${5-}
	local relay_name=${6:-MMRelay}
	local relay_log_path=${7-}
	local expect_present=$8
	"${PYTHON_BIN}" - \
		"${db_path}" \
		"${table_name}" \
		"${meshtastic_id}" \
		"${timeout_seconds}" \
		"${relay_pid}" \
		"${relay_name}" \
		"${relay_log_path}" \
		"${NAMES_TABLE_LONGNAMES}" \
		"${NAMES_TABLE_SHORTNAMES}" \
		"${expect_present}" <<'PY'
import os
import pathlib
import sqlite3
import sys
import time
from collections import deque

(
    db_path,
    table_name,
    meshtastic_id,
    timeout_seconds_raw,
    relay_pid_raw,
    relay_name,
    relay_log_path,
    longnames_table,
    shortnames_table,
    expect_present_raw,
) = sys.argv[1:11]
timeout_seconds = int(timeout_seconds_raw)
allowed_tables = {longnames_table, shortnames_table}
if table_name not in allowed_tables:
    raise SystemExit(f"Invalid table name: {table_name}")
expect_present = expect_present_raw == "1"
expected_state = "presence" if expect_present else "absence"
db_uri = pathlib.Path(db_path).resolve().as_uri() + "?mode=ro"

relay_pid = int(relay_pid_raw) if relay_pid_raw else None
deadline = time.monotonic() + timeout_seconds
last_error = None
attempts = 0
while time.monotonic() < deadline:
    attempts += 1
    if relay_pid is not None:
        try:
            os.kill(relay_pid, 0)
        except OSError:
            print(
                f"{relay_name} process (pid={relay_pid}) exited while waiting for names row {expected_state}",
                file=sys.stderr,
            )
            if relay_log_path and os.path.exists(relay_log_path):
                print(
                    f"Last 20 lines from {relay_name} log ({relay_log_path}):",
                    file=sys.stderr,
                )
                with open(relay_log_path, encoding="utf-8", errors="replace") as handle:
                    for line in deque(handle, 20):
                        print(line.rstrip("\n"), file=sys.stderr)
            raise SystemExit(1)

    try:
        with sqlite3.connect(db_uri, uri=True, timeout=5) as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            row = conn.execute(
                f"SELECT 1 FROM {table_name} WHERE meshtastic_id=? LIMIT 1",
                (meshtastic_id,),
            ).fetchone()
            is_present = row is not None
            if is_present == expect_present:
                raise SystemExit(0)
    except sqlite3.Error as exc:  # pragma: no cover - retry loop
        last_error = str(exc)
    time.sleep(1)

if last_error:
    print(f"Last SQLite error: {last_error}", file=sys.stderr)
try:
    with sqlite3.connect(db_uri, uri=True, timeout=5) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        target_row = conn.execute(
            f"SELECT * FROM {table_name} WHERE meshtastic_id=? LIMIT 1",
            (meshtastic_id,),
        ).fetchone()
        row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
except sqlite3.Error as exc:
    target_row = None
    row_count = None
    print(f"Failed to read debug rows from {table_name}: {exc}", file=sys.stderr)
print(
    f"Timed out waiting for names row {expected_state} in {table_name} "
    f"for meshtastic_id={meshtastic_id} after {attempts} checks",
    file=sys.stderr,
)
if row_count is not None:
    print(f"{table_name} row count at timeout: {row_count[0]}", file=sys.stderr)
if target_row is not None:
    print(f"Target row at timeout: {target_row}", file=sys.stderr)
raise SystemExit(1)
PY
}

# wait_for_name_entry_absent waits until a names-table row is absent for an ID.
wait_for_name_entry_absent() {
	local db_path=$1
	local table_name=$2
	local meshtastic_id=$3
	local timeout_seconds=$4
	local relay_pid=${5-}
	local relay_name=${6:-MMRelay}
	local relay_log_path=${7-}
	wait_for_name_entry_state \
		"${db_path}" \
		"${table_name}" \
		"${meshtastic_id}" \
		"${timeout_seconds}" \
		"${relay_pid}" \
		"${relay_name}" \
		"${relay_log_path}" \
		0
}

# wait_for_name_entry_present waits until a names-table row is present for an ID.
wait_for_name_entry_present() {
	local db_path=$1
	local table_name=$2
	local meshtastic_id=$3
	local timeout_seconds=$4
	local relay_pid=${5-}
	local relay_name=${6:-MMRelay}
	local relay_log_path=${7-}
	wait_for_name_entry_state \
		"${db_path}" \
		"${table_name}" \
		"${meshtastic_id}" \
		"${timeout_seconds}" \
		"${relay_pid}" \
		"${relay_name}" \
		"${relay_log_path}" \
		1
}

# generate_unique_test_id returns a reproducibly prefixed random ID for test rows.
generate_unique_test_id() {
	local prefix=$1
	"${PYTHON_BIN}" - "${prefix}" <<'PY'
import secrets
import sys

prefix = sys.argv[1]
print(f"!{prefix}_{secrets.token_hex(12)}")
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

kernel_name=$(uname -s)
if [[ ${kernel_name} != "Linux" ]]; then
	echo "meshtasticd integration currently requires Linux (Docker host networking)." >&2
	exit 1
fi

# Validate all configuration parameters
require_regex "${MESHTASTICD_CONTAINER_A}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "MESHTASTICD_CONTAINER_A"
require_regex "${MESHTASTICD_CONTAINER_B}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "MESHTASTICD_CONTAINER_B"
require_regex "${MESHTASTICD_CONTAINER_A_PEER}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "MESHTASTICD_CONTAINER_A_PEER"
require_regex "${MESHTASTICD_CONTAINER_B_PEER}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "MESHTASTICD_CONTAINER_B_PEER"
require_regex "${MESHTASTICD_IMAGE}" '^[^[:space:]]+$' "MESHTASTICD_IMAGE"
require_regex "${MESHTASTICD_HOST_A}" '^[A-Za-z0-9._-]+$' "MESHTASTICD_HOST_A"
require_regex "${MESHTASTICD_HOST_B}" '^[A-Za-z0-9._-]+$' "MESHTASTICD_HOST_B"
require_regex "${MESHTASTICD_HOST_A_PEER}" '^[A-Za-z0-9._-]+$' "MESHTASTICD_HOST_A_PEER"
require_regex "${MESHTASTICD_HOST_B_PEER}" '^[A-Za-z0-9._-]+$' "MESHTASTICD_HOST_B_PEER"
require_regex "${MESHTASTICD_PORT_A}" '^[0-9]+$' "MESHTASTICD_PORT_A"
require_regex "${MESHTASTICD_PORT_B}" '^[0-9]+$' "MESHTASTICD_PORT_B"
require_regex "${MESHTASTICD_PORT_A_PEER}" '^[0-9]+$' "MESHTASTICD_PORT_A_PEER"
require_regex "${MESHTASTICD_PORT_B_PEER}" '^[0-9]+$' "MESHTASTICD_PORT_B_PEER"
require_regex "${MESHTASTICD_HWID_A}" '^[0-9]+$' "MESHTASTICD_HWID_A"
require_regex "${MESHTASTICD_HWID_B}" '^[0-9]+$' "MESHTASTICD_HWID_B"
require_regex "${MESHTASTICD_HWID_A_PEER}" '^[0-9]+$' "MESHTASTICD_HWID_A_PEER"
require_regex "${MESHTASTICD_HWID_B_PEER}" '^[0-9]+$' "MESHTASTICD_HWID_B_PEER"
require_regex "${SYNAPSE_CONTAINER}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "SYNAPSE_CONTAINER"
require_regex "${SYNAPSE_IMAGE}" '^[^[:space:]]+$' "SYNAPSE_IMAGE"
require_regex "${SYNAPSE_PORT}" '^[0-9]+$' "SYNAPSE_PORT"
require_regex "${MMRELAY_READY_TIMEOUT_SECONDS}" '^[0-9]+$' "MMRELAY_READY_TIMEOUT_SECONDS"
require_regex "${MATRIX_EVENT_TIMEOUT_SECONDS}" '^[0-9]+$' "MATRIX_EVENT_TIMEOUT_SECONDS"
require_regex "${MESSAGE_MAP_WAIT_TIMEOUT_SECONDS}" '^[0-9]+$' "MESSAGE_MAP_WAIT_TIMEOUT_SECONDS"
require_regex "${NAME_PRUNE_WAIT_TIMEOUT_SECONDS}" '^[0-9]+$' "NAME_PRUNE_WAIT_TIMEOUT_SECONDS"
require_regex "${NODEDB_REFRESH_INTERVAL_SECONDS}" '^[0-9]+([.][0-9]+)?$' "NODEDB_REFRESH_INTERVAL_SECONDS"
require_regex "${MESH_CHANNEL_NAME_A}" '^[[:print:]]+$' "MESH_CHANNEL_NAME_A"
require_regex "${MESH_CHANNEL_NAME_B}" '^[[:print:]]+$' "MESH_CHANNEL_NAME_B"
require_regex "${MESH_PRIMARY_PSK_A}" '^0x[0-9A-Fa-f]{64}$' "MESH_PRIMARY_PSK_A"
require_regex "${MESH_PRIMARY_PSK_B}" '^0x[0-9A-Fa-f]{64}$' "MESH_PRIMARY_PSK_B"

# Port validation
MESHTASTICD_PORT_A_DEC=$((10#${MESHTASTICD_PORT_A}))
MESHTASTICD_PORT_B_DEC=$((10#${MESHTASTICD_PORT_B}))
MESHTASTICD_PORT_A_PEER_DEC=$((10#${MESHTASTICD_PORT_A_PEER}))
MESHTASTICD_PORT_B_PEER_DEC=$((10#${MESHTASTICD_PORT_B_PEER}))
SYNAPSE_PORT_DEC=$((10#${SYNAPSE_PORT}))
HOST_UID=$(id -u)
HOST_GID=$(id -g)

if ((MESHTASTICD_PORT_A_DEC < 1 || MESHTASTICD_PORT_A_DEC > 65535)); then
	echo "MESHTASTICD_PORT_A must be between 1 and 65535." >&2
	exit 1
fi
if ((MESHTASTICD_PORT_B_DEC < 1 || MESHTASTICD_PORT_B_DEC > 65535)); then
	echo "MESHTASTICD_PORT_B must be between 1 and 65535." >&2
	exit 1
fi
if ((MESHTASTICD_PORT_A_PEER_DEC < 1 || MESHTASTICD_PORT_A_PEER_DEC > 65535)); then
	echo "MESHTASTICD_PORT_A_PEER must be between 1 and 65535." >&2
	exit 1
fi
if ((MESHTASTICD_PORT_B_PEER_DEC < 1 || MESHTASTICD_PORT_B_PEER_DEC > 65535)); then
	echo "MESHTASTICD_PORT_B_PEER must be between 1 and 65535." >&2
	exit 1
fi
if ((MESHTASTICD_PORT_A_DEC == MESHTASTICD_PORT_B_DEC || MESHTASTICD_PORT_A_DEC == MESHTASTICD_PORT_A_PEER_DEC || MESHTASTICD_PORT_A_DEC == MESHTASTICD_PORT_B_PEER_DEC || MESHTASTICD_PORT_B_DEC == MESHTASTICD_PORT_A_PEER_DEC || MESHTASTICD_PORT_B_DEC == MESHTASTICD_PORT_B_PEER_DEC || MESHTASTICD_PORT_A_PEER_DEC == MESHTASTICD_PORT_B_PEER_DEC)); then
	echo "Meshtasticd ports must all be different (A, B, A_PEER, B_PEER)." >&2
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
if ((10#${NAME_PRUNE_WAIT_TIMEOUT_SECONDS} <= 0)); then
	echo "NAME_PRUNE_WAIT_TIMEOUT_SECONDS must be greater than zero." >&2
	exit 1
fi
if ! "${PYTHON_BIN}" - "${NODEDB_REFRESH_INTERVAL_SECONDS}" <<'PY'; then
import math
import sys

value = float(sys.argv[1])
if not math.isfinite(value) or value <= 0:
    raise SystemExit(1)
PY
	echo "NODEDB_REFRESH_INTERVAL_SECONDS must be a finite value greater than zero." >&2
	exit 1
fi
if ! "${PYTHON_BIN}" - "${NAME_PRUNE_WAIT_TIMEOUT_SECONDS}" "${NODEDB_REFRESH_INTERVAL_SECONDS}" <<'PY'; then
import math
import sys

name_prune_wait_timeout = float(sys.argv[1])
nodedb_refresh_interval = float(sys.argv[2])
minimum_timeout = nodedb_refresh_interval + 2.0
if (
    not math.isfinite(name_prune_wait_timeout)
    or not math.isfinite(nodedb_refresh_interval)
    or name_prune_wait_timeout < minimum_timeout
):
    raise SystemExit(1)
PY
	echo "NAME_PRUNE_WAIT_TIMEOUT_SECONDS must be at least 2 seconds greater than NODEDB_REFRESH_INTERVAL_SECONDS." >&2
	exit 1
fi
if [[ -z ${MESHNET_NAME_A} || -z ${MESHNET_NAME_B} ]]; then
	echo "MESHNET_NAME_A and MESHNET_NAME_B must be non-empty." >&2
	exit 1
fi

MESHTASTICD_ENDPOINT_A="${MESHTASTICD_HOST_A}:${MESHTASTICD_PORT_A_DEC}"
MESHTASTICD_ENDPOINT_B="${MESHTASTICD_HOST_B}:${MESHTASTICD_PORT_B_DEC}"
MESHTASTICD_ENDPOINT_A_PEER="${MESHTASTICD_HOST_A_PEER}:${MESHTASTICD_PORT_A_PEER_DEC}"
MESHTASTICD_ENDPOINT_B_PEER="${MESHTASTICD_HOST_B_PEER}:${MESHTASTICD_PORT_B_PEER_DEC}"

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
			/bin/sh -c "chown -R ${HOST_UID}:${HOST_GID} /work" >/dev/null
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
	"${MESHTASTICD_CONTAINER_A_PEER}" \
	"${MESHTASTICD_CONTAINER_B_PEER}" \
	"${SYNAPSE_CONTAINER}" >/dev/null 2>&1 || true

set +e
ensure_docker_image_available "${MESHTASTICD_IMAGE}"
meshtasticd_pull_status=$?
set -e
if [[ ${meshtasticd_pull_status} -ne 0 ]]; then
	if [[ ${MESHTASTICD_IMAGE} == "meshtastic/meshtasticd:latest" || ${MESHTASTICD_IMAGE} == "meshtastic/meshtasticd" ]]; then
		echo "Failed to pull ${MESHTASTICD_IMAGE}; retrying with meshtastic/meshtasticd:beta" >&2
		MESHTASTICD_IMAGE="meshtastic/meshtasticd:beta"
		set +e
		ensure_docker_image_available "${MESHTASTICD_IMAGE}"
		meshtasticd_pull_status=$?
		set -e
		if [[ ${meshtasticd_pull_status} -ne 0 ]]; then
			echo "Failed to pull ${MESHTASTICD_IMAGE}" >&2
			exit 1
		fi
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

docker run -d \
	--name "${MESHTASTICD_CONTAINER_A_PEER}" \
	--network host \
	"${MESHTASTICD_IMAGE}" \
	meshtasticd -s --fsdir=/var/lib/meshtasticd-relay-a-peer -p "${MESHTASTICD_PORT_A_PEER_DEC}" -h "${MESHTASTICD_HWID_A_PEER}" >/dev/null

docker run -d \
	--name "${MESHTASTICD_CONTAINER_B_PEER}" \
	--network host \
	"${MESHTASTICD_IMAGE}" \
	meshtasticd -s --fsdir=/var/lib/meshtasticd-relay-b-peer -p "${MESHTASTICD_PORT_B_PEER_DEC}" -h "${MESHTASTICD_HWID_B_PEER}" >/dev/null

wait_for_meshtasticd_ready "${MESHTASTICD_ENDPOINT_A}" "${MESHTASTICD_CONTAINER_A}"
wait_for_meshtasticd_ready "${MESHTASTICD_ENDPOINT_B}" "${MESHTASTICD_CONTAINER_B}"
wait_for_meshtasticd_ready "${MESHTASTICD_ENDPOINT_A_PEER}" "${MESHTASTICD_CONTAINER_A_PEER}"
wait_for_meshtasticd_ready "${MESHTASTICD_ENDPOINT_B_PEER}" "${MESHTASTICD_CONTAINER_B_PEER}"

echo ""
echo "Configuring isolated meshnets (relay + peer node per meshnet)..."
configure_mesh_channel "${MESHTASTICD_ENDPOINT_A}" "${MESH_CHANNEL_NAME_A}" "${MESH_PRIMARY_PSK_A}"
configure_mesh_channel "${MESHTASTICD_ENDPOINT_B}" "${MESH_CHANNEL_NAME_B}" "${MESH_PRIMARY_PSK_B}"
configure_mesh_channel "${MESHTASTICD_ENDPOINT_A_PEER}" "${MESH_CHANNEL_NAME_A}" "${MESH_PRIMARY_PSK_A}"
configure_mesh_channel "${MESHTASTICD_ENDPOINT_B_PEER}" "${MESH_CHANNEL_NAME_B}" "${MESH_PRIMARY_PSK_B}"
wait_for_meshtasticd_ready "${MESHTASTICD_ENDPOINT_A}" "${MESHTASTICD_CONTAINER_A}"
wait_for_meshtasticd_ready "${MESHTASTICD_ENDPOINT_B}" "${MESHTASTICD_CONTAINER_B}"
wait_for_meshtasticd_ready "${MESHTASTICD_ENDPOINT_A_PEER}" "${MESHTASTICD_CONTAINER_A_PEER}"
wait_for_meshtasticd_ready "${MESHTASTICD_ENDPOINT_B_PEER}" "${MESHTASTICD_CONTAINER_B_PEER}"
RELAY_NODE_ID_A="$(get_local_node_id "${MESHTASTICD_ENDPOINT_A}")"
RELAY_NODE_ID_B="$(get_local_node_id "${MESHTASTICD_ENDPOINT_B}")"
echo "Resolved relay node IDs: A=${RELAY_NODE_ID_A}, B=${RELAY_NODE_ID_B}"

echo ""
ensure_docker_image_available "${SYNAPSE_IMAGE}"

echo ""
echo "Generating Synapse config..."
docker run --rm \
	--user "${HOST_UID}:${HOST_GID}" \
	-e SYNAPSE_SERVER_NAME="${SYNAPSE_SERVER_NAME}" \
	-e SYNAPSE_REPORT_STATS=no \
	-v "${SYNAPSE_DATA_DIR}:/data" \
	"${SYNAPSE_IMAGE}" generate >/dev/null

cat >>"${SYNAPSE_DATA_DIR}/homeserver.yaml" <<'YAML'
# Test-only shared secret for ephemeral CI Synapse instance.
registration_shared_secret: "mmrelay-ci-shared-secret"
enable_registration: true
enable_registration_without_verification: true
# Relax CI rate limits to avoid transient 429s during bursty encrypted relay traffic.
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
        "device_id": body["device_id"],
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

def create_room(
    user_token: str,
    name: str,
    topic: str,
    alias_name: str,
    encrypted: bool = False,
) -> str:
    payload = {
        "preset": "private_chat",
        "name": name,
        "topic": topic,
        "room_alias_name": alias_name,
    }
    if encrypted:
        payload["initial_state"] = [
            {
                "type": "m.room.encryption",
                "state_key": "",
                "content": {"algorithm": "m.megolm.v1.aes-sha2"},
            }
        ]
    room_create = post(
        "/_matrix/client/v3/createRoom",
        user_token,
        payload,
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
room_id_plaintext = create_room(
    user1["access_token"],
    "MMRelay CI Plaintext Room",
    "Plaintext room for integration tests",
    f"mmrelay-ci-plain-{room_suffix}",
    encrypted=False,
)
room_id_encrypted = create_room(
    user1["access_token"],
    "MMRelay CI Encrypted Room",
    "Encrypted room for integration tests",
    f"mmrelay-ci-e2ee-{room_suffix}",
    encrypted=True,
)
room_id_dm_a = create_room(
    user1["access_token"],
    "MMRelay CI DM Room A",
    "Direct message forwarding room for relay A plugin tests",
    f"mmrelay-ci-dm-a-{room_suffix}",
    encrypted=False,
)
room_id_dm_b = create_room(
    user1["access_token"],
    "MMRelay CI DM Room B",
    "Direct message forwarding room for relay B plugin tests",
    f"mmrelay-ci-dm-b-{room_suffix}",
    encrypted=False,
)

# Shared room members: user1 + user2 + bot_a + bot_b in both rooms.
for room_id in [room_id_plaintext, room_id_encrypted]:
    for account in [bot_a, bot_b, user2]:
        invite_and_join(room_id, user1["access_token"], account)

# Relay-specific DM rooms
invite_and_join(room_id_dm_a, user1["access_token"], bot_a)
invite_and_join(room_id_dm_b, user1["access_token"], bot_b)

sync_response = requests.get(
    f"{base_url}/_matrix/client/v3/sync",
    headers={"Authorization": f"Bearer {user1['access_token']}"},
    params={"timeout": 0},
    timeout=20,
)
_raise_for_status(sync_response, "initial user1 sync")
initial_sync_user1 = sync_response.json()

runtime = {
    "room_id_plaintext": room_id_plaintext,
    "room_id_encrypted": room_id_encrypted,
    "room_id_dm_a": room_id_dm_a,
    "room_id_dm_b": room_id_dm_b,
    "bot_a_user_id": bot_a["user_id"],
    "bot_a_access_token": bot_a["access_token"],
    "bot_a_device_id": bot_a["device_id"],
    "bot_b_user_id": bot_b["user_id"],
    "bot_b_access_token": bot_b["access_token"],
    "bot_b_device_id": bot_b["device_id"],
    "user_access_token": user1["access_token"],
    "user2_user_id": user2["user_id"],
    "user2_access_token": user2["access_token"],
    "user2_device_id": user2["device_id"],
    "sync_since_user": initial_sync_user1.get("next_batch", ""),
}

json.dump(runtime, fp=sys.stdout)
PY

BOT_A_USER_ID="$(load_json_value bot_a_user_id)"
BOT_A_ACCESS_TOKEN="$(load_json_value bot_a_access_token)"
BOT_A_DEVICE_ID="$(load_json_value bot_a_device_id)"
BOT_B_USER_ID="$(load_json_value bot_b_user_id)"
BOT_B_ACCESS_TOKEN="$(load_json_value bot_b_access_token)"
BOT_B_DEVICE_ID="$(load_json_value bot_b_device_id)"
USER_ACCESS_TOKEN="$(load_json_value user_access_token)"
USER2_USER_ID="$(load_json_value user2_user_id)"
USER2_ACCESS_TOKEN="$(load_json_value user2_access_token)"
USER2_DEVICE_ID="$(load_json_value user2_device_id)"
ROOM_ID_PLAINTEXT="$(load_json_value room_id_plaintext)"
ROOM_ID_ENCRYPTED="$(load_json_value room_id_encrypted)"
ROOM_ID_DM_A="$(load_json_value room_id_dm_a)"
ROOM_ID_DM_B="$(load_json_value room_id_dm_b)"
SYNC_SINCE_USER="$(load_json_value sync_since_user)"

export BOT_A_USER_ID
export BOT_A_ACCESS_TOKEN
export BOT_A_DEVICE_ID
export BOT_B_USER_ID
export BOT_B_ACCESS_TOKEN
export BOT_B_DEVICE_ID
export USER_ACCESS_TOKEN
export USER2_USER_ID
export USER2_ACCESS_TOKEN
export USER2_DEVICE_ID
export ROOM_ID_PLAINTEXT
export ROOM_ID_ENCRYPTED
export ROOM_ID_DM_A
export ROOM_ID_DM_B
export SYNC_SINCE_USER

E2EE_USER2_STORE_DIR="${SHARED_DIR}/user2-e2ee-store"
E2EE_USER2_AUTH_STATE="${SHARED_DIR}/user2-e2ee-auth.json"

# Seed user2 auth state so encrypted sends reuse the existing Matrix login.
write_e2ee_auth_state_json \
	"${E2EE_USER2_AUTH_STATE}" \
	"${USER2_ACCESS_TOKEN}" \
	"${USER2_USER_ID}" \
	"${USER2_DEVICE_ID}"

# Seed bot credentials so each MMRelay instance restores a session instead of
# performing a new login (avoids Synapse login rate-limit churn in CI).
write_matrix_credentials_json \
	"${MMRELAY_HOME_DIR_A}/matrix/credentials.json" \
	"${MATRIX_BASE_URL}" \
	"${BOT_A_USER_ID}" \
	"${BOT_A_ACCESS_TOKEN}" \
	"${BOT_A_DEVICE_ID}"
write_matrix_credentials_json \
	"${MMRELAY_HOME_DIR_B}/matrix/credentials.json" \
	"${MATRIX_BASE_URL}" \
	"${BOT_B_USER_ID}" \
	"${BOT_B_ACCESS_TOKEN}" \
	"${BOT_B_DEVICE_ID}"

# =============================================================================
# Create MMRelay Configurations
# =============================================================================

echo ""
echo "Creating MMRelay configurations..."

# MMRelay A - Connects to Mesh A
cat >"${MMRELAY_CONFIG_PATH_A}" <<EOF_CONFIG
matrix:
  homeserver: "${MATRIX_BASE_URL}"
  bot_user_id: "${BOT_A_USER_ID}"
  e2ee:
    enabled: true
    store_path: "${MMRELAY_HOME_DIR_A}/matrix/store"
matrix_rooms:
  - id: "${ROOM_ID_PLAINTEXT}"
    meshtastic_channel: 0
  - id: "${ROOM_ID_ENCRYPTED}"
    meshtastic_channel: 0
community-plugins:
  dm-rcv-basic:
    active: true
    repository: "https://github.com/jeremiah-k/mmr-dm-rcv-basic.git"
    branch: "main"
    dm_room: "${ROOM_ID_DM_A}"
    dm_prefix: true
meshtastic:
  connection_type: tcp
  host: "${MESHTASTICD_HOST_A}"
  port: ${MESHTASTICD_PORT_A_DEC}
  meshnet_name: "${MESHNET_NAME_A}"
  nodedb_refresh_interval: ${NODEDB_REFRESH_INTERVAL_SECONDS}
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
  bot_user_id: "${BOT_B_USER_ID}"
  e2ee:
    enabled: true
    store_path: "${MMRELAY_HOME_DIR_B}/matrix/store"
matrix_rooms:
  - id: "${ROOM_ID_PLAINTEXT}"
    meshtastic_channel: 0
  - id: "${ROOM_ID_ENCRYPTED}"
    meshtastic_channel: 0
community-plugins:
  dm-rcv-basic:
    active: true
    repository: "https://github.com/jeremiah-k/mmr-dm-rcv-basic.git"
    branch: "main"
    dm_room: "${ROOM_ID_DM_B}"
    dm_prefix: true
meshtastic:
  connection_type: tcp
  host: "${MESHTASTICD_HOST_B}"
  port: ${MESHTASTICD_PORT_B_DEC}
  meshnet_name: "${MESHNET_NAME_B}"
  nodedb_refresh_interval: ${NODEDB_REFRESH_INTERVAL_SECONDS}
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
# Test Scenarios
# =============================================================================

echo ""
echo "============================================================================"
echo "Running Test Scenarios"
echo "============================================================================"

SUITE_START_MS=$(date +%s%3N)
SYNC_CURSOR_USER1="${SYNC_SINCE_USER}"

# Start relay processes after infrastructure setup and configuration generation.
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

# Test 1: Matrix user message in plaintext room → Mesh A + Mesh B
MATRIX_TO_SHARED_TEXT="MMRELAY_CI_M2SHARED_$(date +%s)_${RANDOM}"
log_offset_before_m2shared_a=$(wc -c <"${MMRELAY_LOG_PATH_A}")
log_offset_before_m2shared_b=$(wc -c <"${MMRELAY_LOG_PATH_B}")

start_test "Test 1" "Test 1: Matrix user message in plaintext room → Mesh A + Mesh B..."
matrix_send_message \
	"${USER_ACCESS_TOKEN}" \
	"${ROOM_ID_PLAINTEXT}" \
	"${MATRIX_TO_SHARED_TEXT}" \
	"mmrelay-ci-m2shared" >/dev/null
run_or_fail "Message was not relayed to Mesh A" \
	wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_A}" \
	"Relaying message from" \
	"${log_offset_before_m2shared_a}" \
	45
run_or_fail "Message was not relayed to Mesh B" \
	wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"Relaying message from" \
	"${log_offset_before_m2shared_b}" \
	45
pass_test "Matrix user message relayed to both meshes"

# Test 2: Injected Mesh A-origin event in plaintext room → remote meshnet processing in MMRelay B
#
# Keep one API client per meshtasticd node (the relay itself) to avoid transport churn.
# We inject the same mesh-origin Matrix event shape that MMRelay publishes so we still
# exercise MMRelay's remote-meshnet processing path end-to-end through Matrix.
MESH_A_TO_MATRIX_TEXT="MMRELAY_CI_A2M_$(date +%s)_${RANDOM}"
MESH_A_SIM_ID=$((2000000000 + RANDOM))
log_offset_before_a2m_b=$(wc -c <"${MMRELAY_LOG_PATH_B}")

start_test "Test 2" "Test 2: Injected Mesh A-origin event in plaintext room → remote processing in Mesh B relay..."
run_capture_or_fail \
	MESH_A_MATRIX_EVENT_ID \
	"Failed to inject Mesh A-origin Matrix event" \
	matrix_send_mesh_origin_message \
	"${BOT_A_ACCESS_TOKEN}" \
	"${ROOM_ID_PLAINTEXT}" \
	"${MESH_A_TO_MATRIX_TEXT}" \
	"${MESHNET_NAME_A}" \
	"CI Field Node A" \
	"CFA" \
	"${MESH_A_SIM_ID}" \
	"mmrelay-ci-sim-a2m"
run_capture_or_fail \
	mesh_a_event_json \
	"Injected Mesh A-origin Matrix event was not observed in plaintext room" \
	matrix_wait_event \
	"${USER_ACCESS_TOKEN}" \
	"${ROOM_ID_PLAINTEXT}" \
	"${MATRIX_EVENT_TIMEOUT_SECONDS}" \
	"m.room.message" \
	"${BOT_A_USER_ID}" \
	"${MESH_A_TO_MATRIX_TEXT}" \
	"m.text" \
	"" \
	"${MESH_A_MATRIX_EVENT_ID}" \
	"" \
	"${SYNC_CURSOR_USER1}"
run_capture_or_fail \
	SYNC_CURSOR_USER1 \
	"Failed to update Matrix sync cursor after Test 2" \
	json_extract \
	"${mesh_a_event_json}" \
	"next_batch"
run_or_fail "MMRelay B did not process injected remote mesh event from ${MESHNET_NAME_A}" \
	wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"Processing message from remote meshnet:" \
	"${log_offset_before_a2m_b}" \
	60
pass_test "Injected Mesh A-origin event reached Matrix and processed in Mesh B relay"

# Test 3: Injected Mesh B-origin event in plaintext room → remote meshnet processing in MMRelay A
MESH_B_TO_MATRIX_TEXT="MMRELAY_CI_B2M_$(date +%s)_${RANDOM}"
MESH_B_SIM_ID=$((2100000000 + RANDOM))
log_offset_before_b2m_a=$(wc -c <"${MMRELAY_LOG_PATH_A}")

start_test "Test 3" "Test 3: Injected Mesh B-origin event in plaintext room → remote processing in Mesh A relay..."
run_capture_or_fail \
	MESH_B_MATRIX_EVENT_ID \
	"Failed to inject Mesh B-origin Matrix event" \
	matrix_send_mesh_origin_message \
	"${BOT_B_ACCESS_TOKEN}" \
	"${ROOM_ID_PLAINTEXT}" \
	"${MESH_B_TO_MATRIX_TEXT}" \
	"${MESHNET_NAME_B}" \
	"CI Field Node B" \
	"CFB" \
	"${MESH_B_SIM_ID}" \
	"mmrelay-ci-sim-b2m"
run_capture_or_fail \
	mesh_b_event_json \
	"Injected Mesh B-origin Matrix event was not observed in plaintext room" \
	matrix_wait_event \
	"${USER_ACCESS_TOKEN}" \
	"${ROOM_ID_PLAINTEXT}" \
	"${MATRIX_EVENT_TIMEOUT_SECONDS}" \
	"m.room.message" \
	"${BOT_B_USER_ID}" \
	"${MESH_B_TO_MATRIX_TEXT}" \
	"m.text" \
	"" \
	"${MESH_B_MATRIX_EVENT_ID}" \
	"" \
	"${SYNC_CURSOR_USER1}"
run_capture_or_fail \
	SYNC_CURSOR_USER1 \
	"Failed to update Matrix sync cursor after Test 3" \
	json_extract \
	"${mesh_b_event_json}" \
	"next_batch"
run_or_fail "MMRelay A did not process injected remote mesh event from ${MESHNET_NAME_B}" \
	wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_A}" \
	"Processing message from remote meshnet:" \
	"${log_offset_before_b2m_a}" \
	60
pass_test "Injected Mesh B-origin event reached Matrix and processed in Mesh A relay"

# Test 4: Encrypted-room Matrix user message → Mesh A + Mesh B
MATRIX_USER2_TEXT="MMRELAY_CI_U2_MSG_$(date +%s)_${RANDOM}"
log_offset_before_u2msg_a=$(wc -c <"${MMRELAY_LOG_PATH_A}")
log_offset_before_u2msg_b=$(wc -c <"${MMRELAY_LOG_PATH_B}")

start_test "Test 4" "Test 4: Encrypted-room Matrix user message → Mesh A + Mesh B..."
run_capture_or_fail \
	MATRIX_USER2_EVENT_ID \
	"Failed to send encrypted user message in Test 4" \
	matrix_send_e2ee_message \
	"${USER2_USER_ID}" \
	"${MATRIX_USER2_PASSWORD}" \
	"${ROOM_ID_ENCRYPTED}" \
	"${MATRIX_USER2_TEXT}" \
	"${E2EE_USER2_STORE_DIR}" \
	"${E2EE_USER2_AUTH_STATE}"
run_with_status matrix_wait_event_by_id \
	"${USER_ACCESS_TOKEN}" \
	"${ROOM_ID_ENCRYPTED}" \
	"${MATRIX_USER2_EVENT_ID}" \
	"${MATRIX_EVENT_TIMEOUT_SECONDS}" \
	"${USER2_USER_ID}" \
	"m.room.encrypted" >/dev/null
matrix_wait_status=$?
if ((matrix_wait_status != 0)); then
	fail_test "Encrypted user message event was not visible in Matrix room for Test 4"
fi
run_or_fail "Secondary user message did not relay to Mesh A" \
	wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_A}" \
	"Relaying message from" \
	"${log_offset_before_u2msg_a}" \
	45
run_or_fail "Secondary user message did not relay to Mesh B" \
	wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"Relaying message from" \
	"${log_offset_before_u2msg_b}" \
	45
pass_test "Encrypted-room user message relayed to both meshes"

start_test "Test 5" "Test 5: Encrypted-room user reply to prior Matrix event → both meshes..."

# Ensure both relay instances have persisted the mapping for the replied-to event.
# Without this gate, a fast reply can race ahead of DB persistence and be treated
# as a plain message instead of a Meshtastic reply.
run_with_status wait_for_message_map_meshtastic_id \
	"${MMRELAY_DB_PATH_A}" \
	"${MATRIX_USER2_EVENT_ID}" \
	"${MESSAGE_MAP_WAIT_TIMEOUT_SECONDS}" >/dev/null
message_map_wait_status=$?
if ((message_map_wait_status != 0)); then
	fail_test "Timed out waiting for message_map row in instance A for replied-to Matrix event"
fi
run_with_status wait_for_message_map_meshtastic_id \
	"${MMRELAY_DB_PATH_B}" \
	"${MATRIX_USER2_EVENT_ID}" \
	"${MESSAGE_MAP_WAIT_TIMEOUT_SECONDS}" >/dev/null
message_map_wait_status=$?
if ((message_map_wait_status != 0)); then
	fail_test "Timed out waiting for message_map row in instance B for replied-to Matrix event"
fi

# Test 5: Encrypted-room Matrix user reply to prior Matrix event → both meshes as structured replies
MATRIX_USER2_REPLY_TEXT="MMRELAY_CI_U2_REPLY_$(date +%s)_${RANDOM}"
log_offset_before_u2reply_a=$(wc -c <"${MMRELAY_LOG_PATH_A}")
log_offset_before_u2reply_b=$(wc -c <"${MMRELAY_LOG_PATH_B}")
run_capture_or_fail \
	MATRIX_USER2_REPLY_EVENT_ID \
	"Failed to send encrypted user reply in Test 5" \
	matrix_send_e2ee_message \
	"${USER2_USER_ID}" \
	"${MATRIX_USER2_PASSWORD}" \
	"${ROOM_ID_ENCRYPTED}" \
	"${MATRIX_USER2_REPLY_TEXT}" \
	"${E2EE_USER2_STORE_DIR}" \
	"${E2EE_USER2_AUTH_STATE}" \
	"${MATRIX_USER2_EVENT_ID}"
run_with_status matrix_wait_event_by_id \
	"${USER_ACCESS_TOKEN}" \
	"${ROOM_ID_ENCRYPTED}" \
	"${MATRIX_USER2_REPLY_EVENT_ID}" \
	"${MATRIX_EVENT_TIMEOUT_SECONDS}" \
	"${USER2_USER_ID}" \
	"m.room.encrypted" >/dev/null
matrix_wait_status=$?
if ((matrix_wait_status != 0)); then
	fail_test "Encrypted user reply event was not visible in Matrix room for Test 5"
fi
run_or_fail "Reply did not relay to Mesh A" \
	wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_A}" \
	"Relaying Matrix reply from" \
	"${log_offset_before_u2reply_a}" \
	60
run_or_fail "Reply did not relay to Mesh B" \
	wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"Relaying Matrix reply from" \
	"${log_offset_before_u2reply_b}" \
	60
pass_test "Encrypted-room user reply relayed to both meshes"

# Test 6: Encrypted-room Matrix user reaction to prior Matrix event → both meshes
MATRIX_USER2_REACTION_KEY="👍"
log_offset_before_u2react_a=$(wc -c <"${MMRELAY_LOG_PATH_A}")
log_offset_before_u2react_b=$(wc -c <"${MMRELAY_LOG_PATH_B}")

start_test "Test 6" "Test 6: Encrypted-room user reaction to prior Matrix event → both meshes..."
run_capture_or_fail \
	MATRIX_USER2_REACTION_EVENT_ID \
	"Failed to send encrypted user reaction in Test 6" \
	matrix_send_e2ee_reaction \
	"${USER2_USER_ID}" \
	"${MATRIX_USER2_PASSWORD}" \
	"${ROOM_ID_ENCRYPTED}" \
	"${MATRIX_USER2_EVENT_ID}" \
	"${MATRIX_USER2_REACTION_KEY}" \
	"${E2EE_USER2_STORE_DIR}" \
	"${E2EE_USER2_AUTH_STATE}"
run_with_status matrix_wait_event_by_id \
	"${USER_ACCESS_TOKEN}" \
	"${ROOM_ID_ENCRYPTED}" \
	"${MATRIX_USER2_REACTION_EVENT_ID}" \
	"${MATRIX_EVENT_TIMEOUT_SECONDS}" \
	"${USER2_USER_ID}" \
	"m.room.encrypted,m.reaction" >/dev/null
matrix_wait_status=$?
if ((matrix_wait_status != 0)); then
	fail_test "Encrypted user reaction event was not visible in Matrix room for Test 6"
fi
run_or_fail "Reaction did not relay to Mesh A" \
	wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_A}" \
	"Relaying reaction from" \
	"${log_offset_before_u2react_a}" \
	60
run_or_fail "Reaction did not relay to Mesh B" \
	wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"Relaying reaction from" \
	"${log_offset_before_u2react_b}" \
	60
pass_test "Encrypted-room user reaction relayed to both meshes"

# Test 7: dm-rcv-basic plugin initialization.
# Verifies the dm-rcv-basic community plugin loads and initializes correctly
# with valid dm_room configuration.

start_test "Test 7" "Test 7: dm-rcv-basic plugin initialization..."

run_or_fail "dm-rcv-basic did not initialize in relay A" \
	wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_A}" \
	"initialized - forwarding DMs to room:" \
	0 \
	45
run_or_fail "dm-rcv-basic did not initialize in relay B" \
	wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"initialized - forwarding DMs to room:" \
	0 \
	45
pass_test "dm-rcv-basic plugin initialized in both relays"

# Test 8: stale name rows are pruned to match current node DB snapshot.
start_test "Test 8" "Test 8: stale name rows are pruned to match current node DB..."

POLL_TIMEOUT_SECONDS=$(
	"${PYTHON_BIN}" - "${NODEDB_REFRESH_INTERVAL_SECONDS}" <<'PY'
import math
import sys

DEFAULT_TIMEOUT_SECONDS = 30
SAFETY_MARGIN_SECONDS = 10
try:
    refresh_interval = float(sys.argv[1])
except (TypeError, ValueError):
    refresh_interval = float(DEFAULT_TIMEOUT_SECONDS)
refresh_interval = max(refresh_interval, 0.0)
print(max(DEFAULT_TIMEOUT_SECONDS, math.ceil(refresh_interval + SAFETY_MARGIN_SECONDS)))
PY
)
POLL_INTERVAL_SECONDS=1

CURRENT_LONGNAME_ID_A=""
poll_for_existing_name_entry \
	CURRENT_LONGNAME_ID_A \
	"${MMRELAY_DB_PATH_A}" \
	"${NAMES_TABLE_LONGNAMES}" \
	"instance A" \
	"${MMRELAY_PID_A}" \
	"MMRelay A" \
	"${MMRELAY_LOG_PATH_A}"

CURRENT_SHORTNAME_ID_A=""
poll_for_existing_name_entry \
	CURRENT_SHORTNAME_ID_A \
	"${MMRELAY_DB_PATH_A}" \
	"${NAMES_TABLE_SHORTNAMES}" \
	"instance A" \
	"${MMRELAY_PID_A}" \
	"MMRelay A" \
	"${MMRELAY_LOG_PATH_A}"

CURRENT_LONGNAME_ID_B=""
poll_for_existing_name_entry \
	CURRENT_LONGNAME_ID_B \
	"${MMRELAY_DB_PATH_B}" \
	"${NAMES_TABLE_LONGNAMES}" \
	"instance B" \
	"${MMRELAY_PID_B}" \
	"MMRelay B" \
	"${MMRELAY_LOG_PATH_B}"

CURRENT_SHORTNAME_ID_B=""
poll_for_existing_name_entry \
	CURRENT_SHORTNAME_ID_B \
	"${MMRELAY_DB_PATH_B}" \
	"${NAMES_TABLE_SHORTNAMES}" \
	"instance B" \
	"${MMRELAY_PID_B}" \
	"MMRelay B" \
	"${MMRELAY_LOG_PATH_B}"

STALE_NAME_ID_A=$(generate_unique_test_id "MMRELAY_STALE_A")
STALE_NAME_ID_B=$(generate_unique_test_id "MMRELAY_STALE_B")
run_or_fail "Failed to seed stale longname in instance A" \
	upsert_name_entry \
	"${MMRELAY_DB_PATH_A}" \
	"${NAMES_TABLE_LONGNAMES}" \
	"${NAMES_FIELD_LONGNAME}" \
	"${STALE_NAME_ID_A}" \
	"CI stale longname A"
run_or_fail "Failed to seed stale shortname in instance A" \
	upsert_name_entry \
	"${MMRELAY_DB_PATH_A}" \
	"${NAMES_TABLE_SHORTNAMES}" \
	"${NAMES_FIELD_SHORTNAME}" \
	"${STALE_NAME_ID_A}" \
	"CSA"
run_or_fail "Failed to seed stale longname in instance B" \
	upsert_name_entry \
	"${MMRELAY_DB_PATH_B}" \
	"${NAMES_TABLE_LONGNAMES}" \
	"${NAMES_FIELD_LONGNAME}" \
	"${STALE_NAME_ID_B}" \
	"CI stale longname B"
run_or_fail "Failed to seed stale shortname in instance B" \
	upsert_name_entry \
	"${MMRELAY_DB_PATH_B}" \
	"${NAMES_TABLE_SHORTNAMES}" \
	"${NAMES_FIELD_SHORTNAME}" \
	"${STALE_NAME_ID_B}" \
	"CSB"

run_or_fail "Stale longname row in instance A was not pruned" \
	wait_for_name_entry_absent \
	"${MMRELAY_DB_PATH_A}" \
	"${NAMES_TABLE_LONGNAMES}" \
	"${STALE_NAME_ID_A}" \
	"${NAME_PRUNE_WAIT_TIMEOUT_SECONDS}" \
	"${MMRELAY_PID_A}" \
	"MMRelay A" \
	"${MMRELAY_LOG_PATH_A}"
run_or_fail "Stale shortname row in instance A was not pruned" \
	wait_for_name_entry_absent \
	"${MMRELAY_DB_PATH_A}" \
	"${NAMES_TABLE_SHORTNAMES}" \
	"${STALE_NAME_ID_A}" \
	"${NAME_PRUNE_WAIT_TIMEOUT_SECONDS}" \
	"${MMRELAY_PID_A}" \
	"MMRelay A" \
	"${MMRELAY_LOG_PATH_A}"
run_or_fail "Stale longname row in instance B was not pruned" \
	wait_for_name_entry_absent \
	"${MMRELAY_DB_PATH_B}" \
	"${NAMES_TABLE_LONGNAMES}" \
	"${STALE_NAME_ID_B}" \
	"${NAME_PRUNE_WAIT_TIMEOUT_SECONDS}" \
	"${MMRELAY_PID_B}" \
	"MMRelay B" \
	"${MMRELAY_LOG_PATH_B}"
run_or_fail "Stale shortname row in instance B was not pruned" \
	wait_for_name_entry_absent \
	"${MMRELAY_DB_PATH_B}" \
	"${NAMES_TABLE_SHORTNAMES}" \
	"${STALE_NAME_ID_B}" \
	"${NAME_PRUNE_WAIT_TIMEOUT_SECONDS}" \
	"${MMRELAY_PID_B}" \
	"MMRelay B" \
	"${MMRELAY_LOG_PATH_B}"
run_or_fail "Current longnames row in instance A disappeared unexpectedly" \
	wait_for_name_entry_present \
	"${MMRELAY_DB_PATH_A}" \
	"${NAMES_TABLE_LONGNAMES}" \
	"${CURRENT_LONGNAME_ID_A}" \
	"${NAME_PRUNE_WAIT_TIMEOUT_SECONDS}" \
	"${MMRELAY_PID_A}" \
	"MMRelay A" \
	"${MMRELAY_LOG_PATH_A}"
run_or_fail "Current shortnames row in instance A disappeared unexpectedly" \
	wait_for_name_entry_present \
	"${MMRELAY_DB_PATH_A}" \
	"${NAMES_TABLE_SHORTNAMES}" \
	"${CURRENT_SHORTNAME_ID_A}" \
	"${NAME_PRUNE_WAIT_TIMEOUT_SECONDS}" \
	"${MMRELAY_PID_A}" \
	"MMRelay A" \
	"${MMRELAY_LOG_PATH_A}"
run_or_fail "Current longnames row in instance B disappeared unexpectedly" \
	wait_for_name_entry_present \
	"${MMRELAY_DB_PATH_B}" \
	"${NAMES_TABLE_LONGNAMES}" \
	"${CURRENT_LONGNAME_ID_B}" \
	"${NAME_PRUNE_WAIT_TIMEOUT_SECONDS}" \
	"${MMRELAY_PID_B}" \
	"MMRelay B" \
	"${MMRELAY_LOG_PATH_B}"
run_or_fail "Current shortnames row in instance B disappeared unexpectedly" \
	wait_for_name_entry_present \
	"${MMRELAY_DB_PATH_B}" \
	"${NAMES_TABLE_SHORTNAMES}" \
	"${CURRENT_SHORTNAME_ID_B}" \
	"${NAME_PRUNE_WAIT_TIMEOUT_SECONDS}" \
	"${MMRELAY_PID_B}" \
	"MMRelay B" \
	"${MMRELAY_LOG_PATH_B}"
pass_test "Periodic node-name refresh pruned stale rows in both relays"

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
