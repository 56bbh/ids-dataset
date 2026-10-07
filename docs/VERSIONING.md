# Dataset snapshot policy

Two named snapshots are retained because the submitted baseline and the corrected current release do not have identical run membership.

## `paper_baseline`

This snapshot preserves the exact split and temporal model-input identity used by the submitted LSTM, ResNet1D, and Transformer baseline experiments. Use it when reproducing the saved paper baseline metrics.

## `current_release`

This snapshot includes one completed run that was restored after the submitted baseline had been produced. The deterministic run allocation was regenerated after restoration, so a small number of runs in the affected profile changed split membership.

The current release is used by the rebuttal experiments and should be the default for future dataset use.

## Reporting rule

Do not combine a split, model input, or metric from one snapshot with the name of the other. Public documentation may use the descriptive snapshot names rather than embedding run counts in directory names, but every reported result must identify which snapshot it uses.

