#!/usr/bin/env bash
set -euo pipefail

RUN_NAME="$1"
NOTES_FILE="$2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../config.sh"

TARGET_IP="${VICTIM_IP:-192.168.9.131}"
TARGET_USER="${TARGET_USER:-msfadmin}"
TARGET_PASS="${TARGET_PASS:-msfadmin}"
KALI_IP="${KALI_IP:-$(ip route get "$TARGET_IP" | grep -oP 'src \K\S+')}"

ATTACK_CATEGORY="payload_delivery"
ATTACK_SUBTYPE="http"
PROFILE_ID="${PROFILE_ID:-http_payload_medium_repeat}"

ATTACK_DURATION=300
PRE_IDLE=9
POST_IDLE=9

SEED="${SEED:-$RANDOM}"
RANDOM="$SEED"

PROTOCOL="http"
TOOL="python_http_curl"
HTTP_PORT="${HTTP_PORT:-18080}"

export SSHPASS="$TARGET_PASS"

SSH_OPTS=(
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o LogLevel=ERROR
  -o HostKeyAlgorithms=+ssh-rsa,ssh-dss
  -o ConnectTimeout=8
)

rand_int() {
  local min="$1"
  local max="$2"
  echo $(( RANDOM % (max - min + 1) + min ))
}

log_kv() {
  echo "$1=$2" | tee -a "$NOTES_FILE"
}

log_time() {
  echo "$1 $(date -u +'%Y-%m-%d %H:%M:%S UTC')" | tee -a "$NOTES_FILE"
}

configure_profile() {
  case "$PROFILE_ID" in
    http_payload_small_pull)
      FILE_SIZE_MB=3
      SLEEP_MIN=10
      SLEEP_MAX=20
      LOCAL_BASENAME="kali_payload_small_${RUN_NAME}.bin"
      MODULE_SUMMARY="http_payload_small_pull"
      ;;
    http_payload_medium_repeat)
      FILE_SIZE_MB=20
      SLEEP_MIN=4
      SLEEP_MAX=8
      LOCAL_BASENAME="kali_payload_medium_${RUN_NAME}.bin"
      MODULE_SUMMARY="http_payload_medium_repeat"
      ;;
    http_payload_large_burst)
      FILE_SIZE_MB=80
      SLEEP_MIN=1
      SLEEP_MAX=3
      LOCAL_BASENAME="kali_payload_large_${RUN_NAME}.bin"
      MODULE_SUMMARY="http_payload_large_burst"
      ;;
    *)
      echo "[-] Unknown PROFILE_ID: $PROFILE_ID" >&2
      echo "    Allowed: http_payload_small_pull, http_payload_medium_repeat, http_payload_large_burst" >&2
      exit 1
      ;;
  esac
}

configure_profile

LOCAL_FILE="/tmp/${LOCAL_BASENAME}"
HTTP_URL="http://${KALI_IP}:${HTTP_PORT}/${LOCAL_BASENAME}"
DEBUG_LOG="/tmp/payload_http_debug_${RUN_NAME}.log"
HTTP_SERVER_LOG="/tmp/http_server_${RUN_NAME}.log"
: > "$DEBUG_LOG"
: > "$HTTP_SERVER_LOG"

log_kv RUN_ID "$RUN_NAME"
log_kv ATTACK_CATEGORY "$ATTACK_CATEGORY"
log_kv ATTACK_SUBTYPE "$ATTACK_SUBTYPE"
log_kv PROFILE_ID "$PROFILE_ID"
log_kv RANDOM_SEED "$SEED"
log_kv PROTOCOL "$PROTOCOL"
log_kv TOOL "$TOOL"
log_kv FILE_SIZE_MB "$FILE_SIZE_MB"
log_kv SLEEP_MIN "$SLEEP_MIN"
log_kv SLEEP_MAX "$SLEEP_MAX"
log_kv ATTACK_DURATION "$ATTACK_DURATION"
log_kv PRE_IDLE "$PRE_IDLE"
log_kv POST_IDLE "$POST_IDLE"
log_kv KALI_IP "$KALI_IP"
log_kv HTTP_PORT "$HTTP_PORT"
log_kv LOCAL_FILE "$LOCAL_FILE"
log_kv LOCAL_BASENAME "$LOCAL_BASENAME"
log_kv HTTP_URL "$HTTP_URL"
log_kv MODULE_SUMMARY "$MODULE_SUMMARY"

