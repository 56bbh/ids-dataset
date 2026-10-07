#!/usr/bin/env bash
set -u

BASE_DIR="$HOME/thesis_dataset"
RUN_SCRIPT="$BASE_DIR/scripts/run_experiment.sh"

STATE_DIR="$BASE_DIR/logs/normal_grid_state"
DONE_DIR="$STATE_DIR/done"
FAILED_FILE="$STATE_DIR/failed.txt"

mkdir -p "$DONE_DIR"
touch "$FAILED_FILE"

run_one() {
    local subtype="$1"
    local profile_id="$2"
    local run_id="$3"

    local run_name="normal_${subtype}_${run_id}"
    local tag="${run_name}_${profile_id}"

    local done_file="$DONE_DIR/${tag}.done"
    local scheduler_log="$STATE_DIR/${tag}.scheduler.log"

    if [[ -f "$done_file" ]]; then
        echo "[SKIP] 已完成: $tag"
        return 0
    fi

    echo "============================================================"
    echo "[START] $tag"
    echo "[RUN]   PROFILE_ID=$profile_id $RUN_SCRIPT normal $subtype $run_id"
    echo "[LOG]   $scheduler_log"
    echo "============================================================"

    PROFILE_ID="$profile_id" \
    "$RUN_SCRIPT" normal "$subtype" "$run_id" \
        > "$scheduler_log" 2>&1

    local status=$?

    if [[ $status -eq 0 ]]; then
        if grep -q "\[+\] Finished run: ${run_name}" "$scheduler_log"; then
            echo "[OK] 完整完成: $tag"
            {
                echo "DONE_AT=$(date -u +'%Y-%m-%d %H:%M:%S UTC')"
                echo "RUN_NAME=$run_name"
                echo "SUBTYPE=$subtype"
                echo "PROFILE_ID=$profile_id"
                echo "RUN_ID=$run_id"
            } > "$done_file"
        else
            echo "[WARN] 命令返回 0，但没有检测到 Finished run: $run_name"
            echo "$tag maybe_incomplete" >> "$FAILED_FILE"
        fi
    else
        echo "[FAIL] $tag exit_code=$status"
        echo "$tag exit_code=$status" >> "$FAILED_FILE"
    fi

    return 0
}

run_profile() {
    local subtype="$1"
    local profile_id="$2"
    local start="$3"
    local end="$4"

    for i in $(seq "$start" "$end"); do
        local run_id
        run_id=$(printf "%02d" "$i")

        run_one "$subtype" "$profile_id" "$run_id"

        sleep 5
    done
}

echo "[INFO] normal batch started at $(date)"
echo "[INFO] state dir: $STATE_DIR"

# ============================================================
# idle: 每个 profile 3 轮，总计 6 轮
# idle_pure           01-03
# idle_with_rare_ping 04-06
# ============================================================
run_profile idle idle_pure           1 3
run_profile idle idle_with_rare_ping 4 6

# ============================================================
# web: 每个 profile 6 轮，总计 18 轮
# web_light  01-06
# web_medium 07-12
# web_deep   13-18
# ============================================================
run_profile web web_light  1 6
run_profile web web_medium 7 12
run_profile web web_deep   13 18

# ============================================================
# admin_ssh: 每个 profile 6 轮，总计 18 轮
# ssh_sparse      01-06
# ssh_medium      07-12
# ssh_interactive 13-18
# ============================================================
run_profile admin_ssh ssh_sparse      1 6
run_profile admin_ssh ssh_medium      7 12
run_profile admin_ssh ssh_interactive 13 18

# ============================================================
# ftp_access: 每个 profile 6 轮，总计 18 轮
# ftp_list_only 01-06
# ftp_mixed     07-12
# ftp_small_get 13-18
# ============================================================
run_profile ftp_access ftp_list_only 1 6
run_profile ftp_access ftp_mixed     7 12
run_profile ftp_access ftp_small_get 13 18

# ============================================================
# mixed_light: 每个 profile 6 轮，总计 12 轮
# mixed_low    01-06
# mixed_medium 07-12
# ============================================================
run_profile mixed_light mixed_low    1 6
run_profile mixed_light mixed_medium 7 12

echo "[INFO] normal batch finished at $(date)"

if [[ -s "$FAILED_FILE" ]]; then
    echo "[WARN] 以下任务失败或可能未完整完成："
    cat "$FAILED_FILE"
else
    echo "[INFO] 没有失败任务记录"
fi
