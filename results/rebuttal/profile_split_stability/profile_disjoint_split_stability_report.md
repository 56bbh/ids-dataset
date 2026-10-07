# Profile-disjoint split-stability summary

Partitions included: 6

## Partition-level test macro-F1

| Partition | Split seed | Source | Profiles T/V/T | Runs T/V/T | Test macro-F1 |
|---|---:|---|---:|---:|---:|
| partition_00_seed42_existing | 42 | existing | 56/11/17 | 342/64/104 | 0.9058 ± 0.0150 |
| partition_01_seed101 | 101 | new | 56/11/17 | 342/68/100 | 0.8811 ± 0.0447 |
| partition_02_seed202 | 202 | new | 56/11/17 | 334/68/108 | 0.8184 ± 0.0587 |
| partition_03_seed303 | 303 | new | 56/11/17 | 337/68/105 | 0.9367 ± 0.0372 |
| partition_04_seed404 | 404 | new | 56/11/17 | 342/68/100 | 0.8987 ± 0.0281 |
| partition_05_seed505 | 505 | new | 56/11/17 | 345/68/97 | 0.7422 ± 0.0837 |

## Variance decomposition

- Equal-weight grand mean across partition means: 0.8638
- Pooled within-partition model-seed SD: 0.0497
- Between-partition split-selection SD: 0.0713

The model-seed SD is the square root of the mean within-partition sample variance. The split-selection SD is the sample SD of the partition means. They quantify different sources of uncertainty and should not be merged into one ± value.

## Per-class split stability

| Class | Mean of split means | Model-seed SD | Split-selection SD | Min split mean | Max split mean |
|---|---:|---:|---:|---:|---:|
| backdoor_shell | 0.8773 | 0.1180 | 0.0724 | 0.7843 | 0.9874 |
| brute_force | 0.9841 | 0.0309 | 0.0131 | 0.9686 | 0.9999 |
| data_exfiltration | 0.8200 | 0.2244 | 0.2462 | 0.3965 | 1.0000 |
| exploit | 0.5647 | 0.1850 | 0.1450 | 0.3660 | 0.7565 |
| http_attack | 0.9568 | 0.0669 | 0.0213 | 0.9305 | 0.9944 |
| normal | 0.8119 | 0.0926 | 0.0535 | 0.7116 | 0.8579 |
| payload_delivery | 0.8530 | 0.0962 | 0.3360 | 0.1682 | 1.0000 |
| scanning | 0.9655 | 0.0484 | 0.0199 | 0.9282 | 0.9819 |
| ssh_telnet_abuse | 0.9412 | 0.0477 | 0.0309 | 0.9155 | 0.9989 |

## Reproducibility

Every partition has a saved `profile_split_check.csv`, `run_split_info.csv`, training summary, per-seed class report, and model checkpoint. Assignment paths are indexed in `partition_assignments.csv`.
