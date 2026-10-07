"""Utilities for deterministic temporal-order ablations."""

from __future__ import annotations

import numpy as np


SPLIT_SEED_OFFSETS = {"train": 1000, "val": 2000, "test": 3000}


def shuffle_within_sequences(x: np.ndarray, seed: int) -> np.ndarray:
    """Independently permute the time axis of every sequence."""
    if x.ndim != 3:
        raise ValueError(f"Expected X with shape (N, T, F), got {x.shape}.")
    rng = np.random.default_rng(seed)
    permutations = np.argsort(rng.random((x.shape[0], x.shape[1])), axis=1)
    shuffled = np.take_along_axis(x, permutations[:, :, None], axis=1)
    return np.asarray(shuffled, dtype=x.dtype)


def apply_temporal_order(
    x_train: np.ndarray,
    x_val: np.ndarray,
    x_test: np.ndarray | None,
    mode: str,
    run_seed: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, dict[str, int]]:
    """Return ordered arrays or deterministic per-sequence shuffled copies."""
    if mode == "ordered":
        return x_train, x_val, x_test, {}
    if mode != "shuffled":
        raise ValueError(f"Unknown temporal-order mode: {mode}")

    seeds = {
        split: int(run_seed + offset) for split, offset in SPLIT_SEED_OFFSETS.items()
    }
    return (
        shuffle_within_sequences(x_train, seeds["train"]),
        shuffle_within_sequences(x_val, seeds["val"]),
        None if x_test is None else shuffle_within_sequences(x_test, seeds["test"]),
        seeds,
    )
