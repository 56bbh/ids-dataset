#!/usr/bin/env bash
set -euo pipefail

RUN_NAME="$1"
NOTES_FILE="$2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../config.sh"

TARGET_IP="${VICTIM_IP:-192.168.9.131}"

ATTACK_CATEGORY="scanning"
ATTACK_SUBTYPE="xmas"
PROFILE_ID="${PROFILE_ID:-t4_tp1000_s1-2}"

ATTACK_DURATION=300
PRE_IDLE=9
BOUNDARY_GUARD=4
POST_IDLE=9

SEED="${SEED:-$RANDOM}"
RANDOM="$SEED"

PROTOCOL="tcp"
TOOL="nmap"
NMAP_SCAN_TYPE="sX"

DEBUG_LOG="/tmp/scanning_xmas_debug_${RUN_NAME}.log"
: > "$DEBUG_LOG"

COMMON_TCP_PORTS=(
  20 21 22 23 25 53 80 110 111 135 139 143 443 445
  512 513 514 1099 1524 2049 2121 3306 3632 5432 5900
  6000 6667 6697 8009 8080 8180
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
  mapfile -t shuffled < <(shuffle_array "${COMMON_TCP_PORTS[@]}")

  local selected=()
  local i
  for ((i=0; i<count && i<${#shuffled[@]}; i++)); do
    selected+=("${shuffled[$i]}")
  done

  join_by_comma "${selected[@]}"
}

configure_profile() {
  case "$PROFILE_ID" in
    t2_p12_s5-8)
      INTENSITY="light"
      NMAP_TIMING="T2"
      PORT_MODE="random_common"
      PORT_COUNT=12
      TOP_PORTS=""
      PORT_SPEC="$(pick_random_ports "$PORT_COUNT")"
      SLEEP_MIN=5
      SLEEP_MAX=8
      MODULE_SUMMARY="xmas_${PROFILE_ID}"
      ;;

    t3_tp200_s2-4)
      INTENSITY="medium"
      NMAP_TIMING="T3"
      PORT_MODE="top_ports"
      PORT_COUNT=""
      TOP_PORTS=200
      PORT_SPEC=""
      SLEEP_MIN=2
      SLEEP_MAX=4
      MODULE_SUMMARY="xmas_${PROFILE_ID}"
      ;;

    t4_tp1000_s1-2)
      INTENSITY="burst"
      NMAP_TIMING="T4"
      PORT_MODE="top_ports"
      PORT_COUNT=""
      TOP_PORTS=1000
      PORT_SPEC=""
      SLEEP_MIN=1
      SLEEP_MAX=2
      MODULE_SUMMARY="xmas_${PROFILE_ID}"
      ;;

    *)
      echo "[-] Unknown PROFILE_ID: $PROFILE_ID" >&2
      echo "    Allowed:" >&2
      echo "      t2_p12_s5-8" >&2
      echo "      t3_tp200_s2-4" >&2
      echo "      t4_tp1000_s1-2" >&2
      exit 1
      ;;
  esac
}

build_nmap_cmd() {
  if [[ "$PORT_MODE" == "random_common" ]]; then
    echo "sudo nmap -sX -Pn -${NMAP_TIMING} -p ${PORT_SPEC} ${TARGET_IP}"
  elif [[ "$PORT_MODE" == "top_ports" ]]; then
    echo "sudo nmap -sX -Pn -${NMAP_TIMING} --top-ports ${TOP_PORTS} ${TARGET_IP}"
  else
    echo "[-] Unknown PORT_MODE: $PORT_MODE" >&2
    exit 1
  fi
}

configure_profile

log_kv RUN_ID "$RUN_NAME"
log_kv ATTACK_CATEGORY "$ATTACK_CATEGORY"
log_kv ATTACK_SUBTYPE "$ATTACK_SUBTYPE"
log_kv PROFILE_ID "$PROFILE_ID"
log_kv RANDOM_SEED "$SEED"
log_kv PROTOCOL "$PROTOCOL"
log_kv TOOL "$TOOL"
log_kv INTENSITY "$INTENSITY"
log_kv NMAP_SCAN_TYPE "$NMAP_SCAN_TYPE"
log_kv NMAP_TIMING "$NMAP_TIMING"
log_kv PORT_MODE "$PORT_MODE"

if [[ "$PORT_MODE" == "random_common" ]]; then
  log_kv PORT_POOL "common_tcp"
  log_kv PORT_COUNT "$PORT_COUNT"
  log_kv SELECTED_PORTS "$PORT_SPEC"
else
  log_kv TOP_PORTS "$TOP_PORTS"
fi

log_kv SLEEP_MIN "$SLEEP_MIN"
log_kv SLEEP_MAX "$SLEEP_MAX"
log_kv ATTACK_DURATION "$ATTACK_DURATION"
log_kv PRE_IDLE "$PRE_IDLE"
log_kv BOUNDARY_GUARD "$BOUNDARY_GUARD"
log_kv POST_IDLE "$POST_IDLE"
log_kv MODULE_SUMMARY "$MODULE_SUMMARY"

echo "[*] Xmas scanning started: run=$RUN_NAME profile=$PROFILE_ID seed=$SEED" | tee -a "$DEBUG_LOG"
echo "[*] target=$TARGET_IP intensity=$INTENSITY timing=$NMAP_TIMING port_mode=$PORT_MODE" >> "$DEBUG_LOG"

if [[ "$PORT_MODE" == "random_common" ]]; then
  echo "[*] selected ports: $PORT_SPEC" >> "$DEBUG_LOG"
else
  echo "[*] top ports: $TOP_PORTS" >> "$DEBUG_LOG"
fi

sleep "$PRE_IDLE"
log_time ATTACK_START

echo "[GUARD] sleeping ${BOUNDARY_GUARD}s after ATTACK_START before first scan" >> "$DEBUG_LOG"
sleep "$BOUNDARY_GUARD"

START_TIME=$SECONDS
ITER=0

while [[ $((SECONDS - START_TIME)) -lt "$ATTACK_DURATION" ]]; do
  ITER=$((ITER + 1))
  SLEEP_SEC="$(rand_int "$SLEEP_MIN" "$SLEEP_MAX")"
  NMAP_CMD="$(build_nmap_cmd)"

  echo "[SCAN][XMAS][$ITER] $(date -u +'%Y-%m-%d %H:%M:%S UTC')" >> "$DEBUG_LOG"
  echo "[SCAN][XMAS][$ITER] cmd=$NMAP_CMD" >> "$DEBUG_LOG"

  eval "$NMAP_CMD" >> "$DEBUG_LOG" 2>&1 || true

  echo "[SCAN][XMAS][$ITER] done; sleep=${SLEEP_SEC}s" >> "$DEBUG_LOG"
  sleep "$SLEEP_SEC"
done

log_time ATTACK_END

sleep "$POST_IDLE"

echo "[+] Xmas scanning finished: run=$RUN_NAME profile=$PROFILE_ID iterations=$ITER" | tee -a "$DEBUG_LOG"
