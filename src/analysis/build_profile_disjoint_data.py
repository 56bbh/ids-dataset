from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
import pandas as pd


META_COLS = [
    "run_id",
    "attack_category",
    "attack_subtype",
    "profile_id",
    "label",
    "window_idx",
    "window_id",
]
PROFILE_COLS = ["attack_category", "attack_subtype", "profile_id"]
SPLITS = ("train", "val", "test")
TRAIN_RATIO = 0.7
VAL_RATIO = 0.1
TEST_RATIO = 0.2
SEQ_LENGTH = 10
STRIDE = 1
DOMINANT_RATIO = 0.6
SUFFIX = "ratio0p6_T10_S1"
EPSILON = 1e-6


def parse_args() -> argparse.Namespace:
    dataset_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description=(
            "Build a separate 3-second, T10 profile-disjoint dataset. "
            "Each (category, subtype, profile_id) group belongs to exactly one split."
        )
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=dataset_dir / "3s" / "labeled_csv_3s",
    )
    parser.add_argument(
        "--standardized-dir",
        type=Path,
        default=dataset_dir / "3s" / "standardized_csv_3s_profile_disjoint",
    )
    parser.add_argument(
        "--model-input-dir",
        type=Path,
        default=dataset_dir / "3s" / "model_input_3st10_profile_disjoint",
    )
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def require_new_or_empty(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise FileExistsError(
            f"Output directory is not empty: {path}. "
            "Use a new directory instead of overwriting an existing dataset."
        )


def normalize_text(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def load_run_table(input_dir: Path) -> pd.DataFrame:
    paths = sorted(input_dir.glob("*.csv"))
    if not paths:
        raise FileNotFoundError(f"No labeled CSV files found in {input_dir}")

    records = []
    for path in paths:
        head = pd.read_csv(path, nrows=1)
        required = ["run_id", "attack_category", "attack_subtype", "profile_id"]
        missing = [col for col in required if col not in head.columns]
        if missing:
            raise ValueError(f"{path.name} is missing metadata columns: {missing}")

        record = {col: normalize_text(head[col].iloc[0]) for col in required}
        if any(not record[col] for col in required):
            raise ValueError(f"{path.name} has empty profile metadata: {record}")
        record["path"] = str(path.resolve())
        records.append(record)

    runs = pd.DataFrame(records)
    if not runs["run_id"].is_unique:
        duplicate_ids = runs.loc[runs["run_id"].duplicated(False), "run_id"].tolist()
        raise ValueError(f"Duplicate run_id values detected: {duplicate_ids}")
    return runs


def assign_profile_splits(runs: pd.DataFrame, seed: int) -> pd.DataFrame:
    profiles = (
        runs.groupby(PROFILE_COLS, as_index=False)
        .agg(run_count=("run_id", "size"))
        .sort_values(PROFILE_COLS)
        .reset_index(drop=True)
    )
    profiles["split"] = ""
    rng = random.Random(seed)

    for category, category_profiles in profiles.groupby("attack_category", sort=True):
        category_indices = category_profiles.index.tolist()
        n_profiles = len(category_indices)
        n_val = max(1, int(round(n_profiles * VAL_RATIO)))
        n_test = max(1, int(round(n_profiles * TEST_RATIO)))

        # Reserve one complete profile from every subtype for training. This keeps
        # profile novelty separate from the stronger unseen-subtype problem.
        train_anchors = []
        for _, subtype_profiles in category_profiles.groupby("attack_subtype", sort=True):
            choices = subtype_profiles.index.tolist()
            rng.shuffle(choices)
            train_anchors.append(choices[0])

        remaining = [idx for idx in category_indices if idx not in train_anchors]
        if len(remaining) < n_val + n_test:
            raise ValueError(
                f"Category {category!r} has too few profiles to keep every subtype "
                "in train while also populating validation and test."
            )

        rng.shuffle(remaining)
        test_indices = remaining[:n_test]
        val_indices = remaining[n_test : n_test + n_val]
        train_indices = train_anchors + remaining[n_test + n_val :]

        profiles.loc[train_indices, "split"] = "train"
        profiles.loc[val_indices, "split"] = "val"
        profiles.loc[test_indices, "split"] = "test"

    if profiles["split"].eq("").any():
        raise AssertionError("Some profiles were not assigned to a split")
    if profiles.duplicated(PROFILE_COLS).any():
        raise AssertionError("Duplicate profile keys detected")

    all_categories = set(profiles["attack_category"])
    for split in SPLITS:
        split_categories = set(profiles.loc[profiles["split"] == split, "attack_category"])
        if split_categories != all_categories:
            raise AssertionError(f"{split} does not contain every attack category")

    train_subtypes = set(
        map(
            tuple,
            profiles.loc[profiles["split"] == "train", ["attack_category", "attack_subtype"]]
            .drop_duplicates()
            .to_numpy(),
        )
    )
    all_subtypes = set(
        map(
            tuple,
            profiles[["attack_category", "attack_subtype"]].drop_duplicates().to_numpy(),
        )
    )
    if train_subtypes != all_subtypes:
        raise AssertionError("At least one subtype is absent from train")

    return profiles


def attach_run_splits(runs: pd.DataFrame, profiles: pd.DataFrame) -> pd.DataFrame:
    run_splits = runs.merge(
        profiles[PROFILE_COLS + ["split"]],
        on=PROFILE_COLS,
        how="left",
        validate="many_to_one",
    )
    if run_splits["split"].isna().any():
        raise AssertionError("Some runs have no split assignment")
    return run_splits


def load_split_frames(run_splits: pd.DataFrame) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for split in SPLITS:
        parts = []
        for row in run_splits.loc[run_splits["split"] == split].itertuples(index=False):
            frame = pd.read_csv(row.path)
            if frame["run_id"].astype(str).nunique() != 1:
                raise ValueError(f"{row.path} contains more than one run_id")
            if normalize_text(frame["run_id"].iloc[0]) != row.run_id:
                raise ValueError(f"run_id mismatch in {row.path}")
            parts.append(frame)
        frames[split] = pd.concat(parts, ignore_index=True)
    return frames


def standardize_frames(
    frames: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.DataFrame], list[str], pd.Series, pd.Series]:
    train = frames["train"]
    feature_cols = [
        col
        for col in train.columns
        if col not in META_COLS and pd.api.types.is_numeric_dtype(train[col])
    ]
    if len(feature_cols) != 40:
        raise ValueError(f"Expected 40 numeric features, found {len(feature_cols)}")

    for split, frame in frames.items():
        missing = [col for col in feature_cols if col not in frame.columns]
        if missing:
            raise ValueError(f"{split} is missing feature columns: {missing}")

    mu = train[feature_cols].mean()
    sigma = train[feature_cols].std().replace(0, EPSILON)
    standardized = {}
    for split, frame in frames.items():
        output = frame.copy()
        output[feature_cols] = (frame[feature_cols] - mu) / sigma
        standardized[split] = output
    return standardized, feature_cols, mu, sigma


def build_sequences(
    frame: pd.DataFrame, feature_cols: list[str]
) -> tuple[np.ndarray, np.ndarray, pd.DataFrame, pd.DataFrame]:
    x_list = []
    y_list = []
    retained = []
    discarded = []
    idx_col = "window_idx" if "window_idx" in frame.columns else "window_id"

    for run_id, group in frame.groupby("run_id", sort=True):
        group = group.sort_values(idx_col).reset_index(drop=True)
        for start in range(0, len(group) - SEQ_LENGTH + 1, STRIDE):
            sequence = group.iloc[start : start + SEQ_LENGTH]
            ratios = sequence["label"].astype(str).value_counts(normalize=True)
            dominant_label = str(ratios.index[0])
            dominant_ratio = float(ratios.iloc[0])
            metadata = {
                "run_id": run_id,
                "attack_category": normalize_text(sequence["attack_category"].iloc[0]),
                "attack_subtype": normalize_text(sequence["attack_subtype"].iloc[0]),
                "profile_id": normalize_text(sequence["profile_id"].iloc[0]),
                "start_win": int(sequence[idx_col].iloc[0]),
                "end_win": int(sequence[idx_col].iloc[-1]),
                "dominant_label": dominant_label,
                "dominant_ratio": dominant_ratio,
                "num_unique_labels": int(sequence["label"].nunique()),
            }
            if dominant_ratio < DOMINANT_RATIO:
                discarded.append(metadata)
                continue

            x_list.append(sequence[feature_cols].to_numpy())
            y_list.append(dominant_label)
            retained.append(
                {
                    **metadata,
                    "label": dominant_label,
                    "strategy": "ratio",
                    "seq_length": SEQ_LENGTH,
                    "stride": STRIDE,
                    "is_mixed": int(metadata["num_unique_labels"] > 1),
                }
            )

    if not x_list:
        raise ValueError("No retained sequences were generated")
    discarded_columns = [
        "run_id",
        "attack_category",
        "attack_subtype",
        "profile_id",
        "start_win",
        "end_win",
        "dominant_label",
        "dominant_ratio",
        "num_unique_labels",
    ]
    return (
        np.asarray(x_list),
        np.asarray(y_list),
        pd.DataFrame(retained),
        pd.DataFrame(discarded, columns=discarded_columns),
    )


def save_outputs(
    standardized_dir: Path,
    model_input_dir: Path,
    standardized: dict[str, pd.DataFrame],
    run_splits: pd.DataFrame,
    profiles: pd.DataFrame,
    feature_cols: list[str],
    mu: pd.Series,
    sigma: pd.Series,
    seed: int,
) -> None:
    # Empty pre-created directories are allowed; non-empty ones were rejected
    # before any processing so an existing dataset cannot be overwritten.
    standardized_dir.mkdir(parents=True, exist_ok=True)
    model_input_dir.mkdir(parents=True, exist_ok=True)

    for split, frame in standardized.items():
        frame.to_csv(standardized_dir / f"{split}_windows_std.csv", index=False)

    run_splits.drop(columns=["path"]).to_csv(
        standardized_dir / "run_split_info.csv", index=False
    )
    profile_check = profiles.copy()
    for split in SPLITS:
        profile_check[split] = np.where(
            profile_check["split"].eq(split), profile_check["run_count"], 0
        )
    profile_check.to_csv(standardized_dir / "profile_split_check.csv", index=False)
    pd.DataFrame(
        {
            "feature": feature_cols,
            "mean": [float(mu[col]) for col in feature_cols],
            "std": [float(sigma[col]) for col in feature_cols],
        }
    ).to_csv(standardized_dir / "standardization_params.csv", index=False)
    (standardized_dir / "feature_columns.json").write_text(
        json.dumps(feature_cols, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    sequence_summary = {}
    for split, frame in standardized.items():
        x, y, meta, discarded = build_sequences(frame, feature_cols)
        np.save(model_input_dir / f"X_{split}_{SUFFIX}.npy", x)
        np.save(model_input_dir / f"y_{split}_{SUFFIX}.npy", y)
        meta.to_csv(model_input_dir / f"meta_{split}_{SUFFIX}.csv", index=False)
        discarded.to_csv(
            model_input_dir / f"discarded_{split}_{SUFFIX}.csv", index=False
        )
        (model_input_dir / f"feature_cols_{split}_{SUFFIX}.json").write_text(
            json.dumps(feature_cols, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        sequence_summary[split] = {
            "x_shape": list(x.shape),
            "retained_sequences": int(len(meta)),
            "discarded_sequences": int(len(discarded)),
            "runs": int(meta["run_id"].nunique()),
            "profiles": int(
                meta[PROFILE_COLS].drop_duplicates().shape[0]
            ),
        }

    manifest = {
        "split_type": "profile_disjoint",
        "profile_key": PROFILE_COLS,
        "seed": seed,
        "target_ratios": {
            "train": TRAIN_RATIO,
            "val": VAL_RATIO,
            "test": TEST_RATIO,
        },
        "sequence_length": SEQ_LENGTH,
        "window_width_seconds": 3,
        "stride": STRIDE,
        "dominant_ratio": DOMINANT_RATIO,
        "run_counts": {
            split: int((run_splits["split"] == split).sum()) for split in SPLITS
        },
        "profile_counts": {
            split: int((profiles["split"] == split).sum()) for split in SPLITS
        },
        "sequence_summary": sequence_summary,
    }
    (model_input_dir / "profile_disjoint_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    standardized_dir = args.standardized_dir.resolve()
    model_input_dir = args.model_input_dir.resolve()
    require_new_or_empty(standardized_dir)
    require_new_or_empty(model_input_dir)

    print(f"[1/5] Reading run metadata from {input_dir}")
    runs = load_run_table(input_dir)
    print(f"      runs={len(runs)}")

    print("[2/5] Assigning complete profiles to train/val/test")
    profiles = assign_profile_splits(runs, args.seed)
    run_splits = attach_run_splits(runs, profiles)
    for split in SPLITS:
        print(
            f"      {split}: profiles={(profiles['split'] == split).sum()}, "
            f"runs={(run_splits['split'] == split).sum()}"
        )

    print("[3/5] Loading windows and fitting standardization on train only")
    frames = load_split_frames(run_splits)
    standardized, feature_cols, mu, sigma = standardize_frames(frames)

    print("[4/5] Building T10 sequences within individual runs")
    save_outputs(
        standardized_dir,
        model_input_dir,
        standardized,
        run_splits,
        profiles,
        feature_cols,
        mu,
        sigma,
        args.seed,
    )

    print("[5/5] Complete")
    print(f"Standardized CSVs: {standardized_dir}")
    print(f"Model inputs:      {model_input_dir}")


if __name__ == "__main__":
    main()
