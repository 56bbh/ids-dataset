#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <run_name> <notes_file>" >&2
  exit 1
fi

RUN_NAME="$1"
NOTES_FILE="$2"

log_note() {
  echo "$1 $(date -u +'%Y-%m-%d %H:%M:%S UTC')" | tee -a "$NOTES_FILE"
}

sleep_rand() {
  local min="$1" max="$2"
  local dur=$(( RANDOM % (max - min + 1) + min ))
  sleep "$dur"
}

sleep "$NORMAL_PRE_IDLE_SEC"
log_note "ATTACK_START"
for ((i=1; i<=NORMAL_HTTP_COUNT; i++)); do
  curl -s "http://${VICTIM_IP}/" > /dev/null || true
  sleep_rand "$NORMAL_HTTP_SLEEP_MIN" "$NORMAL_HTTP_SLEEP_MAX"
done
sleep "$NORMAL_MID_IDLE_SEC"
ping -c "$NORMAL_PING_COUNT" "$VICTIM_IP" > /dev/null || true
log_note "ATTACK_END"
sleep "$NORMAL_POST_IDLE_SEC"
