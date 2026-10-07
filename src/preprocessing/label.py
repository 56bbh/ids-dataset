import os
import glob
import pandas as pd
import re
from datetime import datetime
import numpy as np

# ================= 配置参数 =================
BASE_DIR = "."
CSV_DIR = os.path.join(BASE_DIR, "csv")
NOTES_DIR = os.path.join(BASE_DIR, "notes")
OUTPUT_DIR = os.path.join(BASE_DIR, "labeled_csv")
WINDOW_SIZE = 5.0

os.makedirs(OUTPUT_DIR, exist_ok=True)


def get_attack_category(run_id):
    """根据 run_id 推导攻击大类。notes 里没有 ATTACK_CATEGORY 时使用。"""
    run_id_lower = run_id.lower()

    if run_id_lower.startswith("backdoor_shell"):
        return "backdoor_shell"
    if run_id_lower.startswith("brute_force"):
        return "brute_force"
    if run_id_lower.startswith("data_exfiltration"):
        return "data_exfiltration"
    if run_id_lower.startswith("dos"):
        return "dos"
    if run_id_lower.startswith("exploit"):
        return "exploit"
    if run_id_lower.startswith("http_attack"):
        return "http_attack"
    if run_id_lower.startswith("normal"):
        return "normal"
    if run_id_lower.startswith("payload_delivery"):
        return "payload_delivery"
    if run_id_lower.startswith("scanning"):
        return "scanning"
    if run_id_lower.startswith("ssh_telnet_abuse"):
        return "ssh_telnet_abuse"

    parts = run_id.split('_')
    if len(parts) >= 3:
        return "_".join(parts[:-2])

    return "attack"


def parse_experiment_log(log_path):
    """
    解析 notes 日志。

    返回：
        t_pcap_start
        t_attack_start
        t_attack_end
        t_pcap_stop
        metadata dict
    """
    t_pcap_start = None
    t_attack_start = None
    t_attack_end = None
    t_pcap_stop = None

    metadata = {}

    with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue

            # 解析 KEY=VALUE 类型元信息
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key:
                    metadata[key] = value

            # 解析时间
            time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
            if not time_match:
                continue

            dt = datetime.strptime(time_match.group(1), "%Y-%m-%d %H:%M:%S")

            if line.startswith("PCAP_START"):
                t_pcap_start = dt
            elif line.startswith("ATTACK_START"):
                t_attack_start = dt
            elif line.startswith("ATTACK_END"):
                t_attack_end = dt
            elif line.startswith("PCAP_STOP"):
                t_pcap_stop = dt

    return t_pcap_start, t_attack_start, t_attack_end, t_pcap_stop, metadata


def compute_expected_max_idx(t_pcap_start, t_pcap_stop):
    """
    根据 PCAP_START 和 PCAP_STOP 计算应该补到的最大 window_idx。
    使用相对时间。
    """
    if t_pcap_start is None or t_pcap_stop is None:
        return None

    capture_duration = (t_pcap_stop - t_pcap_start).total_seconds()

    if capture_duration <= 0:
        return None

    # 例如 duration=318 秒，WINDOW_SIZE=3，则窗口为 0~105
    return int(np.ceil(capture_duration / WINDOW_SIZE)) - 1


