#!/usr/bin/env bash

set -euo pipefail

# =============================================================================
# Cross-Mesh Integration Test for MMRelay
# =============================================================================
#
# This script tests MMRelay's ability to relay messages between two isolated mesh networks
# via a shared Matrix room. This is the core use case for MMRelay: bridging multiple meshnets.
#
# Architecture:
#   - meshtasticd-A (port 4403) ← MMRelay A ←─┐
#                                              └──→ Matrix Room ←──┐
#   - meshtasticd-B (port 4404) ← MMRelay B ←─┘
#
# Test Scenarios:
#   1. Matrix → Mesh A
#   2. Matrix → Mesh B
#   3. Mesh A → Matrix
#   4. Mesh B → Matrix
#   5. Mesh A → Mesh B (via Matrix bridge)
#   6. Mesh B → Mesh A (via Matrix bridge)
#
# Environment Variables:
#   MESHTASTICD_IMAGE: Docker image for meshtasticd (default: meshtastic/meshtasticd:latest)
#   SYNAPSE_IMAGE: Docker image for Synapse (default: matrixdotorg/synapse:latest)
#   STRICT_MESH_TO_MATRIX: Fail CI if Mesh→Matrix tests fail (default: true)
#   MMRELAY_LOG_ON_SUCCESS: Always show logs (default: true for CI)
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

# Synapse Configuration
SYNAPSE_IMAGE="${SYNAPSE_IMAGE:-matrixdotorg/synapse:latest}"
SYNAPSE_CONTAINER="${SYNAPSE_CONTAINER:-mmrelay-ci-synapse}"
SYNAPSE_PORT="${SYNAPSE_PORT:-8008}"
SYNAPSE_SERVER_NAME="${SYNAPSE_SERVER_NAME:-localhost}"
SYNAPSE_READY_TIMEOUT_SECONDS="${SYNAPSE_READY_TIMEOUT_SECONDS:-180}"

# MMRelay Configuration
MMRELAY_READY_TIMEOUT_SECONDS="${MMRELAY_READY_TIMEOUT_SECONDS:-120}"
MMRELAY_LOG_ON_SUCCESS="${MMRELAY_LOG_ON_SUCCESS:-true}"
STRICT_MESH_TO_MATRIX="${STRICT_MESH_TO_MATRIX:-true}"
PYTHON_BIN="${PYTHON_BIN:-python}"

# Artifacts and Logging - Separated by Instance
CI_ARTIFACT_DIR="${CI_ARTIFACT_DIR:-${PWD}/.ci-artifacts/cross-mesh-integration}"

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
MATRIX_RUNTIME_JSON="${SHARED_DIR}/matrix-runtime.json"

# Process tracking
MMRELAY_PID_A=""
MMRELAY_PID_B=""
LOGS_PRINTED=false

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

