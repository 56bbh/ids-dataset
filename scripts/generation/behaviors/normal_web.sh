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
# normal_web
# PROFILE_ID:
#   web_light
#   web_medium
#   web_deep
# ============================================================

PROFILE_ID="${PROFILE_ID:-web_medium}"

NORMAL_PATHS_LIGHT=(
  "/"
  "/index.php"
)

NORMAL_PATHS_MEDIUM=(
  "/"
  "/index.php"
  "/dvwa/"
  "/mutillidae/"
  "/phpMyAdmin/"
)

NORMAL_PATHS_DEEP=(
  "/"
  "/index.php"
  "/dvwa/"
  "/mutillidae/"
  "/phpMyAdmin/"
  "/twiki/"
  "/tikiwiki/"
)

HTTP_USER_AGENTS=(
  "Mozilla/5.0"
  "Mozilla/5.0 (X11; Linux x86_64)"
  "curl/7.88.1"
)

configure_profile() {
    case "$PROFILE_ID" in
        web_light)
            PATH_ARRAY_NAME="NORMAL_PATHS_LIGHT"
            SLEEP_MIN=8
            SLEEP_MAX=20
            READ_PAUSE_PROB=35
            READ_PAUSE_MIN=12
            READ_PAUSE_MAX=30
            ;;
        web_medium)
            PATH_ARRAY_NAME="NORMAL_PATHS_MEDIUM"
            SLEEP_MIN=4
            SLEEP_MAX=12
            READ_PAUSE_PROB=25
            READ_PAUSE_MIN=8
            READ_PAUSE_MAX=20
            ;;
        web_deep)
            PATH_ARRAY_NAME="NORMAL_PATHS_DEEP"
            SLEEP_MIN=2
            SLEEP_MAX=8
            READ_PAUSE_PROB=15
            READ_PAUSE_MIN=6
            READ_PAUSE_MAX=15
            ;;
        *)
            echo "[-] Unknown PROFILE_ID for normal_web: $PROFILE_ID" >&2
            echo "    Allowed: web_light, web_medium, web_deep" >&2
            exit 1
            ;;
    esac
}

pick_path() {
    local array_name="$1"
    local len idx
    eval "len=\${#${array_name}[@]}"
    idx=$(rand_int 0 $((len - 1)))
    eval "echo \${${array_name}[$idx]}"
}

pick_ua() {
    local idx
    idx=$(rand_int 0 $((${#HTTP_USER_AGENTS[@]} - 1)))
    echo "${HTTP_USER_AGENTS[$idx]}"
}

http_browse_once() {
    local path ua pause
    path="$(pick_path "$PATH_ARRAY_NAME")"
    ua="$(pick_ua)"
    curl -sS --max-time 5 -A "$ua" "http://${TARGET_IP}${path}" >/dev/null 2>&1 || true
    pause=$(rand_int "$SLEEP_MIN" "$SLEEP_MAX")
    sleep "$pause"
}

configure_profile

log_kv RUN_ID "$RUN_NAME"
log_kv ATTACK_CATEGORY "normal"
log_kv ATTACK_SUBTYPE "web"
log_kv PROFILE_ID "$PROFILE_ID"
log_kv RANDOM_SEED "$SEED"
log_kv PROTOCOL "http"
log_kv TOOL "curl"
log_kv MODULE_SUMMARY "normal_http_browsing_${PROFILE_ID}"

echo "[*] normal_web started: $RUN_NAME seed=$SEED profile=$PROFILE_ID"

sleep "$PRE_IDLE"
log_time ATTACK_START
START_TIME=$SECONDS

while [[ $((SECONDS - START_TIME)) -lt "$ATTACK_DURATION" ]]; do
    http_browse_once

    if [[ "$(rand_int 1 100)" -le "$READ_PAUSE_PROB" ]]; then
        sleep "$(rand_int "$READ_PAUSE_MIN" "$READ_PAUSE_MAX")"
    fi
done

log_time ATTACK_END
sleep "$POST_IDLE"

echo "[+] normal_web finished: $RUN_NAME"
