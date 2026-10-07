from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score


WINDOW_SECONDS = 3.0
SEQUENCE_LENGTH = 10
STRIDE = 1
THRESHOLD = 0.6


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze boundary and mixed-activity difficulty from saved test predictions."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="New output directory. Defaults to a timestamped experiment_results directory.",
    )
    return parser.parse_args()


def parse_note(path: Path) -> dict[str, object]:
    timestamps: dict[str, datetime] = {}
    metadata: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            metadata[key.strip()] = value.strip()
        match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if match:
            for key in ("PCAP_START", "ATTACK_START", "ATTACK_END", "PCAP_STOP"):
                if line.startswith(key):
                    timestamps[key] = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                    break

    required_times = {"PCAP_START", "ATTACK_START", "ATTACK_END"}
    missing = sorted(required_times - timestamps.keys())
    if missing:
        raise ValueError(f"{path} is missing timestamps: {missing}")

    run_id = metadata.get("RUN_ID") or path.name.removesuffix("_notes.log")
    attack_start = (timestamps["ATTACK_START"] - timestamps["PCAP_START"]).total_seconds()
    attack_end = (timestamps["ATTACK_END"] - timestamps["PCAP_START"]).total_seconds()
    if attack_end <= attack_start:
        raise ValueError(f"Invalid active interval in {path}: {attack_start} to {attack_end}")
    return {
        "run_id": run_id,
        "attack_start_sec": float(attack_start),
        "attack_end_sec": float(attack_end),
        "note_path": str(path),
        "note_attack_category": metadata.get("ATTACK_CATEGORY", ""),
        "note_attack_subtype": metadata.get("ATTACK_SUBTYPE", ""),
        "note_profile_id": metadata.get("PROFILE_ID", ""),
    }


def load_note_timing(project_root: Path) -> pd.DataFrame:
    note_dirs = [
        project_root / "1 新abuse-exploit-brute" / "notes",
        project_root / "2 新normal-backdoor-http" / "notes",
        project_root / "3 scan-payload-exfiltration" / "notes",
    ]
    note_paths = sorted(path for directory in note_dirs for path in directory.glob("*_notes.log"))
    rows = [parse_note(path) for path in note_paths]
    timing = pd.DataFrame(rows)
    if timing.empty:
        raise ValueError("No notes were found.")
    duplicates = timing.loc[timing["run_id"].duplicated(keep=False), "run_id"].unique().tolist()
    if duplicates:
        raise ValueError(f"Duplicate current notes for run_id values: {duplicates[:10]}")
    return timing


def classify_sequences(meta: pd.DataFrame, timing: pd.DataFrame) -> pd.DataFrame:
    sequences = meta.copy()
    sequences.insert(0, "sample_index", np.arange(len(sequences), dtype=int))
    sequences = sequences.merge(timing, on="run_id", how="left", validate="many_to_one")
    missing = sequences.loc[sequences["attack_start_sec"].isna(), "run_id"].unique().tolist()
    if missing:
        raise ValueError(f"Missing current notes for test runs: {missing[:10]}")

    sequences["sequence_start_sec"] = sequences["start_win"].astype(float) * WINDOW_SECONDS
    sequences["sequence_end_sec"] = (sequences["end_win"].astype(float) + 1.0) * WINDOW_SECONDS

    active_counts: list[int] = []
    activity_patterns: list[str] = []
    for row in sequences.itertuples(index=False):
        states = []
        for window_idx in range(int(row.start_win), int(row.end_win) + 1):
            window_start = window_idx * WINDOW_SECONDS
            window_end = window_start + WINDOW_SECONDS
            active = window_end > row.attack_start_sec and window_start < row.attack_end_sec
            states.append("A" if active else "I")
        if len(states) != SEQUENCE_LENGTH:
            raise ValueError(
                f"Unexpected sequence length for sample {row.sample_index}: {len(states)}"
            )
        active_counts.append(states.count("A"))
        activity_patterns.append("".join(states))

    sequences["behavior_active_count"] = active_counts
    sequences["behavior_idle_count"] = SEQUENCE_LENGTH - sequences["behavior_active_count"]
    sequences["behavior_activity_ratio"] = sequences["behavior_active_count"] / SEQUENCE_LENGTH
    sequences["behavior_activity_pattern"] = activity_patterns
    sequences["behavior_activity_group"] = np.select(
        [
            sequences["behavior_active_count"].eq(SEQUENCE_LENGTH),
            sequences["behavior_active_count"].eq(0),
        ],
        ["all_active", "all_idle"],
        default="mixed_active_idle",
    )

    # Fixed before reading model performance. Strict inequalities mean the sequence
    # contains time on both sides of the true boundary. A boundary exactly at the
    # sequence edge is not considered crossed.
    sequences["crosses_attack_start"] = (
        (sequences["sequence_start_sec"] < sequences["attack_start_sec"])
        & (sequences["attack_start_sec"] < sequences["sequence_end_sec"])
    )
    sequences["crosses_attack_end"] = (
        (sequences["sequence_start_sec"] < sequences["attack_end_sec"])
        & (sequences["attack_end_sec"] < sequences["sequence_end_sec"])
    )
    sequences["crosses_any_boundary"] = (
        sequences["crosses_attack_start"] | sequences["crosses_attack_end"]
    )
    sequences["boundary_near"] = (
        sequences["behavior_activity_group"].eq("mixed_active_idle")
        & sequences["crosses_any_boundary"]
    )
    sequences["boundary_group"] = np.where(
        sequences["boundary_near"], "boundary_near", "non_boundary"
    )
    sequences["boundary_type"] = np.select(
        [
            sequences["boundary_near"]
            & sequences["crosses_attack_start"]
            & sequences["crosses_attack_end"],
            sequences["boundary_near"] & sequences["crosses_attack_start"],
            sequences["boundary_near"] & sequences["crosses_attack_end"],
        ],
        ["start_and_end", "attack_start", "attack_end"],
        default="non_boundary",
    )
    return sequences