def label_and_pad_windows():
    csv_files = glob.glob(os.path.join(CSV_DIR, "*.csv"))

    if not csv_files:
        print("[-] 未在 csv 目录下找到文件，请检查路径。")
        return

    for csv_path in csv_files:
        file_name = os.path.basename(csv_path)
        inferred_run_id = file_name.replace("_features.csv", "").replace("_windows.csv", "")
        log_path = os.path.join(NOTES_DIR, f"{inferred_run_id}_notes.log")

        if not os.path.exists(log_path):
            print(f"[跳过] 找不到 notes: {inferred_run_id}")
            continue

        print(f"\n[*] 正在处理: {inferred_run_id}")

        t_pcap_start, t_attack_start, t_attack_end, t_pcap_stop, metadata = parse_experiment_log(log_path)

        if not all([t_pcap_start, t_attack_start, t_attack_end]):
            print("  [-] 日志缺少 PCAP_START / ATTACK_START / ATTACK_END，跳过。")
            continue

        # notes 里有 RUN_ID 就使用，否则用文件名推断
        run_id = metadata.get("RUN_ID", inferred_run_id)

        # notes 里有 ATTACK_CATEGORY 就使用，否则根据 run_id 推断
        attack_category = metadata.get("ATTACK_CATEGORY", get_attack_category(run_id))

        # notes 里有就填，没有就留空
        attack_subtype = metadata.get("ATTACK_SUBTYPE", "")
        profile_id = metadata.get("PROFILE_ID", "")

        rel_attack_start = (t_attack_start - t_pcap_start).total_seconds()
        rel_attack_end = (t_attack_end - t_pcap_start).total_seconds()

        df = pd.read_csv(csv_path)

        idx_col = 'window_idx' if 'window_idx' in df.columns else 'window_id'

        if idx_col not in df.columns:
            print(f"  [-] 找不到 window_idx/window_id 列，跳过。")
            continue

        # 防止异常巨型时间戳
        if not df.empty and df[idx_col].max() > 100000:
            df[idx_col] = df.index

        existing_max_idx = int(df[idx_col].max()) if not df.empty else -1
        expected_max_idx = compute_expected_max_idx(t_pcap_start, t_pcap_stop)

        if expected_max_idx is not None:
            max_idx = max(existing_max_idx, expected_max_idx)
        else:
            # 如果没有 PCAP_STOP，就退回旧逻辑
            max_idx = existing_max_idx
            print("  [!] notes 中没有 PCAP_STOP，padding 只能补到已有最大 window_idx。")

        if max_idx < 0:
            print("  [-] 无有效窗口，跳过。")
            continue

        # ==========================================
        # 时间轴连续性补全
        # ==========================================
        complete_idx = pd.DataFrame({idx_col: range(0, max_idx + 1)})
        df_padded = pd.merge(complete_idx, df, on=idx_col, how='left')

        missing_count = df_padded['pkt_count'].isna().sum() if 'pkt_count' in df_padded.columns else 0

        if missing_count > 0:
            print(f"  [!] 已自动补全 {missing_count} 个静默窗口。")

        # 所有缺失特征填 0
        df_padded = df_padded.fillna(0)

        # 补充 / 覆盖元信息列
        df_padded['run_id'] = run_id
        df_padded['attack_category'] = attack_category
        df_padded['attack_subtype'] = attack_subtype
        df_padded['profile_id'] = profile_id

        # ==========================================
        # 根据相对时间打标
        # ==========================================
        labels = []

        for _, row in df_padded.iterrows():
            win_idx = row[idx_col]
            win_start = win_idx * WINDOW_SIZE
            win_end = win_start + WINDOW_SIZE

            # 只要窗口与攻击区间有重叠，就标为该攻击大类
            if win_end > rel_attack_start and win_start < rel_attack_end:
                labels.append(attack_category)
            else:
                labels.append('normal')

        if 'label' in df_padded.columns:
            df_padded.drop(columns=['label'], inplace=True)

        # 调整列顺序
        preferred_front_cols = [
            'run_id',
            'attack_category',
            'attack_subtype',
            'profile_id',
            'label',
            idx_col
        ]

        df_padded['label'] = labels

        existing_front_cols = [c for c in preferred_front_cols if c in df_padded.columns]
        remaining_cols = [c for c in df_padded.columns if c not in existing_front_cols]
        df_padded = df_padded[existing_front_cols + remaining_cols]

        out_path = os.path.join(OUTPUT_DIR, file_name)
        df_padded.to_csv(out_path, index=False)

        label_counts = df_padded['label'].value_counts().to_dict()

        print(f"  [+] run_id: {run_id}")
        print(f"  [+] category: {attack_category}")
        print(f"  [+] subtype: {attack_subtype if attack_subtype else '(empty)'}")
        print(f"  [+] profile_id: {profile_id if profile_id else '(empty)'}")
        print(f"  [+] 标签分布: {label_counts}")
        print(f"  [+] 输出: {out_path}")


if __name__ == "__main__":
    label_and_pad_windows()