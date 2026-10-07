import os
import glob
import random
import json
import pandas as pd

# ================= 配置参数 =================
BASE_DIR = "."
LABELED_CSV_DIR = os.path.join(BASE_DIR, "labeled_csv")
OUTPUT_DIR = os.path.join(BASE_DIR, "standardized_csv")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 避免元信息进入模型特征
META_COLS = [
    'run_id',
    'attack_category',
    'attack_subtype',
    'profile_id',
    'label',
    'window_idx',
    'window_id'
]

EPSILON = 1e-6
RANDOM_STATE = 42

# 可选值：category / subtype / profile
# profile = 以 attack_category + attack_subtype + profile_id 为分层桶，桶内按 run 划分 train / val / test
SPLIT_LEVEL = "profile"

# 小样本桶划分策略：优先保证 train 和 test；run >= 3 时 val 也有样本
TRAIN_RATIO = 0.7
VAL_RATIO = 0.1
TEST_RATIO = 0.2

# profile 模式下是否强制要求 profile_id 非空
# 正式实验建议 True，避免空 profile_id 被合并成 default 桶
REQUIRE_PROFILE_ID_FOR_PROFILE_SPLIT = True

# 是否保存标准化参数和 run / profile 划分信息
SAVE_SPLIT_INFO = True
SAVE_PROFILE_SPLIT_CHECK = True
SAVE_STANDARDIZATION_PARAMS = True
SAVE_FEATURE_COLUMNS = True


# ================= 工具函数 =================
def normalize_meta_value(value, default=""):
    """统一处理缺失元信息。"""
    if pd.isna(value):
        return default
    value = str(value).strip()
    if value.lower() in {"nan", "none", "null"}:
        return default
    return value


def infer_subtype_from_run_id(run_id, category):
    """
    当 CSV 中没有 attack_subtype 时，根据 run_id 做一个尽量稳健的推断。

    示例：
        brute_force_ftp_02 -> ftp
        data_exfiltration_http_01 -> http
        backdoor_shell_bind_persistent_01 -> bind_persistent
        ssh_telnet_abuse_telnet_04 -> telnet 或 telnet_abuse，取决于命名
    """
    if not run_id or not category:
        return "unknown"

    prefix = f"{category}_"
    if not run_id.startswith(prefix):
        return "unknown"

    rest = run_id[len(prefix):]
    parts = rest.split("_")

    # 去掉最后的轮次编号，如 01 / 001 / run01 等简单数字形式
    if parts and parts[-1].isdigit():
        parts = parts[:-1]

    if not parts:
        return "unknown"

    return "_".join(parts)


def make_split_key(row):
    category = normalize_meta_value(row.get('category'), 'unknown')
    subtype = normalize_meta_value(row.get('subtype'), '')
    profile_id = normalize_meta_value(row.get('profile_id'), '')

    if SPLIT_LEVEL == "category":
        return category

    if SPLIT_LEVEL == "subtype":
        subtype_part = subtype if subtype else 'unknown'
        return f"{category}__{subtype_part}"

    if SPLIT_LEVEL == "profile":
        subtype_part = subtype if subtype else 'unknown'
        profile_part = profile_id if profile_id else 'default'
        return f"{category}__{subtype_part}__{profile_part}"

    raise ValueError("SPLIT_LEVEL 必须是 category / subtype / profile")


def split_small_group(runs, rng):
    """
    对一个 split_key 内的 run 做划分。
    n=3 时 train/val/test 各 1 个；n=6 时通常 train=4, val=1, test=1。
    """
    runs = list(runs)
    rng.shuffle(runs)
    n = len(runs)

    if n <= 0:
        return [], [], []
    if n == 1:
        return runs, [], []
    if n == 2:
        return [runs[0]], [], [runs[1]]
    if n == 3:
        return [runs[0]], [runs[1]], [runs[2]]
    if n == 4:
        return runs[:2], [runs[2]], [runs[3]]

    # n >= 5：按比例，且 val/test 至少 1
    num_test = max(1, int(round(n * TEST_RATIO)))
    num_val = max(1, int(round(n * VAL_RATIO)))

    # 避免 val + test 过多挤压 train
    if num_val + num_test >= n:
        num_test = 1
        num_val = 1

    num_train = n - num_val - num_test

    train_runs = runs[:num_train]
    val_runs = runs[num_train:num_train + num_val]
    test_runs = runs[num_train + num_val:]

    return train_runs, val_runs, test_runs


