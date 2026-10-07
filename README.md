# TRNID

TRNID is a run-based, boundary-aware, sequence-level temporal benchmark for multi-class network intrusion detection. Traffic is generated through script-controlled behavior runs, converted into fixed-width windows with 40 traffic features, and assembled into temporal sequences without crossing run boundaries.

This repository contains the preprocessing and dataset-construction code, temporal and non-temporal baselines, fixed split assignments, experiment configurations, and compact result summaries. Large raw data artifacts and model checkpoints are not stored in the repository.

## Repository structure

```text
src/preprocessing/   PCAP aggregation, labeling, splitting, and sequence construction
src/models/          MLP, LSTM, ResNet1D, and Transformer baselines
src/analysis/        Supplementary and rebuttal analyses
scripts/generation/  Controlled traffic-generation scripts
scripts/experiments/ Reproducible experiment runners
configs/             Frozen baseline configurations
splits/              Fixed main and profile-disjoint assignments
results/             Compact aggregate metrics and analysis summaries
docs/                Dataset, experiment, and reproducibility documentation
```

## Default benchmark construction

- Window width: 3 seconds
- Sequence length: 10 windows
- Sequence stride: 1 window
- Dominant-label threshold: 0.6
- Input dimension: 40 features per window
- Split unit: behavior run
- Standardization: parameters fitted on training windows only

The main benchmark is run-disjoint and profile-stratified: different runs do not cross train, validation, and test, while known behavior profiles are represented in each split. Profile-disjoint partitions are provided separately as supplementary generalization experiments.

## Dataset snapshots

`paper_baseline` preserves the exact split and model-input identity used for the submitted baseline experiments. `current_release` represents the audited release snapshot and includes one completed run restored during post-submission dataset maintenance. The two snapshots retain separate split assignments and experiment results. See [VERSIONING.md](docs/VERSIONING.md).

## Models and supplementary experiments

The repository includes:

- single-window and orderless MLP baselines;
- ordered and within-sequence-shuffled LSTM, ResNet1D, and Transformer baselines;
- held-profile generalization and profile-partition stability analyses;
- sequence length, window width, stride, and label-threshold sensitivity;
- boundary and mixed-activity difficulty analysis using recorded behavior timing.

See [EXPERIMENTS.md](docs/EXPERIMENTS.md) for the artifact map.

## Environment

The local project uses the Conda environment `ids`. The currently audited package versions are documented in [ENVIRONMENT.md](docs/ENVIRONMENT.md). No automatic package installation is performed by this repository.

## Reproduction

Fixed split tables, frozen model configurations, profile definitions, and preprocessing/training/evaluation code are included. The reproduction guide documents the expected input layout and the pipeline from feature extraction and boundary-aware labeling through train-only standardization, sequence construction, baseline training, and evaluation.

See [REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md).

## Data availability

This repository provides the code, configurations, fixed split assignments, experiment scripts, and documentation required to reproduce the preprocessing, sequence construction, baseline training, and evaluation procedures described in the paper.

Large raw data artifacts, including PCAP files, model-input arrays, and model checkpoints, are not included in the repository.

## Responsible use

Traffic-generation scripts are intended for controlled research testbeds only. Do not run them against systems or networks without explicit authorization.

