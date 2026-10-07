# Audited environment

The project uses the existing Conda environment `ids`. No packages were installed or changed while preparing this release.

Observed package versions on 2026-10-07:

| Component | Version |
|---|---|
| Python | 3.12.13 |
| NumPy | 2.5.2 |
| pandas | 3.0.5 |
| PyTorch | 2.5.1+cu121 |
| CUDA runtime reported by PyTorch | 12.1 |
| scikit-learn | 1.9.0 |
| SciPy | 1.18.1 |
| dpkt | 1.9.8 |
| Matplotlib | 3.11.1 |

These are the restored current-environment versions. They are not asserted to be a bit-for-bit record of the earlier baseline training environment. GPU retraining can show small nondeterministic differences even when seeds and hyperparameters are fixed.

