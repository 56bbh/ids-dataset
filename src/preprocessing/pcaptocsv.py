import os
import glob
import socket
import dpkt
import numpy as np
import pandas as pd
from collections import defaultdict
from scipy.stats import entropy

# ================= 配置参数 =================
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

BASE_DIR = os.path.join(CURRENT_DIR, "pcap")
OUTPUT_DIR = os.path.join(CURRENT_DIR, "csv")

TARGET_IP = "192.168.9.131"          # 靶机 IP
WINDOW_SIZE = 5.0                    # 聚合窗口，单位秒
SHORT_FLOW_THRESHOLD = 1.67
EPSILON = 1e-6

# 纯静默 / 过滤后无目标 IP 包时的兜底窗口长度
# 9s 前置静默 + 300s 主体 + 9s 后置静默 = 318s；3s 窗口 => 106 行
PRE_IDLE_SEC = 9
ATTACK_DURATION_SEC = 300
POST_IDLE_SEC = 9
DEFAULT_TOTAL_DURATION_SEC = PRE_IDLE_SEC + ATTACK_DURATION_SEC + POST_IDLE_SEC

# 端口级协议代理特征
HTTP_PORTS = {80, 8080, 8000, 8008, 8888, 443}
SSH_PORTS = {22}
TELNET_PORTS = {23}
DNS_PORTS = {53}

TARGET_IP_BYTES = socket.inet_aton(TARGET_IP)

FEATURE_COLUMNS = [
    'flow_count', 'new_flow_count', 'pkt_count', 'byte_count',
    'avg_pkt_size', 'min_pkt_size', 'max_pkt_size', 'pkt_size_std',
    'in_pkt_count', 'out_pkt_count', 'in_byte_count', 'out_byte_count',
    'in_out_pkt_ratio', 'in_out_byte_ratio',
    'tcp_flow_count', 'udp_flow_count', 'icmp_flow_count',
    'unique_dst_ip_count', 'unique_src_port_count', 'unique_dst_port_count',
    'avg_flow_duration', 'flow_duration_std', 'short_flow_ratio', 'long_flow_ratio',
    'avg_pkt_per_flow', 'avg_byte_per_flow', 'connection_rate', 'failed_conn_ratio',
    'syn_count', 'syn_ack_count', 'ack_count', 'rst_count', 'fin_count', 'psh_count',
    'tcp_flag_entropy',
    'http_pkt_count', 'ssh_pkt_count', 'telnet_pkt_count', 'dns_pkt_count',
    'icmp_pkt_ratio'
]

os.makedirs(OUTPUT_DIR, exist_ok=True)


def make_window_state():
    return {
        'pkt_sizes': [],
        'in_pkt_count': 0,
        'out_pkt_count': 0,
        'in_byte_count': 0,
        'out_byte_count': 0,
        'tcp_pkt_count': 0,
        'udp_pkt_count': 0,
        'icmp_pkt_count': 0,
        'unique_dst_ips': set(),
        'unique_src_ports': set(),
        'unique_dst_ports': set(),
        'syn_count': 0,
        'syn_ack_count': 0,
        'ack_count': 0,
        'rst_count': 0,
        'fin_count': 0,
        'psh_count': 0,
        'http_pkt_count': 0,
        'ssh_pkt_count': 0,
        'telnet_pkt_count': 0,
        'dns_pkt_count': 0,
        # flow_key -> [start_ts, end_ts, pkt_count, has_rst]
        'flows': {}
    }


def build_zero_window_df(total_duration_sec=DEFAULT_TOTAL_DURATION_SEC):
    """生成全 0 窗口。

    用于 normal_idle 这类纯静默 pcap，或目标 IP 过滤后没有任何有效包的 run。
    不额外加入 window_start/window_end，避免改变后续 feature 列数量。
    """
    n_windows = int(np.ceil(total_duration_sec / WINDOW_SIZE))
    rows = []
    for w_idx in range(n_windows):
        row = {'window_idx': w_idx}
        for col in FEATURE_COLUMNS:
            row[col] = 0
        rows.append(row)
    return pd.DataFrame(rows, columns=['window_idx'] + FEATURE_COLUMNS)


def safe_tcp_flag_entropy(win):
    counts = np.array([
        win['syn_count'],
        win['syn_ack_count'],
        win['ack_count'],
        win['rst_count'],
        win['fin_count'],
        win['psh_count'],
    ], dtype=float)

    if counts.sum() <= 0:
        return 0.0
    return float(entropy(counts + EPSILON))


