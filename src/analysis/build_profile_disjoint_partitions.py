from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from build_profile_disjoint_data import (
    PROFILE_COLS,
    SPLITS,
    assign_profile_splits,
    attach_run_splits,
    load_run_table,
    load_split_frames,
    save_outputs,
    standardize_frames,
)


DEFAULT_SPLIT_SEEDS = [101, 202, 303, 404, 505]


def parse_args() -> argparse.Namespace:
    dataset_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Build 3-5 unique profile-disjoint partitions without changing existing data."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=dataset_dir / "3s" / "labeled_csv_3s",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=dataset_dir / "3s" / "profile_disjoint_stability_20261007",
    )
    parser.add_argument(
        "--split-seeds",
        type=int,
        nargs="+",
        default=DEFAULT_SPLIT_SEEDS,
        help="Three to five deterministic profile-partition seeds.",
    )
    parser.add_argument(
        "--existing-profile-table",
        type=Path,
        default=dataset_dir
        / "3s"
        / "standardized_csv_3s_profile_disjoint"
        / "profile_split_check.csv",
        help="Existing seed-42 profile table used only to reject duplicate assignments.",
    )
    return parser.parse_args()


def assignment_signature(profiles: pd.DataFrame) -> tuple[tuple[str, ...], ...]:
    required = PROFILE_COLS + ["split"]
    missing = [column for column in required if column not in profiles.columns]
    if missing:
        raise ValueError(f"Profile table is missing columns: {missing}")
    ordered = profiles[required].astype(str).sort_values(PROFILE_COLS).reset_index(drop=True)
    return tuple(map(tuple, ordered.to_numpy()))


def validate_partition_count(seeds: list[int]) -> None:
    if not 3 <= len(seeds) <= 5:
        raise ValueError("Provide between 3 and 5 split seeds")
    if len(set(seeds)) != len(seeds):
        raise ValueError("Split seeds must be unique")


def require_new_or_empty(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise FileExistsError(
            f"Output root is not empty: {path}. Use a new output root to avoid overwriting data."
        )


def validate_disjoint_assignment(profiles: pd.DataFrame, runs: pd.DataFrame) -> None:
    profile_sets = {
        split: set(
            map(
                tuple,
                profiles.loc[profiles["split"] == split, PROFILE_COLS].to_numpy(),
            )
        )
        for split in SPLITS
    }
    run_sets = {
        split: set(runs.loc[runs["split"] == split, "run_id"].astype(str))
        for split in SPLITS
    }
    for left, right in (("train", "val"), ("train", "test"), ("val", "test")):
        if profile_sets[left] & profile_sets[right]:
            raise AssertionError(f"Profile overlap detected between {left} and {right}")
        if run_sets[left] & run_sets[right]:
            raise AssertionError(f"Run overlap detected between {left} and {right}")


def main() -> None:
    args = parse_args()
    validate_partition_count(args.split_seeds)
    input_dir = args.input_dir.resolve()
    output_root = args.output_root.resolve()
    require_new_or_empty(output_root)

    print(f"Reading run metadata from {input_dir}")
    runs = load_run_table(input_dir)
    print(f"Runs: {len(runs)}")

    seen_signatures: dict[tuple[tuple[str, ...], ...], str] = {}
    existing_table = args.existing_profile_table.resolve()
    if existing_table.is_file():
        existing_profiles = pd.read_csv(existing_table)
        seen_signatures[assignment_signature(existing_profiles)] = "existing_seed42"
        print(f"Loaded existing assignment for duplicate checking: {existing_table}")

    output_root.mkdir(parents=True, exist_ok=True)
    root_records = []

    for number, split_seed in enumerate(args.split_seeds, start=1):
        partition_name = f"partition_{number:02d}_seed{split_seed}"
        partition_root = output_root / partition_name
        standardized_dir = partition_root / "standardized_csv"
        model_input_dir = partition_root / "model_input"

        print("")
        print(f"[{number}/{len(args.split_seeds)}] Building {partition_name}")
        profiles = assign_profile_splits(runs, split_seed)
        signature = assignment_signature(profiles)
        if signature in seen_signatures:
            raise ValueError(
                f"{partition_name} duplicates profile assignment {seen_signatures[signature]}"
            )
        seen_signatures[signature] = partition_name

        run_splits = attach_run_splits(runs, profiles)
        validate_disjoint_assignment(profiles, run_splits)
        frames = load_split_frames(run_splits)
        standardized, feature_cols, mu, sigma = standardize_frames(frames)
        save_outputs(
            standardized_dir,
            model_input_dir,
            standardized,
            run_splits,
            profiles,
            feature_cols,
            mu,
            sigma,
            split_seed,
        )

        profile_counts = {
            split: int((profiles["split"] == split).sum()) for split in SPLITS
        }
        run_counts = {
            split: int((run_splits["split"] == split).sum()) for split in SPLITS
        }
        partition_record = {
            "partition": partition_name,
            "split_seed": split_seed,
            "profile_counts": profile_counts,
            "run_counts": run_counts,
            "standardized_dir": str(standardized_dir),
            "model_input_dir": str(model_input_dir),
        }
        (partition_root / "partition_manifest.json").write_text(
            json.dumps(partition_record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        root_records.append(partition_record)
        print(f"Profiles: {profile_counts}; runs: {run_counts}")

    root_manifest = {
        "experiment": "profile_disjoint_split_stability",
        "input_dir": str(input_dir),
        "partitions": root_records,
    }
    (output_root / "partitions_manifest.json").write_text(
        json.dumps(root_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("")
    print(f"Completed {len(root_records)} unique new partitions")
    print(f"Output root: {output_root}")


if __name__ == "__main__":
    main()
