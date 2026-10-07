# Experiment artifact map

## Main paper baselines

- LSTM
- ResNet1D
- Transformer encoder

Frozen configurations are in `configs/paper_baseline/`. Compact aggregate metrics are in `results/paper_baselines/`. These experiments use the `paper_baseline` snapshot.

## Rebuttal experiments

### Non-temporal baselines

An MLP is evaluated on the center window, the sequence mean, and sequence mean plus standard deviation.

### Within-sequence permutation

LSTM, ResNet1D, and Transformer are evaluated on ordered sequences and deterministic within-sequence permutations while preserving sequence membership.

### Profile-disjoint generalization

Whole behavior profiles are assigned to one split only. The original held-profile partition is evaluated with all three temporal models. Additional partitions evaluate the sensitivity of ResNet1D results to profile selection.

### Construction sensitivity

ResNet1D is used to study sequence length, label threshold, sequence stride, and window-width settings.

### Boundary and mixed-activity analysis

Saved predictions and recorded attack timing are used to compare all-active sequences with mixed active/idle sequences and attack-start with attack-end boundaries. This analysis does not retrain models.

Compact result tables are stored under `results/rebuttal/`. Large checkpoints and repeated per-epoch logs are intentionally excluded.

