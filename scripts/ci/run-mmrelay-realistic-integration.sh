#!/usr/bin/env bash

set -euo pipefail

MESHTASTICD_IMAGE="${MESHTASTICD_IMAGE:-meshtastic/meshtasticd:latest}"
MESHTASTICD_CONTAINER="${MESHTASTICD_CONTAINER:-mmrelay-ci-meshtasticd}"
MESHTASTICD_CONTAINER_B="${MESHTASTICD_CONTAINER_B:-mmrelay-ci-meshtasticd-b}"
MESHTASTICD_HOST="${MESHTASTICD_HOST:-localhost}"
MESHTASTICD_HOST_B="${MESHTASTICD_HOST_B:-localhost:4404}"
MESHTASTICD_PORT="${MESHTASTICD_PORT:-4403}"
MESHTASTICD_PORT_B="${MESHTASTICD_PORT_B:-4404}"
MESHTASTICD_HWID="${MESHTASTICD_HWID:-11}"
MESHTASTICD_HWID_B="${MESHTASTICD_HWID_B:-22}"
MESHTASTICD_READY_TIMEOUT_SECONDS="${MESHTASTICD_READY_TIMEOUT_SECONDS:-180}"

SYNAPSE_IMAGE="${SYNAPSE_IMAGE:-matrixdotorg/synapse:latest}"
SYNAPSE_CONTAINER="${SYNAPSE_CONTAINER:-mmrelay-ci-synapse}"
SYNAPSE_PORT="${SYNAPSE_PORT:-8008}"
SYNAPSE_SERVER_NAME="${SYNAPSE_SERVER_NAME:-localhost}"
SYNAPSE_READY_TIMEOUT_SECONDS="${SYNAPSE_READY_TIMEOUT_SECONDS:-180}"

MMRELAY_READY_TIMEOUT_SECONDS="${MMRELAY_READY_TIMEOUT_SECONDS:-120}"
MMRELAY_LOG_ON_SUCCESS="${MMRELAY_LOG_ON_SUCCESS:-false}"
STRICT_MESH_TO_MATRIX="${STRICT_MESH_TO_MATRIX:-false}"
PYTHON_BIN="${PYTHON_BIN:-python}"

CI_ARTIFACT_DIR="${CI_ARTIFACT_DIR:-${PWD}/.ci-artifacts/realistic-integration}"
SYNAPSE_DATA_DIR="${CI_ARTIFACT_DIR}/synapse-data"
MMRELAY_HOME_DIR="${CI_ARTIFACT_DIR}/mmrelay-home"
MMRELAY_CONFIG_PATH="${CI_ARTIFACT_DIR}/mmrelay-config.yaml"
MMRELAY_LOG_PATH="${CI_ARTIFACT_DIR}/mmrelay.log"
MESHTASTICD_LOG_PATH="${CI_ARTIFACT_DIR}/meshtasticd-a.log"
MESHTASTICD_LOG_PATH_B="${CI_ARTIFACT_DIR}/meshtasticd-b.log"
SYNAPSE_LOG_PATH="${CI_ARTIFACT_DIR}/synapse.log"
MATRIX_RUNTIME_JSON="${CI_ARTIFACT_DIR}/matrix-runtime.json"

MMRELAY_PID=""
LOGS_PRINTED=false

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
	echo "===== MMRelay log ====="
	if [[ -f ${MMRELAY_LOG_PATH} ]]; then
		cat "${MMRELAY_LOG_PATH}" || true
	fi
	echo "===== meshtasticd log ====="
	if [[ -f ${MESHTASTICD_LOG_PATH} ]]; then
		cat "${MESHTASTICD_LOG_PATH}" || true
	fi
	echo "===== meshtasticd-b log ====="
	if [[ -f ${MESHTASTICD_LOG_PATH_B} ]]; then
		cat "${MESHTASTICD_LOG_PATH_B}" || true
	fi
	echo "===== Synapse log ====="
	if [[ -f ${SYNAPSE_LOG_PATH} ]]; then
		cat "${SYNAPSE_LOG_PATH}" || true
	fi
}

