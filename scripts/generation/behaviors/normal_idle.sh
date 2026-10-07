#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../config.sh"

RUN_NAME="$1"
NOTES_FILE="$2"

TARGET_IP="${VICTIM_IP:-192.168.9.131}"

ATTACK_DURATION=300
PRE_IDLE=9
POST_IDLE=9

SEED="${SEED:-$RANDOM}"
RANDOM="$SEED"

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

# ============================================================
# normal_idle
# PROFILE_ID:
#   idle_pure
#   idle_with_rare_ping
# ============================================================

PROFILE_ID="${PROFILE_ID:-idle_pure}"

configure_profile() {
    case "$PROFILE_ID" in
        idle_pure)
            IDLE_MODE="pure"
            ;;
        idle_with_rare_ping)
            IDLE_MODE="rare_ping"
            ;;
        *)
            echo "[-] Unknown PROFILE_ID for normal_idle: $PROFILE_ID" >&2
            echo "    Allowed: idle_pure, idle_with_rare_ping" >&2
            exit 1
            ;;
    esac
}

configure_profile

log_kv RUN_ID "$RUN_NAME"
log_kv ATTACK_CATEGORY "normal"
log_kv ATTACK_SUBTYPE "idle"
log_kv PROFILE_ID "$PROFILE_ID"
log_kv RANDOM_SEED "$SEED"
log_kv PROTOCOL "none"
log_kv TOOL "sleep_ping_optional"
log_kv MODULE_SUMMARY "normal_idle_${PROFILE_ID}"

echo "[*] normal_idle started: $RUN_NAME seed=$SEED profile=$PROFILE_ID"

sleep "$PRE_IDLE"
log_time ATTACK_START
START_TIME=$SECONDS

if [[ "$IDLE_MODE" == "pure" ]]; then
    sleep "$ATTACK_DURATION"
else
    while [[ $((SECONDS - START_TIME)) -lt "$ATTACK_DURATION" ]]; do
        if [[ "$(rand_int 1 100)" -le 15 ]]; then
            ping -c 1 -W 1 "$TARGET_IP" >/dev/null 2>&1 || true
        fi
        sleep "$(rand_int 20 45)"
    done
fi

log_time ATTACK_END
sleep "$POST_IDLE"

echo "[+] normal_idle finished: $RUN_NAME"
