"""Build order-invariant tabular inputs from temporal sequence arrays.

The generated samples keep the exact labels, metadata, and split membership of
the source temporal dataset. Three representations are supported:

- center: one center window from each sequence;
- mean: mean pooling over time;
- mean_std: concatenated temporal mean and standard deviation.

Each representation is standardized again using statistics fitted only on its
training split. The canonical ``X_<split>_<suffix>.npy`` files contain the
scaled features; raw pooled features are retained beside them for auditing.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler


SPLITS = ("train", "val", "test")
REPRESENTATIONS = ("center", "mean", "mean_std")


def infer_suffix(data_dir: Path) -> str:
    candidates = sorted(data_dir.glob("X_train_*.npy"))
    if len(candidates) != 1:
        raise ValueError(
            f"Expected exactly one X_train_*.npy in {data_dir}, found {len(candidates)}. "
            "Pass --suffix explicitly."
        )
    return candidates[0].stem.removeprefix("X_train_")


def load_feature_names(data_dir: Path, suffix: str, feature_count: int) -> list[str]:
    path = data_dir / f"feature_cols_train_{suffix}.json"
    if not path.exists():
        names = [f"feature_{i}" for i in range(feature_count)]
    else:
        names = json.loads(path.read_text(encoding="utf-8"))
    if len(names) != feature_count:
        raise ValueError(
            f"Feature-name count {len(names)} does not match sequence feature count {feature_count}."
        )
    return names


def load_split(data_dir: Path, split: str, suffix: str) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    x_path = data_dir / f"X_{split}_{suffix}.npy"
    y_path = data_dir / f"y_{split}_{suffix}.npy"
    meta_path = data_dir / f"meta_{split}_{suffix}.csv"

    missing = [path for path in (x_path, y_path, meta_path) if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing source files: {missing}")

    x = np.load(x_path, mmap_mode="r")
    y = np.load(y_path, mmap_mode="r")
    meta = pd.read_csv(meta_path)

    if x.ndim != 3:
        raise ValueError(f"{x_path} must have shape (N, T, F), got {x.shape}.")
    if len(x) != len(y) or len(x) != len(meta):
        raise ValueError(
            f"Length mismatch for {split}: X={len(x)}, y={len(y)}, meta={len(meta)}."
        )
    if "label" in meta.columns and not np.array_equal(y.astype(str), meta["label"].astype(str).to_numpy()):
        raise ValueError(f"Label mismatch between y and metadata for split {split}.")
    return x, y, meta


def transform_sequence(x: np.ndarray, representation: str) -> np.ndarray:
    if representation == "center":
        result = x[:, x.shape[1] // 2, :]
    elif representation == "mean":
        result = np.mean(x, axis=1)
    elif representation == "mean_std":
        result = np.concatenate((np.mean(x, axis=1), np.std(x, axis=1, ddof=0)), axis=1)
    else:
        raise ValueError(f"Unknown representation: {representation}")

    result = np.asarray(result, dtype=np.float32)
    if not np.isfinite(result).all():
        raise ValueError(f"Non-finite values found in {representation} features.")
    return result


def representation_feature_names(base_names: list[str], representation: str) -> list[str]:
    if representation == "center":
        return [f"{name}__center" for name in base_names]
    if representation == "mean":
        return [f"{name}__mean" for name in base_names]
    if representation == "mean_std":
        return [f"{name}__mean" for name in base_names] + [f"{name}__std" for name in base_names]
    raise ValueError(f"Unknown representation: {representation}")


def build_representation(
    data_dir: Path,
    output_dir: Path,
    suffix: str,
    representation: str,
    feature_names: list[str],
) -> None:
    rep_dir = output_dir / representation
    rep_dir.mkdir(parents=True, exist_ok=True)

    x_train, _, _ = load_split(data_dir, "train", suffix)
    train_raw = transform_sequence(x_train, representation)
    scaler = StandardScaler().fit(train_raw)

    np.savez(
        rep_dir / f"scaler_params_{suffix}.npz",
        mean=scaler.mean_,
        scale=scaler.scale_,
        var=scaler.var_,
        n_samples_seen=np.asarray(scaler.n_samples_seen_),
    )
    rep_names = representation_feature_names(feature_names, representation)
    (rep_dir / f"feature_names_{suffix}.json").write_text(
        json.dumps(rep_names, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    split_summary: dict[str, dict[str, object]] = {}
    for split in SPLITS:
        x, _, meta = load_split(data_dir, split, suffix)
        raw = train_raw if split == "train" else transform_sequence(x, representation)
        scaled = scaler.transform(raw).astype(np.float32, copy=False)
        if not np.isfinite(scaled).all():
            raise ValueError(f"Non-finite scaled values found in {representation}/{split}.")

        np.save(rep_dir / f"X_{split}_raw_{suffix}.npy", raw)
        np.save(rep_dir / f"X_{split}_{suffix}.npy", scaled)
        shutil.copy2(data_dir / f"y_{split}_{suffix}.npy", rep_dir / f"y_{split}_{suffix}.npy")
        shutil.copy2(data_dir / f"meta_{split}_{suffix}.csv", rep_dir / f"meta_{split}_{suffix}.csv")

        split_summary[split] = {
            "samples": int(len(meta)),
            "runs": int(meta["run_id"].nunique()),
            "shape": list(scaled.shape),
        }

    config = {
        "source_data_dir": str(data_dir.resolve()),
        "source_suffix": suffix,
        "representation": representation,
        "order_invariant": representation in {"mean", "mean_std"},
        "center_index": int(x_train.shape[1] // 2) if representation == "center" else None,
        "source_sequence_shape": [int(x_train.shape[1]), int(x_train.shape[2])],
        "output_feature_count": len(rep_names),
        "scaler_fit_split": "train",
        "split_summary": split_summary,
    }
    (rep_dir / f"representation_config_{suffix}.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[+] {representation}: {rep_dir}")
    for split, summary in split_summary.items():
        print(
            f"    {split}: shape={tuple(summary['shape'])}, "
            f"runs={summary['runs']}, samples={summary['samples']}"
        )


def parse_args() -> argparse.Namespace:
    base_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Build center-window and order-invariant tabular inputs from temporal arrays."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=base_dir / "3s" / "model_input_3st10",
        help="Temporal model_input directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=base_dir / "3s" / "model_input_non_temporal_orderless_3st10",
        help="Destination root; one subdirectory is created per representation.",
    )
    parser.add_argument("--suffix", default="auto", help="Input suffix, for example ratio0p6_T10_S1.")
    parser.add_argument(
        "--representations",
        nargs="+",
        choices=REPRESENTATIONS,
        default=list(REPRESENTATIONS),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = args.data_dir.resolve()
    output_dir = args.output_dir.resolve()
    suffix = infer_suffix(data_dir) if args.suffix == "auto" else args.suffix

    x_train, _, _ = load_split(data_dir, "train", suffix)
    feature_names = load_feature_names(data_dir, suffix, x_train.shape[2])

    print(f"[*] Source: {data_dir}")
    print(f"[*] Suffix: {suffix}")
    print(f"[*] Output: {output_dir}")
    for representation in args.representations:
        build_representation(data_dir, output_dir, suffix, representation, feature_names)


if __name__ == "__main__":
    main()