def add_packet_activity(sequences: pd.DataFrame, audit_path: Path) -> pd.DataFrame:
    audit = pd.read_csv(audit_path)
    audit = audit.loc[(audit["split"] == "test") & (audit["status"] == "retained")].copy()
    audit = audit.sort_values("source_row_index").reset_index(drop=True)
    if len(audit) != len(sequences):
        raise ValueError(f"Activity audit has {len(audit)} retained test rows; expected {len(sequences)}")
    if not np.array_equal(audit["source_row_index"].to_numpy(), sequences["sample_index"].to_numpy()):
        raise ValueError("Activity audit source_row_index is not aligned to test metadata.")
    checks = {
        "run_id": "run_id",
        "start_win": "start_win",
        "end_win": "end_win",
        "assigned_label": "label",
    }
    for audit_col, sequence_col in checks.items():
        if not audit[audit_col].astype(str).equals(sequences[sequence_col].astype(str)):
            raise ValueError(f"Activity audit mismatch in {audit_col} versus {sequence_col}")

    result = sequences.copy()
    result["packet_active_count"] = audit["active_count"].to_numpy()
    result["packet_idle_count"] = audit["idle_count"].to_numpy()
    result["packet_activity_ratio"] = audit["activity_ratio"].to_numpy()
    result["packet_activity_pattern"] = audit["activity_pattern"].to_numpy()
    result["packet_activity_group"] = np.select(
        [result["packet_active_count"].eq(SEQUENCE_LENGTH), result["packet_active_count"].eq(0)],
        ["all_packet_active", "all_packet_idle"],
        default="mixed_packet_active_idle",
    )
    result["packet_mixed_boundary_group"] = np.where(
        result["packet_activity_group"].eq("mixed_packet_active_idle"),
        np.where(result["crosses_any_boundary"], "boundary_near", "non_boundary"),
        "not_packet_mixed",
    )
    return result


def find_prediction_files(prediction_root: Path) -> dict[str, list[Path]]:
    patterns = {
        "LSTM": "lstm/lstm_ordered_*",
        "ResNet1D": "resnet1d/resnet1d_ordered_*",
        "Transformer": "transformer/transformer_ordered_*",
    }
    found: dict[str, list[Path]] = {}
    for model, pattern in patterns.items():
        result_dirs = sorted(prediction_root.glob(pattern))
        if len(result_dirs) != 1:
            raise ValueError(f"Expected one ordered result directory for {model}; found {len(result_dirs)}")
        files = sorted(result_dirs[0].glob("run_*_seed*/test_predictions.csv"))
        if not files:
            raise ValueError(f"No saved ordered test predictions found for {model}")
        found[model] = files
    return found


def seed_from_path(path: Path) -> int:
    match = re.search(r"seed(\d+)", path.parent.name)
    if not match:
        raise ValueError(f"Cannot parse seed from {path}")
    return int(match.group(1))


