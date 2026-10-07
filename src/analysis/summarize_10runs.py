from pathlib import Path
import re
import numpy as np
import pandas as pd


# ==============================
# 这里是你以后主要需要改的地方
# ==============================

# 你的总结果目录
# 如果 summarize_10runs.py 和 resnet1d_results / lstm_results / transformer_results 在同一级，
# 这里就写 "."
ROOT_DIR = Path(".")

# 三个模型的配置
MODEL_CONFIGS = {
    "lstm": {
        "result_dir": "lstm_results",
        "exp_folder_prefix": "lstm_ratio0p6_T10_S1",
        "file_prefix": "lstm",
    },
    "resnet1d": {
        "result_dir": "resnet1d_results",
        "exp_folder_prefix": "resnet1d_ratio0p6_T10_S1",
        "file_prefix": "resnet1d",
    },
    "transformer": {
        "result_dir": "transformer_results",
        "exp_folder_prefix": "transformer_ratio0p6_T10_S1",
        "file_prefix": "transformer",
    },
}

# 类别顺序，必须和你的混淆矩阵行列顺序一致
CLASSES = [
    "backdoor_shell",
    "brute_force",
    "data_exfiltration",
    "exploit",
    "http_attack",
    "normal",
    "payload_delivery",
    "scanning",
    "ssh_telnet_abuse",
]

# 输出目录
OUTPUT_DIR = Path("summary_10runs")


# ==============================
# 下面一般不用改
# ==============================

def find_latest_experiment_dir(model_result_dir: Path, exp_folder_prefix: str) -> Path:
    """
    在模型结果目录下，自动寻找最新的实验文件夹。
    例如 transformer_results/transformer_ratio0p6_T10_S1_20260607_114244
    """
    candidates = [
        p for p in model_result_dir.iterdir()
        if p.is_dir() and p.name.startswith(exp_folder_prefix)
    ]

    if not candidates:
        raise FileNotFoundError(
            f"没有找到实验文件夹：{model_result_dir / (exp_folder_prefix + '_*')}"
        )

    # 按文件夹名字排序，时间戳大的通常在最后
    candidates = sorted(candidates, key=lambda x: x.name)
    return candidates[-1]


def find_run_dirs(exp_dir: Path):
    """
    找 run_01_seed42 到 run_10_seedxxx。
    只要求前面是 run_01 到 run_10，后面的 seed 可以不同。
    """
    run_dirs = []

    for p in exp_dir.iterdir():
        if not p.is_dir():
            continue

        m = re.match(r"run_(\d{2})_seed", p.name)
        if m:
            run_id = int(m.group(1))
            if 1 <= run_id <= 10:
                run_dirs.append((run_id, p))

    run_dirs = sorted(run_dirs, key=lambda x: x[0])

    found_ids = [x[0] for x in run_dirs]
    expected_ids = list(range(1, 11))

    missing = sorted(set(expected_ids) - set(found_ids))
    if missing:
        raise FileNotFoundError(
            f"{exp_dir} 缺少这些 run 文件夹：{missing}"
        )

    return [p for _, p in run_dirs]


def read_confusion_matrix(cm_path: Path) -> pd.DataFrame:
    """
    读取混淆矩阵 csv。
    兼容第一列是类别名的格式。
    """
    cm_df = pd.read_csv(cm_path, index_col=0)

    # 保证行列顺序一致
    cm_df = cm_df.loc[CLASSES, CLASSES]

    return cm_df


def read_classification_report(report_path: Path) -> pd.DataFrame:
    """
    读取 classification_report csv。
    要求里面有 f1-score 这一列。
    """
    report_df = pd.read_csv(report_path, index_col=0)

    if "f1-score" not in report_df.columns:
        raise ValueError(f"{report_path} 中没有 f1-score 列")

    return report_df


def per_class_f1_from_cm(cm_df: pd.DataFrame) -> pd.Series:
    """
    如果 classification_report 读不到，也可以从混淆矩阵计算 per-class F1。
    """
    cm = cm_df.values.astype(float)

    f1_scores = {}

    for i, cls in enumerate(CLASSES):
        tp = cm[i, i]
        fn = cm[i, :].sum() - tp
        fp = cm[:, i].sum() - tp

        denom = 2 * tp + fp + fn
        f1 = 0.0 if denom == 0 else (2 * tp) / denom

        f1_scores[cls] = f1

    return pd.Series(f1_scores)


