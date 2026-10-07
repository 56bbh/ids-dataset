#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Train a 1D ResNet temporal baseline for TRNID sequence data.

Main changes in this version:
  1) Support --runs N independent train/validation/test runs.
     Each run uses a different seed, trains from scratch, selects the best
     checkpoint by validation macro-F1, and then evaluates on the test set.
     This is the correct basis for reporting mean ± standard deviation.
  2) Save per-run results plus aggregate mean/std CSV files.

Example:
  python train_resnet1d_baseline_unified.py --suffix ratio0p6_T10_S1 --runs 12
  python train_resnet1d_baseline_unified.py --runs 12 --seed 42 --seed-step 1
"""

import argparse
import json
import random
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)
from sklearn.preprocessing import LabelEncoder
from torch.utils.data import DataLoader, Dataset

from temporal_order_utils import apply_temporal_order


def load_checkpoint(path, device):
    """Load a checkpoint across PyTorch versions."""
    try:
        return torch.load(path, map_location=device, weights_only=False)
    except TypeError:
        return torch.load(path, map_location=device)


def set_seed(seed: int = 42) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def infer_suffix(data_dir: Path) -> str:
    candidates = sorted(data_dir.glob("X_train_*.npy"))
    if not candidates:
        raise FileNotFoundError(
            f"No X_train_*.npy files found in {data_dir}. Please run csvtonpy.py first."
        )
    if len(candidates) > 1:
        print("[!] Multiple X_train_*.npy files found. Using the latest modified one:")
        candidates = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)
        for p in candidates[:5]:
            print(f"    {p.name}")
    name = candidates[0].name
    return name[len("X_train_") : -len(".npy")]


def load_split(data_dir: Path, split: str, suffix: str):
    x_path = data_dir / f"X_{split}_{suffix}.npy"
    y_path = data_dir / f"y_{split}_{suffix}.npy"
    if not x_path.exists():
        raise FileNotFoundError(f"Missing file: {x_path}")
    if not y_path.exists():
        raise FileNotFoundError(f"Missing file: {y_path}")

    X = np.load(x_path)
    y = np.load(y_path, allow_pickle=True)
    if X.ndim != 3:
        raise ValueError(
            f"{x_path.name} should have shape [N, T, F], but got {X.shape}. "
            "ResNet should use temporal data from model_input/, not RF/MLP tabular data."
        )
    if len(X) != len(y):
        raise ValueError(f"X/y length mismatch for {split}: {len(X)} vs {len(y)}")
    return X.astype(np.float32), y.astype(str)


class SequenceDataset(Dataset):
    def __init__(self, X: np.ndarray, y: np.ndarray):
        self.X = torch.from_numpy(X).float()
        self.y = torch.from_numpy(y).long()

    def __len__(self):
        return len(self.y)

    def __getitem__(self, idx):
        # Original per-sample shape: [T, F]
        # Conv1d expects [C, L], so use [F, T].
        return self.X[idx].transpose(0, 1), self.y[idx]


class ResidualBlock1D(nn.Module):
    def __init__(self, channels: int, kernel_size: int = 3, dropout: float = 0.1):
        super().__init__()
        padding = kernel_size // 2
        self.block = nn.Sequential(
            nn.Conv1d(channels, channels, kernel_size=kernel_size, padding=padding, bias=False),
            nn.BatchNorm1d(channels),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Conv1d(channels, channels, kernel_size=kernel_size, padding=padding, bias=False),
            nn.BatchNorm1d(channels),
        )
        self.activation = nn.ReLU(inplace=True)

    def forward(self, x):
        return self.activation(x + self.block(x))


class ResNet1DClassifier(nn.Module):
    """Lightweight 1D ResNet for multivariate time-series classification."""

    def __init__(
        self,
        input_features: int,
        num_classes: int,
        base_channels: int = 64,
        num_blocks: int = 3,
        kernel_size: int = 3,
        dropout: float = 0.2,
    ):
        super().__init__()
        padding = kernel_size // 2
        self.stem = nn.Sequential(
            nn.Conv1d(input_features, base_channels, kernel_size=kernel_size, padding=padding, bias=False),
            nn.BatchNorm1d(base_channels),
            nn.ReLU(inplace=True),
        )
        self.res_blocks = nn.Sequential(
            *[
                ResidualBlock1D(base_channels, kernel_size=kernel_size, dropout=dropout)
                for _ in range(num_blocks)
            ]
        )
        self.pool = nn.AdaptiveAvgPool1d(1)
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Dropout(dropout),
            nn.Linear(base_channels, num_classes),
        )

    def forward(self, x):
        x = self.stem(x)
        x = self.res_blocks(x)
        x = self.pool(x)
        return self.classifier(x)


def compute_class_weights(y_train: np.ndarray, num_classes: int, mode: str, device: torch.device):
    if mode == "none":
        return None
    counts = np.bincount(y_train, minlength=num_classes).astype(np.float32)
    counts = np.maximum(counts, 1.0)
    weights = counts.sum() / (num_classes * counts)
    return torch.tensor(weights, dtype=torch.float32, device=device)


@torch.no_grad()
def predict(model, loader, device):
    model.eval()
    all_logits, all_y = [], []
    for x, y in loader:
        x = x.to(device)
        logits = model(x)
        all_logits.append(logits.cpu())
        all_y.append(y)
    logits = torch.cat(all_logits, dim=0).numpy()
    y_true = torch.cat(all_y, dim=0).numpy()
    y_pred = logits.argmax(axis=1)
    return y_true, y_pred, logits


def evaluate(model, loader, criterion, device):
    model.eval()
    total_loss, total_count = 0.0, 0
    all_y, all_pred = [], []
    with torch.no_grad():
        for x, y in loader:
            x = x.to(device)
            y = y.to(device)
            logits = model(x)
            loss = criterion(logits, y)
            bs = y.size(0)
            total_loss += float(loss.item()) * bs
            total_count += bs
            all_y.append(y.cpu().numpy())
            all_pred.append(logits.argmax(dim=1).cpu().numpy())
    y_true = np.concatenate(all_y)
    y_pred = np.concatenate(all_pred)
    return {
        "loss": total_loss / max(total_count, 1),
        "accuracy": accuracy_score(y_true, y_pred),
        "balanced_accuracy": balanced_accuracy_score(y_true, y_pred),
        "macro_f1": f1_score(y_true, y_pred, average="macro", zero_division=0),
        "weighted_f1": f1_score(y_true, y_pred, average="weighted", zero_division=0),
    }


def save_reports(split_name, y_true, y_pred, logits, label_encoder, out_dir: Path, model_name: str = "resnet1d"):
    labels = np.arange(len(label_encoder.classes_))
    target_names = list(label_encoder.classes_)

    report = classification_report(
        y_true,
        y_pred,
        labels=labels,
        target_names=target_names,
        output_dict=True,
        zero_division=0,
    )
    pd.DataFrame(report).transpose().to_csv(out_dir / f"{model_name}_{split_name}_classification_report.csv")

    cm = confusion_matrix(y_true, y_pred, labels=labels)
    pd.DataFrame(cm, index=target_names, columns=target_names).to_csv(
        out_dir / f"{model_name}_{split_name}_confusion_matrix.csv"
    )

    probs = torch.softmax(torch.tensor(logits), dim=1).numpy()
    pred_df = pd.DataFrame({
        "y_true_id": y_true,
        "y_pred_id": y_pred,
        "y_true": label_encoder.inverse_transform(y_true),
        "y_pred": label_encoder.inverse_transform(y_pred),
        "pred_confidence": probs.max(axis=1),
    })
    pred_df.to_csv(out_dir / f"{model_name}_{split_name}_predictions.csv", index=False)

    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "balanced_accuracy": balanced_accuracy_score(y_true, y_pred),
        "macro_f1": f1_score(y_true, y_pred, average="macro", zero_division=0),
        "weighted_f1": f1_score(y_true, y_pred, average="weighted", zero_division=0),
    }


def aggregate_runs(summary_df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    metric_cols = [
        "val_accuracy",
        "val_balanced_accuracy",
        "val_macro_f1",
        "val_weighted_f1",
        "test_accuracy",
        "test_balanced_accuracy",
        "test_macro_f1",
        "test_weighted_f1",
    ]
    metric_cols = [metric for metric in metric_cols if metric in summary_df.columns]
    rows = []
    for metric in metric_cols:
        values = summary_df[metric].astype(float)
        rows.append({
            "model": "resnet1d",
            "metric": metric,
            "mean": values.mean(),
            "std": values.std(ddof=1),
            "n": values.count(),
        })
    aggregate_df = pd.DataFrame(rows)
    aggregate_df.to_csv(out_dir / f"{summary_df['model'].iloc[0]}_aggregate_metrics_mean_std.csv", index=False)
    return aggregate_df


def train_one_run(
    args,
    suffix: str,
    X_train: np.ndarray,
    y_train_raw: np.ndarray,
    X_val: np.ndarray,
    y_val_raw: np.ndarray,
    X_test: np.ndarray,
    y_test_raw: np.ndarray,
    base_out_root: Path,
    run_idx: int,
    run_seed: int,
):
    set_seed(run_seed)
    X_train_run, X_val_run, X_test_run, order_seeds = apply_temporal_order(
        X_train, X_val, X_test, args.temporal_order, run_seed
    )
    device = torch.device(
        "cuda"
        if (args.device == "auto" and torch.cuda.is_available())
        else ("cpu" if args.device == "auto" else args.device)
    )

    out_root = base_out_root / f"run_{run_idx:02d}_seed{run_seed}"
    out_root.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 60)
    print(f"[*] ResNet1D run {run_idx}/{args.runs} | seed={run_seed} | device={device}")
    print("=" * 60)

    label_encoder = LabelEncoder()
    y_train = label_encoder.fit_transform(y_train_raw)
    y_val = label_encoder.transform(y_val_raw)
    y_test = None if y_test_raw is None else label_encoder.transform(y_test_raw)

    label_mapping = {label: int(idx) for idx, label in enumerate(label_encoder.classes_)}
    with open(out_root / "resnet1d_label_mapping.json", "w", encoding="utf-8") as f:
        json.dump(label_mapping, f, ensure_ascii=False, indent=2)
    num_classes = len(label_encoder.classes_)
    seq_len = X_train_run.shape[1]
    num_features = X_train_run.shape[2]

    train_loader = DataLoader(
        SequenceDataset(X_train_run, y_train),
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=args.num_workers,
        pin_memory=(device.type == "cuda"),
    )
    val_loader = DataLoader(
        SequenceDataset(X_val_run, y_val),
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=args.num_workers,
        pin_memory=(device.type == "cuda"),
    )
    test_loader = None
    if not args.validation_only:
        test_loader = DataLoader(
            SequenceDataset(X_test_run, y_test),
            batch_size=args.batch_size,
            shuffle=False,
            num_workers=args.num_workers,
            pin_memory=(device.type == "cuda"),
        )

    model = ResNet1DClassifier(
        input_features=num_features,
        num_classes=num_classes,
        base_channels=args.base_channels,
        num_blocks=args.num_blocks,
        kernel_size=args.kernel_size,
        dropout=args.dropout,
    ).to(device)

    class_weights = compute_class_weights(y_train, num_classes, args.class_weight, device)
    criterion = nn.CrossEntropyLoss(weight=class_weights)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=0.5, patience=max(2, args.patience // 3)
    )

    config = vars(args).copy()
    config.update({
        "suffix": suffix,
        "current_run": run_idx,
        "current_seed": run_seed,
        "num_classes": num_classes,
        "seq_len": seq_len,
        "num_features": num_features,
        "classes": [str(x) for x in label_encoder.classes_],
        "temporal_order_seeds": order_seeds,
    })
    with open(out_root / "resnet1d_run_config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    best_val_macro_f1 = -1.0
    best_epoch = -1
    patience_counter = 0
    history = []
    best_model_path = out_root / "resnet1d_best.pt"

    print("[*] Start training ResNet1D...")
    for epoch in range(1, args.epochs + 1):
        model.train()
        train_loss_sum, train_count = 0.0, 0
        for x, y in train_loader:
            x = x.to(device)
            y = y.to(device)
            optimizer.zero_grad(set_to_none=True)
            logits = model(x)
            loss = criterion(logits, y)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
            optimizer.step()
            bs = y.size(0)
            train_loss_sum += float(loss.item()) * bs
            train_count += bs

        train_loss = train_loss_sum / max(train_count, 1)
        val_metrics = evaluate(model, val_loader, criterion, device)
        scheduler.step(val_metrics["macro_f1"])

        row = {
            "epoch": epoch,
            "train_loss": train_loss,
            **{f"val_{k}": v for k, v in val_metrics.items()},
            "lr": optimizer.param_groups[0]["lr"],
        }
        history.append(row)

        print(
            f"Epoch {epoch:03d} | train_loss={train_loss:.4f} | "
            f"val_loss={val_metrics['loss']:.4f} | val_acc={val_metrics['accuracy']:.4f} | "
            f"val_macro_f1={val_metrics['macro_f1']:.4f} | lr={optimizer.param_groups[0]['lr']:.2e}"
        )

        if val_metrics["macro_f1"] > best_val_macro_f1 + 1e-6:
            best_val_macro_f1 = float(val_metrics["macro_f1"])
            best_epoch = int(epoch)
            patience_counter = 0
            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "args": config,
                    "label_mapping": {str(k): int(v) for k, v in label_mapping.items()},
                    "best_epoch": int(best_epoch),
                    "best_val_macro_f1": float(best_val_macro_f1),
                },
                best_model_path,
            )
        else:
            patience_counter += 1
            if patience_counter >= args.patience:
                print(f"[*] Early stopping at epoch {epoch}. Best epoch: {best_epoch}")
                break

    pd.DataFrame(history).to_csv(out_root / "resnet1d_training_history.csv", index=False)

    ckpt = load_checkpoint(best_model_path, device)
    model.load_state_dict(ckpt["model_state_dict"])

    print("[*] Evaluating best ResNet1D checkpoint...")
    yv_true, yv_pred, yv_logits = predict(model, val_loader, device)
    val_metrics = save_reports("val", yv_true, yv_pred, yv_logits, label_encoder, out_root, "resnet1d")

    final_row = {
        "model": "resnet1d",
        "run": run_idx,
        "seed": run_seed,
        "best_epoch": int(best_epoch),
        "best_val_macro_f1": float(best_val_macro_f1),
        **{f"val_{k}": v for k, v in val_metrics.items()},
        "model_path": str(best_model_path),
        "result_dir": str(out_root),
    }
    if not args.validation_only:
        yt_true, yt_pred, yt_logits = predict(model, test_loader, device)
        test_metrics = save_reports(
            "test", yt_true, yt_pred, yt_logits, label_encoder, out_root, "resnet1d"
        )
        final_row.update({f"test_{k}": v for k, v in test_metrics.items()})
    with open(out_root / "resnet1d_metrics.json", "w", encoding="utf-8") as f:
        json.dump(final_row, f, ensure_ascii=False, indent=2)

    message = f"[RUN {run_idx:02d}] Val macro-F1={val_metrics['macro_f1']:.4f}"
    if not args.validation_only:
        message += f", Test macro-F1={test_metrics['macro_f1']:.4f}"
    print(message)
    return final_row


def main():
    parser = argparse.ArgumentParser(description="Train a 1D ResNet temporal baseline.")
    parser.add_argument("--data-dir", type=str, default="model_input")
    parser.add_argument("--suffix", type=str, default=None, help="Example: ratio0p6_T10_S1")
    parser.add_argument("--output-dir", type=str, default="resnet1d_results")

    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--base-channels", type=int, default=64)
    parser.add_argument("--num-blocks", type=int, default=3)
    parser.add_argument("--kernel-size", type=int, default=3)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--class-weight", type=str, default="none", choices=["none", "balanced"])

    parser.add_argument("--runs", type=int, default=1, help="Number of independent train/val/test runs.")
    parser.add_argument("--seed-step", type=int, default=1, help="Seed increment between runs.")
    parser.add_argument("--device", type=str, default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument(
        "--temporal-order",
        choices=["ordered", "shuffled"],
        default="ordered",
        help="Use original ordering or independently shuffle time steps within each sequence.",
    )
    parser.add_argument(
        "--validation-only",
        action="store_true",
        help="Do not load or evaluate the test split.",
    )
    args = parser.parse_args()

    if args.runs < 1:
        raise ValueError("--runs must be at least 1")

    data_dir = Path(args.data_dir)
    suffix = args.suffix or infer_suffix(data_dir)

    print(f"[*] Data dir: {data_dir}")
    print(f"[*] Data suffix: {suffix}")
    print(f"[*] Independent runs: {args.runs}")

    X_train, y_train_raw = load_split(data_dir, "train", suffix)
    X_val, y_val_raw = load_split(data_dir, "val", suffix)
    if args.validation_only:
        X_test, y_test_raw = None, None
    else:
        X_test, y_test_raw = load_split(data_dir, "test", suffix)
    print(f"[*] X_train shape: {X_train.shape}")
    print(f"[*] X_val shape:   {X_val.shape}")
    if X_test is not None:
        print(f"[*] X_test shape:  {X_test.shape}")
    else:
        print("[*] X_test: not loaded (--validation-only)")

    if X_train.shape[1:] != X_val.shape[1:]:
        raise ValueError("Train/val/test sequence shapes are inconsistent.")
    if X_test is not None and X_train.shape[1:] != X_test.shape[1:]:
        raise ValueError("Train/val/test sequence shapes are inconsistent.")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    mode_suffix = f"{args.temporal_order}_{'valonly' if args.validation_only else 'full'}"
    base_out_root = Path(args.output_dir) / f"resnet1d_{mode_suffix}_{suffix}_{timestamp}"
    base_out_root.mkdir(parents=True, exist_ok=True)

    # Save top-level metadata with the same naming scheme as the LSTM/temporal script.
    root_label_encoder = LabelEncoder()
    label_arrays = [y_train_raw, y_val_raw]
    if y_test_raw is not None:
        label_arrays.append(y_test_raw)
    root_label_encoder.fit(np.concatenate(label_arrays))
    root_label_mapping = {label: int(idx) for idx, label in enumerate(root_label_encoder.classes_)}
    with open(base_out_root / "resnet1d_label_mapping.json", "w", encoding="utf-8") as f:
        json.dump(root_label_mapping, f, ensure_ascii=False, indent=2)
    root_config = vars(args).copy()
    root_config.update({"suffix": suffix, "model": "resnet1d", "classes": [str(x) for x in root_label_encoder.classes_]})
    with open(base_out_root / "resnet1d_run_config.json", "w", encoding="utf-8") as f:
        json.dump(root_config, f, ensure_ascii=False, indent=2)

    summary_rows = []
    for run_idx in range(1, args.runs + 1):
        run_seed = int(args.seed + (run_idx - 1) * args.seed_step)
        row = train_one_run(
            args,
            suffix,
            X_train,
            y_train_raw,
            X_val,
            y_val_raw,
            X_test,
            y_test_raw,
            base_out_root,
            run_idx,
            run_seed,
        )
        summary_rows.append(row)

    summary_df = pd.DataFrame(summary_rows)
    summary_path = base_out_root / "resnet1d_all_runs_summary_metrics.csv"
    summary_df.to_csv(summary_path, index=False)
    aggregate_df = aggregate_runs(summary_df, base_out_root)

    print("\n[完成] ResNet1D training/evaluation finished.")
    print(f"Results saved to: {base_out_root}")
    print(f"Per-run summary: {summary_path}")
    print(f"Mean/std summary: {base_out_root / 'resnet1d_aggregate_metrics_mean_std.csv'}")
    if args.runs > 1 and not args.validation_only:
        test_macro = aggregate_df[aggregate_df["metric"] == "test_macro_f1"].iloc[0]
        print(
            f"Test macro-F1 mean±std over {int(test_macro['n'])} runs: "
            f"{test_macro['mean']:.4f} ± {test_macro['std']:.4f}"
        )


if __name__ == "__main__":
    main()
