#!/usr/bin/env bash
set -u

BASE_DIR="$HOME/thesis_dataset"
RUN_SCRIPT="$BASE_DIR/scripts/run_experiment.sh"

# 这里只保存“批量调度日志”和“完成标记”
# 不保存真正实验数据
GRID_STATE_DIR="$BASE_DIR/logs/bruteforce_grid_state"
DONE_DIR="$GRID_STATE_DIR/done"
FAILED_FILE="$GRID_STATE_DIR/failed.txt"

mkdir -p "$DONE_DIR"
: > "$FAILED_FILE"

run_one() {
    local protocol="$1"
    local threads="$2"
    local timeout="$3"
    local run_id="$4"

    local profile_id="T${threads}_TO${timeout}"
    local tag="brute_force_${protocol}_${run_id}_${profile_id}"

    local done_file="$DONE_DIR/${tag}.done"
    local scheduler_log="$GRID_STATE_DIR/${tag}.scheduler.log"

    if [[ -f "$done_file" ]]; then
        echo "[SKIP] 已完成: $tag"
        return 0
    fi

    echo "============================================================"
    echo "[START] protocol=$protocol run_id=$run_id THREADS=$threads TIMEOUT_PER_BATCH=$timeout PROFILE_ID=$profile_id"
    echo "[INFO] 实验输出目录由 run_experiment.sh 自动决定"
    echo "[INFO] 调度日志: $scheduler_log"
    echo "============================================================"

    THREADS="$threads" \
    TIMEOUT_PER_BATCH="$timeout" \
    PROFILE_ID="$profile_id" \
    "$RUN_SCRIPT" brute_force "$protocol" "$run_id" \
        > "$scheduler_log" 2>&1

    local status=$?

    if [[ $status -eq 0 ]]; then
        echo "[OK] $tag"
        date > "$done_file"
    else
        echo "[FAIL] $tag, exit_code=$status"
        echo "$tag exit_code=$status" >> "$FAILED_FILE"
    fi

    return 0
}

run_range() {
    local protocol="$1"
    local threads="$2"
    local timeout="$3"
    local start="$4"
    local end="$5"

    for i in $(seq "$start" "$end"); do
        local run_id
        run_id=$(printf "%02d" "$i")

        run_one "$protocol" "$threads" "$timeout" "$run_id"

        sleep 5
    done
}

echo "[INFO] brute_force batch started at $(date)"

# SSH
run_range ssh 8  12 2  6
run_range ssh 16 12 7  12
run_range ssh 32 12 14 18
run_range ssh 32 15 20 24

# Telnet
run_range telnet 16 12 2  6
run_range telnet 24 12 8  12
run_range telnet 32 12 14 18
run_range telnet 32 15 19 24

# FTP
run_range ftp 8  12 2  6
run_range ftp 16 12 8  12
run_range ftp 32 12 14 18
run_range ftp 16 15 20 24

# Postgres
run_range postgres 8  12 2  6
run_range postgres 16 12 8  12
run_range postgres 32 12 14 18
run_range postgres 16 15 20 24

echo "[INFO] brute_force batch finished at $(date)"

if [[ -s "$FAILED_FILE" ]]; then
    echo "[WARN] 有失败任务：$FAILED_FILE"
else
    echo "[INFO] 没有失败任务记录"
fi
