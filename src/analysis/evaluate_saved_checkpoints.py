"""Evaluate frozen validation checkpoints on the held-out test split only."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from torch.utils.data import DataLoader, TensorDataset

MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
if str(MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DIR))

from temporal_order_utils import SPLIT_SEED_OFFSETS, shuffle_within_sequences
from train_lstm_baseline import LSTMClassifier
from train_mlp_baseline import MLPClassifier
from train_resnet1d_baseline import ResNet1DClassifier
from train_transformer_baseline import TransformerEncoderClassifier


MODEL_DIRS = ("mlp", "lstm", "resnet1d", "transformer")
RUN_PATTERN = re.compile(r"run_(\d+)_seed(\d+)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    script_dir = Path(__file__).resolve().parent
    parser.add_argument("--checkpoint-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--temporal-data-dir",
        type=Path,
        default=script_dir / "3s" / "model_input_3st10",
    )
    parser.add_argument(
        "--non-temporal-data-dir",
        type=Path,
        default=script_dir / "3s" / "model_input_non_temporal_orderless_3st10",
    )
    parser.add_argument("--suffix", default="ratio0p6_T10_S1")
    parser.add_argument("--expected-epochs", type=int, default=50)
    parser.add_argument("--group-pattern", default="*")
    parser.add_argument("--limit-runs", type=int)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    return parser.parse_args()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def encode_labels(y: np.ndarray, labels: list[str]) -> np.ndarray:
    label_to_id = {label: idx for idx, label in enumerate(labels)}
    values = y.astype(str)
    unknown = sorted(set(values) - set(label_to_id))
    if unknown:
        raise ValueError(f"Unknown test labels: {unknown}")
    return np.array([label_to_id[value] for value in values], dtype=np.int64)


def make_loader(
    x: np.ndarray,
    y: np.ndarray,
    batch_size: int,
    device: torch.device,
) -> DataLoader:
    dataset = TensorDataset(
        torch.from_numpy(x.astype(np.float32, copy=False)),
        torch.from_numpy(y),
    )
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=0,
        pin_memory=device.type == "cuda",
    )


@torch.inference_mode()
def predict(
    model: torch.nn.Module,
    loader: DataLoader,
    device: torch.device,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    model.eval()
    true_parts: list[np.ndarray] = []
    pred_parts: list[np.ndarray] = []
    confidence_parts: list[np.ndarray] = []
    for x, y in loader:
        logits = model(x.to(device, non_blocking=True))
        probabilities = torch.softmax(logits, dim=1)
        confidence, prediction = probabilities.max(dim=1)
        true_parts.append(y.numpy())
        pred_parts.append(prediction.cpu().numpy())
        confidence_parts.append(confidence.cpu().numpy())
    return (
        np.concatenate(true_parts),
        np.concatenate(pred_parts),
        np.concatenate(confidence_parts),
    )


def metrics_dict(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
        "macro_precision": float(
            precision_score(y_true, y_pred, average="macro", zero_division=0)
        ),
        "macro_recall": float(
            recall_score(y_true, y_pred, average="macro", zero_division=0)
        ),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "weighted_f1": float(
            f1_score(y_true, y_pred, average="weighted", zero_division=0)
        ),
    }


def save_outputs(
    output_dir: Path,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    confidence: np.ndarray,
    labels: list[str],
    metrics: dict[str, float],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    ids = np.arange(len(labels))
    pd.DataFrame(
        confusion_matrix(y_true, y_pred, labels=ids), index=labels, columns=labels
    ).to_csv(output_dir / "test_confusion_matrix.csv")
    pd.DataFrame(
        classification_report(
            y_true,
            y_pred,
            labels=ids,
            target_names=labels,
            output_dict=True,
            zero_division=0,
        )
    ).transpose().to_csv(output_dir / "test_classification_report.csv")
    pd.DataFrame(
        {
            "sample_index": np.arange(len(y_true)),
            "true_id": y_true,
            "pred_id": y_pred,
            "true_label": [labels[idx] for idx in y_true],
            "pred_label": [labels[idx] for idx in y_pred],
            "confidence": confidence,
        }
    ).to_csv(output_dir / "test_predictions.csv", index=False)
    save_json(output_dir / "test_metrics.json", metrics)


def load_test_arrays(data_dir: Path, suffix: str) -> tuple[np.ndarray, np.ndarray]:
    x_path = data_dir / f"X_test_{suffix}.npy"
    y_path = data_dir / f"y_test_{suffix}.npy"
    if not x_path.exists() or not y_path.exists():
        raise FileNotFoundError(f"Missing test arrays in {data_dir} for suffix {suffix}.")
    x = np.load(x_path).astype(np.float32, copy=False)
    y = np.load(y_path).astype(str)
    if len(x) != len(y):
        raise ValueError(f"Test length mismatch in {data_dir}: X={len(x)}, y={len(y)}")
    if not np.isfinite(x).all():
        raise ValueError(f"Non-finite values found in {x_path}.")
    return x, y


def build_model(
    model_kind: str,
    checkpoint: dict,
    group_config: dict,
    input_dim: int,
    num_classes: int,
) -> torch.nn.Module:
    if model_kind == "mlp":
        return MLPClassifier(
            input_dim=input_dim,
            hidden_dims=group_config["hidden_dims"],
            num_classes=num_classes,
            dropout=group_config["dropout"],
        )

    config = checkpoint["args"]
    if model_kind == "lstm":
        return LSTMClassifier(
            input_dim=input_dim,
            num_classes=num_classes,
            hidden_dim=config["hidden_dim"],
            num_layers=config["num_layers"],
            dropout=config["dropout"],
            bidirectional=config["bidirectional"],
        )
    if model_kind == "resnet1d":
        return ResNet1DClassifier(
            input_features=input_dim,
            num_classes=num_classes,
            base_channels=config["base_channels"],
            num_blocks=config["num_blocks"],
            kernel_size=config["kernel_size"],
            dropout=config["dropout"],
        )
    if model_kind == "transformer":
        return TransformerEncoderClassifier(
            input_dim=input_dim,
            num_classes=num_classes,
            d_model=config["d_model"],
            num_heads=config["num_heads"],
            num_layers=config["num_layers"],
            dim_feedforward=config["dim_feedforward"],
            dropout=config["dropout"],
            max_len=config["max_len"],
            pooling=config["pooling"],
        )
    raise ValueError(f"Unsupported model kind: {model_kind}")


def group_config_path(model_kind: str, group_dir: Path) -> Path:
    if model_kind == "mlp":
        return group_dir / "run_config.json"
    return group_dir / f"{model_kind}_run_config.json"


def checkpoint_path(model_kind: str, run_dir: Path) -> Path:
    if model_kind == "mlp":
        return run_dir / "best_model.pt"
    return run_dir / f"{model_kind}_best.pt"


def evaluate_group(
    model_kind: str,
    group_dir: Path,
    output_root: Path,
    args: argparse.Namespace,
    device: torch.device,
) -> list[dict[str, object]]:
    config = load_json(group_config_path(model_kind, group_dir))
    if not config.get("validation_only", False):
        raise ValueError(f"Checkpoint group was not validation-only: {group_dir}")
    if int(config["epochs"]) != args.expected_epochs:
        raise ValueError(
            f"Expected {args.expected_epochs} epochs, found {config['epochs']} in {group_dir}"
        )

    if model_kind == "mlp":
        representation = config["representation"]
        test_dir = args.non_temporal_data_dir / representation
        temporal_order = None
        mapping = load_json(group_dir / "label_mapping.json")
        labels = [label for label, _ in sorted(mapping.items(), key=lambda item: item[1])]
    else:
        test_dir = args.temporal_data_dir
        temporal_order = config["temporal_order"]
        labels = []

    x_base, y_text = load_test_arrays(test_dir, args.suffix)
    run_dirs = sorted(path for path in group_dir.glob("run_*_seed*") if path.is_dir())
    if args.limit_runs is not None:
        run_dirs = run_dirs[: args.limit_runs]
    if not run_dirs:
        raise ValueError(f"No checkpoint runs found in {group_dir}")

    rows: list[dict[str, object]] = []
    for run_dir in run_dirs:
        match = RUN_PATTERN.fullmatch(run_dir.name)
        if match is None:
            raise ValueError(f"Unexpected run directory name: {run_dir.name}")
        run_index, run_seed = map(int, match.groups())
        model_path = checkpoint_path(model_kind, run_dir)
        checkpoint = torch.load(model_path, map_location="cpu", weights_only=False)

        if model_kind != "mlp":
            labels = list(checkpoint.get("labels", []))
            if not labels and model_kind == "resnet1d":
                mapping = checkpoint["label_mapping"]
                labels = [
                    label for label, _ in sorted(mapping.items(), key=lambda item: item[1])
                ]
        y_test = encode_labels(y_text, labels)

        x_test = x_base
        if temporal_order == "shuffled":
            x_test = shuffle_within_sequences(
                x_base, run_seed + SPLIT_SEED_OFFSETS["test"]
            )
        if model_kind == "resnet1d":
            x_test = np.transpose(x_test, (0, 2, 1))

        input_dim = x_test.shape[1] if model_kind == "mlp" else (
            x_test.shape[1] if model_kind == "resnet1d" else x_test.shape[2]
        )
        model = build_model(model_kind, checkpoint, config, input_dim, len(labels)).to(device)
        model.load_state_dict(checkpoint["model_state_dict"], strict=True)
        loader = make_loader(x_test, y_test, args.batch_size, device)
        y_true, y_pred, confidence = predict(model, loader, device)
        metrics = metrics_dict(y_true, y_pred)

        run_output = output_root / model_kind / group_dir.name / run_dir.name
        output_metrics = {
            "model": model_kind,
            "group": group_dir.name,
            "run": run_index,
            "seed": run_seed,
            "temporal_order": temporal_order,
            "checkpoint_path": str(model_path.resolve()),
            "checkpoint_best_epoch": checkpoint.get("best_epoch"),
            "checkpoint_best_val_macro_f1": checkpoint.get("best_val_macro_f1"),
            **{f"test_{key}": value for key, value in metrics.items()},
        }
        save_outputs(run_output, y_true, y_pred, confidence, labels, output_metrics)
        rows.append(output_metrics)
        print(
            f"[{group_dir.name}] run={run_index:02d} seed={run_seed} "
            f"test_macro_f1={metrics['macro_f1']:.6f}"
        )

    group_output = output_root / model_kind / group_dir.name
    summary = pd.DataFrame(rows)
    summary.to_csv(group_output / "test_all_runs_summary_metrics.csv", index=False)
    metric_columns = [column for column in summary.columns if column.startswith("test_")]
    aggregate_rows = []
    for column in metric_columns:
        aggregate_rows.append(
            {
                "model": model_kind,
                "group": group_dir.name,
                "metric": column,
                "mean": float(summary[column].mean()),
                "std": float(summary[column].std(ddof=1)) if len(summary) > 1 else 0.0,
                "n": int(len(summary)),
            }
        )
    pd.DataFrame(aggregate_rows).to_csv(
        group_output / "test_aggregate_metrics_mean_std.csv", index=False
    )
    return rows


def main() -> None:
    args = parse_args()
    checkpoint_root = args.checkpoint_root.resolve()
    output_root = args.output_dir.resolve()
    args.temporal_data_dir = args.temporal_data_dir.resolve()
    args.non_temporal_data_dir = args.non_temporal_data_dir.resolve()

    if output_root.exists():
        raise FileExistsError(f"Output directory already exists: {output_root}")
    if not checkpoint_root.is_dir():
        raise FileNotFoundError(f"Checkpoint root not found: {checkpoint_root}")

    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available.")

    groups: list[tuple[str, Path]] = []
    for model_kind in MODEL_DIRS:
        model_root = checkpoint_root / model_kind
        if not model_root.is_dir():
            continue
        for group_dir in sorted(path for path in model_root.iterdir() if path.is_dir()):
            if fnmatch.fnmatch(group_dir.name, args.group_pattern):
                groups.append((model_kind, group_dir))
    if not groups:
        raise ValueError(
            f"No groups matched {args.group_pattern!r} under {checkpoint_root}"
        )

    output_root.mkdir(parents=True, exist_ok=False)
    save_json(
        output_root / "evaluation_config.json",
        {
            "checkpoint_root": str(checkpoint_root),
            "temporal_data_dir": str(args.temporal_data_dir),
            "non_temporal_data_dir": str(args.non_temporal_data_dir),
            "suffix": args.suffix,
            "expected_epochs": args.expected_epochs,
            "group_pattern": args.group_pattern,
            "limit_runs": args.limit_runs,
            "batch_size": args.batch_size,
            "device": str(device),
            "groups": [str(path) for _, path in groups],
        },
    )

    all_rows: list[dict[str, object]] = []
    for model_kind, group_dir in groups:
        all_rows.extend(
            evaluate_group(model_kind, group_dir, output_root, args, device)
        )
    pd.DataFrame(all_rows).to_csv(output_root / "test_all_conditions_runs.csv", index=False)
    print(f"All requested checkpoint evaluations completed: {output_root}")


if __name__ == "__main__":
    main()