echo "[*] HTTP payload delivery started: run=$RUN_NAME profile=$PROFILE_ID seed=$SEED" | tee -a "$DEBUG_LOG"

cleanup() {
  echo "[CLEANUP] stopping HTTP server and removing local payload" >> "$DEBUG_LOG" 2>/dev/null || true
  if [[ -n "${HTTP_SERVER_PID:-}" ]]; then
    kill "$HTTP_SERVER_PID" 2>/dev/null || true
  fi
  rm -f "$LOCAL_FILE" || true
}
trap cleanup EXIT

echo "[PREP] creating local payload: $LOCAL_FILE size=${FILE_SIZE_MB}MB" >> "$DEBUG_LOG"
rm -f "$LOCAL_FILE" || true
dd if=/dev/urandom of="$LOCAL_FILE" bs=1M count="$FILE_SIZE_MB" >> "$DEBUG_LOG" 2>&1
ls -lh "$LOCAL_FILE" >> "$DEBUG_LOG" 2>&1
test -s "$LOCAL_FILE"

echo "[PREP] starting local HTTP server on port $HTTP_PORT" >> "$DEBUG_LOG"

# 避免旧 server 占用端口
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${HTTP_PORT}/tcp" >> "$DEBUG_LOG" 2>&1 || true
fi

python3 -m http.server "$HTTP_PORT" --directory /tmp > "$HTTP_SERVER_LOG" 2>&1 &
HTTP_SERVER_PID=$!

sleep 2

if ! kill -0 "$HTTP_SERVER_PID" 2>/dev/null; then
  echo "[-] HTTP server failed to start. Check $HTTP_SERVER_LOG" >> "$DEBUG_LOG"
  exit 1
fi

echo "[CHECK] local HTTP server process is running: pid=$HTTP_SERVER_PID" >> "$DEBUG_LOG"

# 只做本机轻量 HEAD 检查，不让靶机提前下载 payload，避免污染 normal 窗口
curl -f -sS -I --max-time 10 "http://127.0.0.1:${HTTP_PORT}/${LOCAL_BASENAME}" >> "$DEBUG_LOG" 2>&1
echo "[CHECK] local HTTP HEAD succeeded; skip target pre-download to avoid normal-window contamination" >> "$DEBUG_LOG"

sleep "$PRE_IDLE"
log_time ATTACK_START

# Boundary guard:
# Avoid the first large transfer falling into the aggregation window
# that contains ATTACK_START. Window size is usually 3s, so 4s is safer.
BOUNDARY_GUARD=4
echo "[GUARD] sleeping ${BOUNDARY_GUARD}s after ATTACK_START before first transfer" >> "$DEBUG_LOG"
sleep "$BOUNDARY_GUARD"

START_TIME=$SECONDS
ITER=0

while [[ $((SECONDS - START_TIME)) -lt "$ATTACK_DURATION" ]]; do
  ITER=$((ITER + 1))
  NOW_TS="$(date -u +'%Y-%m-%d %H:%M:%S UTC')"

  echo "[PAYLOAD][HTTP][$ITER] $NOW_TS target pulling $HTTP_URL" >> "$DEBUG_LOG"

  sshpass -e ssh -T "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
    "curl -f -sS --max-time 120 -o /dev/null '$HTTP_URL'" \
    >> "$DEBUG_LOG" 2>&1

  echo "[PAYLOAD][HTTP][$ITER] done" >> "$DEBUG_LOG"

  SLEEP_SEC="$(rand_int "$SLEEP_MIN" "$SLEEP_MAX")"
  echo "[PAYLOAD][HTTP][$ITER] sleep ${SLEEP_SEC}s" >> "$DEBUG_LOG"
  sleep "$SLEEP_SEC"
done

log_time ATTACK_END

trap - EXIT
cleanup

sleep "$POST_IDLE"

echo "[+] HTTP payload delivery finished: run=$RUN_NAME profile=$PROFILE_ID iterations=$ITER" | tee -a "$DEBUG_LOG"