cleanup() {
	local exit_code=$?
	local shutdown_timeout=10

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
	local host=$1
	local port=$2
	local container=$3
	local deadline=$((SECONDS + 10#${MESHTASTICD_READY_TIMEOUT_SECONDS}))
	until "${PYTHON_BIN}" -m meshtastic --timeout 5 --host "${host}" --port "${port}" --info >/dev/null 2>&1; do
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

# =============================================================================
# Validation
# =============================================================================

trap cleanup EXIT

if ! command -v docker >/dev/null 2>&1; then
	echo "docker is required for cross-mesh integration tests." >&2
	exit 1
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
	echo "Python runtime '${PYTHON_BIN}' is required." >&2
	exit 1
fi

# Validate all configuration parameters
require_regex "${MESHTASTICD_CONTAINER_A}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "MESHTASTICD_CONTAINER_A"
require_regex "${MESHTASTICD_CONTAINER_B}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "MESHTASTICD_CONTAINER_B"
require_regex "${MESHTASTICD_IMAGE}" '^[^[:space:]]+$' "MESHTASTICD_IMAGE"
require_regex "${MESHTASTICD_PORT_A}" '^[0-9]+$' "MESHTASTICD_PORT_A"
require_regex "${MESHTASTICD_PORT_B}" '^[0-9]+$' "MESHTASTICD_PORT_B"
require_regex "${MESHTASTICD_HWID_A}" '^[0-9]+$' "MESHTASTICD_HWID_A"
require_regex "${MESHTASTICD_HWID_B}" '^[0-9]+$' "MESHTASTICD_HWID_B"
require_regex "${SYNAPSE_CONTAINER}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "SYNAPSE_CONTAINER"
require_regex "${SYNAPSE_IMAGE}" '^[^[:space:]]+$' "SYNAPSE_IMAGE"
require_regex "${SYNAPSE_PORT}" '^[0-9]+$' "SYNAPSE_PORT"

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
if ((SYNAPSE_PORT_DEC < 1 || SYNAPSE_PORT_DEC > 65535)); then
	echo "SYNAPSE_PORT must be between 1 and 65535." >&2
	exit 1
fi

# =============================================================================
# Setup
# =============================================================================

echo "Cross-Mesh Integration Test - Testing MMRelay's core use case"
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
mkdir -p "${CI_ARTIFACT_DIR}" "${SYNAPSE_DATA_DIR}" "${MMRELAY_HOME_DIR_A}" "${MMRELAY_HOME_DIR_B}"

MATRIX_BASE_URL="http://localhost:${SYNAPSE_PORT_DEC}"

# User configuration
MATRIX_BOT_USER_A_LOCALPART="mmrelaybot-a"
MATRIX_BOT_USER_B_LOCALPART="mmrelaybot-b"
MATRIX_USER_LOCALPART="mmrelayuser"
MATRIX_BOT_A_PASSWORD="mmrelay-bot-a-pass"
MATRIX_BOT_B_PASSWORD="mmrelay-bot-b-pass"
MATRIX_USER_PASSWORD="mmrelay-user-pass"

export MATRIX_BASE_URL
export MATRIX_BOT_USER_A_LOCALPART
export MATRIX_BOT_USER_B_LOCALPART
export MATRIX_USER_LOCALPART
export MATRIX_BOT_A_PASSWORD
export MATRIX_BOT_B_PASSWORD
export MATRIX_USER_PASSWORD

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
	meshtasticd -s --fsdir=/var/lib/meshtasticd-a -p "${MESHTASTICD_PORT_A_DEC}" -h "${MESHTASTICD_HWID_A}" >/dev/null

docker run -d \
	--name "${MESHTASTICD_CONTAINER_B}" \
	--network host \
	"${MESHTASTICD_IMAGE}" \
	meshtasticd -s --fsdir=/var/lib/meshtasticd-b -p "${MESHTASTICD_PORT_B_DEC}" -h "${MESHTASTICD_HWID_B}" >/dev/null

wait_for_meshtasticd_ready "${MESHTASTICD_HOST_A}" "${MESHTASTICD_PORT_A}" "${MESHTASTICD_CONTAINER_A}"
wait_for_meshtasticd_ready "${MESHTASTICD_HOST_B}" "${MESHTASTICD_PORT_B}" "${MESHTASTICD_CONTAINER_B}"

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
user_localpart = os.environ["MATRIX_USER_LOCALPART"]
bot_a_password = os.environ["MATRIX_BOT_A_PASSWORD"]
bot_b_password = os.environ["MATRIX_BOT_B_PASSWORD"]
user_password = os.environ["MATRIX_USER_PASSWORD"]

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

bot_a = login(bot_a_localpart, bot_a_password)
bot_b = login(bot_b_localpart, bot_b_password)
user = login(user_localpart, user_password)

room_suffix = int(time.time())
create_payload = {
    "preset": "private_chat",
    "name": "MMRelay Cross-Mesh CI Room",
    "topic": "MMRelay cross-mesh integration test",
    "room_alias_name": f"mmrelay-ci-cross-mesh-{room_suffix}",
}
room_create = post("/_matrix/client/v3/createRoom", user["access_token"], create_payload)
room_id = room_create["room_id"]

# Invite both bots
for bot in [bot_a, bot_b]:
    quoted_room_id = urllib.parse.quote(room_id, safe="")
    post(
        f"/_matrix/client/v3/rooms/{quoted_room_id}/invite",
        user["access_token"],
        {"user_id": bot["user_id"]},
    )
    post(
        f"/_matrix/client/v3/join/{quoted_room_id}",
        bot["access_token"],
        {},
    )

sync_response = requests.get(
    f"{base_url}/_matrix/client/v3/sync",
    headers={"Authorization": f"Bearer {user['access_token']}"},
    params={"timeout": 0},
    timeout=20,
)
_raise_for_status(sync_response, "initial user sync")
initial_sync = sync_response.json()

runtime = {
    "room_id": room_id,
    "bot_a_user_id": bot_a["user_id"],
    "bot_a_access_token": bot_a["access_token"],
    "bot_b_user_id": bot_b["user_id"],
    "bot_b_access_token": bot_b["access_token"],
    "user_access_token": user["access_token"],
    "sync_since": initial_sync.get("next_batch", ""),
}

json.dump(runtime, fp=sys.stdout)
PY

BOT_A_USER_ID="$(load_json_value bot_a_user_id)"
BOT_A_ACCESS_TOKEN="$(load_json_value bot_a_access_token)"
BOT_B_USER_ID="$(load_json_value bot_b_user_id)"
BOT_B_ACCESS_TOKEN="$(load_json_value bot_b_access_token)"
USER_ACCESS_TOKEN="$(load_json_value user_access_token)"
ROOM_ID="$(load_json_value room_id)"
SYNC_SINCE="$(load_json_value sync_since)"

export BOT_A_USER_ID
export BOT_A_ACCESS_TOKEN
export BOT_B_USER_ID
export BOT_B_ACCESS_TOKEN
export USER_ACCESS_TOKEN
export ROOM_ID
export SYNC_SINCE

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
  meshnet_name: "Mesh A"
  broadcast_enabled: true
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
  meshnet_name: "Mesh B"
  broadcast_enabled: true
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
[[ -f ${MMRELAY_LOG_PATH_A} ]] && startup_offset_a=$(wc -c <"${MMRELAY_LOG_PATH_A}")
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
[[ -f ${MMRELAY_LOG_PATH_B} ]] && startup_offset_b=$(wc -c <"${MMRELAY_LOG_PATH_B}")
wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"Listening for inbound Matrix messages..." \
	"${startup_offset_b}" \
	$((10#${MMRELAY_READY_TIMEOUT_SECONDS}))
echo "MMRelay B is ready (PID ${MMRELAY_PID_B})"

# =============================================================================
# Test Scenarios
# =============================================================================

echo ""
echo "============================================================================"
echo "Running Test Scenarios"
echo "============================================================================"

# Test 1: Matrix → Mesh A
MATRIX_TO_MESH_A_TEXT="MMRELAY_CI_M2A_$(date +%s)_${RANDOM}"
export MATRIX_TO_MESH_A_TEXT
log_offset_before_m2a=$(wc -c <"${MMRELAY_LOG_PATH_A}")

echo ""
echo "Test 1: Matrix → Mesh A..."
"${PYTHON_BIN}" - <<'PY'
import os
import urllib.parse
import requests

base_url = os.environ["MATRIX_BASE_URL"]
room_id = os.environ["ROOM_ID"]
user_access_token = os.environ["USER_ACCESS_TOKEN"]
message_text = os.environ["MATRIX_TO_MESH_A_TEXT"]

txn_id = f"mmrelay-ci-m2a-{message_text}"
quoted_room_id = urllib.parse.quote(room_id, safe="")
quoted_txn_id = urllib.parse.quote(txn_id, safe="")
response = requests.put(
    f"{base_url}/_matrix/client/v3/rooms/{quoted_room_id}/send/m.room.message/{quoted_txn_id}",
    headers={"Authorization": f"Bearer {user_access_token}"},
    json={"msgtype": "m.text", "body": message_text},
    timeout=20,
)
if response.status_code >= 400:
    raise RuntimeError(
        f"Failed to send Matrix test message ({response.status_code}): {response.text}"
    )
PY

if wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_A}" \
	"Relaying message from" \
	"${log_offset_before_m2a}" \
	45; then
	echo "✓ Test 1 PASSED: Matrix → Mesh A"
else
	echo "✗ Test 1 FAILED: Matrix → Mesh A" >&2
	exit 1
fi

# Test 2: Matrix → Mesh B
MATRIX_TO_MESH_B_TEXT="MMRELAY_CI_M2B_$(date +%s)_${RANDOM}"
export MATRIX_TO_MESH_B_TEXT
log_offset_before_m2b=$(wc -c <"${MMRELAY_LOG_PATH_B}")

echo ""
echo "Test 2: Matrix → Mesh B..."
"${PYTHON_BIN}" - <<'PY'
import os
import urllib.parse
import requests

base_url = os.environ["MATRIX_BASE_URL"]
room_id = os.environ["ROOM_ID"]
user_access_token = os.environ["USER_ACCESS_TOKEN"]
message_text = os.environ["MATRIX_TO_MESH_B_TEXT"]

txn_id = f"mmrelay-ci-m2b-{message_text}"
quoted_room_id = urllib.parse.quote(room_id, safe="")
quoted_txn_id = urllib.parse.quote(txn_id, safe="")
response = requests.put(
    f"{base_url}/_matrix/client/v3/rooms/{quoted_room_id}/send/m.room.message/{quoted_txn_id}",
    headers={"Authorization": f"Bearer {user_access_token}"},
    json={"msgtype": "m.text", "body": message_text},
    timeout=20,
)
if response.status_code >= 400:
    raise RuntimeError(
        f"Failed to send Matrix test message ({response.status_code}): {response.text}"
    )
PY

if wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"Relaying message from" \
	"${log_offset_before_m2b}" \
	45; then
	echo "✓ Test 2 PASSED: Matrix → Mesh B"
else
	echo "✗ Test 2 FAILED: Matrix → Mesh B" >&2
	exit 1
fi

# Test 3: Mesh A → Matrix
MESH_A_TO_MATRIX_TEXT="MMRELAY_CI_A2M_$(date +%s)_${RANDOM}"
export MESH_A_TO_MATRIX_TEXT
log_offset_before_a2m=$(wc -c <"${MMRELAY_LOG_PATH_A}")

echo ""
echo "Test 3: Mesh A → Matrix..."
"${PYTHON_BIN}" -m meshtastic \
	--timeout 10 \
	--host "${MESHTASTICD_HOST_A}" \
	--port "${MESHTASTICD_PORT_A}" \
	--sendtext "${MESH_A_TO_MATRIX_TEXT}" >/dev/null

if wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_A}" \
	"[SIMULATOR_APP]" \
	"${log_offset_before_a2m}" \
	45; then
	echo "  MMRelay A received packet from Mesh A"
else
	echo "  MMRelay A did not receive packet from Mesh A" >&2
	exit 1
fi

# Verify message reached Matrix
echo "  Checking Matrix timeline..."
if ! "${PYTHON_BIN}" - <<'PY'; then
import os
import time
import requests

base_url = os.environ["MATRIX_BASE_URL"]
room_id = os.environ["ROOM_ID"]
user_access_token = os.environ["USER_ACCESS_TOKEN"]
message_text = os.environ["MESH_A_TO_MATRIX_TEXT"]
since = os.environ.get("SYNC_SINCE", "")

headers = {"Authorization": f"Bearer {user_access_token}"}
deadline = time.monotonic() + 45
last_events: list[str] = []

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

    body = response.json()
    since = body.get("next_batch", since)
    rooms = body.get("rooms", {}).get("join", {})
    room_data = rooms.get(room_id, {})
    events = room_data.get("timeline", {}).get("events", [])

    for event in events:
        if event.get("type") != "m.room.message":
            continue
        content = event.get("content", {})
        body_text = content.get("body", "")
        if isinstance(body_text, str):
            last_events.append(body_text)
            if message_text in body_text:
                raise SystemExit(0)

    if len(last_events) > 10:
        last_events = last_events[-10:]

raise SystemExit(1)
PY
	echo "✓ Test 3 PASSED: Mesh A → Matrix"
else
	echo "✗ Test 3 FAILED: Mesh A → Matrix" >&2
	case "${STRICT_MESH_TO_MATRIX,,}" in
	1 | true | yes | on)
		exit 1
		;;
	*)
		echo "  [WARNING] STRICT_MESH_TO_MATRIX is disabled, continuing..."
		;;
	esac
fi

# Test 4: Mesh B → Matrix
MESH_B_TO_MATRIX_TEXT="MMRELAY_CI_B2M_$(date +%s)_${RANDOM}"
export MESH_B_TO_MATRIX_TEXT
log_offset_before_b2m=$(wc -c <"${MMRELAY_LOG_PATH_B}")

echo ""
echo "Test 4: Mesh B → Matrix..."
"${PYTHON_BIN}" -m meshtastic \
	--timeout 10 \
	--host "${MESHTASTICD_HOST_B}" \
	--port "${MESHTASTICD_PORT_B}" \
	--sendtext "${MESH_B_TO_MATRIX_TEXT}" >/dev/null

if wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"[SIMULATOR_APP]" \
	"${log_offset_before_b2m}" \
	45; then
	echo "  MMRelay B received packet from Mesh B"
else
	echo "  MMRelay B did not receive packet from Mesh B" >&2
	exit 1
fi

# Verify message reached Matrix
echo "  Checking Matrix timeline..."
if ! "${PYTHON_BIN}" - <<'PY'; then
import os
import time
import requests

base_url = os.environ["MATRIX_BASE_URL"]
room_id = os.environ["ROOM_ID"]
user_access_token = os.environ["USER_ACCESS_TOKEN"]
message_text = os.environ["MESH_B_TO_MATRIX_TEXT"]
since = os.environ.get("SYNC_SINCE", "")

headers = {"Authorization": f"Bearer {user_access_token}"}
deadline = time.monotonic() + 45
last_events: list[str] = []

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

    body = response.json()
    since = body.get("next_batch", since)
    rooms = body.get("rooms", {}).get("join", {})
    room_data = rooms.get(room_id, {})
    events = room_data.get("timeline", {}).get("events", [])

    for event in events:
        if event.get("type") != "m.room.message":
            continue
        content = event.get("content", {})
        body_text = content.get("body", "")
        if isinstance(body_text, str):
            last_events.append(body_text)
            if message_text in body_text:
                raise SystemExit(0)

    if len(last_events) > 10:
        last_events = last_events[-10:]

raise SystemExit(1)
PY
	echo "✓ Test 4 PASSED: Mesh B → Matrix"
else
	echo "✗ Test 4 FAILED: Mesh B → Matrix" >&2
	case "${STRICT_MESH_TO_MATRIX,,}" in
	1 | true | yes | on)
		exit 1
		;;
	*)
		echo "  [WARNING] STRICT_MESH_TO_MATRIX is disabled, continuing..."
		;;
	esac