def summarize_one_model(model_name: str, config: dict):
    print(f"\n==============================")
    print(f"正在处理模型：{model_name}")
    print(f"==============================")

    model_result_dir = ROOT_DIR / config["result_dir"]
    exp_folder_prefix = config["exp_folder_prefix"]
    file_prefix = config["file_prefix"]

    if not model_result_dir.exists():
        raise FileNotFoundError(f"模型结果目录不存在：{model_result_dir}")

    exp_dir = find_latest_experiment_dir(model_result_dir, exp_folder_prefix)
    print(f"使用实验目录：{exp_dir}")

    run_dirs = find_run_dirs(exp_dir)

    all_cms = []
    all_f1 = []

    for run_dir in run_dirs:
        run_name = run_dir.name
        print(f"读取：{run_name}")

        report_path = run_dir / f"{file_prefix}_test_classification_report.csv"
        cm_path = run_dir / f"{file_prefix}_test_confusion_matrix.csv"

        if not cm_path.exists():
            raise FileNotFoundError(f"找不到混淆矩阵文件：{cm_path}")

        cm_df = read_confusion_matrix(cm_path)
        all_cms.append(cm_df)

        if report_path.exists():
            report_df = read_classification_report(report_path)

            # 只取九个类别，不取 accuracy / macro avg / weighted avg
            run_f1 = report_df.loc[CLASSES, "f1-score"]
        else:
            print(f"没有找到 classification_report，改为从混淆矩阵计算 F1：{report_path}")
            run_f1 = per_class_f1_from_cm(cm_df)

        run_f1.name = run_name
        all_f1.append(run_f1)

    # ==============================
    # 1. 十轮混淆矩阵累加
    # ==============================
    cm_total = sum(all_cms)

    # ==============================
    # 2. 行归一化百分比
    # ==============================
    row_sum = cm_total.sum(axis=1).replace(0, np.nan)
    cm_percent = cm_total.div(row_sum, axis=0) * 100
    cm_percent = cm_percent.fillna(0)

    # ==============================
    # 3. per-class F1 mean ± std
    # ==============================
    f1_runs_df = pd.DataFrame(all_f1)

    f1_summary = pd.DataFrame({
        "Category": CLASSES,
        f"{model_name}_F1_mean": [f1_runs_df[c].mean() for c in CLASSES],
        f"{model_name}_F1_std": [f1_runs_df[c].std(ddof=1) for c in CLASSES],
    })

    f1_summary[f"{model_name}_F1_mean±std"] = (
        f1_summary[f"{model_name}_F1_mean"].map(lambda x: f"{x:.4f}")
        + " ± "
        + f1_summary[f"{model_name}_F1_std"].map(lambda x: f"{x:.4f}")
    )

    # ==============================
    # 4. 保存结果
    # ==============================
    model_out_dir = OUTPUT_DIR / model_name
    model_out_dir.mkdir(parents=True, exist_ok=True)

    cm_total.to_csv(
        model_out_dir / f"{model_name}_accumulated_confusion_matrix_counts.csv",
        encoding="utf-8-sig"
    )

    cm_percent.to_csv(
        model_out_dir / f"{model_name}_accumulated_confusion_matrix_percent.csv",
        encoding="utf-8-sig"
    )

    f1_runs_df.to_csv(
        model_out_dir / f"{model_name}_per_class_f1_each_run.csv",
        encoding="utf-8-sig"
    )

    f1_summary.to_csv(
        model_out_dir / f"{model_name}_per_class_f1_mean_std.csv",
        index=False,
        encoding="utf-8-sig"
    )

    print(f"\n已保存：")
    print(model_out_dir / f"{model_name}_accumulated_confusion_matrix_counts.csv")
    print(model_out_dir / f"{model_name}_accumulated_confusion_matrix_percent.csv")
    print(model_out_dir / f"{model_name}_per_class_f1_each_run.csv")
    print(model_out_dir / f"{model_name}_per_class_f1_mean_std.csv")

    return f1_summary


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_model_f1_tables = []

    for model_name, config in MODEL_CONFIGS.items():
        f1_summary = summarize_one_model(model_name, config)

        # 只保留最终论文需要的 mean±std 字符串列
        keep = f1_summary[["Category", f"{model_name}_F1_mean±std"]]
        all_model_f1_tables.append(keep)

    # 合并三个模型的 Table 8
    table8 = all_model_f1_tables[0]

    for t in all_model_f1_tables[1:]:
        table8 = table8.merge(t, on="Category", how="outer")

    table8.to_csv(
        OUTPUT_DIR / "table8_per_class_f1_all_models.csv",
        index=False,
        encoding="utf-8-sig"
    )

    print("\n==============================")
    print("全部模型处理完成")
    print("==============================")
    print(f"论文 Table 8 可用文件：{OUTPUT_DIR / 'table8_per_class_f1_all_models.csv'}")


if __name__ == "__main__":
    main()