# TRNID boundary / mixed-activity difficulty analysis

## Fixed definitions

- Benchmark setting: 3-second windows, T=10, stride=1, θ=0.6.
- A window is behavior-active iff its interval `[3i, 3(i+1))` overlaps the run's recorded `[ATTACK_START, ATTACK_END)` interval, using the same strict overlap rule as labeling: `window_end > attack_start` and `window_start < attack_end`.
- `all_active`: all 10 windows are behavior-active; `mixed_active_idle`: 1–9 are active; `all_idle`: none is active.
- A sequence crosses the start boundary iff `sequence_start < ATTACK_START < sequence_end`; the end boundary is defined analogously. Equality at a sequence edge is not a crossing.
- `boundary_near` is fixed as a behavior-mixed sequence crossing either true boundary. This definition was set before inspecting performance.
- Packet activity (`pkt_count > 0`) is reported only as a supplemental axis and is not substituted for the behavior interval definition.

## Source and alignment checks

- Test sequences: 8,671; test runs: 88; current notes parsed: 88 test-run notes.
- Saved ordered predictions: LSTM (10 seeds), ResNet1D (10 seeds), Transformer (10 seeds).
- Every prediction file matched the test metadata on row count, `sample_index`, and `true_label`. The packet-activity audit matched `source_row_index`, run, sequence bounds, and assigned label.

## Group sizes

| Group | Sequences | Share of test | Runs | True classes |
|---|---:|---:|---:|---:|
| all_active | 8,098 | 93.39% | 88 | 9 |
| mixed_active_idle | 530 | 6.11% | 88 | 9 |
| all_idle | 43 | 0.50% | 1 | 1 |

True boundary-near mixed sequences: 530 (6.11% of test).
Start-boundary: 262; end-boundary: 268; both boundaries: 0.

Because each run has one contiguous active interval and each sequence contains consecutive windows, behavior-mixed sequences should normally cross a recorded start or end boundary. The exported sequence table makes any exceptions explicit.
A further 67 all-active sequences cross a boundary inside an edge window. They remain all-active under the benchmark's overlap-based window rule and are not included in the pre-defined mixed-only boundary-near subset.

## Model results across saved seeds

The headline macro-F1 averages only classes that truly occur in the group. A fixed nine-class macro-F1 is also exported because groups have different class support; neither should be interpreted without the per-class support table.

| Model | Group | N | Accuracy | Macro-F1 (present classes) | Macro-F1 (fixed 9) | Weighted-F1 |
|---|---|---:|---:|---:|---:|---:|
| ResNet1D | all_active | 8,098 | 0.9889 ± 0.0010 | 0.9902 ± 0.0010 | 0.9902 ± 0.0010 | 0.9889 ± 0.0010 |
| ResNet1D | mixed_active_idle | 530 | 0.9304 ± 0.0066 | 0.9366 ± 0.0068 | 0.9366 ± 0.0068 | 0.9313 ± 0.0062 |
| ResNet1D | all_idle | 43 | 0.0000 ± 0.0000 | 0.0000 ± 0.0000 | 0.0000 ± 0.0000 | 0.0000 ± 0.0000 |
| LSTM | all_active | 8,098 | 0.9851 ± 0.0017 | 0.9867 ± 0.0015 | 0.9867 ± 0.0015 | 0.9851 ± 0.0017 |
| LSTM | mixed_active_idle | 530 | 0.9274 ± 0.0065 | 0.9349 ± 0.0059 | 0.9349 ± 0.0059 | 0.9284 ± 0.0064 |
| LSTM | all_idle | 43 | 0.0000 ± 0.0000 | 0.0000 ± 0.0000 | 0.0000 ± 0.0000 | 0.0000 ± 0.0000 |
| Transformer | all_active | 8,098 | 0.9908 ± 0.0013 | 0.9918 ± 0.0012 | 0.9918 ± 0.0012 | 0.9908 ± 0.0013 |
| Transformer | mixed_active_idle | 530 | 0.9466 ± 0.0055 | 0.9516 ± 0.0050 | 0.9516 ± 0.0050 | 0.9474 ± 0.0055 |
| Transformer | all_idle | 43 | 0.0000 ± 0.0000 | 0.0000 ± 0.0000 | 0.0000 ± 0.0000 | 0.0000 ± 0.0000 |

### Attack-start versus attack-end boundary

| Model | Boundary | N | Accuracy | Macro-F1 (present classes) | Weighted-F1 |
|---|---|---:|---:|---:|---:|
| ResNet1D | attack_start | 262 | 0.9668 ± 0.0040 | 0.9710 ± 0.0036 | 0.9667 ± 0.0039 |
| ResNet1D | attack_end | 268 | 0.8948 ± 0.0104 | 0.9029 ± 0.0118 | 0.8967 ± 0.0101 |
| LSTM | attack_start | 262 | 0.9622 ± 0.0077 | 0.9670 ± 0.0069 | 0.9623 ± 0.0078 |
| LSTM | attack_end | 268 | 0.8933 ± 0.0081 | 0.9034 ± 0.0076 | 0.8950 ± 0.0080 |
| Transformer | attack_start | 262 | 0.9779 ± 0.0053 | 0.9807 ± 0.0048 | 0.9777 ± 0.0055 |
| Transformer | attack_end | 268 | 0.9160 ± 0.0081 | 0.9233 ± 0.0077 | 0.9180 ± 0.0081 |

