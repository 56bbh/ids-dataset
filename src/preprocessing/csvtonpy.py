import os
import pandas as pd
import numpy as np
import json

# ================= 配置参数 =================
BASE_DIR = "."
STD_CSV_DIR = os.path.join(BASE_DIR, "standardized_csv")
OUTPUT_DIR = os.path.join(BASE_DIR, "model_input")
os.makedirs(OUTPUT_DIR, exist_ok=True)

SEQ_LENGTH = 6
STRIDE = 1

# 标签策略：
# strict: 序列内所有窗口标签完全一致才保留
# ratio : 某个类别在序列中占比达到阈值就保留
# center: 序列标签由中心窗口决定
LABEL_STRATEGY = "ratio"
DOMINANT_RATIO_THRESHOLD = 0.6
LABEL_COL = "label"
NORMAL_LABEL = "normal"

# 新增 attack_subtype / profile_id，避免进入模型特征
META_COLS = [
    'run_id',
    'attack_category',
    'attack_subtype',
    'profile_id',
    'label',
    'window_idx',
    'window_id'
]


# ================= 工具函数 =================
def get_feature_columns(df):
    feature_cols = [c for c in df.columns if c not in META_COLS]

    # 防御性处理：只保留数值列，避免字符串元信息误入模型
    non_numeric_cols = [c for c in feature_cols if not pd.api.types.is_numeric_dtype(df[c])]
    if non_numeric_cols:
        print("  [!] 检测到非数值特征列，已自动排除:")
        for c in non_numeric_cols:
            print(f"      {c}")
        feature_cols = [c for c in feature_cols if c not in non_numeric_cols]

    return feature_cols


def get_idx_column(df):
    if "window_idx" in df.columns:
        return "window_idx"
    if "window_id" in df.columns:
        return "window_id"
    raise ValueError("CSV 中找不到 window_idx 或 window_id 列。")


def safe_first(seq_df, col, default=""):
    if col not in seq_df.columns:
        return default
    val = seq_df[col].iloc[0]
    if pd.isna(val):
        return default
    return val


def assign_sequence_label(seq_df, label_col=LABEL_COL):
    labels = seq_df[label_col].astype(str)

    label_counts = labels.value_counts()
    label_ratios = labels.value_counts(normalize=True)

    dominant_label = label_ratios.index[0]
    dominant_ratio = float(label_ratios.iloc[0])

    label_distribution = {f"count_{label}": int(count) for label, count in label_counts.items()}
    ratio_distribution = {f"ratio_{label}": float(ratio) for label, ratio in label_ratios.items()}

    common_meta = {
        "dominant_label": dominant_label,
        "dominant_ratio": dominant_ratio,
        "num_unique_labels": int(labels.nunique()),
        "is_mixed": int(labels.nunique() > 1),
    }
    common_meta.update(label_distribution)
    common_meta.update(ratio_distribution)

    if LABEL_STRATEGY == "strict":
        if labels.nunique() == 1:
            return labels.iloc[0], False, common_meta
        return None, True, common_meta

    if LABEL_STRATEGY == "ratio":
        if dominant_ratio >= DOMINANT_RATIO_THRESHOLD:
            return dominant_label, False, common_meta
        return None, True, common_meta

    if LABEL_STRATEGY == "center":
        center_pos = len(seq_df) // 2
        center_label = labels.iloc[center_pos]
        common_meta["center_pos"] = int(center_pos)
        common_meta["center_label"] = center_label
        return center_label, False, common_meta

    raise ValueError(f"未知 LABEL_STRATEGY: {LABEL_STRATEGY}，请使用 strict / ratio / center。")


