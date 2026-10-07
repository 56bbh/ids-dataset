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
# normal_admin_ssh
# PROFILE_ID:
#   ssh_sparse
#   ssh_medium
#   ssh_interactive
# ============================================================

PROFILE_ID="${PROFILE_ID:-ssh_medium}"
VICTIM_HOST_ALIAS="${VICTIM_HOST_ALIAS:?VICTIM_HOST_ALIAS not set}"

NORMAL_CMDS=(
  "date -u"
  "uptime"
  "hostname"
  "whoami"
  "pwd"
  "ls"
  "df -h | head -n 5"
  "free -m | head -n 3"
)

configure_profile() {
    case "$PROFILE_ID" in
        ssh_sparse)
            GROUP_MIN=1
            GROUP_MAX=1
            CMD_SLEEP_MIN=12
            CMD_SLEEP_MAX=28
            THINK_PROB=45
            THINK_MIN=15
            THINK_MAX=35
            ;;
        ssh_medium)
            GROUP_MIN=1
            GROUP_MAX=2
            CMD_SLEEP_MIN=5
            CMD_SLEEP_MAX=14
            THINK_PROB=35
            THINK_MIN=8
            THINK_MAX=20
            ;;
        ssh_interactive)
            GROUP_MIN=2
            GROUP_MAX=4
            CMD_SLEEP_MIN=2
            CMD_SLEEP_MAX=7
            THINK_PROB=25
            THINK_MIN=6
            THINK_MAX=15
            ;;
        *)
            echo "[-] Unknown PROFILE_ID for normal_admin_ssh: $PROFILE_ID" >&2
            echo "    Allowed: ssh_sparse, ssh_medium, ssh_interactive" >&2
            exit 1
            ;;
    esac
}

pick_cmd() {
    local idx
    idx=$(rand_int 0 $((${#NORMAL_CMDS[@]} - 1)))
    echo "${NORMAL_CMDS[$idx]}"
}

ssh_admin_once() {
    local cmd pause
    cmd="$(pick_cmd)"
    ssh -o BatchMode=yes \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=5 \
        "$VICTIM_HOST_ALIAS" "$cmd >/dev/null 2>&1" \
        >/dev/null 2>&1 || true
    pause=$(rand_int "$CMD_SLEEP_MIN" "$CMD_SLEEP_MAX")
    sleep "$pause"
}

configure_profile

log_kv RUN_ID "$RUN_NAME"
log_kv ATTACK_CATEGORY "normal"
log_kv ATTACK_SUBTYPE "admin_ssh"
log_kv PROFILE_ID "$PROFILE_ID"
log_kv RANDOM_SEED "$SEED"
log_kv PROTOCOL "ssh"
log_kv TOOL "ssh_batchmode"
log_kv MODULE_SUMMARY "normal_ssh_admin_${PROFILE_ID}_no_privilege_abuse"

echo "[*] normal_admin_ssh started: $RUN_NAME seed=$SEED profile=$PROFILE_ID"

sleep "$PRE_IDLE"
log_time ATTACK_START
START_TIME=$SECONDS

while [[ $((SECONDS - START_TIME)) -lt "$ATTACK_DURATION" ]]; do
    GROUP_COUNT=$(rand_int "$GROUP_MIN" "$GROUP_MAX")
    for _ in $(seq 1 "$GROUP_COUNT"); do
        ssh_admin_once
        if [[ $((SECONDS - START_TIME)) -ge "$ATTACK_DURATION" ]]; then
            break
        fi
    done

    if [[ "$(rand_int 1 100)" -le "$THINK_PROB" ]]; then
        sleep "$(rand_int "$THINK_MIN" "$THINK_MAX")"
    fi
done

log_time ATTACK_END
sleep "$POST_IDLE"

echo "[+] normal_admin_ssh finished: $RUN_NAME"