fi

# Test 5: Mesh A → Mesh B (via Matrix bridge)
MESH_A_TO_B_TEXT="MMRELAY_CI_A2B_$(date +%s)_${RANDOM}"
export MESH_A_TO_B_TEXT
log_offset_before_a2b_a=$(wc -c <"${MMRELAY_LOG_PATH_A}")
log_offset_before_a2b_b=$(wc -c <"${MMRELAY_LOG_PATH_B}")

echo ""
echo "Test 5: Mesh A → Mesh B (cross-mesh via Matrix bridge)..."
echo "  Sending from Mesh A..."
"${PYTHON_BIN}" -m meshtastic \
	--timeout 10 \
	--host "${MESHTASTICD_HOST_A}" \
	--port "${MESHTASTICD_PORT_A}" \
	--sendtext "${MESH_A_TO_B_TEXT}" >/dev/null

# Wait for MMRelay A to receive and relay to Matrix
if wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_A}" \
	"[SIMULATOR_APP]" \
	"${log_offset_before_a2b_a}" \
	60; then
	echo "  MMRelay A received packet from Mesh A"
else
	echo "  MMRelay A did not receive packet from Mesh A" >&2
	exit 1
fi

# Wait for MMRelay B to receive from Matrix and relay to Mesh B
if wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"Relaying message from" \
	"${log_offset_before_a2b_b}" \
	60; then
	echo "  MMRelay B relayed to Mesh B"