cleanup() {
	local exit_code=$?

	if [[ -n ${MMRELAY_PID} ]] && kill -0 "${MMRELAY_PID}" >/dev/null 2>&1; then
		kill "${MMRELAY_PID}" >/dev/null 2>&1 || true
		wait "${MMRELAY_PID}" >/dev/null 2>&1 || true
	fi

	if docker ps -a --format '{{.Names}}' | grep -Fxq "${MESHTASTICD_CONTAINER}"; then
		docker logs "${MESHTASTICD_CONTAINER}" >"${MESHTASTICD_LOG_PATH}" 2>&1 || true
		docker rm -f "${MESHTASTICD_CONTAINER}" >/dev/null 2>&1 || true
	fi
	if docker ps -a --format '{{.Names}}' | grep -Fxq "${MESHTASTICD_CONTAINER_B}"; then
		docker logs "${MESHTASTICD_CONTAINER_B}" >"${MESHTASTICD_LOG_PATH_B}" 2>&1 || true
		docker rm -f "${MESHTASTICD_CONTAINER_B}" >/dev/null 2>&1 || true
	fi

	if docker ps -a --format '{{.Names}}' | grep -Fxq "${SYNAPSE_CONTAINER}"; then
		docker logs "${SYNAPSE_CONTAINER}" >"${SYNAPSE_LOG_PATH}" 2>&1 || true
		docker rm -f "${SYNAPSE_CONTAINER}" >/dev/null 2>&1 || true
	fi

	print_logs_if_needed "${exit_code}"
	exit "${exit_code}"
}

