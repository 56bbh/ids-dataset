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
# normal_mixed_light
# PROFILE_ID:
#   mixed_low
#   mixed_medium
# ============================================================

PROFILE_ID="${PROFILE_ID:-mixed_medium}"
VICTIM_HOST_ALIAS="${VICTIM_HOST_ALIAS:?VICTIM_HOST_ALIAS not set}"

NORMAL_PATHS=(
  "/"
  "/index.php"
  "/dvwa/"
  "/mutillidae/"
  "/phpMyAdmin/"
)

NORMAL_CMDS=(
  "date -u"
  "uptime"
  "hostname"
  "whoami"
  "df -h | head -n 5"
)

configure_profile() {
    case "$PROFILE_ID" in
        mixed_low)
            HTTP_MAX=20
            SSH_MAX=35
            FTP_MAX=50
            ICMP_MAX=65
            ;;
        mixed_medium)
            HTTP_MAX=30
            SSH_MAX=50
            FTP_MAX=68
            ICMP_MAX=82
            ;;
        *)
            echo "[-] Unknown PROFILE_ID for normal_mixed_light: $PROFILE_ID" >&2
            echo "    Allowed: mixed_low, mixed_medium" >&2
            exit 1
            ;;
    esac
}

pick_path() {
    local idx
    idx=$(rand_int 0 $((${#NORMAL_PATHS[@]} - 1)))
    echo "${NORMAL_PATHS[$idx]}"
}

pick_cmd() {
    local idx
    idx=$(rand_int 0 $((${#NORMAL_CMDS[@]} - 1)))
    echo "${NORMAL_CMDS[$idx]}"
}

do_http() {
    curl -sS --max-time 5 "http://${TARGET_IP}$(pick_path)" >/dev/null 2>&1 || true
    sleep "$(rand_int 3 10)"
}

do_ssh() {
    local cmd
    cmd="$(pick_cmd)"
    ssh -o BatchMode=yes \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=5 \
        "$VICTIM_HOST_ALIAS" "$cmd >/dev/null 2>&1" \
        >/dev/null 2>&1 || true
    sleep "$(rand_int 5 14)"
}

do_ftp() {
    timeout 12 bash -c "cat <<EOF | ftp -inv ${TARGET_IP}
user anonymous anonymous
pwd
ls
bye
EOF" >/dev/null 2>&1 || true
    sleep "$(rand_int 8 18)"
}

do_icmp() {
    ping -c 1 -W 1 "$TARGET_IP" >/dev/null 2>&1 || true
    sleep "$(rand_int 5 14)"
}

do_idle() {
    sleep "$(rand_int 10 25)"
}

configure_profile

log_kv RUN_ID "$RUN_NAME"
log_kv ATTACK_CATEGORY "normal"
log_kv ATTACK_SUBTYPE "mixed_light"
log_kv PROFILE_ID "$PROFILE_ID"
log_kv RANDOM_SEED "$SEED"
log_kv PROTOCOL "mixed"
log_kv TOOL "curl_ssh_ftp_ping_sleep"
log_kv MODULE_SUMMARY "normal_mixed_light_${PROFILE_ID}"

echo "[*] normal_mixed_light started: $RUN_NAME seed=$SEED profile=$PROFILE_ID"

sleep "$PRE_IDLE"
log_time ATTACK_START
START_TIME=$SECONDS

while [[ $((SECONDS - START_TIME)) -lt "$ATTACK_DURATION" ]]; do
    choice=$(rand_int 1 100)

    if [[ "$choice" -le "$HTTP_MAX" ]]; then
        do_http
    elif [[ "$choice" -le "$SSH_MAX" ]]; then
        do_ssh
    elif [[ "$choice" -le "$FTP_MAX" ]]; then
        do_ftp
    elif [[ "$choice" -le "$ICMP_MAX" ]]; then
        do_icmp
    else
        do_idle
    fi
done

log_time ATTACK_END
sleep "$POST_IDLE"

echo "[+] normal_mixed_light finished: $RUN_NAME"
