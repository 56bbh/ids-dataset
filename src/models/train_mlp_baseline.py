"""Train MLP baselines on center-window or order-invariant sequence features."""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.preprocessing import LabelEncoder
from sklearn.utils.class_weight import compute_class_weight
from torch.utils.data import DataLoader, TensorDataset


SPLITS = ("train", "val", "test")
REPRESENTATIONS = ("center", "mean", "mean_std")


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def infer_suffix(data_dir: Path) -> str:
    candidates = sorted(data_dir.glob("X_train_*.npy"))
    candidates = [path for path in candidates if "_raw_" not in path.name]
    if len(candidates) != 1:
        raise ValueError(
            f"Expected exactly one scaled X_train_*.npy in {data_dir}, found {len(candidates)}. "
            "Pass --suffix explicitly."
        )
    return candidates[0].stem.removeprefix("X_train_")


def load_split(data_dir: Path, split: str, suffix: str) -> tuple[np.ndarray, np.ndarray]:
    x_path = data_dir / f"X_{split}_{suffix}.npy"
    y_path = data_dir / f"y_{split}_{suffix}.npy"
    x = np.load(x_path)
    y = np.load(y_path)
    if x.ndim != 2:
        raise ValueError(f"MLP input must have shape (N, F), got {x.shape} from {x_path}.")
    if len(x) != len(y):
        raise ValueError(f"Length mismatch for {split}: X={len(x)}, y={len(y)}.")
    if not np.isfinite(x).all():
        raise ValueError(f"Non-finite values found in {x_path}.")
    return x.astype(np.float32, copy=False), y.astype(str)


class MLPClassifier(nn.Module):
    def __init__(self, input_dim: int, hidden_dims: list[int], num_classes: int, dropout: float):
        super().__init__()
        layers: list[nn.Module] = []
        previous = input_dim
        for hidden in hidden_dims:
            layers.extend((nn.Linear(previous, hidden), nn.ReLU(), nn.Dropout(dropout)))
            previous = hidden
        layers.append(nn.Linear(previous, num_classes))
        self.network = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.network(x)


def make_loader(
    x: np.ndarray,
    y: np.ndarray,
    batch_size: int,
    shuffle: bool,
    device: torch.device,
    num_workers: int,
) -> DataLoader:
    dataset = TensorDataset(torch.from_numpy(x), torch.from_numpy(y.astype(np.int64)))
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=num_workers,
        pin_memory=device.type == "cuda",
    )


def predict(model: nn.Module, loader: DataLoader, device: torch.device) -> tuple[np.ndarray, np.ndarray]:
    model.eval()
    true_parts: list[np.ndarray] = []
    pred_parts: list[np.ndarray] = []
    with torch.no_grad():
        for x, y in loader:
            logits = model(x.to(device, non_blocking=True))
            true_parts.append(y.numpy())
            pred_parts.append(logits.argmax(dim=1).cpu().numpy())
    return np.concatenate(true_parts), np.concatenate(pred_parts)