wait_for_meshtasticd_ready() {
	local host=$1
	local container=$2
	local deadline=$((SECONDS + 10#${MESHTASTICD_READY_TIMEOUT_SECONDS}))
	until "${PYTHON_BIN}" -m meshtastic --timeout 5 --host "${host}" --info >/dev/null 2>&1; do
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
		if [[ -n ${MMRELAY_PID} ]] && ! kill -0 "${MMRELAY_PID}" >/dev/null 2>&1; then
			echo "MMRelay process exited unexpectedly while waiting for '${pattern}'." >&2
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

trap cleanup EXIT

if ! command -v docker >/dev/null 2>&1; then
	echo "docker is required for realistic integration checks." >&2
	exit 1
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
	echo "Python runtime '${PYTHON_BIN}' is required." >&2
	exit 1
fi

require_regex "${MESHTASTICD_CONTAINER}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "MESHTASTICD_CONTAINER"
require_regex "${MESHTASTICD_CONTAINER_B}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "MESHTASTICD_CONTAINER_B"
require_regex "${MESHTASTICD_IMAGE}" '^[^[:space:]]+$' "MESHTASTICD_IMAGE"
require_regex "${MESHTASTICD_PORT}" '^[0-9]+$' "MESHTASTICD_PORT"
require_regex "${MESHTASTICD_PORT_B}" '^[0-9]+$' "MESHTASTICD_PORT_B"
require_regex "${MESHTASTICD_HWID}" '^[0-9]+$' "MESHTASTICD_HWID"
require_regex "${MESHTASTICD_HWID_B}" '^[0-9]+$' "MESHTASTICD_HWID_B"
require_regex "${MESHTASTICD_READY_TIMEOUT_SECONDS}" '^[0-9]+$' "MESHTASTICD_READY_TIMEOUT_SECONDS"
require_regex "${SYNAPSE_CONTAINER}" '^[A-Za-z0-9][A-Za-z0-9_.-]*$' "SYNAPSE_CONTAINER"
require_regex "${SYNAPSE_IMAGE}" '^[^[:space:]]+$' "SYNAPSE_IMAGE"
require_regex "${SYNAPSE_PORT}" '^[0-9]+$' "SYNAPSE_PORT"
require_regex "${SYNAPSE_READY_TIMEOUT_SECONDS}" '^[0-9]+$' "SYNAPSE_READY_TIMEOUT_SECONDS"
require_regex "${MMRELAY_READY_TIMEOUT_SECONDS}" '^[0-9]+$' "MMRELAY_READY_TIMEOUT_SECONDS"

MESHTASTICD_PORT_DEC=$((10#${MESHTASTICD_PORT}))
MESHTASTICD_PORT_B_DEC=$((10#${MESHTASTICD_PORT_B}))
SYNAPSE_PORT_DEC=$((10#${SYNAPSE_PORT}))
if ((MESHTASTICD_PORT_DEC < 1 || MESHTASTICD_PORT_DEC > 65535)); then
	echo "MESHTASTICD_PORT must be between 1 and 65535." >&2
	exit 1
fi
if ((MESHTASTICD_PORT_B_DEC < 1 || MESHTASTICD_PORT_B_DEC > 65535)); then
	echo "MESHTASTICD_PORT_B must be between 1 and 65535." >&2
	exit 1
fi
if ((MESHTASTICD_PORT_DEC != 4403)); then
	echo "MESHTASTICD_PORT must be 4403 for MMRelay TCP integration checks." >&2
	exit 1
fi
if ((MESHTASTICD_PORT_B_DEC != 4404)); then
	echo "MESHTASTICD_PORT_B must be 4404 for MMRelay TCP integration checks." >&2
	exit 1
fi
if ((SYNAPSE_PORT_DEC < 1 || SYNAPSE_PORT_DEC > 65535)); then
	echo "SYNAPSE_PORT must be between 1 and 65535." >&2
	exit 1
fi

if [[ -d ${CI_ARTIFACT_DIR} ]]; then
	chmod -R u+rwX "${CI_ARTIFACT_DIR}" >/dev/null 2>&1 || true
	if ! rm -rf "${CI_ARTIFACT_DIR}"; then
		# If a prior run created root-owned files, fix ownership through Docker then retry.
		docker run --rm \
			--user root \
			-v "${CI_ARTIFACT_DIR}:/work" \
			"alpine:3.22" \
			/bin/sh -c "chown -R $(id -u):$(id -g) /work" >/dev/null
		rm -rf "${CI_ARTIFACT_DIR}"
	fi
fi
mkdir -p "${CI_ARTIFACT_DIR}" "${SYNAPSE_DATA_DIR}" "${MMRELAY_HOME_DIR}"

MATRIX_BASE_URL="http://localhost:${SYNAPSE_PORT_DEC}"
MATRIX_BOT_USER_LOCALPART="mmrelaybot"
MATRIX_USER_LOCALPART="mmrelayuser"
MATRIX_BOT_PASSWORD="mmrelay-bot-pass"
MATRIX_USER_PASSWORD="mmrelay-user-pass"
export MATRIX_BASE_URL
export MATRIX_BOT_USER_LOCALPART
export MATRIX_USER_LOCALPART
export MATRIX_BOT_PASSWORD
export MATRIX_USER_PASSWORD

# Ensure a clean slate from previous runs.
docker rm -f \
	"${MESHTASTICD_CONTAINER}" \
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

echo "Starting meshtasticd containers..."
docker run -d \
	--name "${MESHTASTICD_CONTAINER}" \
	--network host \
	"${MESHTASTICD_IMAGE}" \
	meshtasticd -s --fsdir=/var/lib/meshtasticd-a -p "${MESHTASTICD_PORT_DEC}" -h "${MESHTASTICD_HWID}" >/dev/null

docker run -d \
	--name "${MESHTASTICD_CONTAINER_B}" \
	--network host \
	"${MESHTASTICD_IMAGE}" \
	meshtasticd -s --fsdir=/var/lib/meshtasticd-b -p "${MESHTASTICD_PORT_B_DEC}" -h "${MESHTASTICD_HWID_B}" >/dev/null

wait_for_meshtasticd_ready "${MESHTASTICD_HOST}" "${MESHTASTICD_CONTAINER}"
wait_for_meshtasticd_ready "${MESHTASTICD_HOST_B}" "${MESHTASTICD_CONTAINER_B}"

echo "Pulling Synapse image: ${SYNAPSE_IMAGE}"
docker pull "${SYNAPSE_IMAGE}"

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

echo "Creating Matrix users..."
docker exec "${SYNAPSE_CONTAINER}" register_new_matrix_user \
	-u "${MATRIX_BOT_USER_LOCALPART}" \
	-p "${MATRIX_BOT_PASSWORD}" \
	-a \
	-c /data/homeserver.yaml \
	"http://localhost:8008" >/dev/null

docker exec "${SYNAPSE_CONTAINER}" register_new_matrix_user \
	-u "${MATRIX_USER_LOCALPART}" \
	-p "${MATRIX_USER_PASSWORD}" \
	--no-admin \
	-c /data/homeserver.yaml \
	"http://localhost:8008" >/dev/null

echo "Preparing Matrix room and runtime credentials..."
"${PYTHON_BIN}" - <<'PY' >"${MATRIX_RUNTIME_JSON}"
import json
import os
import sys
import time
import urllib.parse

import requests

base_url = os.environ["MATRIX_BASE_URL"]
bot_localpart = os.environ["MATRIX_BOT_USER_LOCALPART"]
user_localpart = os.environ["MATRIX_USER_LOCALPART"]
bot_password = os.environ["MATRIX_BOT_PASSWORD"]
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

bot = login(bot_localpart, bot_password)
user = login(user_localpart, user_password)

room_suffix = int(time.time())
create_payload = {
    "preset": "private_chat",
    "name": "MMRelay CI Room",
    "topic": "MMRelay realistic integration",
    "room_alias_name": f"mmrelay-ci-{room_suffix}",
}
room_create = post("/_matrix/client/v3/createRoom", user["access_token"], create_payload)
room_id = room_create["room_id"]

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
    "bot_user_id": bot["user_id"],
    "bot_access_token": bot["access_token"],
    "user_access_token": user["access_token"],
    "sync_since": initial_sync.get("next_batch", ""),
}

json.dump(runtime, fp=sys.stdout)
PY

BOT_USER_ID="$(load_json_value bot_user_id)"
BOT_ACCESS_TOKEN="$(load_json_value bot_access_token)"
USER_ACCESS_TOKEN="$(load_json_value user_access_token)"
ROOM_ID="$(load_json_value room_id)"
SYNC_SINCE="$(load_json_value sync_since)"
export BOT_USER_ID
export BOT_ACCESS_TOKEN
export USER_ACCESS_TOKEN
export ROOM_ID
export SYNC_SINCE

cat >"${MMRELAY_CONFIG_PATH}" <<EOF_CONFIG
matrix:
  homeserver: "${MATRIX_BASE_URL}"
  access_token: "${BOT_ACCESS_TOKEN}"
  bot_user_id: "${BOT_USER_ID}"
matrix_rooms:
  - id: "${ROOM_ID}"
    meshtastic_channel: 0
meshtastic:
  connection_type: tcp
  host: "${MESHTASTICD_HOST}"
  meshnet_name: "MMRelay CI Mesh"
  broadcast_enabled: true
logging:
  level: debug
EOF_CONFIG

echo "Starting MMRelay process..."
PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -m mmrelay.cli \
	--config "${MMRELAY_CONFIG_PATH}" \
	--home "${MMRELAY_HOME_DIR}" \
	--log-level debug >"${MMRELAY_LOG_PATH}" 2>&1 &
MMRELAY_PID=$!

startup_offset=0
wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH}" \
	"Listening for inbound Matrix messages..." \
	"${startup_offset}" \
	$((10#${MMRELAY_READY_TIMEOUT_SECONDS}))

MATRIX_TO_MESH_TEXT="MMRELAY_CI_M2M_$(date +%s)_${RANDOM}"
export MATRIX_TO_MESH_TEXT
log_offset_before_matrix_send=$(wc -c <"${MMRELAY_LOG_PATH}")

echo "Sending Matrix -> Meshtastic test message..."
"${PYTHON_BIN}" - <<'PY'
import os
import urllib.parse

import requests

base_url = os.environ["MATRIX_BASE_URL"]
room_id = os.environ["ROOM_ID"]
user_access_token = os.environ["USER_ACCESS_TOKEN"]
message_text = os.environ["MATRIX_TO_MESH_TEXT"]

txn_id = f"mmrelay-ci-m2m-{message_text}"
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

wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH}" \
	"Relaying message from" \
	"${log_offset_before_matrix_send}" \
	45

MESH_TO_MATRIX_TEXT="MMRELAY_CI_MESH_$(date +%s)_${RANDOM}"
export MESH_TO_MATRIX_TEXT
echo "Sending Meshtastic -> Matrix test message..."
log_offset_before_mesh_send=$(wc -c <"${MMRELAY_LOG_PATH}")
"${PYTHON_BIN}" -m meshtastic \
	--timeout 10 \
	--host "${MESHTASTICD_HOST_B}" \
	--sendtext "${MESH_TO_MATRIX_TEXT}" >/dev/null

wait_for_log_pattern_since \
	"${MMRELAY_LOG_PATH}" \
	"[SIMULATOR_APP]" \
	"${log_offset_before_mesh_send}" \
	45

echo "Checking whether Meshtastic message reaches Matrix timeline..."
if ! "${PYTHON_BIN}" - <<'PY'; then
import os
import time

import requests

base_url = os.environ["MATRIX_BASE_URL"]
room_id = os.environ["ROOM_ID"]
user_access_token = os.environ["USER_ACCESS_TOKEN"]
message_text = os.environ["MESH_TO_MATRIX_TEXT"]
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
	case "${STRICT_MESH_TO_MATRIX,,}" in
	1 | true | yes | on)
		echo "Meshtastic->Matrix delivery check failed and STRICT_MESH_TO_MATRIX is enabled." >&2
		exit 1
		;;
	*)
		echo "##[warning]Meshtastic packet ingress was observed, but end-to-end Meshtastic->Matrix delivery was not observed within the timeout." >&2
		;;
	esac
fi

echo "Realistic integration checks passed."
echo "Artifacts written to: ${CI_ARTIFACT_DIR}"
