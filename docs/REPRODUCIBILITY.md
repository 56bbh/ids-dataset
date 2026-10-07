# Reproducibility guide

## Environment

Use the existing project environment:

```powershell
conda activate ids
```

No script in this repository installs or upgrades dependencies automatically.

## Expected model-input files

Training scripts expect NumPy arrays with a shared suffix, for example `ratio0p6_T10_S1`:

```text
X_train_<suffix>.npy    y_train_<suffix>.npy
X_val_<suffix>.npy      y_val_<suffix>.npy
X_test_<suffix>.npy     y_test_<suffix>.npy
```

Sequence metadata and feature-column JSON files should be retained alongside the arrays.

## Paper baselines

Use the `paper_baseline` model-input snapshot and the values in `configs/paper_baseline/`. Current script defaults are not a substitute for the frozen configurations.

Example ResNet1D invocation:

```powershell
python src/models/train_resnet1d_baseline.py `
  --data-dir <paper-baseline-model-input> `
  --suffix ratio0p6_T10_S1 `
  --output-dir <new-output-directory> `
  --runs 10 `
  --seed 42 `
  --seed-step 1 `
  --epochs 30 `
  --patience 5 `
  --batch-size 128 `
  --lr 0.001 `
  --weight-decay 0.0001 `
  --base-channels 32 `
  --num-blocks 2 `
  --kernel-size 3 `
  --dropout 0.3 `
  --class-weight none
```

Equivalent LSTM and Transformer values are recorded in their JSON configuration files.

## Rebuttal runner

`scripts/experiments/run_all_rebuttal_experiments.ps1` coordinates the MLP, ordered, and shuffled conditions. Review its input and output paths before running. Always write to a new experiment directory.

## Fixed splits

The exact main and held-profile assignments are included under `splits/`. Use these files for result reproduction rather than creating an unrecorded new random partition.

## Large data

Model-input arrays, PCAPs, labeled windows, and checkpoints are distributed separately and are not tracked by normal Git commits.

