#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Train a Transformer Encoder temporal baseline for TRNID sequence data.

Input files in --data-dir:
  X_train_<suffix>.npy  # shape: [N, T, F]
  y_train_<suffix>.npy
  X_val_<suffix>.npy
  y_val_<suffix>.npy
  X_test_<suffix>.npy
  y_test_<suffix>.npy

This script follows the same evaluation strategy as the LSTM and ResNet1D baselines:
  - independent repeated runs controlled by --runs and seed
  - train on train split
  - select best checkpoint by validation macro-F1
  - evaluate the selected checkpoint on val and test splits
  - save per-run reports and aggregate mean/std metrics

Examples:
  python train_transformer_baseline.py --suffix ratio0p6_T10_S1
  python train_transformer_baseline.py --runs 10 --epochs 40 --patience 8 --device cuda --suffix ratio0p6_T10_S1
  python train_transformer_baseline.py --runs 3 --epochs 5 --patience 2 --device cuda --suffix ratio0p6_T10_S1
"""

import argparse
import json
import math
import os
import random
import time
from glob import glob
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, Dataset
except ImportError as e:
    raise ImportError("This script requires PyTorch. Please install torch first.") from e

from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)

from temporal_order_utils import apply_temporal_order


# =========================
# Utilities
# =========================
def set_seed(seed: int = 42) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def auto_find_suffix(data_dir: str) -> str:
    candidates = glob(os.path.join(data_dir, "X_train_*.npy"))
    if not candidates:
        raise FileNotFoundError(
            f"No X_train_*.npy found in {data_dir}. Please run csvtonpy.py first."
        )
    candidates = sorted(candidates, key=os.path.getmtime, reverse=True)
    chosen = os.path.basename(candidates[0])
    suffix = chosen[len("X_train_") : -len(".npy")]
    if len(candidates) > 1:
        print("[!] Multiple X_train_*.npy files detected. Auto-selected newest one:")
        print(f"    {chosen}")
        print("    You can specify --suffix manually if needed.\n")
    return suffix


def load_split(data_dir: str, split: str, suffix: str):
    x_path = os.path.join(data_dir, f"X_{split}_{suffix}.npy")
    y_path = os.path.join(data_dir, f"y_{split}_{suffix}.npy")
    if not os.path.exists(x_path):
        raise FileNotFoundError(f"Missing file: {x_path}")
    if not os.path.exists(y_path):
        raise FileNotFoundError(f"Missing file: {y_path}")

    X = np.load(x_path).astype(np.float32)
    y = np.load(y_path, allow_pickle=True).astype(str)
    if X.ndim != 3:
        raise ValueError(f"Expected X_{split} to be 3D [N, T, F], got shape={X.shape}")
    if len(X) != len(y):
        raise ValueError(f"X/y length mismatch for {split}: {len(X)} vs {len(y)}")
    return X, y


def build_label_mapping(*ys):
    labels = sorted(set(np.concatenate([np.asarray(y).astype(str) for y in ys]).tolist()))
    label_to_id = {label: i for i, label in enumerate(labels)}
    id_to_label = {i: label for label, i in label_to_id.items()}
    return label_to_id, id_to_label


def encode_labels(y, label_to_id):
    unknown = sorted(set(y) - set(label_to_id))
    if unknown:
        raise ValueError(f"Unknown labels found: {unknown}")
    return np.array([label_to_id[v] for v in y], dtype=np.int64)


class SequenceDataset(Dataset):
    def __init__(self, X: np.ndarray, y: np.ndarray):
        self.X = torch.from_numpy(X.astype(np.float32))
        self.y = torch.from_numpy(y.astype(np.int64))

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        # Shape: [T, F]
        return self.X[idx], self.y[idx]


# =========================
# Model
# =========================
class SinusoidalPositionalEncoding(nn.Module):
    """Standard sinusoidal positional encoding for short time sequences."""

    def __init__(self, d_model: int, max_len: int = 512, dropout: float = 0.1):
        super().__init__()
        self.dropout = nn.Dropout(dropout)

        position = torch.arange(max_len, dtype=torch.float32).unsqueeze(1)
        div_term = torch.exp(
            torch.arange(0, d_model, 2, dtype=torch.float32) * (-math.log(10000.0) / d_model)
        )
        pe = torch.zeros(max_len, d_model, dtype=torch.float32)
        pe[:, 0::2] = torch.sin(position * div_term)
        if d_model % 2 == 1:
            pe[:, 1::2] = torch.cos(position * div_term[:-1])
        else:
            pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer("pe", pe.unsqueeze(0), persistent=False)  # [1, max_len, d_model]

    def forward(self, x):
        # x: [B, T, D]
        if x.size(1) > self.pe.size(1):
            raise ValueError(
                f"Sequence length {x.size(1)} exceeds max_len {self.pe.size(1)}. "
                "Increase --max-len."
            )
        x = x + self.pe[:, : x.size(1), :].to(dtype=x.dtype)
        return self.dropout(x)


class TransformerEncoderClassifier(nn.Module):
    """Transformer Encoder baseline for multivariate temporal classification."""

    def __init__(
        self,
        input_dim: int,
        num_classes: int,
        d_model: int = 128,
        num_heads: int = 4,
        num_layers: int = 2,
        dim_feedforward: int = 256,
        dropout: float = 0.2,
        max_len: int = 512,
        pooling: str = "cls",
    ):
        super().__init__()
        if d_model % num_heads != 0:
            raise ValueError(f"d_model={d_model} must be divisible by num_heads={num_heads}.")
        if pooling not in {"cls", "mean"}:
            raise ValueError("pooling must be either 'cls' or 'mean'.")

        self.pooling = pooling
        self.input_projection = nn.Linear(input_dim, d_model)
        self.pos_encoder = SinusoidalPositionalEncoding(d_model=d_model, max_len=max_len + 1, dropout=dropout)

        if pooling == "cls":
            self.cls_token = nn.Parameter(torch.zeros(1, 1, d_model))
            nn.init.trunc_normal_(self.cls_token, std=0.02)
        else:
            self.cls_token = None

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=num_heads,
            dim_feedforward=dim_feedforward,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.classifier = nn.Sequential(
            nn.LayerNorm(d_model),
            nn.Dropout(dropout),
            nn.Linear(d_model, num_classes),
        )

    def forward(self, x):
        # x: [B, T, F]
        z = self.input_projection(x)
        if self.pooling == "cls":
            cls = self.cls_token.expand(z.size(0), -1, -1)
            z = torch.cat([cls, z], dim=1)
        z = self.pos_encoder(z)
        z = self.encoder(z)
        if self.pooling == "cls":
            pooled = z[:, 0]
        else:
            pooled = z.mean(dim=1)
        return self.classifier(pooled)


def create_model(input_dim: int, num_classes: int, args):
    return TransformerEncoderClassifier(
        input_dim=input_dim,
        num_classes=num_classes,
        d_model=args.d_model,
        num_heads=args.num_heads,
        num_layers=args.num_layers,
        dim_feedforward=args.dim_feedforward,
        dropout=args.dropout,
        max_len=args.max_len,
        pooling=args.pooling,
    )


# =========================
# Training and evaluation
# =========================
def compute_class_weights(y_train, num_classes, device):
    counts = np.bincount(y_train, minlength=num_classes).astype(np.float32)
    counts[counts == 0] = 1.0
    weights = counts.sum() / (num_classes * counts)
    return torch.tensor(weights, dtype=torch.float32, device=device)


def run_one_epoch(model, loader, criterion, optimizer, device, train=True):
    model.train(train)
    total_loss = 0.0
    total_count = 0

    for X, y in loader:
        X = X.to(device)
        y = y.to(device)

        if train:
            optimizer.zero_grad(set_to_none=True)

        with torch.set_grad_enabled(train):
            logits = model(X)
            loss = criterion(logits, y)
            if train:
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
                optimizer.step()

        batch_size = X.size(0)
        total_loss += float(loss.item()) * batch_size
        total_count += batch_size

    return total_loss / max(total_count, 1)


@torch.no_grad()
def predict(model, loader, device):
    model.eval()
    y_true, y_pred, y_prob = [], [], []
    for X, y in loader:
        X = X.to(device)
        logits = model(X)
        prob = torch.softmax(logits, dim=1)
        pred = torch.argmax(prob, dim=1)
        y_true.append(y.cpu().numpy())
        y_pred.append(pred.cpu().numpy())
        y_prob.append(prob.cpu().numpy())
    return np.concatenate(y_true), np.concatenate(y_pred), np.concatenate(y_prob)


def metrics_dict(y_true, y_pred):
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "balanced_accuracy": balanced_accuracy_score(y_true, y_pred),
        "macro_f1": f1_score(y_true, y_pred, average="macro", zero_division=0),
        "weighted_f1": f1_score(y_true, y_pred, average="weighted", zero_division=0),
    }


def save_eval_outputs(out_dir, split_name, y_true, y_pred, y_prob, labels, model_name="transformer"):
    ensure_dir(out_dir)

    report = classification_report(
        y_true,
        y_pred,
        labels=list(range(len(labels))),
        target_names=labels,
        output_dict=True,
        zero_division=0,
    )
    pd.DataFrame(report).transpose().to_csv(
        os.path.join(out_dir, f"{model_name}_{split_name}_classification_report.csv")
    )

    cm = confusion_matrix(y_true, y_pred, labels=list(range(len(labels))))
    pd.DataFrame(cm, index=labels, columns=labels).to_csv(
        os.path.join(out_dir, f"{model_name}_{split_name}_confusion_matrix.csv")
    )

    pred_df = pd.DataFrame({
        "y_true_id": y_true,
        "y_pred_id": y_pred,
        "y_true": [labels[i] for i in y_true],
        "y_pred": [labels[i] for i in y_pred],
    })
    for i, label in enumerate(labels):
        pred_df[f"prob_{label}"] = y_prob[:, i]
    pred_df.to_csv(os.path.join(out_dir, f"{model_name}_{split_name}_predictions.csv"), index=False)


def aggregate_runs(summary_df, out_dir, model_name="transformer"):
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
            "model": model_name,
            "metric": metric,
            "mean": values.mean(),
            "std": values.std(ddof=1) if values.count() > 1 else 0.0,
            "n": values.count(),
        })
    aggregate_df = pd.DataFrame(rows)
    aggregate_df.to_csv(os.path.join(out_dir, f"{model_name}_aggregate_metrics_mean_std.csv"), index=False)
    return aggregate_df


def train_one_run(X_train, y_train, X_val, y_val, X_test, y_test, labels, args, run_dir, run_idx, run_seed):
    model_name = "transformer"
    set_seed(run_seed)
    X_train_run, X_val_run, X_test_run, order_seeds = apply_temporal_order(
        X_train, X_val, X_test, args.temporal_order, run_seed
    )
    device = torch.device(args.device if args.device else ("cuda" if torch.cuda.is_available() else "cpu"))

    print("\n==============================")
    print(f"[*] Training TRANSFORMER | run={run_idx}/{args.runs} | seed={run_seed} | device={device}")
    print("==============================")

    input_dim = X_train_run.shape[-1]
    num_classes = len(labels)
    model = create_model(input_dim, num_classes, args).to(device)

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

    class_weights = compute_class_weights(y_train, num_classes, device) if args.class_weight else None
    if class_weights is not None:
        print("[*] Using balanced class weights:")
        for label, w in zip(labels, class_weights.detach().cpu().numpy().tolist()):
            print(f"    {label}: {w:.4f}")

    criterion = nn.CrossEntropyLoss(weight=class_weights)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=0.5, patience=max(2, args.patience // 2)
    )

    model_dir = os.path.join(run_dir, f"run_{run_idx:02d}_seed{run_seed}")
    ensure_dir(model_dir)

    with open(os.path.join(model_dir, f"{model_name}_label_mapping.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"label_to_id": {label: i for i, label in enumerate(labels)},
             "id_to_label": {i: label for i, label in enumerate(labels)}},
            f,
            ensure_ascii=False,
            indent=2,
        )

    run_config = vars(args).copy()
    run_config.update({
        "model": model_name,
        "run_idx": int(run_idx),
        "run_seed": int(run_seed),
        "input_dim": int(input_dim),
        "num_classes": int(num_classes),
        "temporal_order_seeds": order_seeds,
    })
    with open(os.path.join(model_dir, f"{model_name}_run_config.json"), "w", encoding="utf-8") as f:
        json.dump(run_config, f, ensure_ascii=False, indent=2)

    best_path = os.path.join(model_dir, f"{model_name}_best.pt")
    best_val_macro_f1 = -1.0
    best_epoch = -1
    patience_counter = 0
    history = []

    for epoch in range(1, args.epochs + 1):
        train_loss = run_one_epoch(model, train_loader, criterion, optimizer, device, train=True)
        val_loss = run_one_epoch(model, val_loader, criterion, optimizer, device, train=False)
        yv_true, yv_pred, _ = predict(model, val_loader, device)
        val_metrics = metrics_dict(yv_true, yv_pred)
        val_macro_f1 = val_metrics["macro_f1"]
        scheduler.step(val_macro_f1)

        current_lr = optimizer.param_groups[0]["lr"]
        row = {
            "epoch": epoch,
            "train_loss": train_loss,
            "val_loss": val_loss,
            "lr": current_lr,
            **{f"val_{k}": v for k, v in val_metrics.items()},
        }
        history.append(row)

        print(
            f"Epoch {epoch:03d} | train_loss={train_loss:.4f} | val_loss={val_loss:.4f} | "
            f"val_acc={val_metrics['accuracy']:.4f} | val_macro_f1={val_macro_f1:.4f} | "
            f"lr={current_lr:.2e}"
        )

        if val_macro_f1 > best_val_macro_f1 + 1e-6:
            best_val_macro_f1 = float(val_macro_f1)
            best_epoch = int(epoch)
            patience_counter = 0
            torch.save(
                {
                    "model_name": model_name,
                    "model_state_dict": model.state_dict(),
                    "input_dim": input_dim,
                    "num_classes": num_classes,
                    "labels": labels,
                    "args": vars(args),
                    "best_epoch": int(best_epoch),
                    "best_val_macro_f1": float(best_val_macro_f1),
                },
                best_path,
            )
        else:
            patience_counter += 1
            if patience_counter >= args.patience:
                print(f"[*] Early stopping triggered at epoch {epoch}. Best epoch={best_epoch}.")
                break

    pd.DataFrame(history).to_csv(os.path.join(model_dir, f"{model_name}_training_history.csv"), index=False)

    checkpoint = torch.load(best_path, map_location=device, weights_only=False)
    model.load_state_dict(checkpoint["model_state_dict"])

    yv_true, yv_pred, yv_prob = predict(model, val_loader, device)
    val_metrics = metrics_dict(yv_true, yv_pred)
    save_eval_outputs(model_dir, "val", yv_true, yv_pred, yv_prob, labels, model_name)

    final_row = {
        "model": model_name,
        "run": int(run_idx),
        "seed": int(run_seed),
        "best_epoch": int(best_epoch),
        "best_val_macro_f1": float(best_val_macro_f1),
        **{f"val_{k}": v for k, v in val_metrics.items()},
        "model_path": best_path,
        "result_dir": model_dir,
    }
    if not args.validation_only:
        yt_true, yt_pred, yt_prob = predict(model, test_loader, device)
        test_metrics = metrics_dict(yt_true, yt_pred)
        save_eval_outputs(model_dir, "test", yt_true, yt_pred, yt_prob, labels, model_name)
        final_row.update({f"test_{k}": v for k, v in test_metrics.items()})

    with open(os.path.join(model_dir, f"{model_name}_metrics.json"), "w", encoding="utf-8") as f:
        json.dump(final_row, f, ensure_ascii=False, indent=2)

    print("\n[+] TRANSFORMER final metrics:")
    print(f"    Val  macro-F1: {val_metrics['macro_f1']:.4f}, acc: {val_metrics['accuracy']:.4f}")
    if not args.validation_only:
        print(f"    Test macro-F1: {test_metrics['macro_f1']:.4f}, acc: {test_metrics['accuracy']:.4f}")
    print(f"    Saved to: {model_dir}")

    return final_row


def main():
    parser = argparse.ArgumentParser(description="Train a Transformer Encoder baseline on TRNID sequence data.")
    parser.add_argument("--data-dir", type=str, default="model_input", help="Directory containing X/y npy files.")
    parser.add_argument("--suffix", type=str, default="auto", help="File suffix after X_train_, e.g. ratio0p6_T10_S1.")
    parser.add_argument("--out-dir", type=str, default="transformer_results", help="Output directory.")
    parser.add_argument("--runs", type=int, default=1, help="Number of independent train/val/test runs.")
    parser.add_argument("--seed-step", type=int, default=1, help="Seed increment between runs.")

    # Training hyperparameters
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--lr", type=float, default=8e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--dropout", type=float, default=0.2)

    # Transformer hyperparameters
    parser.add_argument("--d-model", type=int, default=128, help="Transformer embedding dimension.")
    parser.add_argument("--num-heads", type=int, default=4, help="Number of attention heads.")
    parser.add_argument("--num-layers", type=int, default=2, help="Number of Transformer encoder layers.")
    parser.add_argument("--dim-feedforward", type=int, default=256, help="Feed-forward dimension in Transformer layers.")
    parser.add_argument("--pooling", type=str, default="cls", choices=["cls", "mean"], help="Sequence pooling method.")
    parser.add_argument("--max-len", type=int, default=512, help="Maximum sequence length for positional encoding.")

    # Class weighting: default is no reweighting, consistent with a mildly imbalanced dataset setting.
    parser.add_argument("--class-weight", action="store_true", help="Use balanced class weights in CrossEntropyLoss.")
    parser.add_argument("--no-class-weight", dest="class_weight", action="store_false", help=argparse.SUPPRESS)
    parser.set_defaults(class_weight=False)

    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", type=str, default=None, help="cuda, cpu, cuda:0, etc. Default auto.")
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
    if args.d_model % args.num_heads != 0:
        raise ValueError("--d-model must be divisible by --num-heads")

    suffix = auto_find_suffix(args.data_dir) if args.suffix == "auto" else args.suffix

    print(f"[*] Data dir: {args.data_dir}")
    print(f"[*] Using suffix: {suffix}")

    X_train, y_train_raw = load_split(args.data_dir, "train", suffix)
    X_val, y_val_raw = load_split(args.data_dir, "val", suffix)
    if args.validation_only:
        X_test, y_test_raw = None, None
    else:
        X_test, y_test_raw = load_split(args.data_dir, "test", suffix)

    label_arrays = [y_train_raw, y_val_raw]
    if y_test_raw is not None:
        label_arrays.append(y_test_raw)
    label_to_id, id_to_label = build_label_mapping(*label_arrays)
    labels = [id_to_label[i] for i in range(len(id_to_label))]

    y_train = encode_labels(y_train_raw, label_to_id)
    y_val = encode_labels(y_val_raw, label_to_id)
    y_test = None if y_test_raw is None else encode_labels(y_test_raw, label_to_id)

    print("\n[*] Loaded data:")
    print(f"    X_train: {X_train.shape}, y_train: {y_train.shape}")
    print(f"    X_val:   {X_val.shape}, y_val:   {y_val.shape}")
    if X_test is not None:
        print(f"    X_test:  {X_test.shape}, y_test:  {y_test.shape}")
    else:
        print("    X_test:  not loaded (--validation-only)")
    print("\n[*] Label mapping:")
    for i, label in enumerate(labels):
        print(f"    {i}: {label}")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    model_name = "transformer"
    mode_suffix = f"{args.temporal_order}_{'valonly' if args.validation_only else 'full'}"
    run_dir = os.path.join(args.out_dir, f"{model_name}_{mode_suffix}_{suffix}_{timestamp}")
    ensure_dir(run_dir)

    with open(os.path.join(run_dir, f"{model_name}_label_mapping.json"), "w", encoding="utf-8") as f:
        json.dump({"label_to_id": label_to_id, "id_to_label": id_to_label}, f, ensure_ascii=False, indent=2)

    run_config = vars(args).copy()
    run_config["resolved_suffix"] = suffix
    run_config["model"] = model_name
    with open(os.path.join(run_dir, f"{model_name}_run_config.json"), "w", encoding="utf-8") as f:
        json.dump(run_config, f, ensure_ascii=False, indent=2)

    summary_rows = []
    for run_idx in range(1, args.runs + 1):
        run_seed = int(args.seed + (run_idx - 1) * args.seed_step)
        row = train_one_run(
            X_train,
            y_train,
            X_val,
            y_val,
            X_test,
            y_test,
            labels,
            args,
            run_dir,
            run_idx,
            run_seed,
        )
        summary_rows.append(row)

    summary_df = pd.DataFrame(summary_rows)
    summary_path = os.path.join(run_dir, f"{model_name}_all_runs_summary_metrics.csv")
    summary_df.to_csv(summary_path, index=False)
    aggregate_df = aggregate_runs(summary_df, run_dir, model_name=model_name)

    print("\n[完成] Transformer baseline 训练完成。")
    print(f"结果目录: {run_dir}")
    print(f"逐次结果: {summary_path}")
    print(f"均值标准差: {os.path.join(run_dir, f'{model_name}_aggregate_metrics_mean_std.csv')}")
    if not args.validation_only:
        row = aggregate_df[aggregate_df["metric"] == "test_macro_f1"].iloc[0]
        print(
            f"TRANSFORMER test macro-F1 mean±std over {int(row['n'])} runs: "
            f"{row['mean']:.4f} ± {row['std']:.4f}"
        )


if __name__ == "__main__":
    main()
