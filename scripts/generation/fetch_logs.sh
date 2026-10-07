#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <run_name>" >&2
  exit 1
fi

RUN_NAME="$1"
RUN_LOG_DIR="$LOGS_DIR/$RUN_NAME"

# 乖乖提前把文件夹建好
mkdir -p "$RUN_LOG_DIR"

fetch_if_exists() {
  local remote_path="$1"
  local local_name="$2"
  local tmp_path="/tmp/fetch_staging_${local_name}"
  
  if ssh "$VICTIM_HOST_ALIAS" "sudo test -f '$remote_path' >/dev/null 2>&1"; then
    ssh "$VICTIM_HOST_ALIAS" "sudo cp '$remote_path' '$tmp_path' 2>/dev/null && sudo chmod 644 '$tmp_path' 2>/dev/null"
    scp "$VICTIM_HOST_ALIAS:$tmp_path" "$RUN_LOG_DIR/$local_name" >/dev/null
    ssh "$VICTIM_HOST_ALIAS" "sudo rm -f '$tmp_path' 2>/dev/null"
    echo "fetched $local_name"
  else
    echo "missing $local_name"
  fi
}

# 每次必抓的基础日志
fetch_if_exists "/var/log/auth.log" "auth.log"
fetch_if_exists "/var/log/syslog" "syslog"
fetch_if_exists "/var/log/apache2/access.log" "access.log"
fetch_if_exists "/var/log/apache2/error.log" "error.log"

# 【你的专属规矩 1】：名字里有 samba，才去抓 log.smbd
if [[ "$RUN_NAME" == *"samba"* ]]; then
    fetch_if_exists "/var/log/samba/log.smbd" "log.smbd"
fi

# 【你的专属规矩 2】：名字里有 vsftpd，才去抓 vsftpd.log
if [[ "$RUN_NAME" == *"vsftpd"* ]]; then
    fetch_if_exists "/var/log/vsftpd.log" "vsftpd.log"
fi