def validate_run_info(info_df):
    """正式划分前做必要的数据一致性检查。"""
    if not info_df['run_id'].is_unique:
        dup = info_df[info_df['run_id'].duplicated(keep=False)].sort_values('run_id')
        print("\n[-] 检测到重复 run_id:")
        print(dup[['run_id', 'category', 'subtype', 'profile_id', 'file']])
        raise ValueError("run_id 必须唯一，否则 split 和加载会出错。")

    if SPLIT_LEVEL == "profile" and REQUIRE_PROFILE_ID_FOR_PROFILE_SPLIT:
        missing_profile = info_df[info_df['profile_id'].astype(str).str.strip() == ""]
        if not missing_profile.empty:
            print("\n[-] 以下 run 缺少 profile_id，不能进行可靠的 profile-stratified run-level split:")
            print(missing_profile[['run_id', 'category', 'subtype', 'file']])
            raise ValueError("SPLIT_LEVEL='profile' 时建议所有 run 都有 profile_id。")

    if info_df['run_id'].astype(str).str.strip().eq('').any():
        bad = info_df[info_df['run_id'].astype(str).str.strip() == '']
        print("\n[-] 检测到空 run_id:")
        print(bad[['category', 'subtype', 'profile_id', 'file']])
        raise ValueError("run_id 不能为空。")


def print_distribution(info_df, split_name, run_list):
    sub = info_df[info_df['run_id'].isin(run_list)]
    print(f"\n[{split_name}] category distribution:")
    print(sub['category'].value_counts() if not sub.empty else "  (empty)")

    print(f"\n[{split_name}] category + subtype distribution:")
    if sub.empty:
        print("  (empty)")
    else:
        print(sub.groupby(['category', 'subtype']).size().sort_values(ascending=False))

    if SPLIT_LEVEL == "profile":
        print(f"\n[{split_name}] category + subtype + profile_id distribution:")
        if sub.empty:
            print("  (empty)")
        else:
            print(sub.groupby(['category', 'subtype', 'profile_id']).size().sort_values(ascending=False))


def save_standardization_params(mu, sigma, feature_cols):
    params_df = pd.DataFrame({
        'feature': feature_cols,
        'mean': [float(mu[c]) for c in feature_cols],
        'std': [float(sigma[c]) for c in feature_cols],
    })
    csv_path = os.path.join(OUTPUT_DIR, "standardization_params.csv")
    json_path = os.path.join(OUTPUT_DIR, "standardization_params.json")
    params_df.to_csv(csv_path, index=False)
    params_df.to_json(json_path, orient='records', force_ascii=False, indent=2)
    print("[+] 标准化参数已保存:")
    print(f"    {csv_path}")
    print(f"    {json_path}")


