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
# normal_ftp_access
# PROFILE_ID:
#   ftp_list_only
#   ftp_small_get
#   ftp_mixed
# ============================================================

PROFILE_ID="${PROFILE_ID:-ftp_list_only}"

configure_profile() {
    case "$PROFILE_ID" in
        ftp_list_only)
            SMALL_GET_PROB=0
            FTP_SLEEP_MIN=8
            FTP_SLEEP_MAX=20
            EXTRA_IDLE_PROB=30
            ;;
        ftp_small_get)
            SMALL_GET_PROB=70
            FTP_SLEEP_MIN=10
            FTP_SLEEP_MAX=24
            EXTRA_IDLE_PROB=35
            ;;
        ftp_mixed)
            SMALL_GET_PROB=30
            FTP_SLEEP_MIN=6
            FTP_SLEEP_MAX=18
            EXTRA_IDLE_PROB=25
            ;;
        *)
            echo "[-] Unknown PROFILE_ID for normal_ftp_access: $PROFILE_ID" >&2
            echo "    Allowed: ftp_list_only, ftp_small_get, ftp_mixed" >&2
            exit 1
            ;;
    esac
}

ftp_once() {
    timeout 12 bash -c "cat <<EOF | ftp -inv ${TARGET_IP}
user anonymous anonymous
pwd
ls
bye
EOF" >/dev/null 2>&1 || true

    sleep "$(rand_int "$FTP_SLEEP_MIN" "$FTP_SLEEP_MAX")"
}

ftp_small_get_once() {
    timeout 15 bash -c "cat <<EOF | ftp -inv ${TARGET_IP}
user anonymous anonymous
binary
get README /tmp/normal_ftp_readme_${RUN_NAME}_$$.tmp
bye
EOF" >/dev/null 2>&1 || true
    rm -f /tmp/normal_ftp_readme_${RUN_NAME}_*.tmp >/dev/null 2>&1 || true

    sleep "$(rand_int "$FTP_SLEEP_MIN" "$FTP_SLEEP_MAX")"
}

configure_profile

log_kv RUN_ID "$RUN_NAME"
log_kv ATTACK_CATEGORY "normal"
log_kv ATTACK_SUBTYPE "ftp_access"
log_kv PROFILE_ID "$PROFILE_ID"
log_kv RANDOM_SEED "$SEED"
log_kv PROTOCOL "ftp"
log_kv TOOL "ftp"
log_kv MODULE_SUMMARY "normal_ftp_access_${PROFILE_ID}"

echo "[*] normal_ftp_access started: $RUN_NAME seed=$SEED profile=$PROFILE_ID"

sleep "$PRE_IDLE"
log_time ATTACK_START
START_TIME=$SECONDS

while [[ $((SECONDS - START_TIME)) -lt "$ATTACK_DURATION" ]]; do
    if [[ "$(rand_int 1 100)" -le "$SMALL_GET_PROB" ]]; then
        ftp_small_get_once
    else
        ftp_once
    fi

    if [[ "$(rand_int 1 100)" -le "$EXTRA_IDLE_PROB" ]]; then
        sleep "$(rand_int 8 18)"
    fi
done

log_time ATTACK_END
sleep "$POST_IDLE"

echo "[+] normal_ftp_access finished: $RUN_NAME"
