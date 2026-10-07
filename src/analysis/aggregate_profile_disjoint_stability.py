from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd


PROFILE_COLS = ["attack_category", "attack_subtype", "profile_id"]
SPLITS = ("train", "val", "test")
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
EXPECTED_MODEL_SEEDS = {42, 43, 44, 45, 46}


def parse_args() -> argparse.Namespace:
    dataset_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate hierarchical model-seed and profile-split variability for "
            "profile-disjoint ResNet1D experiments."
        )
    )
    parser.add_argument(
        "--partitions-root",
        type=Path,
        default=dataset_dir / "3s" / "profile_disjoint_stability_20261007",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=dataset_dir
        / "experiment_results"
        / "profile_disjoint_split_stability_20261007",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument(
        "--include-existing",
        action="store_true",
        help="Include the original seed-42 profile partition as an additional split.",
    )
    parser.add_argument(
        "--existing-standardized-dir",
        type=Path,
        default=dataset_dir / "3s" / "standardized_csv_3s_profile_disjoint",
    )
    parser.add_argument(
        "--existing-result-dir",
        type=Path,
        default=dataset_dir / "experiment_results" / "profile_disjoint_resnet1d",
    )
    return parser.parse_args()


def find_one_summary(result_dir: Path) -> Path:
    matches = sorted(result_dir.rglob("resnet1d_all_runs_summary_metrics.csv"))
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one ResNet1D summary under {result_dir}, found {len(matches)}: "
            f"{[str(path) for path in matches]}"
        )
    return matches[0]


def validate_assignment(
    partition: str, standardized_dir: Path
) -> tuple[dict[str, int], dict[str, int]]:
    profile_path = standardized_dir / "profile_split_check.csv"
    run_path = standardized_dir / "run_split_info.csv"
    if not profile_path.is_file() or not run_path.is_file():
        raise FileNotFoundError(f"Missing assignment tables for {partition}")

    profiles = pd.read_csv(profile_path)
    runs = pd.read_csv(run_path)
    required_profile = PROFILE_COLS + ["split"]
    missing = [column for column in required_profile if column not in profiles.columns]
    if missing:
        raise ValueError(f"{profile_path} is missing columns {missing}")
    if profiles.duplicated(PROFILE_COLS).any():
        raise ValueError(f"Duplicate profile keys in {profile_path}")
    if not runs["run_id"].astype(str).is_unique:
        raise ValueError(f"Duplicate run_id values in {run_path}")

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
            raise ValueError(f"Profile leakage in {partition}: {left}/{right}")
        if run_sets[left] & run_sets[right]:
            raise ValueError(f"Run leakage in {partition}: {left}/{right}")

    categories = set(profiles["attack_category"].astype(str))
    for split in SPLITS:
        split_categories = set(
            profiles.loc[profiles["split"] == split, "attack_category"].astype(str)
        )
        if split_categories != categories:
            raise ValueError(f"{partition} {split} does not contain all categories")

    all_subtypes = set(
        map(
            tuple,
            profiles[["attack_category", "attack_subtype"]]
            .drop_duplicates()
            .to_numpy(),
        )
    )
    train_subtypes = set(
        map(
            tuple,
            profiles.loc[
                profiles["split"] == "train", ["attack_category", "attack_subtype"]
            ]
            .drop_duplicates()
            .to_numpy(),
        )
    )
    if train_subtypes != all_subtypes:
        raise ValueError(f"{partition} leaves at least one subtype out of train")

    return (
        {split: len(profile_sets[split]) for split in SPLITS},
        {split: len(run_sets[split]) for split in SPLITS},
    )