def classification_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_precision": float(precision_score(y_true, y_pred, average="macro", zero_division=0)),
        "macro_recall": float(recall_score(y_true, y_pred, average="macro", zero_division=0)),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y_true, y_pred, average="weighted", zero_division=0)),
    }


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def train_one_run(
    representation: str,
    suffix: str,
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
    x_test: np.ndarray,
    y_test: np.ndarray,
    classes: list[str],
    args: argparse.Namespace,
    output_root: Path,
    run_index: int,
    run_seed: int,
) -> dict[str, object]:
    set_seed(run_seed)
    device = torch.device(
        "cuda" if args.device == "auto" and torch.cuda.is_available() else (
            "cpu" if args.device == "auto" else args.device
        )
    )

    model = MLPClassifier(x_train.shape[1], args.hidden_dims, len(classes), args.dropout).to(device)
    train_loader = make_loader(x_train, y_train, args.batch_size, True, device, args.num_workers)
    val_loader = make_loader(x_val, y_val, args.batch_size, False, device, args.num_workers)
    test_loader = None
    if not args.validation_only:
        test_loader = make_loader(
            x_test, y_test, args.batch_size, False, device, args.num_workers
        )

    class_weights = None
    if args.class_weight:
        weights = compute_class_weight(
            class_weight="balanced", classes=np.arange(len(classes)), y=y_train
        )
        class_weights = torch.tensor(weights, dtype=torch.float32, device=device)

    criterion = nn.CrossEntropyLoss(weight=class_weights)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.lr, weight_decay=args.weight_decay
    )
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=0.5, patience=max(2, args.patience // 2)
    )

    run_dir = output_root / f"run_{run_index:02d}_seed{run_seed}"
    run_dir.mkdir(parents=True, exist_ok=False)
    best_path = run_dir / "best_model.pt"
    history: list[dict[str, float | int]] = []
    best_macro_f1 = -1.0
    best_epoch = 0
    epochs_without_improvement = 0

    for epoch in range(1, args.epochs + 1):
        model.train()
        loss_sum = 0.0
        sample_count = 0
        for x, y in train_loader:
            x = x.to(device, non_blocking=True)
            y = y.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            logits = model(x)
            loss = criterion(logits, y)
            loss.backward()
            optimizer.step()
            loss_sum += float(loss.item()) * len(x)
            sample_count += len(x)

        val_true, val_pred = predict(model, val_loader, device)
        val_macro_f1 = float(f1_score(val_true, val_pred, average="macro", zero_division=0))
        scheduler.step(val_macro_f1)
        train_loss = loss_sum / sample_count
        history.append(
            {
                "epoch": epoch,
                "train_loss": train_loss,
                "val_macro_f1": val_macro_f1,
                "lr": optimizer.param_groups[0]["lr"],
            }
        )
        print(
            f"[{representation}] run={run_index}/{args.runs} epoch={epoch:03d} "
            f"train_loss={train_loss:.4f} val_macro_f1={val_macro_f1:.4f}"
        )

        if val_macro_f1 > best_macro_f1:
            best_macro_f1 = val_macro_f1
            best_epoch = epoch
            epochs_without_improvement = 0
            torch.save({"model_state_dict": model.state_dict()}, best_path)
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= args.patience:
                break

    checkpoint = torch.load(best_path, map_location=device, weights_only=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    val_true, val_pred = predict(model, val_loader, device)
    val_metrics = classification_metrics(val_true, val_pred)

    pd.DataFrame(history).to_csv(run_dir / "training_history.csv", index=False)

    result: dict[str, object] = {
        "representation": representation,
        "suffix": suffix,
        "run": run_index,
        "seed": run_seed,
        "best_epoch": best_epoch,
        "input_dim": int(x_train.shape[1]),
        "parameter_count": int(sum(parameter.numel() for parameter in model.parameters())),
        "val": val_metrics,
    }
    if not args.validation_only:
        test_true, test_pred = predict(model, test_loader, device)
        test_metrics = classification_metrics(test_true, test_pred)
        pd.DataFrame(
            confusion_matrix(test_true, test_pred), index=classes, columns=classes
        ).to_csv(run_dir / "test_confusion_matrix.csv")
        save_json(
            run_dir / "test_classification_report.json",
            classification_report(
                test_true,
                test_pred,
                labels=np.arange(len(classes)),
                target_names=classes,
                output_dict=True,
                zero_division=0,
            ),
        )
        result["test"] = test_metrics
    save_json(run_dir / "metrics.json", result)
    return result


def check_only(
    representation: str,
    x_train: np.ndarray,
    y_train: np.ndarray,
    args: argparse.Namespace,
    num_classes: int,
) -> None:
    device = torch.device(
        "cuda" if args.device == "auto" and torch.cuda.is_available() else (
            "cpu" if args.device == "auto" else args.device
        )
    )
    model = MLPClassifier(x_train.shape[1], args.hidden_dims, num_classes, args.dropout).to(device)
    size = min(args.batch_size, len(x_train))
    with torch.no_grad():
        logits = model(torch.from_numpy(x_train[:size]).to(device))
    if logits.shape != (size, num_classes):
        raise ValueError(f"Unexpected logits shape: {tuple(logits.shape)}")
    print(
        f"[check] {representation}: X={x_train.shape}, y={y_train.shape}, "
        f"logits={tuple(logits.shape)}, device={device}"
    )


def parse_args() -> argparse.Namespace:
    base_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Train order-free MLP baselines for TRNID.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=base_dir / "3s" / "model_input_non_temporal_orderless_3st10",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=base_dir / "non_temporal_results"
    )
    parser.add_argument(
        "--representation",
        choices=(*REPRESENTATIONS, "all"),
        default="all",
    )
    parser.add_argument("--suffix", default="auto")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--seed-step", type=int, default=1)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--hidden-dims", type=int, nargs="+", default=[128, 64])
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--class-weight", action="store_true")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Load all requested data and run one forward pass without training or writing results.",
    )
    parser.add_argument(
        "--validation-only",
        action="store_true",
        help="Do not load or evaluate the test split.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    representations = REPRESENTATIONS if args.representation == "all" else (args.representation,)

    for representation in representations:
        data_dir = args.data_dir.resolve() / representation
        suffix = infer_suffix(data_dir) if args.suffix == "auto" else args.suffix
        requested_splits = ("train", "val") if args.validation_only else SPLITS
        loaded = {split: load_split(data_dir, split, suffix) for split in requested_splits}

        encoder = LabelEncoder().fit(loaded["train"][1])
        classes = encoder.classes_.tolist()
        encoded: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for split, (x, y) in loaded.items():
            unknown = sorted(set(y) - set(classes))
            if unknown:
                raise ValueError(f"Unknown labels in {split}: {unknown}")
            encoded[split] = (x, encoder.transform(y))

        if args.check_only:
            check_only(
                representation,
                encoded["train"][0],
                encoded["train"][1],
                args,
                len(classes),
            )
            continue

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        evaluation_mode = "valonly" if args.validation_only else "full"
        output_root = (
            args.output_dir.resolve()
            / f"mlp_{representation}_{evaluation_mode}_{suffix}_{timestamp}"
        )
        output_root.mkdir(parents=True, exist_ok=False)
        save_json(output_root / "label_mapping.json", {label: i for i, label in enumerate(classes)})
        save_json(
            output_root / "run_config.json",
            {
                "representation": representation,
                "suffix": suffix,
                "data_dir": str(data_dir),
                "runs": args.runs,
                "seed": args.seed,
                "seed_step": args.seed_step,
                "epochs": args.epochs,
                "batch_size": args.batch_size,
                "lr": args.lr,
                "weight_decay": args.weight_decay,
                "patience": args.patience,
                "hidden_dims": args.hidden_dims,
                "dropout": args.dropout,
                "class_weight": args.class_weight,
                "validation_only": args.validation_only,
            },
        )

        results: list[dict[str, object]] = []
        for run_index in range(1, args.runs + 1):
            run_seed = args.seed + (run_index - 1) * args.seed_step
            results.append(
                train_one_run(
                    representation,
                    suffix,
                    encoded["train"][0],
                    encoded["train"][1],
                    encoded["val"][0],
                    encoded["val"][1],
                    None if args.validation_only else encoded["test"][0],
                    None if args.validation_only else encoded["test"][1],
                    classes,
                    args,
                    output_root,
                    run_index,
                    run_seed,
                )
            )

        summary_rows = []
        for result in results:
            row = {
                "run": result["run"],
                "seed": result["seed"],
                "best_epoch": result["best_epoch"],
                **{f"val_{key}": value for key, value in result["val"].items()},
            }
            if "test" in result:
                row.update({f"test_{key}": value for key, value in result["test"].items()})
            summary_rows.append(row)
        summary = pd.DataFrame(summary_rows)
        summary.to_csv(output_root / "summary.csv", index=False)
        metric_columns = [
            column for column in summary.columns if column.startswith(("val_", "test_"))
        ]
        summary_statistics = summary[metric_columns].agg(["mean", "std"]).transpose()
        summary_statistics.to_csv(output_root / "summary_statistics.csv")
        print(f"[+] Results: {output_root}")


if __name__ == "__main__":
    main()
