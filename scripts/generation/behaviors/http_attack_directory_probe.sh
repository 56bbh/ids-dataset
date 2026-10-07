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
# http_attack_directory_probe.sh
#
# 语义：
#   Web 目录/文件枚举和目录爆破。
#   不做 PHP-CGI RCE，不做路径遍历，不做 DoS 洪泛。
#
# PROFILE_ID:
#   dir_light_probe
#   dir_medium_probe
#   dir_burst_probe
# ============================================================

ATTACK_SUBTYPE="directory_probe"
PROFILE_ID="${PROFILE_ID:-dir_medium_probe}"

COMMON_PATHS=(
  "/admin"
  "/login"
  "/backup"
  "/config"
  "/uploads"
  "/images"
  "/css"
  "/js"
  "/test"
  "/old"
  "/tmp"
  "/private"
  "/server-status"
  "/phpinfo.php"
  "/db"
  "/database"
  "/wordpress"
  "/wp-admin"
  "/wp-login.php"
  "/robots.txt"
  "/sitemap.xml"
  "/.git/"
  "/.env"
  "/README"
  "/README.txt"
  "/index.php.bak"
  "/backup.zip"
)

USER_AGENTS=(
  "Mozilla/5.0"
  "curl/7.88.1"
  "Wget/1.21"
  "DirProbe/1.0"
)

configure_profile() {
  case "$PROFILE_ID" in
    dir_light_probe)
      REQ_MIN=1
      REQ_MAX=2
      SLEEP_MIN=12
      SLEEP_MAX=25
      CURL_TIMEOUT=4
      ;;
    dir_medium_probe)
      REQ_MIN=2
      REQ_MAX=5
      SLEEP_MIN=6
      SLEEP_MAX=12
      CURL_TIMEOUT=4
      ;;
    dir_burst_probe)
      REQ_MIN=5
      REQ_MAX=10
      SLEEP_MIN=3
      SLEEP_MAX=7
      CURL_TIMEOUT=3
      ;;
    *)
      echo "[-] Unknown PROFILE_ID for directory_probe: $PROFILE_ID" >&2
      echo "    Allowed: dir_light_probe, dir_medium_probe, dir_burst_probe" >&2
      exit 1
      ;;
  esac
}

http_probe_once() {
  local p ua
  p="$(pick_from_array COMMON_PATHS)"
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
log_kv TOOL "curl_directory_probe"
log_kv MODULE_SUMMARY "http_directory_probe_${PROFILE_ID}"

echo "[*] http_attack directory_probe started: $RUN_NAME profile=$PROFILE_ID seed=$SEED"

sleep "$PRE_IDLE"
log_time ATTACK_START

START_TIME=$SECONDS
while [[ $((SECONDS - START_TIME)) -lt "$ATTACK_DURATION" ]]; do
  req_count=$(rand_int "$REQ_MIN" "$REQ_MAX")
  for _ in $(seq 1 "$req_count"); do
    http_probe_once
    if [[ $((SECONDS - START_TIME)) -ge "$ATTACK_DURATION" ]]; then
      break
    fi
  done
  sleep "$(rand_int "$SLEEP_MIN" "$SLEEP_MAX")"
done

log_time ATTACK_END
sleep "$POST_IDLE"

echo "[+] http_attack directory_probe finished: $RUN_NAME"