def load_partition_result(
    partition: str,
    split_seed: int,
    standardized_dir: Path,
    result_dir: Path,
    source: str,
) -> tuple[list[dict], list[dict], dict]:
    profile_counts, run_counts = validate_assignment(partition, standardized_dir)
    summary_path = find_one_summary(result_dir)
    summary = pd.read_csv(summary_path)
    seeds = set(summary["seed"].astype(int))
    if seeds != EXPECTED_MODEL_SEEDS or len(summary) != len(EXPECTED_MODEL_SEEDS):
        raise ValueError(
            f"{partition} must contain exactly model seeds 42-46; found {sorted(seeds)}"
        )

    run_records = []
    class_records = []
    for row in summary.sort_values("seed").itertuples(index=False):
        run_records.append(
            {
                "partition": partition,
                "partition_source": source,
                "split_seed": split_seed,
                "model_seed": int(row.seed),
                "best_epoch": int(row.best_epoch),
                "val_macro_f1": float(row.val_macro_f1),
                "test_accuracy": float(row.test_accuracy),
                "test_balanced_accuracy": float(row.test_balanced_accuracy),
                "test_macro_f1": float(row.test_macro_f1),
                "test_weighted_f1": float(row.test_weighted_f1),
            }
        )

        report_path = (
            summary_path.parent
            / f"run_{int(row.run):02d}_seed{int(row.seed)}"
            / "resnet1d_test_classification_report.csv"
        )
        if not report_path.is_file():
            raise FileNotFoundError(f"Missing class report: {report_path}")
        report = pd.read_csv(report_path, index_col=0)
        missing_classes = [label for label in CLASSES if label not in report.index]
        if missing_classes:
            raise ValueError(f"{report_path} is missing classes {missing_classes}")
        for label in CLASSES:
            class_records.append(
                {
                    "partition": partition,
                    "partition_source": source,
                    "split_seed": split_seed,
                    "model_seed": int(row.seed),
                    "class": label,
                    "precision": float(report.loc[label, "precision"]),
                    "recall": float(report.loc[label, "recall"]),
                    "f1": float(report.loc[label, "f1-score"]),
                    "support": int(report.loc[label, "support"]),
                }
            )

    assignment_record = {
        "partition": partition,
        "partition_source": source,
        "split_seed": split_seed,
        "profile_train": profile_counts["train"],
        "profile_val": profile_counts["val"],
        "profile_test": profile_counts["test"],
        "run_train": run_counts["train"],
        "run_val": run_counts["val"],
        "run_test": run_counts["test"],
        "profile_overlap": 0,
        "run_overlap": 0,
        "summary_path": str(summary_path),
        "profile_table": str(standardized_dir / "profile_split_check.csv"),
        "run_table": str(standardized_dir / "run_split_info.csv"),
    }
    return run_records, class_records, assignment_record


def pooled_within_sd(std_values: pd.Series) -> float:
    return math.sqrt(float((std_values.astype(float) ** 2).mean()))


