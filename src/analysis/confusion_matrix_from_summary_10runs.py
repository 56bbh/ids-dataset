import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from pathlib import Path

# =========================
# 1. 基础路径设置
# =========================
# 如果 summary_10runs 不和脚本在同一级目录，
# 就把下面这一行改成你的完整路径，例如：
# BASE_DIR = Path(r"E:\PythonProject\summary_10runs")
BASE_DIR = Path("summary_10runs")

OUTPUT_DIR = BASE_DIR / "confusion_matrix_figures"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# =========================
# 2. 模型与文件名映射
# =========================
model_info = {
    "lstm": "(a) LSTM",
    "resnet1d": "(b) ResNet1D",
    "transformer": "(c) Transformer"
}

# =========================
# 3. 定义浅蓝色配色
# =========================
light_blues = LinearSegmentedColormap.from_list(
    "light_blues",
    plt.cm.Blues(np.linspace(0.0, 0.72, 256))
)

# =========================
# 4. 读取百分比混淆矩阵
# =========================
def load_percent_confusion_matrix(csv_path):
    df = pd.read_csv(csv_path, index_col=0)

    # 尝试全部转成数值，防止读出来是字符串
    df = df.apply(pd.to_numeric, errors="coerce")

    if df.isnull().values.any():
        raise ValueError(f"{csv_path} 中存在无法转换为数值的内容，请检查文件。")

    return df

# =========================
# 5. 绘制单个混淆矩阵
# =========================
def draw_single_cm(cm_percent_df, out_prefix, panel_label=None):
    labels = cm_percent_df.index.tolist()
    cm_percent = cm_percent_df.values.astype(float)

    fig, ax = plt.subplots(figsize=(7.2, 6.6))

    im = ax.imshow(
        cm_percent,
        cmap=light_blues,
        vmin=0,
        vmax=100,
        aspect="equal"
    )

    ax.set_xticks(np.arange(len(labels)))
    ax.set_yticks(np.arange(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.set_yticklabels(labels, fontsize=9)

    ax.set_xlabel("Predicted label", fontsize=11, labelpad=8)
    ax.set_ylabel("True label", fontsize=11)

    # 左上角小字标注模型名（不是标题）
    if panel_label:
        ax.text(
            0.5, -0.34, panel_label,
            transform=ax.transAxes,
            ha="center", va="top",
            fontsize=12
        )

    # 在每个格子中标注百分比
    for i in range(cm_percent.shape[0]):
        for j in range(cm_percent.shape[1]):
            value = cm_percent[i, j]
            if value == 0:
                continue
            ax.text(
                j, i, f"{value:.1f}%",
                ha="center", va="center",
                fontsize=7, color="black"
            )

    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("Percentage (%)", fontsize=11)
    fig.subplots_adjust(bottom=0.34)

    png_path = f"{out_prefix}.png"
    pdf_path = f"{out_prefix}.pdf"

    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    plt.close(fig)

    print(f"已保存: {png_path}")
    print(f"已保存: {pdf_path}")

# =========================
# 6. 主流程
# =========================
def main():
    for model_dir_name, panel_label in model_info.items():
        csv_path = BASE_DIR / model_dir_name / f"{model_dir_name}_accumulated_confusion_matrix_percent.csv"

        if not csv_path.exists():
            print(f"未找到文件，已跳过: {csv_path}")
            continue

        print(f"正在处理: {csv_path}")

        cm_percent_df = load_percent_confusion_matrix(csv_path)

        out_prefix = OUTPUT_DIR / f"{model_dir_name}_accumulated_confusion_matrix_percent"

        draw_single_cm(
            cm_percent_df=cm_percent_df,
            out_prefix=str(out_prefix),
            panel_label=panel_label
        )

    print("\n全部处理完成。")

if __name__ == "__main__":
    main()
