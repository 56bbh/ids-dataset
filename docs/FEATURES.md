# TRNID 40-feature window representation

The authoritative implementation is `src/preprocessing/pcaptocsv.py`. The feature order is preserved in the JSON manifests under `configs/data/`.

## Traffic inclusion and window clock

- The time origin is the first packet timestamp in the PCAP.
- Only IPv4 packets whose source or destination equals the configured victim address contribute to features.
- Windows are non-overlapping fixed-width bins during PCAP-to-CSV conversion.
- Flow keys are direction-normalized endpoint/port pairs plus protocol.
- `new_flow_count` uses a run-global set of previously observed flows.
- Silent windows added during labeling contain zero for every feature.

## Feature groups

| Group | Features | Definition |
|---|---|---|
| Volume | `flow_count`, `new_flow_count`, `pkt_count`, `byte_count` | Distinct flows, first-seen flows, packets, and bytes in the window |
| Packet size | `avg_pkt_size`, `min_pkt_size`, `max_pkt_size`, `pkt_size_std` | Statistics over packet lengths |
| Direction | `in_pkt_count`, `out_pkt_count`, `in_byte_count`, `out_byte_count`, `in_out_pkt_ratio`, `in_out_byte_ratio` | Victim-bound versus victim-originated traffic |
| Protocol/endpoint diversity | `tcp_flow_count`, `udp_flow_count`, `icmp_flow_count`, `unique_dst_ip_count`, `unique_src_port_count`, `unique_dst_port_count` | Protocol flow counts and unique peer/port counts |
| Flow duration | `avg_flow_duration`, `flow_duration_std`, `short_flow_ratio`, `long_flow_ratio` | Per-window flow-duration statistics; the short-flow threshold is 1.67 seconds |
| Per-flow intensity | `avg_pkt_per_flow`, `avg_byte_per_flow`, `connection_rate`, `failed_conn_ratio` | Packets/bytes per flow, new flows per second, and RST count divided by flow count |
| TCP flags | `syn_count`, `syn_ack_count`, `ack_count`, `rst_count`, `fin_count`, `psh_count`, `tcp_flag_entropy` | Flag counts and Shannon entropy over flag-pattern frequencies |
| Application-port proxies | `http_pkt_count`, `ssh_pkt_count`, `telnet_pkt_count`, `dns_pkt_count` | Packets involving configured HTTP, SSH, Telnet, or DNS ports |
| ICMP share | `icmp_pkt_ratio` | ICMP packet count divided by all packets |

The implementation uses `1e-6` for safe division. Window-level packet-size and flow-duration standard deviations are population statistics; dataset standardization later uses the pandas sample standard deviation fitted on training windows.