else
	echo "  MMRelay B did not relay to Mesh B" >&2
	case "${STRICT_MESH_TO_MATRIX,,}" in
	1 | true | yes | on)
		exit 1
		;;
	*)
		echo "  [WARNING] STRICT_MESH_TO_MATRIX is disabled, continuing..."
		;;
	esac
fi

echo "✓ Test 5 PASSED: Mesh A → Mesh B"

# Test 6: Mesh B → Mesh A (via Matrix bridge)
MESH_B_TO_A_TEXT="MMRELAY_CI_B2A_$(date +%s)_${RANDOM}"
export MESH_B_TO_A_TEXT
log_offset_before_b2a_a=$(wc -c <"${MMRELAY_LOG_PATH_A}")
log_offset_before_b2a_b=$(wc -c <"${MMRELAY_LOG_PATH_B}")

echo ""
echo "Test 6: Mesh B → Mesh A (cross-mesh via Matrix bridge)..."
echo "  Sending from Mesh B..."
"${PYTHON_BIN}" -m meshtastic \
	--timeout 10 \
	--host "${MESHTASTICD_HOST_B}" \
	--port "${MESHTASTICD_PORT_B}" \
	--sendtext "${MESH_B_TO_A_TEXT}" >/dev/null

# Wait for MMRelay B to receive and relay to Matrix
if wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_B}" \
	"[SIMULATOR_APP]" \
	"${log_offset_before_b2a_b}" \
	60; then
	echo "  MMRelay B received packet from Mesh B"
else
	echo "  MMRelay B did not receive packet from Mesh B" >&2
	exit 1
fi

# Wait for MMRelay A to receive from Matrix and relay to Mesh A
if wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH_A}" \
	"Relaying message from" \
	"${log_offset_before_b2a_a}" \
	60; then
	echo "  MMRelay A relayed to Mesh A"
else
	echo "  MMRelay A did not relay to Mesh A" >&2
	case "${STRICT_MESH_TO_MATRIX,,}" in
	1 | true | yes | on)
		exit 1
		;;
	*)
		echo "  [WARNING] STRICT_MESH_TO_MATRIX is disabled, continuing..."
		;;
	esac
fi

echo "✓ Test 6 PASSED: Mesh B → Mesh A"

# =============================================================================
# Success
# =============================================================================

echo ""
echo "============================================================================"
echo "All test scenarios passed!"
echo "============================================================================"
echo ""
echo "Cross-mesh integration tests completed successfully."
echo "Artifacts written to: ${CI_ARTIFACT_DIR}"