## Paired difficulty differences

- LSTM: mixed minus all-active macro-F1 = -0.0518 ± 0.0050 across 10 paired seeds.
- ResNet1D: mixed minus all-active macro-F1 = -0.0536 ± 0.0066 across 10 paired seeds.
- Transformer: mixed minus all-active macro-F1 = -0.0402 ± 0.0053 across 10 paired seeds.
- LSTM: boundary-near minus non-boundary macro-F1 = -0.0476 ± 0.0050. This comparison is composition-sensitive because the groups differ in labels and size.
- ResNet1D: boundary-near minus non-boundary macro-F1 = -0.0493 ± 0.0066. This comparison is composition-sensitive because the groups differ in labels and size.
- Transformer: boundary-near minus non-boundary macro-F1 = -0.0359 ± 0.0053. This comparison is composition-sensitive because the groups differ in labels and size.

## Classes most affected

- ResNet1D, largest mixed-vs-active F1 changes: exploit -0.1882 (active n=925, mixed n=62); normal -0.1499 (active n=1207, mixed n=83); backdoor_shell -0.0713 (active n=473, mixed n=30); http_attack -0.0302 (active n=836, mixed n=54).
- LSTM, largest mixed-vs-active F1 changes: exploit -0.1935 (active n=925, mixed n=62); normal -0.1476 (active n=1207, mixed n=83); backdoor_shell -0.0516 (active n=473, mixed n=30); http_attack -0.0453 (active n=836, mixed n=54).
- Transformer, largest mixed-vs-active F1 changes: exploit -0.1355 (active n=925, mixed n=62); normal -0.1198 (active n=1207, mixed n=83); backdoor_shell -0.0514 (active n=473, mixed n=30); http_attack -0.0387 (active n=836, mixed n=54).

## Main confusion pairs

- exploit → normal: 18.7 errors per seed on average (50.7% of mixed-group errors).
- normal → exploit: 6.1 errors per seed on average (16.5% of mixed-group errors).
- http_attack → normal: 4.0 errors per seed on average (10.8% of mixed-group errors).
- backdoor_shell → normal: 3.7 errors per seed on average (10.0% of mixed-group errors).
- brute_force → normal: 1.1 errors per seed on average (3.0% of mixed-group errors).
- brute_force → ssh_telnet_abuse: 1.0 errors per seed on average (2.7% of mixed-group errors).
- data_exfiltration → normal: 1.0 errors per seed on average (2.7% of mixed-group errors).
- scanning → normal: 0.5 errors per seed on average (1.4% of mixed-group errors).

## Interpretation limits and rebuttal-safe wording

- Behavior-all-idle sequences contain 43 samples from 1 run(s): ['exploit_distcc_exec_05']. Their label distribution is: {'normal': 43}. All three models classify these samples as `exploit`, so this tiny single-run group is descriptive only.
- Packet-all-idle sequences contain 238 samples, including 19 attack-labelled samples. Treat that attack-labelled subset descriptively because it is small and packet idleness is not the same as being outside the behavior interval.
- Safe claim: under the pre-registered timing definition, mixed boundary sequences have lower macro-F1 than all-active sequences for all three saved temporal models, so the boundary-aware construction includes measurably harder samples.
- Safe claim: the dataset exposes performance variation around recorded behavior boundaries and supports boundary-specific diagnostic evaluation.
- Do not claim that boundary awareness alone causes the difficulty. Group sizes and class distributions differ, and this is an observational subgroup analysis rather than a controlled counterfactual experiment.
- Do not claim enterprise realism or cross-dataset generalization from this analysis.
- Do not use raw accuracy alone; cite sample sizes, macro-F1, and affected per-class F1 values together.

## Exported files

- `sequence_groups.csv`: timing, behavior composition, packet activity, and boundary flags for every test sequence.
- `group_counts.csv` and `group_class_distribution.csv`: group sizes and label composition.
- `group_metrics_by_seed.csv` and `group_metrics_summary.csv`: model-seed results and mean/std.
- `per_class_f1_by_seed.csv` and `per_class_f1_summary.csv`: per-class results with support.
- `confusion_matrix_long.csv` and `major_confusion_pairs.csv`: confusion details.
- `paired_differences_by_seed.csv` and `paired_differences_summary.csv`: paired seed comparisons.
- `analysis_manifest.json`: inputs, fixed parameters, and validation counts.
