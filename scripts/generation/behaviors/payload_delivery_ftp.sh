#!/usr/bin/env bash
set -euo pipefail

RUN_NAME="$1"
NOTES_FILE="$2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../config.sh"

TARGET_IP="${VICTIM_IP:-192.168.9.131}"
TARGET_USER="${TARGET_USER:-msfadmin}"
TARGET_PASS="${TARGET_PASS:-msfadmin}"

ATTACK_CATEGORY="payload_delivery"
ATTACK_SUBTYPE="ftp"
PROFILE_ID="${PROFILE_ID:-ftp_payload_medium_repeat}"

ATTACK_DURATION=300
PRE_IDLE=9
POST_IDLE=9

SEED="${SEED:-$RANDOM}"
RANDOM="$SEED"

PROTOCOL="ftp"
TOOL="curl"

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
    ftp_payload_small_push)
      FILE_SIZE_MB=3
      SLEEP_MIN=10
      SLEEP_MAX=20
      LOCAL_FILE="/tmp/local_ftp_payload_small_${RUN_NAME}.bin"
      REMOTE_FILE="/home/${TARGET_USER}/uploaded_payload_small_${RUN_NAME}.bin"
      MODULE_SUMMARY="ftp_payload_small_push"
      ;;
    ftp_payload_medium_repeat)
      FILE_SIZE_MB=20
      SLEEP_MIN=4
      SLEEP_MAX=8
      LOCAL_FILE="/tmp/local_ftp_payload_medium_${RUN_NAME}.bin"
      REMOTE_FILE="/home/${TARGET_USER}/uploaded_payload_medium_${RUN_NAME}.bin"
      MODULE_SUMMARY="ftp_payload_medium_repeat"
      ;;
    ftp_payload_large_burst)
      FILE_SIZE_MB=80
      SLEEP_MIN=1
      SLEEP_MAX=3
      LOCAL_FILE="/tmp/local_ftp_payload_large_${RUN_NAME}.bin"
      REMOTE_FILE="/home/${TARGET_USER}/uploaded_payload_large_${RUN_NAME}.bin"
      MODULE_SUMMARY="ftp_payload_large_burst"
      ;;
    *)
      echo "[-] Unknown PROFILE_ID: $PROFILE_ID" >&2
      echo "    Allowed: ftp_payload_small_push, ftp_payload_medium_repeat, ftp_payload_large_burst" >&2
      exit 1
      ;;
  esac
}

configure_profile

REMOTE_BASENAME="$(basename "$REMOTE_FILE")"
FTP_URL="ftp://${TARGET_USER}:${TARGET_PASS}@${TARGET_IP}/${REMOTE_BASENAME}"
DEBUG_LOG="/tmp/payload_ftp_debug_${RUN_NAME}.log"
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
log_kv LOCAL_FILE "$LOCAL_FILE"
log_kv REMOTE_FILE "$REMOTE_FILE"
log_kv REMOTE_BASENAME "$REMOTE_BASENAME"
log_kv MODULE_SUMMARY "$MODULE_SUMMARY"

echo "[*] FTP payload delivery started: run=$RUN_NAME profile=$PROFILE_ID seed=$SEED" | tee -a "$DEBUG_LOG"

echo "[PREP] creating local payload: $LOCAL_FILE size=${FILE_SIZE_MB}MB" >> "$DEBUG_LOG"
rm -f "$LOCAL_FILE" || true
dd if=/dev/urandom of="$LOCAL_FILE" bs=1M count="$FILE_SIZE_MB" >> "$DEBUG_LOG" 2>&1
ls -lh "$LOCAL_FILE" >> "$DEBUG_LOG" 2>&1
test -s "$LOCAL_FILE"

echo "[PREP] removing old remote payload if exists: $REMOTE_FILE" >> "$DEBUG_LOG"
sshpass -e ssh -T "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
  "rm -f '$REMOTE_FILE'" >> "$DEBUG_LOG" 2>&1 || true

echo "[CHECK] local payload exists; skip pre-upload to avoid normal-window contamination" >> "$DEBUG_LOG"

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

  echo "[PAYLOAD][FTP][$ITER] $NOW_TS uploading $LOCAL_FILE -> $REMOTE_BASENAME" >> "$DEBUG_LOG"

  curl -f -sS --max-time 120 -T "$LOCAL_FILE" "$FTP_URL" >> "$DEBUG_LOG" 2>&1

  echo "[PAYLOAD][FTP][$ITER] done" >> "$DEBUG_LOG"

  SLEEP_SEC="$(rand_int "$SLEEP_MIN" "$SLEEP_MAX")"
  echo "[PAYLOAD][FTP][$ITER] sleep ${SLEEP_SEC}s" >> "$DEBUG_LOG"
  sleep "$SLEEP_SEC"
done

log_time ATTACK_END

echo "[CLEANUP] removing local payload and remote payload" >> "$DEBUG_LOG"
rm -f "$LOCAL_FILE" || true

sshpass -e ssh -T "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
  "rm -f '$REMOTE_FILE'" >> "$DEBUG_LOG" 2>&1 || true

sleep "$POST_IDLE"

echo "[+] FTP payload delivery finished: run=$RUN_NAME profile=$PROFILE_ID iterations=$ITER" | tee -a "$DEBUG_LOG"