def save_feature_columns(feature_cols):
    out_path = os.path.join(OUTPUT_DIR, "feature_columns.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(list(feature_cols), f, ensure_ascii=False, indent=2)
    print(f"[+] 特征列顺序已保存: {out_path}")


def save_split_info(info_df, train_runs, val_runs, test_runs):
    split_records = []
    for r in train_runs:
        split_records.append({'run_id': r, 'split': 'train'})
    for r in val_runs:
        split_records.append({'run_id': r, 'split': 'val'})
    for r in test_runs:
        split_records.append({'run_id': r, 'split': 'test'})

    split_df = pd.DataFrame(split_records)
    split_df = split_df.merge(info_df, on='run_id', how='left')

    split_out = os.path.join(OUTPUT_DIR, "run_split_info.csv")
    split_df.to_csv(split_out, index=False)
    print(f"\n[+] run 划分信息已保存: {split_out}")

    if SAVE_PROFILE_SPLIT_CHECK and SPLIT_LEVEL == "profile":
        profile_check = (
            split_df
            .groupby(['category', 'subtype', 'profile_id', 'split'])
            .size()
            .unstack(fill_value=0)
            .reset_index()
        )

        # 保证列顺序稳定
        for col in ['train', 'val', 'test']:
            if col not in profile_check.columns:
                profile_check[col] = 0
        profile_check = profile_check[['category', 'subtype', 'profile_id', 'train', 'val', 'test']]
        profile_check['total'] = profile_check[['train', 'val', 'test']].sum(axis=1)

        profile_out = os.path.join(OUTPUT_DIR, "profile_split_check.csv")
        profile_check.to_csv(profile_out, index=False)
        print(f"[+] profile 分配检查表已保存: {profile_out}")
        print("\n[*] profile 分配检查预览:")
        print(profile_check.sort_values(['category', 'subtype', 'profile_id']).to_string(index=False))

    return split_df


# ================= 主流程 =================
def split_and_standardize():
    all_files = glob.glob(os.path.join(LABELED_CSV_DIR, "*.csv"))
    if not all_files:
        print("[-] 未找到打好标签的 CSV 文件，请检查 labeled_csv 目录。")
        return

    print(f"[*] 找到 labeled CSV 文件数量: {len(all_files)}")

    # 1. 提取所有 Run 的宏观信息
    run_info = []
    for f in all_files:
        try:
            df_head = pd.read_csv(f, nrows=1)
            run_id = normalize_meta_value(df_head['run_id'].iloc[0])
            category = normalize_meta_value(df_head['attack_category'].iloc[0])

            if 'attack_subtype' in df_head.columns:
                subtype = normalize_meta_value(df_head['attack_subtype'].iloc[0])
            else:
                subtype = ""

            if not subtype:
                subtype = infer_subtype_from_run_id(run_id, category)

            if 'profile_id' in df_head.columns:
                profile_id = normalize_meta_value(df_head['profile_id'].iloc[0])
            else:
                profile_id = ""

            run_info.append({
                'run_id': run_id,
                'category': category,
                'subtype': subtype,
                'profile_id': profile_id,
                'file': f,
            })
        except Exception as e:
            print(f"[-] 读取 {f} 失败: {e}")

    info_df = pd.DataFrame(run_info)
    if info_df.empty:
        print("[-] 没有成功读取任何 run 信息。")
        return

    validate_run_info(info_df)
    info_df['split_key'] = info_df.apply(make_split_key, axis=1)

    print(f"[*] 成功读取 run 数量: {len(info_df)}")
    print(f"[*] 当前划分级别 SPLIT_LEVEL = {SPLIT_LEVEL}")

    print("\n[*] 全部 run 的类别分布:")
    print(info_df['category'].value_counts())

    print("\n[*] 全部 run 的 category + subtype 分布:")
    print(info_df.groupby(['category', 'subtype']).size().sort_values(ascending=False))

    if SPLIT_LEVEL == "profile":
        print("\n[*] 全部 run 的 category + subtype + profile_id 分布:")
        print(info_df.groupby(['category', 'subtype', 'profile_id']).size().sort_values(ascending=False))

    # 2. 按 split_key 分层桶进行 run-level 划分
    rng = random.Random(RANDOM_STATE)
    train_runs, val_runs, test_runs = [], [], []

    print("\n[*] 按 split_key 进行 run 级别划分:")
    for split_key, group in info_df.groupby('split_key'):
        runs = group['run_id'].tolist()
        tr, va, te = split_small_group(runs, rng)

        train_runs.extend(tr)
        val_runs.extend(va)
        test_runs.extend(te)

        cats = group[['category', 'subtype', 'profile_id']].drop_duplicates().to_dict('records')
        desc = ", ".join([
            f"{x['category']}:{x['subtype']}:{x['profile_id']}" for x in cats
        ])
        print(f"  -> {split_key} ({desc}) | total={len(runs)} train={len(tr)} val={len(va)} test={len(te)}")

    # 3. run_id 泄漏检查
    train_set, val_set, test_set = set(train_runs), set(val_runs), set(test_runs)
    assert train_set.isdisjoint(val_set), "train 和 val 存在重复 run_id"
    assert train_set.isdisjoint(test_set), "train 和 test 存在重复 run_id"
    assert val_set.isdisjoint(test_set), "val 和 test 存在重复 run_id"
    print("\n[+] run_id 泄漏检查通过：train / val / test 之间没有重复 run。")

    print(f"\n[*] 数据集 Run 级别划分结果:")
    print(f"  -> 总实验轮次: {len(info_df)}")
    print(f"  -> 训练集 Train 轮次: {len(train_runs)}")
    print(f"  -> 验证集 Val 轮次: {len(val_runs)}")
    print(f"  -> 测试集 Test 轮次: {len(test_runs)}")

    print_distribution(info_df, "TRAIN", train_runs)
    print_distribution(info_df, "VAL", val_runs)
    print_distribution(info_df, "TEST", test_runs)

    if SAVE_SPLIT_INFO:
        save_split_info(info_df, train_runs, val_runs, test_runs)

    # 4. 加载真实数据
    def load_sets(run_list, name):
        dfs = []
        for r in run_list:
            rows = info_df[info_df['run_id'] == r]
            if rows.empty:
                continue
            f = rows['file'].values[0]
            dfs.append(pd.read_csv(f))
        out = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        print(f"  [+] {name} 集加载完成 | shape: {out.shape} | window 数: {len(out)}")
        return out

    print("\n[*] 正在将各 split 的 run 数据拼装为大表...")
    train_df = load_sets(train_runs, "Train")
    val_df = load_sets(val_runs, "Validation")
    test_df = load_sets(test_runs, "Test")

    if train_df.empty:
        print("[-] 训练集为空，程序终止。")
        return

    # 保证新版 meta 列存在，防止旧文件缺列造成后续不一致
    for df in [train_df, val_df, test_df]:
        if df.empty:
            continue
        if 'attack_subtype' not in df.columns:
            df['attack_subtype'] = ''
        if 'profile_id' not in df.columns:
            df['profile_id'] = ''

    # 5. 只在训练集上计算 μ 和 σ
    feature_cols = [c for c in train_df.columns if c not in META_COLS]

    # 只保留数值列，避免字符串列误入模型特征
    non_numeric_cols = [c for c in feature_cols if not pd.api.types.is_numeric_dtype(train_df[c])]
    if non_numeric_cols:
        print("[!] 检测到非数值特征列，已自动从 feature_cols 中排除:")
        for c in non_numeric_cols:
            print(f"    {c}")
        feature_cols = [c for c in feature_cols if c not in non_numeric_cols]

    print(f"\n[*] 最终用于模型的特征列数量: {len(feature_cols)}")
    print("[*] Feature columns:")
    for c in feature_cols:
        print(f"    {c}")

    if SAVE_FEATURE_COLUMNS:
        save_feature_columns(feature_cols)

    print("\n[*] 正在训练集上计算均值 μ 和标准差 σ...")
    mu = train_df[feature_cols].mean()
    sigma = train_df[feature_cols].std()
    sigma = sigma.replace(0, EPSILON)

    if SAVE_STANDARDIZATION_PARAMS:
        save_standardization_params(mu, sigma, feature_cols)

    # 6. 标准化
    def standardize(df, mu, sigma, name):
        if df.empty:
            return df
        df_std = df.copy()
        df_std[feature_cols] = (df[feature_cols] - mu) / sigma
        print(f"  [+] {name} 集已标准化 | shape: {df_std.shape} | window 数: {len(df_std)}")
        return df_std

    print("\n[*] 正在标准化 train / val / test...")
    train_std = standardize(train_df, mu, sigma, "Train")
    val_std = standardize(val_df, mu, sigma, "Validation")
    test_std = standardize(test_df, mu, sigma, "Test")

    # 7. 保存标准化大表
    train_std.to_csv(os.path.join(OUTPUT_DIR, "train_windows_std.csv"), index=False)
    if not val_std.empty:
        val_std.to_csv(os.path.join(OUTPUT_DIR, "val_windows_std.csv"), index=False)
    if not test_std.empty:
        test_std.to_csv(os.path.join(OUTPUT_DIR, "test_windows_std.csv"), index=False)

    print(f"\n[*] 预处理阶段完成！输出目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    split_and_standardize()
