# Dataset construction

## Run metadata

Each controlled behavior run produces a PCAP and a note file containing at least:

- run identifier;
- attack/normal category and subtype;
- behavior profile identifier;
- random seed;
- PCAP start and stop times;
- behavior start and end times.

A profile represents a script parameter configuration. Multiple independently executed runs can share one profile.

## PCAP aggregation

`src/preprocessing/pcaptocsv.py` uses the first PCAP packet as the run time origin and retains IPv4 packets involving the configured victim address. Packets are aggregated into fixed-width, non-overlapping windows. The output contains 40 traffic features; their frozen order and definitions are documented in [FEATURES.md](FEATURES.md).

## Boundary-aware labeling

`src/preprocessing/label.py` converts the recorded behavior boundaries to the PCAP-relative time axis. A window receives the behavior label when its interval overlaps the recorded behavior interval. Windows outside that interval are normal. Missing windows on the recorded PCAP timeline are inserted with zero-valued features before temporal sequence construction.

## Main split

Runs are grouped by category, subtype, and profile. Different runs within each profile group are assigned to train, validation, and test with a fixed seed. This prevents run overlap while retaining known profiles in every split. The exact assignments are included under `splits/`.

## Standardization

Feature means and sample standard deviations are fitted using training windows only. The same parameters are applied to validation and test. Metadata and labels are excluded from the model feature columns.

## Temporal sequences

Windows are sorted by window index and sequences are built independently inside each run. Under the default setting, a sample contains ten consecutive windows with stride one. The dominant window label is used when its ratio reaches the configured threshold; otherwise the candidate sequence is discarded and recorded.