def write_markdown_report(
    output_path: Path,
    partition_summary: pd.DataFrame,
    variance_summary: pd.DataFrame,
    class_variance: pd.DataFrame,
) -> None:
    overall = variance_summary.iloc[0]
    lines = [
        "# Profile-disjoint split-stability summary",
        "",
        f"Partitions included: {len(partition_summary)}",
        "",
        "## Partition-level test macro-F1",
        "",
        "| Partition | Split seed | Source | Profiles T/V/T | Runs T/V/T | Test macro-F1 |",
        "|---|---:|---|---:|---:|---:|",
    ]
    for row in partition_summary.itertuples(index=False):
        lines.append(
            f"| {row.partition} | {row.split_seed} | {row.partition_source} | "
            f"{row.profile_train}/{row.profile_val}/{row.profile_test} | "
            f"{row.run_train}/{row.run_val}/{row.run_test} | "
            f"{row.test_macro_f1_mean:.4f} ± {row.test_macro_f1_std:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Variance decomposition",
            "",
            f"- Equal-weight grand mean across partition means: {overall.grand_mean:.4f}",
            f"- Pooled within-partition model-seed SD: {overall.model_seed_sd:.4f}",
            f"- Between-partition split-selection SD: {overall.split_selection_sd:.4f}",
            "",
            "The model-seed SD is the square root of the mean within-partition sample variance. "
            "The split-selection SD is the sample SD of the partition means. They quantify "
            "different sources of uncertainty and should not be merged into one ± value.",
            "",
            "## Per-class split stability",
            "",
            "| Class | Mean of split means | Model-seed SD | Split-selection SD | Min split mean | Max split mean |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in class_variance.itertuples(index=False):
        lines.append(
            f"| {row.class_name} | {row.grand_mean:.4f} | {row.model_seed_sd:.4f} | "
            f"{row.split_selection_sd:.4f} | {row.min_partition_mean:.4f} | "
            f"{row.max_partition_mean:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Reproducibility",
            "",
            "Every partition has a saved `profile_split_check.csv`, `run_split_info.csv`, "
            "training summary, per-seed class report, and model checkpoint. Assignment paths "
            "are indexed in `partition_assignments.csv`.",
        ]
    )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    partitions_root = args.partitions_root.resolve()
    results_root = args.results_root.resolve()
    output_dir = (args.output_dir or results_root / "summary").resolve()

    partition_dirs = sorted(
        path
        for path in partitions_root.glob("partition_*_seed*")
        if path.is_dir()
    )
    if not 3 <= len(partition_dirs) <= 5:
        raise ValueError(
            f"Expected 3-5 new partition directories in {partitions_root}, found {len(partition_dirs)}"
        )

    all_runs = []
    all_classes = []
    assignments = []
    for partition_dir in partition_dirs:
        manifest = json.loads(
            (partition_dir / "partition_manifest.json").read_text(encoding="utf-8")
        )
        partition = str(manifest["partition"])
        run_records, class_records, assignment = load_partition_result(
            partition=partition,
            split_seed=int(manifest["split_seed"]),
            standardized_dir=partition_dir / "standardized_csv",
            result_dir=results_root / partition,
            source="new",
        )
        all_runs.extend(run_records)
        all_classes.extend(class_records)
        assignments.append(assignment)

    if args.include_existing:
        run_records, class_records, assignment = load_partition_result(
            partition="partition_00_seed42_existing",
            split_seed=42,
            standardized_dir=args.existing_standardized_dir.resolve(),
            result_dir=args.existing_result_dir.resolve(),
            source="existing",
        )
        all_runs.extend(run_records)
        all_classes.extend(class_records)
        assignments.append(assignment)

    runs = pd.DataFrame(all_runs)
    classes = pd.DataFrame(all_classes)
    assignment_df = pd.DataFrame(assignments)

    metric_summary = (
        runs.groupby(["partition", "partition_source", "split_seed"], as_index=False)
        .agg(
            model_seeds=("model_seed", "count"),
            val_macro_f1_mean=("val_macro_f1", "mean"),
            val_macro_f1_std=("val_macro_f1", "std"),
            test_macro_f1_mean=("test_macro_f1", "mean"),
            test_macro_f1_std=("test_macro_f1", "std"),
            test_macro_f1_min=("test_macro_f1", "min"),
            test_macro_f1_max=("test_macro_f1", "max"),
        )
        .merge(
            assignment_df.drop(columns=["partition_source", "split_seed"]),
            on="partition",
            how="left",
            validate="one_to_one",
        )
        .sort_values("split_seed")
        .reset_index(drop=True)
    )

    variance_summary = pd.DataFrame(
        [
            {
                "metric": "test_macro_f1",
                "partitions": len(metric_summary),
                "model_seeds_per_partition": 5,
                "grand_mean": float(metric_summary["test_macro_f1_mean"].mean()),
                "model_seed_sd": pooled_within_sd(
                    metric_summary["test_macro_f1_std"]
                ),
                "split_selection_sd": float(
                    metric_summary["test_macro_f1_mean"].std(ddof=1)
                ),
                "min_partition_mean": float(
                    metric_summary["test_macro_f1_mean"].min()
                ),
                "max_partition_mean": float(
                    metric_summary["test_macro_f1_mean"].max()
                ),
            }
        ]
    )

    partition_class = (
        classes.groupby(
            ["partition", "partition_source", "split_seed", "class"],
            as_index=False,
        )
        .agg(
            f1_mean=("f1", "mean"),
            f1_std=("f1", "std"),
            precision_mean=("precision", "mean"),
            recall_mean=("recall", "mean"),
            support=("support", "first"),
        )
        .sort_values(["class", "split_seed"])
        .reset_index(drop=True)
    )

    class_variance_records = []
    for label, group in partition_class.groupby("class", sort=True):
        class_variance_records.append(
            {
                "class_name": label,
                "partitions": len(group),
                "grand_mean": float(group["f1_mean"].mean()),
                "model_seed_sd": pooled_within_sd(group["f1_std"]),
                "split_selection_sd": float(group["f1_mean"].std(ddof=1)),
                "min_partition_mean": float(group["f1_mean"].min()),
                "max_partition_mean": float(group["f1_mean"].max()),
            }
        )
    class_variance = pd.DataFrame(class_variance_records)

    output_dir.mkdir(parents=True, exist_ok=True)
    runs.to_csv(output_dir / "per_run_metrics.csv", index=False)
    classes.to_csv(output_dir / "per_class_f1_runs.csv", index=False)
    assignment_df.to_csv(output_dir / "partition_assignments.csv", index=False)
    metric_summary.to_csv(output_dir / "partition_summary.csv", index=False)
    variance_summary.to_csv(output_dir / "variance_summary.csv", index=False)
    partition_class.to_csv(output_dir / "partition_class_summary.csv", index=False)
    class_variance.to_csv(output_dir / "class_variance_summary.csv", index=False)
    write_markdown_report(
        output_dir / "profile_disjoint_split_stability_report.md",
        metric_summary,
        variance_summary,
        class_variance,
    )

    print(metric_summary[["partition", "test_macro_f1_mean", "test_macro_f1_std"]].to_string(index=False))
    print("")
    print(variance_summary.to_string(index=False))
    print("")
    print(f"Summary written to: {output_dir}")


if __name__ == "__main__":
    main()
