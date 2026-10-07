#!/usr/bin/env bash
set -euo pipefail

RUN_NAME="$1"
NOTES_FILE="$2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../config.sh"

TARGET_IP="${VICTIM_IP:-192.168.9.131}"

ATTACK_CATEGORY="scanning"
ATTACK_SUBTYPE="udp"
PROFILE_ID="${PROFILE_ID:-t4_f_ht60_s2-5}"

ATTACK_DURATION=300
PRE_IDLE=9
BOUNDARY_GUARD=4
POST_IDLE=9

SEED="${SEED:-$RANDOM}"
RANDOM="$SEED"

PROTOCOL="udp"
TOOL="nmap"
NMAP_SCAN_TYPE="sU"

DEBUG_LOG="/tmp/scanning_udp_debug_${RUN_NAME}.log"
: > "$DEBUG_LOG"

COMMON_UDP_PORTS=(
  53 67 68 69 111 123 137 138 161 162 500 514 520
  631 1434 1900 2049 5353 11211
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

shuffle_array() {
  local arr=("$@")
  local n="${#arr[@]}"
  local i j tmp

  for ((i=n-1; i>0; i--)); do
    j=$(( RANDOM % (i + 1) ))
    tmp="${arr[$i]}"
    arr[$i]="${arr[$j]}"
    arr[$j]="$tmp"
  done

  printf "%s\n" "${arr[@]}"
}

join_by_comma() {
  local IFS=","
  echo "$*"
}

pick_random_ports() {
  local count="$1"
  mapfile -t shuffled < <(shuffle_array "${COMMON_UDP_PORTS[@]}")

  local selected=()
  local i
  for ((i=0; i<count && i<${#shuffled[@]}; i++)); do
    selected+=("${shuffled[$i]}")
  done

  join_by_comma "${selected[@]}"
}

configure_profile() {
  case "$PROFILE_ID" in
    t2_p6_ht30_s8-14)
      INTENSITY="light"
      NMAP_TIMING="T2"
      PORT_MODE="random_common"
      PORT_COUNT=6
      PORT_SPEC="$(pick_random_ports "$PORT_COUNT")"
      HOST_TIMEOUT="30s"
      SLEEP_MIN=8
      SLEEP_MAX=14
      MODULE_SUMMARY="udp_t2_p6_ht30_s8-14"
      ;;

    t3_tp20_ht45_s5-10)
      INTENSITY="medium"
      NMAP_TIMING="T3"
      PORT_MODE="top_ports"
      TOP_PORTS=20
      PORT_SPEC=""
      HOST_TIMEOUT="45s"
      SLEEP_MIN=5
      SLEEP_MAX=10
      MODULE_SUMMARY="udp_t3_tp20_ht45_s5-10"
      ;;

    t4_f_ht60_s2-5)
      INTENSITY="burst"
      NMAP_TIMING="T4"
      PORT_MODE="fast"
      PORT_SPEC=""
      HOST_TIMEOUT="60s"
      SLEEP_MIN=2
      SLEEP_MAX=5
      MODULE_SUMMARY="udp_t4_f_ht60_s2-5"
      ;;

    *)
      echo "[-] Unknown PROFILE_ID: $PROFILE_ID" >&2
      echo "    Allowed:"
      echo "      t2_p6_ht30_s8-14"
      echo "      t3_tp20_ht45_s5-10"
      echo "      t4_f_ht60_s2-5"
      exit 1
      ;;
  esac
}

configure_profile

log_kv RUN_ID "$RUN_NAME"
log_kv ATTACK_CATEGORY "$ATTACK_CATEGORY"
log_kv ATTACK_SUBTYPE "$ATTACK_SUBTYPE"
log_kv PROFILE_ID "$PROFILE_ID"
log_kv RANDOM_SEED "$SEED"
log_kv INTENSITY "$INTENSITY"
log_kv PROTOCOL "$PROTOCOL"
log_kv TOOL "$TOOL"
log_kv NMAP_SCAN_TYPE "$NMAP_SCAN_TYPE"
log_kv NMAP_TIMING "$NMAP_TIMING"
log_kv PORT_MODE "$PORT_MODE"
if [[ "$PORT_MODE" == "random_common" ]]; then
  log_kv PORT_COUNT "$PORT_COUNT"
  log_kv PORT_POOL "common_udp"
  log_kv SELECTED_PORTS "$PORT_SPEC"
elif [[ "$PORT_MODE" == "top_ports" ]]; then
  log_kv TOP_PORTS "$TOP_PORTS"
fi
log_kv HOST_TIMEOUT "$HOST_TIMEOUT"
log_kv SLEEP_MIN "$SLEEP_MIN"
log_kv SLEEP_MAX "$SLEEP_MAX"
log_kv ATTACK_DURATION "$ATTACK_DURATION"
log_kv PRE_IDLE "$PRE_IDLE"
log_kv BOUNDARY_GUARD "$BOUNDARY_GUARD"
log_kv POST_IDLE "$POST_IDLE"
log_kv TARGET_IP "$TARGET_IP"
log_kv MODULE_SUMMARY "$MODULE_SUMMARY"

echo "[*] UDP scan started: run=$RUN_NAME profile=$PROFILE_ID seed=$SEED" | tee -a "$DEBUG_LOG"

sleep "$PRE_IDLE"
log_time ATTACK_START

echo "[GUARD] sleeping ${BOUNDARY_GUARD}s after ATTACK_START before first scan" >> "$DEBUG_LOG"
sleep "$BOUNDARY_GUARD"

START_TIME=$SECONDS
ITER=0

while [[ $((SECONDS - START_TIME)) -lt "$ATTACK_DURATION" ]]; do
  ITER=$((ITER + 1))
  NOW_TS="$(date -u +'%Y-%m-%d %H:%M:%S UTC')"

  if [[ "$PORT_MODE" == "random_common" ]]; then
    CMD=(sudo nmap -sU -Pn "-${NMAP_TIMING}" -p "$PORT_SPEC" --host-timeout "$HOST_TIMEOUT" "$TARGET_IP")
  elif [[ "$PORT_MODE" == "top_ports" ]]; then
    CMD=(sudo nmap -sU -Pn "-${NMAP_TIMING}" --top-ports "$TOP_PORTS" --host-timeout "$HOST_TIMEOUT" "$TARGET_IP")
  else
    CMD=(sudo nmap -sU -Pn "-${NMAP_TIMING}" -F --host-timeout "$HOST_TIMEOUT" "$TARGET_IP")
  fi

  echo "[SCAN][UDP][$ITER] $NOW_TS ${CMD[*]}" >> "$DEBUG_LOG"
  "${CMD[@]}" >> "$DEBUG_LOG" 2>&1 || true

  SLEEP_SEC="$(rand_int "$SLEEP_MIN" "$SLEEP_MAX")"
  echo "[SCAN][UDP][$ITER] sleep ${SLEEP_SEC}s" >> "$DEBUG_LOG"
  sleep "$SLEEP_SEC"
done

log_time ATTACK_END

sleep "$POST_IDLE"

echo "[+] UDP scan finished: run=$RUN_NAME profile=$PROFILE_ID iterations=$ITER" | tee -a "$DEBUG_LOG"