# ================= 主流程 =================
def build_sequences_for_dataset(csv_file_name, prefix):
    csv_path = os.path.join(STD_CSV_DIR, csv_file_name)
    if not os.path.exists(csv_path):
        print(f"[-] 找不到文件 {csv_path}，已跳过。")
        return

    print(f"\n[*] 正在处理 {prefix} 集: {csv_file_name}")
    print(f"[*] 当前标签策略: {LABEL_STRATEGY}")
    if LABEL_STRATEGY == "ratio":
        print(f"[*] ratio 阈值: {DOMINANT_RATIO_THRESHOLD}")

    df = pd.read_csv(csv_path)

    if LABEL_COL not in df.columns:
        raise ValueError(f"CSV 中找不到标签列: {LABEL_COL}")
    if "run_id" not in df.columns:
        raise ValueError("CSV 中找不到 run_id 列。为了避免跨实验滑窗，必须保留 run_id。")

    # 兼容旧 CSV：缺列则补空
    if 'attack_subtype' not in df.columns:
        df['attack_subtype'] = ''
    if 'profile_id' not in df.columns:
        df['profile_id'] = ''

    feature_cols = get_feature_columns(df)
    idx_col = get_idx_column(df)

    print(f"[*] 特征维度数量: {len(feature_cols)}")
    print(f"[*] 使用窗口索引列: {idx_col}")

    X_list = []
    y_list = []
    meta_list = []
    discarded_meta_list = []

    discard_count = 0
    total_seq_count = 0

    # 严格按照 run_id 分组，绝不允许跨实验滑动
    for run_id, group in df.groupby('run_id'):
        group = group.sort_values(by=idx_col).reset_index(drop=True)
        total_windows = len(group)

        if total_windows < SEQ_LENGTH:
            print(f"  [!] run_id={run_id} 窗口数 {total_windows} 小于 SEQ_LENGTH={SEQ_LENGTH}，跳过。")
            continue

        for i in range(0, total_windows - SEQ_LENGTH + 1, STRIDE):
            total_seq_count += 1
            seq_df = group.iloc[i:i + SEQ_LENGTH]

            target_label, should_discard, label_meta = assign_sequence_label(seq_df)

            if should_discard:
                discard_count += 1
                discarded_meta = {
                    "run_id": run_id,
                    "attack_category": safe_first(seq_df, "attack_category"),
                    "attack_subtype": safe_first(seq_df, "attack_subtype"),
                    "profile_id": safe_first(seq_df, "profile_id"),
                    "start_win": seq_df[idx_col].iloc[0],
                    "end_win": seq_df[idx_col].iloc[-1],
                    "dominant_label": label_meta["dominant_label"],
                    "dominant_ratio": label_meta["dominant_ratio"],
                    "num_unique_labels": label_meta["num_unique_labels"],
                }
                discarded_meta_list.append(discarded_meta)
                continue

            seq_features = seq_df[feature_cols].values
            X_list.append(seq_features)
            y_list.append(target_label)

            meta = {
                "run_id": run_id,
                "attack_category": safe_first(seq_df, "attack_category"),
                "attack_subtype": safe_first(seq_df, "attack_subtype"),
                "profile_id": safe_first(seq_df, "profile_id"),
                "start_win": seq_df[idx_col].iloc[0],
                "end_win": seq_df[idx_col].iloc[-1],
                "label": target_label,
                "strategy": LABEL_STRATEGY,
                "seq_length": SEQ_LENGTH,
                "stride": STRIDE,
            }

            if LABEL_STRATEGY == "center":
                center_pos = len(seq_df) // 2
                meta["center_win"] = seq_df[idx_col].iloc[center_pos]

            meta.update(label_meta)
            meta_list.append(meta)

    if not X_list:
        print(f"  [-] 警告: {prefix} 集没有生成任何有效序列！")
        print(f"  [-] 总候选序列数: {total_seq_count}")
        print(f"  [-] 丢弃序列数: {discard_count}")
        return

    X = np.array(X_list)
    y = np.array(y_list)
    df_meta = pd.DataFrame(meta_list)

    print("  [+] 序列生成完毕！")
    print(f"  [+] 总候选序列数: {total_seq_count}")
    print(f"  [+] 保留序列数: {len(X_list)}")
    print(f"  [+] 丢弃序列数: {discard_count}")
    print(f"  [+] X shape: {X.shape}")
    print(f"  [+] y shape: {y.shape}")

    print("  [+] 生成后的类别分布:")
    for label, count in pd.Series(y).value_counts().items():
        print(f"      {label}: {count}")

    out_suffix = f"{prefix}_{LABEL_STRATEGY}_T{SEQ_LENGTH}_S{STRIDE}"
    if LABEL_STRATEGY == "ratio":
        threshold_str = str(DOMINANT_RATIO_THRESHOLD).replace(".", "p")
        out_suffix = f"{prefix}_{LABEL_STRATEGY}{threshold_str}_T{SEQ_LENGTH}_S{STRIDE}"

    np.save(os.path.join(OUTPUT_DIR, f"X_{out_suffix}.npy"), X)
    np.save(os.path.join(OUTPUT_DIR, f"y_{out_suffix}.npy"), y)
    df_meta.to_csv(os.path.join(OUTPUT_DIR, f"meta_{out_suffix}.csv"), index=False)

    with open(os.path.join(OUTPUT_DIR, f"feature_cols_{out_suffix}.json"), "w", encoding="utf-8") as f:
        json.dump(feature_cols, f, ensure_ascii=False, indent=2)

    if discarded_meta_list:
        pd.DataFrame(discarded_meta_list).to_csv(
            os.path.join(OUTPUT_DIR, f"discarded_{out_suffix}.csv"),
            index=False
        )

    print(f"  [+] 已保存到目录: {OUTPUT_DIR}")
    print(f"      X_{out_suffix}.npy")
    print(f"      y_{out_suffix}.npy")
    print(f"      meta_{out_suffix}.csv")
    print(f"      feature_cols_{out_suffix}.json")
    if discarded_meta_list:
        print(f"      discarded_{out_suffix}.csv")


def main():
    build_sequences_for_dataset("train_windows_std.csv", "train")
    build_sequences_for_dataset("val_windows_std.csv", "val")
    build_sequences_for_dataset("test_windows_std.csv", "test")

    print("\n[完成] 全部数据处理完毕。")
    print(f"输出目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