def metric_row(y_true: pd.Series, y_pred: pd.Series, classes: list[str]) -> dict[str, float | int]:
    present = [label for label in classes if (y_true == label).any()]
    return {
        "n_sequences": int(len(y_true)),
        "n_true_classes": int(len(present)),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_f1_present_classes": float(
            f1_score(y_true, y_pred, labels=present, average="macro", zero_division=0)
        ),
        "macro_f1_fixed_9_classes": float(
            f1_score(y_true, y_pred, labels=classes, average="macro", zero_division=0)
        ),
        "weighted_f1": float(
            f1_score(y_true, y_pred, labels=classes, average="weighted", zero_division=0)
        ),
    }


def group_specs(sequences: pd.DataFrame) -> list[dict[str, object]]:
    return [
        {
            "dimension": "behavior_activity",
            "column": "behavior_activity_group",
            "groups": ["all_active", "mixed_active_idle", "all_idle"],
            "mask": pd.Series(True, index=sequences.index),
        },
        {
            "dimension": "boundary_vs_non_boundary",
            "column": "boundary_group",
            "groups": ["boundary_near", "non_boundary"],
            "mask": pd.Series(True, index=sequences.index),
        },
        {
            "dimension": "boundary_type",
            "column": "boundary_type",
            "groups": ["attack_start", "attack_end", "start_and_end", "non_boundary"],
            "mask": pd.Series(True, index=sequences.index),
        },
        {
            "dimension": "packet_activity_supplement",
            "column": "packet_activity_group",
            "groups": ["all_packet_active", "mixed_packet_active_idle", "all_packet_idle"],
            "mask": pd.Series(True, index=sequences.index),
        },
        {
            "dimension": "packet_mixed_boundary_supplement",
            "column": "packet_mixed_boundary_group",
            "groups": ["boundary_near", "non_boundary"],
            "mask": sequences["packet_activity_group"].eq("mixed_packet_active_idle"),
        },
    ]


