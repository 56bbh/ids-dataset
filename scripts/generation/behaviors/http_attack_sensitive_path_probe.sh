#!/usr/bin/env bash
set -euo pipefail

RUN_NAME="$1"
NOTES_FILE="$2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../config.sh"

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

pick_from_array() {
  local array_name="$1"
  local len idx
  eval "len=\${#${array_name}[@]}"
  idx=$(rand_int 0 $((len - 1)))
  eval "echo \${${array_name}[$idx]}"
}

log_kv() {
  echo "$1=$2" | tee -a "$NOTES_FILE"
}

log_time() {
  echo "$1 $(date -u +'%Y-%m-%d %H:%M:%S UTC')" | tee -a "$NOTES_FILE"
}

# ============================================================
# http_attack_sensitive_path_probe.sh
#
# 语义：
#   敏感入口、隐藏文件、备份文件、管理路径探测。
#   不做 RCE，不做路径遍历，不做持续 HTTP flood。
#
# PROFILE_ID:
#   sensitive_light_probe
#   sensitive_medium_probe
#   sensitive_burst_probe
# ============================================================

ATTACK_SUBTYPE="sensitive_path_probe"
PROFILE_ID="${PROFILE_ID:-sensitive_medium_probe}"

SENSITIVE_PATHS=(
  "/admin"
  "/administrator"
  "/login"
  "/user/login"
  "/phpMyAdmin/"
  "/phpmyadmin/"
  "/mysql/"
  "/dbadmin/"
  "/backup/"
  "/backups/"
  "/backup.zip"
  "/db.sql"
  "/dump.sql"
  "/config.php"
  "/config.inc.php"
  "/.env"
  "/.git/"
  "/.svn/"
  "/server-status"
  "/server-info"
  "/wp-admin/"
  "/wp-login.php"
  "/xmlrpc.php"
  "/robots.txt"
  "/test.php"
  "/info.php"
  "/phpinfo.php"
  "/old/"
  "/dev/"
)

USER_AGENTS=(
  "Mozilla/5.0"
  "curl/7.88.1"
  "SensitiveProbe/1.0"
)

configure_profile() {
  case "$PROFILE_ID" in
    sensitive_light_probe)
      REQ_MIN=1
      REQ_MAX=2
      SLEEP_MIN=12
      SLEEP_MAX=22
      CURL_TIMEOUT=4
      ;;
    sensitive_medium_probe)
      REQ_MIN=2
      REQ_MAX=5
      SLEEP_MIN=6
      SLEEP_MAX=12
      CURL_TIMEOUT=4
      ;;
    sensitive_burst_probe)
      REQ_MIN=5
      REQ_MAX=9
      SLEEP_MIN=3
      SLEEP_MAX=7
      CURL_TIMEOUT=3
      ;;
    *)
      echo "[-] Unknown PROFILE_ID for sensitive_path_probe: $PROFILE_ID" >&2
      echo "    Allowed: sensitive_light_probe, sensitive_medium_probe, sensitive_burst_probe" >&2
      exit 1
      ;;
  esac
}

sensitive_probe_once() {
  local p ua
  p="$(pick_from_array SENSITIVE_PATHS)"
  ua="$(pick_from_array USER_AGENTS)"
  curl -sS -k --max-time "$CURL_TIMEOUT" -A "$ua" "http://${TARGET_IP}${p}" >/dev/null 2>&1 || true
}

configure_profile

log_kv RUN_ID "$RUN_NAME"
log_kv ATTACK_CATEGORY "http_attack"
log_kv ATTACK_SUBTYPE "$ATTACK_SUBTYPE"
log_kv PROFILE_ID "$PROFILE_ID"
log_kv RANDOM_SEED "$SEED"
log_kv PROTOCOL "http"
log_kv TOOL "curl_sensitive_path_probe"
log_kv MODULE_SUMMARY "http_sensitive_path_probe_${PROFILE_ID}"

echo "[*] http_attack sensitive_path_probe started: $RUN_NAME profile=$PROFILE_ID seed=$SEED"

sleep "$PRE_IDLE"
log_time ATTACK_START

START_TIME=$SECONDS
while [[ $((SECONDS - START_TIME)) -lt "$ATTACK_DURATION" ]]; do
  req_count=$(rand_int "$REQ_MIN" "$REQ_MAX")
  for _ in $(seq 1 "$req_count"); do
    sensitive_probe_once
    if [[ $((SECONDS - START_TIME)) -ge "$ATTACK_DURATION" ]]; then
      break
    fi
  done
  sleep "$(rand_int "$SLEEP_MIN" "$SLEEP_MAX")"
done

log_time ATTACK_END
sleep "$POST_IDLE"

echo "[+] http_attack sensitive_path_probe finished: $RUN_NAME"
