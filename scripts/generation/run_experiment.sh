#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 <attack_class> <variant> <run_id>" >&2
  exit 1
fi

ATTACK_CLASS="$1"
export ATTACK_CLASS

VARIANT="$2"
export VARIANT

RUN_ID="$3"
export RUN_ID

RUN_NAME="${ATTACK_CLASS}_${VARIANT}_${RUN_ID}"
export RUN_NAME

RUN_LOG_DIR="$LOGS_DIR/$RUN_NAME"
export RUN_LOG_DIR

mkdir -p "$PCAP_DIR/$RUN_NAME" "$RUN_LOG_DIR" "$NOTES_DIR"

NOTES_FILE="$NOTES_DIR/${RUN_NAME}_notes.log"
export NOTES_FILE

PCAP_FILE="$PCAP_DIR/$RUN_NAME/${RUN_NAME}.pcap"
export PCAP_FILE

TCPDUMP_STDERR="/tmp/${RUN_NAME}_tcpdump.log"
TCPDUMP_PID=""

PAYLOAD_SCRIPT="$SCRIPT_DIR/behaviors/${ATTACK_CLASS}_${VARIANT}.sh"

if [[ ! -x "$PAYLOAD_SCRIPT" ]]; then
  echo "ERROR: behavior script not found or not executable: $PAYLOAD_SCRIPT" >&2
  exit 1
fi

cleanup() {
  if [[ -n "${TCPDUMP_PID:-}" ]] && kill -0 "$TCPDUMP_PID" >/dev/null 2>&1; then
    sudo /bin/kill -2 "$TCPDUMP_PID" >/dev/null 2>&1 || true
    wait "$TCPDUMP_PID" || true
  fi
}
trap cleanup EXIT

RUN_START_PREP_TIME="$(date -u +'%Y-%m-%d %H:%M:%S UTC')"
echo "RUN_START_PREP $RUN_START_PREP_TIME" | tee -a "$NOTES_FILE"

# 可选：抓取实验前快照/日志。保留原有流程，但不再生成 metadata json/csv。
"$SCRIPT_DIR/snapshot_remote.sh" "$RUN_LOG_DIR" before | tee -a "$NOTES_FILE"

sudo /usr/bin/tcpdump -i "$CAPTURE_IFACE" -s 0 -w "$PCAP_FILE" >"$TCPDUMP_STDERR" 2>&1 &
TCPDUMP_PID=$!
sleep 1

PCAP_START_TIME="$(date -u +'%Y-%m-%d %H:%M:%S UTC')"
echo "PCAP_START $PCAP_START_TIME" | tee -a "$NOTES_FILE"

"$PAYLOAD_SCRIPT" "$RUN_NAME" "$NOTES_FILE"

cleanup
trap - EXIT

PCAP_STOP_TIME="$(date -u +'%Y-%m-%d %H:%M:%S UTC')"
echo "PCAP_STOP $PCAP_STOP_TIME" | tee -a "$NOTES_FILE"

"$SCRIPT_DIR/snapshot_remote.sh" "$RUN_LOG_DIR" after | tee -a "$NOTES_FILE"
"$SCRIPT_DIR/fetch_logs.sh" "$RUN_NAME" | tee -a "$NOTES_FILE"

RUN_END_TIME="$(date -u +'%Y-%m-%d %H:%M:%S UTC')"
echo "RUN_END $RUN_END_TIME" | tee -a "$NOTES_FILE"

printf '[+] Finished run: %s\n' "$RUN_NAME"
printf '[+] PCAP: %s\n' "$PCAP_FILE"
printf '[+] Notes: %s\n' "$NOTES_FILE"
printf '[+] Logs: %s\n' "$RUN_LOG_DIR"