def summarize_counts(sequences: pd.DataFrame, specs: list[dict[str, object]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    count_rows: list[dict[str, object]] = []
    class_rows: list[dict[str, object]] = []
    for spec in specs:
        scope = sequences.loc[spec["mask"]]
        for group in spec["groups"]:
            subset = scope.loc[scope[spec["column"]] == group]
            count_rows.append(
                {
                    "dimension": spec["dimension"],
                    "group": group,
                    "n_sequences": len(subset),
                    "scope_n_sequences": len(scope),
                    "proportion_within_scope": len(subset) / len(scope) if len(scope) else np.nan,
                    "n_runs": subset["run_id"].nunique(),
                    "n_true_classes": subset["label"].nunique(),
                }
            )
            label_counts = subset["label"].value_counts()
            for label, count in label_counts.items():
                class_rows.append(
                    {
                        "dimension": spec["dimension"],
                        "group": group,
                        "class_label": label,
                        "support": int(count),
                        "class_proportion": count / len(subset) if len(subset) else np.nan,
                    }
                )
    return pd.DataFrame(count_rows), pd.DataFrame(class_rows)


def analyze_predictions(
    sequences: pd.DataFrame,
    prediction_files: dict[str, list[Path]],
    specs: list[dict[str, object]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    first_prediction = pd.read_csv(next(iter(prediction_files.values()))[0])
    id_labels = (
        first_prediction[["true_id", "true_label"]]
        .drop_duplicates()
        .sort_values("true_id")
    )
    classes = id_labels["true_label"].tolist()
    if len(classes) != 9:
        raise ValueError(f"Expected 9 classes in test predictions; found {classes}")

    metric_rows: list[dict[str, object]] = []
    per_class_rows: list[dict[str, object]] = []
    confusion_totals: dict[tuple[str, str, str], np.ndarray] = defaultdict(
        lambda: np.zeros((len(classes), len(classes)), dtype=int)
    )

    for model, files in prediction_files.items():
        for path in files:
            seed = seed_from_path(path)
            prediction = pd.read_csv(path).sort_values("sample_index").reset_index(drop=True)
            if len(prediction) != len(sequences):
                raise ValueError(f"{path} has {len(prediction)} predictions; expected {len(sequences)}")
            if not np.array_equal(prediction["sample_index"].to_numpy(), sequences["sample_index"].to_numpy()):
                raise ValueError(f"sample_index is not aligned in {path}")
            if not prediction["true_label"].astype(str).equals(sequences["label"].astype(str)):
                raise ValueError(f"true_label is not aligned to metadata in {path}")

            for spec in specs:
                scope = sequences.loc[spec["mask"]]
                scope_n = len(scope)
                for group in spec["groups"]:
                    indices = scope.index[scope[spec["column"]] == group]
                    if len(indices) == 0:
                        continue
                    y_true = prediction.loc[indices, "true_label"]
                    y_pred = prediction.loc[indices, "pred_label"]
                    row = {
                        "model": model,
                        "seed": seed,
                        "dimension": spec["dimension"],
                        "group": group,
                        "scope_n_sequences": scope_n,
                        "proportion_within_scope": len(indices) / scope_n,
                        "prediction_file": str(path),
                    }
                    row.update(metric_row(y_true, y_pred, classes))
                    metric_rows.append(row)

                    f1_values = f1_score(
                        y_true, y_pred, labels=classes, average=None, zero_division=0
                    )
                    supports = y_true.value_counts()
                    for label, value in zip(classes, f1_values):
                        per_class_rows.append(
                            {
                                "model": model,
                                "seed": seed,
                                "dimension": spec["dimension"],
                                "group": group,
                                "class_label": label,
                                "support": int(supports.get(label, 0)),
                                "f1": float(value),
                            }
                        )
                    confusion_totals[(model, spec["dimension"], group)] += confusion_matrix(
                        y_true, y_pred, labels=classes
                    )

    metrics = pd.DataFrame(metric_rows)
    per_class = pd.DataFrame(per_class_rows)
    confusion_rows: list[dict[str, object]] = []
    major_rows: list[dict[str, object]] = []
    n_seeds_by_model = {model: len(files) for model, files in prediction_files.items()}
    for (model, dimension, group), matrix in confusion_totals.items():
        n_seeds = n_seeds_by_model[model]
        row_totals = matrix.sum(axis=1)
        total_errors = int(matrix.sum() - np.trace(matrix))
        for i, true_label in enumerate(classes):
            for j, pred_label in enumerate(classes):
                count = int(matrix[i, j])
                confusion_rows.append(
                    {
                        "model": model,
                        "dimension": dimension,
                        "group": group,
                        "true_label": true_label,
                        "pred_label": pred_label,
                        "count_across_seeds": count,
                        "mean_count_per_seed": count / n_seeds,
                        "row_rate": count / row_totals[i] if row_totals[i] else np.nan,
                    }
                )
                if i != j and count:
                    major_rows.append(
                        {
                            "model": model,
                            "dimension": dimension,
                            "group": group,
                            "true_label": true_label,
                            "pred_label": pred_label,
                            "count_across_seeds": count,
                            "mean_count_per_seed": count / n_seeds,
                            "share_of_group_errors": count / total_errors if total_errors else np.nan,
                        }
                    )
    major = pd.DataFrame(major_rows)
    if not major.empty:
        major = major.sort_values(
            ["model", "dimension", "group", "count_across_seeds"],
            ascending=[True, True, True, False],
        )
        major["error_pair_rank"] = major.groupby(["model", "dimension", "group"]).cumcount() + 1
    return metrics, per_class, pd.DataFrame(confusion_rows), major


def aggregate_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    metric_columns = [
        "accuracy",
        "macro_f1_present_classes",
        "macro_f1_fixed_9_classes",
        "weighted_f1",
    ]
    summary = metrics.groupby(["model", "dimension", "group"], as_index=False).agg(
        n_model_seeds=("seed", "nunique"),
        n_sequences=("n_sequences", "first"),
        scope_n_sequences=("scope_n_sequences", "first"),
        proportion_within_scope=("proportion_within_scope", "first"),
        n_true_classes=("n_true_classes", "first"),
        **{f"{column}_mean": (column, "mean") for column in metric_columns},
        **{f"{column}_std": (column, "std") for column in metric_columns},
    )
    return summary


def aggregate_per_class(per_class: pd.DataFrame) -> pd.DataFrame:
    return per_class.groupby(
        ["model", "dimension", "group", "class_label"], as_index=False
    ).agg(
        n_model_seeds=("seed", "nunique"),
        support=("support", "first"),
        f1_mean=("f1", "mean"),
        f1_std=("f1", "std"),
    )


def paired_differences(metrics: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    comparisons = [
        ("behavior_activity", "mixed_active_idle", "all_active"),
        ("boundary_vs_non_boundary", "boundary_near", "non_boundary"),
        ("boundary_type", "attack_start", "attack_end"),
        ("packet_activity_supplement", "mixed_packet_active_idle", "all_packet_active"),
        ("packet_mixed_boundary_supplement", "boundary_near", "non_boundary"),
    ]
    metric_columns = [
        "accuracy",
        "macro_f1_present_classes",
        "macro_f1_fixed_9_classes",
        "weighted_f1",
    ]
    rows: list[dict[str, object]] = []
    for dimension, left, right in comparisons:
        subset = metrics.loc[metrics["dimension"] == dimension]
        for model in sorted(subset["model"].unique()):
            model_data = subset.loc[subset["model"] == model].set_index(["seed", "group"])
            for seed in sorted(subset.loc[subset["model"] == model, "seed"].unique()):
                if (seed, left) not in model_data.index or (seed, right) not in model_data.index:
                    continue
                for metric in metric_columns:
                    left_value = model_data.loc[(seed, left), metric]
                    right_value = model_data.loc[(seed, right), metric]
                    rows.append(
                        {
                            "model": model,
                            "seed": seed,
                            "dimension": dimension,
                            "left_group": left,
                            "right_group": right,
                            "metric": metric,
                            "left_value": left_value,
                            "right_value": right_value,
                            "difference_left_minus_right": left_value - right_value,
                        }
                    )
    details = pd.DataFrame(rows)
    summary = details.groupby(
        ["model", "dimension", "left_group", "right_group", "metric"], as_index=False
    ).agg(
        n_model_seeds=("seed", "nunique"),
        difference_mean=("difference_left_minus_right", "mean"),
        difference_std=("difference_left_minus_right", "std"),
        min_difference=("difference_left_minus_right", "min"),
        max_difference=("difference_left_minus_right", "max"),
    )
    return details, summary


def format_score(mean: float, std: float) -> str:
    return f"{mean:.4f} ± {std:.4f}"


def make_report(
    sequences: pd.DataFrame,
    counts: pd.DataFrame,
    metrics_summary: pd.DataFrame,
    per_class_summary: pd.DataFrame,
    differences_summary: pd.DataFrame,
    major_confusions: pd.DataFrame,
    prediction_files: dict[str, list[Path]],
) -> str:
    behavior_counts = counts.loc[counts["dimension"] == "behavior_activity"].set_index("group")
    boundary_counts = counts.loc[counts["dimension"] == "boundary_vs_non_boundary"].set_index("group")
    boundary_type_counts = counts.loc[counts["dimension"] == "boundary_type"].set_index("group")

    lines = [
        "# TRNID boundary / mixed-activity difficulty analysis",
        "",
        "## Fixed definitions",
        "",
        f"- Benchmark setting: {WINDOW_SECONDS:g}-second windows, T={SEQUENCE_LENGTH}, stride={STRIDE}, θ={THRESHOLD}.",
        "- A window is behavior-active iff its interval `[3i, 3(i+1))` overlaps the run's recorded `[ATTACK_START, ATTACK_END)` interval, using the same strict overlap rule as labeling: `window_end > attack_start` and `window_start < attack_end`.",
        "- `all_active`: all 10 windows are behavior-active; `mixed_active_idle`: 1–9 are active; `all_idle`: none is active.",
        "- A sequence crosses the start boundary iff `sequence_start < ATTACK_START < sequence_end`; the end boundary is defined analogously. Equality at a sequence edge is not a crossing.",
        "- `boundary_near` is fixed as a behavior-mixed sequence crossing either true boundary. This definition was set before inspecting performance.",
        "- Packet activity (`pkt_count > 0`) is reported only as a supplemental axis and is not substituted for the behavior interval definition.",
        "",
        "## Source and alignment checks",
        "",
        f"- Test sequences: {len(sequences):,}; test runs: {sequences['run_id'].nunique():,}; current notes parsed: {sequences['note_path'].nunique():,} test-run notes.",
        f"- Saved ordered predictions: {', '.join(f'{model} ({len(files)} seeds)' for model, files in prediction_files.items())}.",
        "- Every prediction file matched the test metadata on row count, `sample_index`, and `true_label`. The packet-activity audit matched `source_row_index`, run, sequence bounds, and assigned label.",
        "",
        "## Group sizes",
        "",
        "| Group | Sequences | Share of test | Runs | True classes |",
        "|---|---:|---:|---:|---:|",
    ]
    for group in ["all_active", "mixed_active_idle", "all_idle"]:
        row = behavior_counts.loc[group]
        lines.append(
            f"| {group} | {int(row.n_sequences):,} | {row.proportion_within_scope:.2%} | {int(row.n_runs):,} | {int(row.n_true_classes)} |"
        )
    lines += [
        "",
        f"True boundary-near mixed sequences: {int(boundary_counts.loc['boundary_near', 'n_sequences']):,} ({boundary_counts.loc['boundary_near', 'proportion_within_scope']:.2%} of test).",
        f"Start-boundary: {int(boundary_type_counts.loc['attack_start', 'n_sequences']):,}; end-boundary: {int(boundary_type_counts.loc['attack_end', 'n_sequences']):,}; both boundaries: {int(boundary_type_counts.loc['start_and_end', 'n_sequences']):,}.",
        "",
        "Because each run has one contiguous active interval and each sequence contains consecutive windows, behavior-mixed sequences should normally cross a recorded start or end boundary. The exported sequence table makes any exceptions explicit.",
        f"A further {int((sequences['crosses_any_boundary'] & sequences['behavior_activity_group'].eq('all_active')).sum()):,} all-active sequences cross a boundary inside an edge window. They remain all-active under the benchmark's overlap-based window rule and are not included in the pre-defined mixed-only boundary-near subset.",
        "",
        "## Model results across saved seeds",
        "",
        "The headline macro-F1 averages only classes that truly occur in the group. A fixed nine-class macro-F1 is also exported because groups have different class support; neither should be interpreted without the per-class support table.",
        "",
        "| Model | Group | N | Accuracy | Macro-F1 (present classes) | Macro-F1 (fixed 9) | Weighted-F1 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    display = metrics_summary.loc[
        (metrics_summary["dimension"] == "behavior_activity")
        & metrics_summary["group"].isin(["all_active", "mixed_active_idle", "all_idle"])
    ]
    for model in ["ResNet1D", "LSTM", "Transformer"]:
        for group in ["all_active", "mixed_active_idle", "all_idle"]:
            rows = display.loc[(display["model"] == model) & (display["group"] == group)]
            if rows.empty:
                continue
            row = rows.iloc[0]
            lines.append(
                f"| {model} | {group} | {int(row.n_sequences):,} | "
                f"{format_score(row.accuracy_mean, row.accuracy_std)} | "
                f"{format_score(row.macro_f1_present_classes_mean, row.macro_f1_present_classes_std)} | "
                f"{format_score(row.macro_f1_fixed_9_classes_mean, row.macro_f1_fixed_9_classes_std)} | "
                f"{format_score(row.weighted_f1_mean, row.weighted_f1_std)} |"
            )

    lines += [
        "",
        "### Attack-start versus attack-end boundary",
        "",
        "| Model | Boundary | N | Accuracy | Macro-F1 (present classes) | Weighted-F1 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    boundary_display = metrics_summary.loc[
        (metrics_summary["dimension"] == "boundary_type")
        & metrics_summary["group"].isin(["attack_start", "attack_end"])
    ]
    for model in ["ResNet1D", "LSTM", "Transformer"]:
        for group in ["attack_start", "attack_end"]:
            rows = boundary_display.loc[
                (boundary_display["model"] == model) & (boundary_display["group"] == group)
            ]
            if rows.empty:
                continue
            row = rows.iloc[0]
            lines.append(
                f"| {model} | {group} | {int(row.n_sequences):,} | "
                f"{format_score(row.accuracy_mean, row.accuracy_std)} | "
                f"{format_score(row.macro_f1_present_classes_mean, row.macro_f1_present_classes_std)} | "
                f"{format_score(row.weighted_f1_mean, row.weighted_f1_std)} |"
            )

    lines += ["", "## Paired difficulty differences", ""]
    diff = differences_summary.loc[
        (differences_summary["dimension"] == "behavior_activity")
        & (differences_summary["metric"] == "macro_f1_present_classes")
    ]
    for row in diff.itertuples(index=False):
        lines.append(
            f"- {row.model}: mixed minus all-active macro-F1 = {row.difference_mean:+.4f} ± {row.difference_std:.4f} across {row.n_model_seeds} paired seeds."
        )
    boundary_diff = differences_summary.loc[
        (differences_summary["dimension"] == "boundary_vs_non_boundary")
        & (differences_summary["metric"] == "macro_f1_present_classes")
    ]
    for row in boundary_diff.itertuples(index=False):
        lines.append(
            f"- {row.model}: boundary-near minus non-boundary macro-F1 = {row.difference_mean:+.4f} ± {row.difference_std:.4f}. This comparison is composition-sensitive because the groups differ in labels and size."
        )

    lines += ["", "## Classes most affected", ""]
    class_view = per_class_summary.loc[
        (per_class_summary["dimension"] == "behavior_activity")
        & per_class_summary["group"].isin(["all_active", "mixed_active_idle"])
    ]
    pivot = class_view.pivot_table(
        index=["model", "class_label"], columns="group", values=["f1_mean", "support"]
    )
    affected_rows: list[tuple[str, str, int, int, float, float, float]] = []
    for (model, label), row in pivot.iterrows():
        try:
            active_support = int(row[("support", "all_active")])
            mixed_support = int(row[("support", "mixed_active_idle")])
            active_f1 = float(row[("f1_mean", "all_active")])
            mixed_f1 = float(row[("f1_mean", "mixed_active_idle")])
        except (KeyError, ValueError):
            continue
        if active_support and mixed_support:
            affected_rows.append(
                (model, label, active_support, mixed_support, active_f1, mixed_f1, mixed_f1 - active_f1)
            )
    affected_rows.sort(key=lambda item: item[-1])
    for model in ["ResNet1D", "LSTM", "Transformer"]:
        rows = [row for row in affected_rows if row[0] == model][:4]
        if rows:
            formatted = "; ".join(
                f"{label} {delta:+.4f} (active n={active_n}, mixed n={mixed_n})"
                for _, label, active_n, mixed_n, _, _, delta in rows
            )
            lines.append(f"- {model}, largest mixed-vs-active F1 changes: {formatted}.")

    lines += ["", "## Main confusion pairs", ""]
    selected_confusions = major_confusions.loc[
        (major_confusions["model"] == "ResNet1D")
        & (major_confusions["dimension"] == "behavior_activity")
        & (major_confusions["group"] == "mixed_active_idle")
        & (major_confusions["error_pair_rank"] <= 8)
    ]
    for row in selected_confusions.itertuples(index=False):
        lines.append(
            f"- {row.true_label} → {row.pred_label}: {row.mean_count_per_seed:.1f} errors per seed on average ({row.share_of_group_errors:.1%} of mixed-group errors)."
        )

    all_idle = sequences.loc[sequences["behavior_activity_group"] == "all_idle"]
    packet_idle = sequences.loc[sequences["packet_activity_group"] == "all_packet_idle"]
    packet_idle_attack = packet_idle.loc[packet_idle["label"] != "normal"]
    lines += [
        "",
        "## Interpretation limits and rebuttal-safe wording",
        "",
        f"- Behavior-all-idle sequences contain {len(all_idle):,} samples from {all_idle['run_id'].nunique()} run(s): {sorted(all_idle['run_id'].unique().tolist())}. Their label distribution is: {all_idle['label'].value_counts().to_dict()}. All three models classify these samples as `exploit`, so this tiny single-run group is descriptive only.",
        f"- Packet-all-idle sequences contain {len(packet_idle):,} samples, including {len(packet_idle_attack):,} attack-labelled samples. Treat that attack-labelled subset descriptively because it is small and packet idleness is not the same as being outside the behavior interval.",
    ]

    mixed_diffs = diff.set_index("model")["difference_mean"].to_dict()
    consistently_lower = bool(mixed_diffs) and all(value < 0 for value in mixed_diffs.values())
    if consistently_lower:
        lines.append(
            "- Safe claim: under the pre-registered timing definition, mixed boundary sequences have lower macro-F1 than all-active sequences for all three saved temporal models, so the boundary-aware construction includes measurably harder samples."
        )
    else:
        lines.append(
            "- Safe claim: the analysis quantifies performance by true timing composition, but mixed boundary sequences are not uniformly harder across all saved models; report the model-specific differences rather than a universal claim."
        )
    lines += [
        "- Safe claim: the dataset exposes performance variation around recorded behavior boundaries and supports boundary-specific diagnostic evaluation.",
        "- Do not claim that boundary awareness alone causes the difficulty. Group sizes and class distributions differ, and this is an observational subgroup analysis rather than a controlled counterfactual experiment.",
        "- Do not claim enterprise realism or cross-dataset generalization from this analysis.",
        "- Do not use raw accuracy alone; cite sample sizes, macro-F1, and affected per-class F1 values together.",
        "",
        "## Exported files",
        "",
        "- `sequence_groups.csv`: timing, behavior composition, packet activity, and boundary flags for every test sequence.",
        "- `group_counts.csv` and `group_class_distribution.csv`: group sizes and label composition.",
        "- `group_metrics_by_seed.csv` and `group_metrics_summary.csv`: model-seed results and mean/std.",
        "- `per_class_f1_by_seed.csv` and `per_class_f1_summary.csv`: per-class results with support.",
        "- `confusion_matrix_long.csv` and `major_confusion_pairs.csv`: confusion details.",
        "- `paired_differences_by_seed.csv` and `paired_differences_summary.csv`: paired seed comparisons.",
        "- `analysis_manifest.json`: inputs, fixed parameters, and validation counts.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    script_path = Path(__file__).resolve()
    dataset_root = script_path.parent
    project_root = dataset_root.parent
    if args.output_dir is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = (
            dataset_root
            / "experiment_results"
            / f"boundary_mixed_activity_analysis_{timestamp}"
        )
    else:
        output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing output directory: {output_dir}")
    output_dir.mkdir(parents=True)

    meta_path = (
        dataset_root
        / "3s"
        / "model_input_3s_t10_p6_s1"
        / "meta_test_ratio0p6_T10_S1.csv"
    )
    audit_path = (
        dataset_root
        / "validation_experiments"
        / "validation1_activity_ratio0p6_T10_S1_20261006_212323"
        / "activity_sequence_audit.csv"
    )
    prediction_root = (
        dataset_root
        / "experiment_results"
        / "rebuttal_20261006"
        / "final_test_epoch50"
    )

    meta = pd.read_csv(meta_path)
    timing = load_note_timing(project_root)
    sequences = classify_sequences(meta, timing)
    sequences = add_packet_activity(sequences, audit_path)
    prediction_files = find_prediction_files(prediction_root)
    specs = group_specs(sequences)

    counts, class_distribution = summarize_counts(sequences, specs)
    metrics, per_class, confusion, major_confusions = analyze_predictions(
        sequences, prediction_files, specs
    )
    metrics_summary = aggregate_metrics(metrics)
    per_class_summary = aggregate_per_class(per_class)
    difference_details, difference_summary = paired_differences(metrics)

    sequences.to_csv(output_dir / "sequence_groups.csv", index=False)
    counts.to_csv(output_dir / "group_counts.csv", index=False)
    class_distribution.to_csv(output_dir / "group_class_distribution.csv", index=False)
    metrics.to_csv(output_dir / "group_metrics_by_seed.csv", index=False)
    metrics_summary.to_csv(output_dir / "group_metrics_summary.csv", index=False)
    per_class.to_csv(output_dir / "per_class_f1_by_seed.csv", index=False)
    per_class_summary.to_csv(output_dir / "per_class_f1_summary.csv", index=False)
    confusion.to_csv(output_dir / "confusion_matrix_long.csv", index=False)
    major_confusions.to_csv(output_dir / "major_confusion_pairs.csv", index=False)
    difference_details.to_csv(output_dir / "paired_differences_by_seed.csv", index=False)
    difference_summary.to_csv(output_dir / "paired_differences_summary.csv", index=False)

    report = make_report(
        sequences,
        counts,
        metrics_summary,
        per_class_summary,
        difference_summary,
        major_confusions,
        prediction_files,
    )
    (output_dir / "boundary_activity_report.md").write_text(report, encoding="utf-8")

    manifest = {
        "created_at": datetime.now().astimezone().isoformat(),
        "script": str(script_path),
        "output_dir": str(output_dir),
        "fixed_parameters": {
            "window_seconds": WINDOW_SECONDS,
            "sequence_length": SEQUENCE_LENGTH,
            "stride": STRIDE,
            "threshold": THRESHOLD,
        },
        "definitions": {
            "window_behavior_active": "window_end > attack_start and window_start < attack_end",
            "all_active": "behavior_active_count == 10",
            "mixed_active_idle": "0 < behavior_active_count < 10",
            "all_idle": "behavior_active_count == 0",
            "cross_start": "sequence_start < ATTACK_START < sequence_end",
            "cross_end": "sequence_start < ATTACK_END < sequence_end",
            "boundary_near": "mixed_active_idle and (cross_start or cross_end)",
            "boundary_type": "start/end/both only within boundary_near; otherwise non_boundary",
        },
        "inputs": {
            "test_metadata": str(meta_path),
            "packet_activity_audit": str(audit_path),
            "prediction_root": str(prediction_root),
            "prediction_files": {
                model: [str(path) for path in paths] for model, paths in prediction_files.items()
            },
        },
        "validation": {
            "test_sequences": len(sequences),
            "test_runs": int(sequences["run_id"].nunique()),
            "current_notes_total": len(timing),
            "test_notes_used": int(sequences["note_path"].nunique()),
            "models": {model: len(paths) for model, paths in prediction_files.items()},
            "mixed_without_true_boundary_crossing": int(
                (
                    sequences["behavior_activity_group"].eq("mixed_active_idle")
                    & ~sequences["crosses_any_boundary"]
                ).sum()
            ),
        },
    }
    (output_dir / "analysis_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Results saved to: {output_dir}")
    print(f"Test sequences: {len(sequences)}")
    print(f"Saved prediction seeds: {manifest['validation']['models']}")


if __name__ == "__main__":
    main()