# ================= 核心处理函数 =================
def extract_features_from_pcap(pcap_file, allow_empty_windows=True):
    windows_data = defaultdict(make_window_state)
    global_seen_flows = set()

    first_timestamp = None
    packet_counter = 0
    target_ip_packet_counter = 0

    with open(pcap_file, 'rb') as f:
        try:
            pcap = dpkt.pcap.Reader(f)
        except ValueError:
            f.seek(0)
            pcap = dpkt.pcapng.Reader(f)

        # ------------------------------------------------
        # 第一阶段：解析 pcap 并聚合到窗口
        # ------------------------------------------------
        for ts, buf in pcap:
            packet_counter += 1
            if packet_counter % 1000000 == 0:
                print(f"    [~] 已处理 {packet_counter} 个包...")

            if first_timestamp is None:
                first_timestamp = ts

            try:
                eth = dpkt.ethernet.Ethernet(buf)
            except Exception:
                continue

            if not isinstance(eth.data, dpkt.ip.IP):
                continue

            ip = eth.data
            src_ip_bytes = ip.src
            dst_ip_bytes = ip.dst

            # 只保留 Kali ↔ Metasploitable2 / 靶机相关 IP 包。
            # 注意：过滤必须发生在创建窗口之前，否则非靶机 IP 包会制造“空窗口”。
            if src_ip_bytes != TARGET_IP_BYTES and dst_ip_bytes != TARGET_IP_BYTES:
                continue

            target_ip_packet_counter += 1

            pkt_len = len(buf)
            proto = ip.p
            rel_time = ts - first_timestamp
            w_idx = int(rel_time // WINDOW_SIZE)
            win = windows_data[w_idx]

            sport, dport = 0, 0
            is_tcp = False
            is_udp = False
            packet_has_rst = False

            # ---------------- 协议解析 ----------------
            if proto == 6 and isinstance(ip.data, dpkt.tcp.TCP):
                tcp = ip.data
                sport, dport = tcp.sport, tcp.dport
                is_tcp = True
                win['tcp_pkt_count'] += 1

                flags = tcp.flags
                if flags & dpkt.tcp.TH_SYN and not flags & dpkt.tcp.TH_ACK:
                    win['syn_count'] += 1
                if flags & dpkt.tcp.TH_SYN and flags & dpkt.tcp.TH_ACK:
                    win['syn_ack_count'] += 1
                if flags & dpkt.tcp.TH_ACK:
                    win['ack_count'] += 1
                if flags & dpkt.tcp.TH_RST:
                    win['rst_count'] += 1
                    packet_has_rst = True
                if flags & dpkt.tcp.TH_FIN:
                    win['fin_count'] += 1
                if flags & dpkt.tcp.TH_PUSH:
                    win['psh_count'] += 1

                if dport in HTTP_PORTS or sport in HTTP_PORTS:
                    win['http_pkt_count'] += 1
                if dport in SSH_PORTS or sport in SSH_PORTS:
                    win['ssh_pkt_count'] += 1
                if dport in TELNET_PORTS or sport in TELNET_PORTS:
                    win['telnet_pkt_count'] += 1

            elif proto == 17 and isinstance(ip.data, dpkt.udp.UDP):
                udp = ip.data
                sport, dport = udp.sport, udp.dport
                is_udp = True
                win['udp_pkt_count'] += 1
                if dport in DNS_PORTS or sport in DNS_PORTS:
                    win['dns_pkt_count'] += 1

            elif proto == 1 and isinstance(ip.data, dpkt.icmp.ICMP):
                win['icmp_pkt_count'] += 1

            # ---------------- flow 追踪 ----------------
            ep1 = (src_ip_bytes, sport)
            ep2 = (dst_ip_bytes, dport)
            flow_key = (ep2, ep1, proto) if ep1 > ep2 else (ep1, ep2, proto)

            if flow_key not in win['flows']:
                win['flows'][flow_key] = [ts, ts, 1, packet_has_rst]
            else:
                win['flows'][flow_key][1] = ts
                win['flows'][flow_key][2] += 1
                if packet_has_rst:
                    win['flows'][flow_key][3] = True

            # ---------------- 方向性统计 ----------------
            win['pkt_sizes'].append(pkt_len)

            if dst_ip_bytes == TARGET_IP_BYTES:
                win['in_pkt_count'] += 1
                win['in_byte_count'] += pkt_len
            elif src_ip_bytes == TARGET_IP_BYTES:
                win['out_pkt_count'] += 1
                win['out_byte_count'] += pkt_len

            win['unique_dst_ips'].add(dst_ip_bytes)
            if is_tcp or is_udp:
                win['unique_src_ports'].add(sport)
                win['unique_dst_ports'].add(dport)

    # ------------------------------------------------
    # 第二阶段：计算派生特征
    # ------------------------------------------------
    records = []

    for w_idx in sorted(windows_data.keys()):
        win = windows_data[w_idx]
        sizes = win['pkt_sizes']
        pkt_count = len(sizes)

        if pkt_count == 0:
            continue

        flow_count = len(win['flows'])
        new_flow_count = 0
        flow_durations = []
        short_flows = 0
        long_flows = 0
        tcp_flow_count = 0
        udp_flow_count = 0
        icmp_flow_count = 0

        for f_key, flow_info in win['flows'].items():
            # 暂时保留原来的内存保护逻辑，不改 new_flow_count 方案
            if len(global_seen_flows) > 500000:
                global_seen_flows.clear()

            if f_key not in global_seen_flows:
                new_flow_count += 1
                global_seen_flows.add(f_key)

            start_ts, end_ts, flow_pkt_count, has_rst = flow_info
            duration = end_ts - start_ts
            flow_durations.append(duration)

            if duration < SHORT_FLOW_THRESHOLD:
                short_flows += 1
            else:
                long_flows += 1

            proto = f_key[2]
            if proto == 6:
                tcp_flow_count += 1
            elif proto == 17:
                udp_flow_count += 1
            elif proto == 1:
                icmp_flow_count += 1

        record = {
            'window_idx': w_idx,
            'flow_count': flow_count,
            'new_flow_count': new_flow_count,
            'pkt_count': pkt_count,
            'byte_count': sum(sizes),
            'avg_pkt_size': float(np.mean(sizes)),
            'min_pkt_size': int(np.min(sizes)),
            'max_pkt_size': int(np.max(sizes)),
            'pkt_size_std': float(np.std(sizes)),

            'in_pkt_count': win['in_pkt_count'],
            'out_pkt_count': win['out_pkt_count'],
            'in_byte_count': win['in_byte_count'],
            'out_byte_count': win['out_byte_count'],
            'in_out_pkt_ratio': win['in_pkt_count'] / (win['out_pkt_count'] + EPSILON),
            'in_out_byte_ratio': win['in_byte_count'] / (win['out_byte_count'] + EPSILON),

            'tcp_flow_count': tcp_flow_count,
            'udp_flow_count': udp_flow_count,
            'icmp_flow_count': icmp_flow_count,
            'unique_dst_ip_count': len(win['unique_dst_ips']),
            'unique_src_port_count': len(win['unique_src_ports']),
            'unique_dst_port_count': len(win['unique_dst_ports']),

            'avg_flow_duration': float(np.mean(flow_durations)) if flow_durations else 0,
            'flow_duration_std': float(np.std(flow_durations)) if len(flow_durations) > 1 else 0,
            'short_flow_ratio': short_flows / (flow_count + EPSILON),
            'long_flow_ratio': long_flows / (flow_count + EPSILON),
            'avg_pkt_per_flow': pkt_count / (flow_count + EPSILON),
            'avg_byte_per_flow': sum(sizes) / (flow_count + EPSILON),
            'connection_rate': new_flow_count / WINDOW_SIZE,
            'failed_conn_ratio': win['rst_count'] / (flow_count + EPSILON),

            'syn_count': win['syn_count'],
            'syn_ack_count': win['syn_ack_count'],
            'ack_count': win['ack_count'],
            'rst_count': win['rst_count'],
            'fin_count': win['fin_count'],
            'psh_count': win['psh_count'],
            'tcp_flag_entropy': safe_tcp_flag_entropy(win),

            'http_pkt_count': win['http_pkt_count'],
            'ssh_pkt_count': win['ssh_pkt_count'],
            'telnet_pkt_count': win['telnet_pkt_count'],
            'dns_pkt_count': win['dns_pkt_count'],
            'icmp_pkt_ratio': win['icmp_pkt_count'] / (pkt_count + EPSILON)
        }
        records.append(record)

    if records:
        return pd.DataFrame(records, columns=['window_idx'] + FEATURE_COLUMNS)

    if allow_empty_windows:
        print(
            f"    [!] pcap 内总包数={packet_counter}，但目标 IP({TARGET_IP}) 相关 IP 包={target_ip_packet_counter}，"
            f"生成 {int(np.ceil(DEFAULT_TOTAL_DURATION_SEC / WINDOW_SIZE))} 个全 0 窗口。"
        )
        return build_zero_window_df()

    return pd.DataFrame(columns=['window_idx'] + FEATURE_COLUMNS)


# ================= 批处理逻辑 =================
def process_all_pcaps():
    experiment_dirs = glob.glob(os.path.join(BASE_DIR, "*"))
    for exp_dir in experiment_dirs:
        if not os.path.isdir(exp_dir):
            continue

        exp_name = os.path.basename(exp_dir)
        pcap_files = glob.glob(os.path.join(exp_dir, "*.pcap"))

        for pcap in pcap_files:
            file_name = os.path.basename(pcap).replace('.pcap', '')
            out_path = os.path.join(OUTPUT_DIR, f"{file_name}_features.csv")

            # 断点续传
            if os.path.exists(out_path):
                print(f"  [⏭️] 跳过已处理: {file_name}")
                continue

            print(f"\n  [▶️] 正在解析: {exp_name} -> {file_name}")
            try:
                df = extract_features_from_pcap(pcap, allow_empty_windows=True)
                df.insert(0, 'run_id', file_name)
                df.insert(1, 'attack_category', exp_name)
                df.to_csv(out_path, index=False)
                print(f"  [+] 提取成功: 产出 {len(df)} 个时间窗口样本。")
            except Exception as e:
                print(f"  [❌] 处理失败 {file_name}: {e}")


if __name__ == "__main__":
    process_all_pcaps()
