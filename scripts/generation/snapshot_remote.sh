#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"

OUTDIR="${1:?usage: snapshot_remote.sh <outdir> <before|after>}"
PHASE="${2:?usage: snapshot_remote.sh <outdir> <before|after>}"
mkdir -p "$OUTDIR"

REMOTE_TMP_DIR="/tmp/oai_dataset_snapshot"
ssh "$VICTIM_HOST_ALIAS" "mkdir -p '$REMOTE_TMP_DIR'"

# 每次必做的基础快照
ssh "$VICTIM_HOST_ALIAS" "
date -u > '$REMOTE_TMP_DIR/victim_utc_${PHASE}.txt'
sudo /bin/ps aux > '$REMOTE_TMP_DIR/ps_${PHASE}.txt' 2>/dev/null
sudo /bin/netstat -antp > '$REMOTE_TMP_DIR/netstat_${PHASE}.txt' 2>/dev/null
"
scp "$VICTIM_HOST_ALIAS:$REMOTE_TMP_DIR/victim_utc_${PHASE}.txt" "$OUTDIR/" >/dev/null 2>&1
scp "$VICTIM_HOST_ALIAS:$REMOTE_TMP_DIR/ps_${PHASE}.txt" "$OUTDIR/" >/dev/null 2>&1
scp "$VICTIM_HOST_ALIAS:$REMOTE_TMP_DIR/netstat_${PHASE}.txt" "$OUTDIR/" >/dev/null 2>&1
echo "${PHASE} base snapshot completed"

# 【你的专属规矩 3】：名字里包含 ssh_telnet，才去做深度会话快照
if [[ "$OUTDIR" == *"ssh_telnet"* ]]; then
    ssh "$VICTIM_HOST_ALIAS" "
    sudo /usr/bin/last -n 20 > '$REMOTE_TMP_DIR/last_${PHASE}.txt' 2>/dev/null
    sudo /usr/bin/who > '$REMOTE_TMP_DIR/who_${PHASE}.txt' 2>/dev/null
    sudo /usr/bin/w > '$REMOTE_TMP_DIR/w_${PHASE}.txt' 2>/dev/null
    "
    scp "$VICTIM_HOST_ALIAS:$REMOTE_TMP_DIR/last_${PHASE}.txt" "$OUTDIR/" >/dev/null 2>&1
    scp "$VICTIM_HOST_ALIAS:$REMOTE_TMP_DIR/who_${PHASE}.txt" "$OUTDIR/" >/dev/null 2>&1
    scp "$VICTIM_HOST_ALIAS:$REMOTE_TMP_DIR/w_${PHASE}.txt" "$OUTDIR/" >/dev/null 2>&1
    echo "${PHASE} dedicated session snapshot (last, who, w) completed"
fi
