#!/usr/bin/env bash
set -u

BASE_DIR="$HOME/thesis_dataset"
RUN_SCRIPT="$BASE_DIR/scripts/run_experiment.sh"

STATE_DIR="$BASE_DIR/logs/payload_exfil_grid_state"
DONE_DIR="$STATE_DIR/done"
FAILED_FILE="$STATE_DIR/failed.txt"

mkdir -p "$DONE_DIR"
touch "$FAILED_FILE"

run_one() {
    local attack_class="$1"
    local variant="$2"
    local profile_id="$3"
    local run_id="$4"

    local run_name="${attack_class}_${variant}_${run_id}"
    local tag="${run_name}_${profile_id}"

    local done_file="$DONE_DIR/${tag}.done"
    local scheduler_log="$STATE_DIR/${tag}.scheduler.log"

    if [[ -f "$done_file" ]]; then
        echo "[SKIP] 已完成: $tag"
        return 0
    fi

    echo "============================================================"
    echo "[START] $tag"
    echo "[RUN]   PROFILE_ID=$profile_id $RUN_SCRIPT $attack_class $variant $run_id"
    echo "[LOG]   $scheduler_log"
    echo "============================================================"

    PROFILE_ID="$profile_id" \
    "$RUN_SCRIPT" "$attack_class" "$variant" "$run_id" \
        > "$scheduler_log" 2>&1

    local status=$?

    if [[ $status -eq 0 ]]; then
        if grep -q "\[+\] Finished run: ${run_name}" "$scheduler_log"; then
            echo "[OK] 完整完成: $tag"
            {
                echo "DONE_AT=$(date -u +'%Y-%m-%d %H:%M:%S UTC')"
                echo "RUN_NAME=$run_name"
                echo "ATTACK_CLASS=$attack_class"
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
    local attack_class="$1"
    local variant="$2"
    local profile_id="$3"
    local start="$4"
    local end="$5"

    for i in $(seq "$start" "$end"); do
        local run_id
        run_id=$(printf "%02d" "$i")

        run_one "$attack_class" "$variant" "$profile_id" "$run_id"

        sleep 5
    done
}

echo "[INFO] payload_delivery + data_exfiltration batch started at $(date)"
echo "[INFO] state dir: $STATE_DIR"

# ============================================================
# data_exfiltration ftp: 每个 profile 6 轮，总计 18 轮
# ftp_exfil_small_slow     01-06
# ftp_exfil_medium_repeat  07-12
# ftp_exfil_large_burst    13-18
# ============================================================
run_profile data_exfiltration ftp ftp_exfil_small_slow    1 6
run_profile data_exfiltration ftp ftp_exfil_medium_repeat 7 12
run_profile data_exfiltration ftp ftp_exfil_large_burst   13 18

# ============================================================
# data_exfiltration http: 每个 profile 6 轮，总计 18 轮
# http_exfil_small_slow     01-06
# http_exfil_medium_repeat  07-12
# http_exfil_large_burst    13-18
# ============================================================
run_profile data_exfiltration http http_exfil_small_slow    1 6
run_profile data_exfiltration http http_exfil_medium_repeat 7 12
run_profile data_exfiltration http http_exfil_large_burst   13 18

# ============================================================
# payload_delivery ftp: 每个 profile 6 轮，总计 18 轮
# ftp_payload_small_push     01-06
# ftp_payload_medium_repeat  07-12
# ftp_payload_large_burst    13-18
# ============================================================
run_profile payload_delivery ftp ftp_payload_small_push    1 6
run_profile payload_delivery ftp ftp_payload_medium_repeat 7 12
run_profile payload_delivery ftp ftp_payload_large_burst   13 18

# ============================================================
# payload_delivery http: 每个 profile 6 轮，总计 18 轮
# http_payload_small_pull     01-06
# http_payload_medium_repeat  07-12
# http_payload_large_burst    13-18
# ============================================================
run_profile payload_delivery http http_payload_small_pull    1 6
run_profile payload_delivery http http_payload_medium_repeat 7 12
run_profile payload_delivery http http_payload_large_burst   13 18

echo "[INFO] payload_delivery + data_exfiltration batch finished at $(date)"

if [[ -s "$FAILED_FILE" ]]; then
    echo "[WARN] 以下任务失败或可能未完整完成："
    cat "$FAILED_FILE"
else
    echo "[INFO] 没有失败任务记录"
fi
