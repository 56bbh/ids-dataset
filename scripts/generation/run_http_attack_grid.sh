#!/usr/bin/env bash
set -u

BASE_DIR="$HOME/thesis_dataset"
RUN_SCRIPT="$BASE_DIR/scripts/run_experiment.sh"

STATE_DIR="$BASE_DIR/logs/http_attack_grid_state"
DONE_DIR="$STATE_DIR/done"
FAILED_FILE="$STATE_DIR/failed.txt"

mkdir -p "$DONE_DIR"
touch "$FAILED_FILE"

run_one() {
    local variant="$1"
    local profile_id="$2"
    local run_id="$3"

    local run_name="http_attack_${variant}_${run_id}"
    local tag="${run_name}_${profile_id}"

    local done_file="$DONE_DIR/${tag}.done"
    local scheduler_log="$STATE_DIR/${tag}.scheduler.log"

    if [[ -f "$done_file" ]]; then
        echo "[SKIP] 已完成: $tag"
        return 0
    fi

    echo "============================================================"
    echo "[START] $tag"
    echo "[RUN]   PROFILE_ID=$profile_id $RUN_SCRIPT http_attack $variant $run_id"
    echo "[LOG]   $scheduler_log"
    echo "============================================================"

    PROFILE_ID="$profile_id" \
    "$RUN_SCRIPT" http_attack "$variant" "$run_id" \
        > "$scheduler_log" 2>&1

    local status=$?

    if [[ $status -eq 0 ]]; then
        if grep -q "\[+\] Finished run: ${run_name}" "$scheduler_log"; then
            echo "[OK] 完整完成: $tag"
            {
                echo "DONE_AT=$(date -u +'%Y-%m-%d %H:%M:%S UTC')"
                echo "RUN_NAME=$run_name"
                echo "VARIANT=$variant"
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
    local variant="$1"
    local profile_id="$2"
    local start="$3"
    local end="$4"

    for i in $(seq "$start" "$end"); do
        local run_id
        run_id=$(printf "%02d" "$i")

        run_one "$variant" "$profile_id" "$run_id"

        sleep 5
    done
}

echo "[INFO] http_attack batch started at $(date)"
echo "[INFO] state dir: $STATE_DIR"

# ============================================================
# directory_probe: 每个 profile 6 轮，总计 18 轮
# dir_light_probe  01-06
# dir_medium_probe 07-12
# dir_burst_probe  13-18
# ============================================================
run_profile directory_probe dir_light_probe  1 6
run_profile directory_probe dir_medium_probe 7 12
run_profile directory_probe dir_burst_probe  13 18

# ============================================================
# path_traversal: 每个 profile 6 轮，总计 18 轮
# traversal_light_probe  01-06
# traversal_medium_probe 07-12
# traversal_burst_probe  13-18
# ============================================================
run_profile path_traversal traversal_light_probe  1 6
run_profile path_traversal traversal_medium_probe 7 12
run_profile path_traversal traversal_burst_probe  13 18

# ============================================================
# sensitive_path_probe: 每个 profile 6 轮，总计 18 轮
# sensitive_light_probe  01-06
# sensitive_medium_probe 07-12
# sensitive_burst_probe  13-18
# ============================================================
run_profile sensitive_path_probe sensitive_light_probe  1 6
run_profile sensitive_path_probe sensitive_medium_probe 7 12
run_profile sensitive_path_probe sensitive_burst_probe  13 18

echo "[INFO] http_attack batch finished at $(date)"

if [[ -s "$FAILED_FILE" ]]; then
    echo "[WARN] 以下任务失败或可能未完整完成："
    cat "$FAILED_FILE"
else
    echo "[INFO] 没有失败任务记录"
fi
