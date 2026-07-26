"""export.py -- the core join. Produces the ML-ready labeled Parquet table.

Pulls, for a time window:
  - interface metrics  (per device+interface  -> entity_type=interface)
  - tunnel metrics     (per device+tunnel     -> entity_type=tunnel)
  - flow aggregates    (per device, bucketed) -> flow_bytes/flow_packets
  - events             (not columns; available via /events -- kept thin)
  - fault labels       (LEFT-joined so each row is tagged)

Alignment: one row per (device, entity, entity_type, ts-bucket). ts buckets are
`step`-second aligned UTC. Fault labels are LEFT-joined on device + whether the
bucket ts falls inside any [t_start, t_end] window for that device; matched rows
get is_fault=true + scenario fields + lead_time_s + time_to_impact_s.

Canonical columns are fixed (see schema/) so downstream stays stable.

Run:  python3 export.py --start <epoch> --end <epoch> [--step 30]
      python3 export.py --minutes 60          # last 60 min
"""
import argparse
import os
import time
from datetime import datetime, timezone

import pandas as pd

import sources

DATASETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datasets")
os.makedirs(DATASETS_DIR, exist_ok=True)

# canonical column order (keep stable for downstream)
# The first 21 columns are the original schema and keep their order, so readers
# written against it still work. Columns after them were added for the device
# health / environmental feature set -- see the entity_type note below.
COLUMNS = [
    "ts", "device", "site_type", "vrf", "entity", "entity_type",
    "if_in_octets", "if_out_octets", "if_oper_status",
    "tunnel_latency_ms", "tunnel_jitter_ms", "tunnel_loss_pct", "tunnel_rekeys",
    "flow_bytes", "flow_packets",
    "is_fault", "scenario_id", "fault_type", "severity",
    "lead_time_s", "time_to_impact_s",
    # --- interface-scoped additions (entity_type == "interface") ---
    "if_in_errors", "if_in_discards", "if_out_errors", "if_out_discards",
    "q_backlog_bytes", "q_drops",
    "xcvr_temp_c", "xcvr_rx_power_dbm", "xcvr_tx_bias_ma",
    # --- device-scoped additions (entity_type == "device") ---
    "cpu_pct", "mem_pct",
    "bgp_msg_rx", "bgp_msg_tx", "rib_routes", "ospf_lsa_count",
    "device_temp_c", "device_power_watts", "device_fan_rpm", "device_psu_voltage_v",
]

# entity_type values and what carries their metrics:
#   "interface"  one row per (device, physical interface) -- SNMP IF-MIB counters,
#                qdisc queue stats, and the transceiver (SFF-8472) readings
#   "tunnel"     one row per (device, WireGuard tunnel)   -- SD-WAN controller
#   "device"     one row per device, entity == device name -- whole-box signals
#                (CPU, memory, routing-table churn, chassis sensors)

# interface metric name -> column
_IF_METRICS = {
    "interface_ifHCInOctets": "if_in_octets",
    "interface_ifHCOutOctets": "if_out_octets",
    "interface_ifOperStatus": "if_oper_status",
    # Error/discard counters: the single highest value-per-effort signal in the
    # set. Already on the wire in IF-MIB (we were polling the table and taking
    # only three OIDs) and the top-ranked feature in ClusterRCA's HPC network
    # failure diagnosis (arXiv 2506.20673), whose equivalents are
    # rx_crc_errors_phy / rx_discards_phy / tx_discards_phy.
    "interface_ifInErrors": "if_in_errors",
    "interface_ifInDiscards": "if_in_discards",
    "interface_ifOutErrors": "if_out_errors",
    "interface_ifOutDiscards": "if_out_discards",
    # Queue occupancy: fills BEFORE latency rises and long before loss starts.
    "iface_queue_backlog_bytes": "q_backlog_bytes",
    "iface_queue_drops": "q_drops",
    # Transceiver (SFF-8472 DOM). MODELLED -- see telemetry/envmodel.py.
    "xcvr_temp_c": "xcvr_temp_c",
    "xcvr_rx_power_dbm": "xcvr_rx_power_dbm",
    "xcvr_tx_bias_ma": "xcvr_tx_bias_ma",
}
# tunnel metric name -> column
_TUN_METRICS = {
    "sdwan_tunnel_latency_ms": "tunnel_latency_ms",
    "sdwan_tunnel_jitter_ms": "tunnel_jitter_ms",
    "sdwan_tunnel_loss_pct": "tunnel_loss_pct",
    "sdwan_tunnel_rekeys_total": "tunnel_rekeys",
}
# device-scoped metric name -> column. Emitted by telemetry/env-metrics.py; the
# series label carrying the entity IS "device", so entity == device on these rows.
_DEV_METRICS = {
    "node_cpu_pct": "cpu_pct",
    "node_mem_pct": "mem_pct",
    "bgp_msg_rx_total": "bgp_msg_rx",
    "bgp_msg_tx_total": "bgp_msg_tx",
    "rib_routes": "rib_routes",
    "ospf_lsa_count": "ospf_lsa_count",
    "device_temp_c": "device_temp_c",
    "device_power_watts": "device_power_watts",
    "device_fan_rpm": "device_fan_rpm",
    "device_psu_voltage_v": "device_psu_voltage_v",
}


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def _matrix_to_records(result, value_col, entity_key, entity_type):
    """Turn a VM range-matrix into long records keyed by (device, entity, ts)."""
    recs = []
    for series in result:
        m = series["metric"]
        device = m.get("device")
        entity = m.get(entity_key)
        site_type = m.get("site_type")
        if device is None or entity is None:
            continue
        for ts, val in series["values"]:
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            recs.append({
                "ts": _iso(int(ts)), "device": device, "site_type": site_type,
                "entity": entity, "entity_type": entity_type,
                value_col: fval,
            })
    return recs


def _collect(metric_map, entity_key, entity_type, start, end, step):
    """Query each metric, melt to long, pivot to one row per (device,entity,ts)."""
    frames = []
    for metric, col in metric_map.items():
        recs = _matrix_to_records(
            sources.vm_query_range(metric, start, end, step), col, entity_key, entity_type)
        if recs:
            frames.append(pd.DataFrame(recs))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    keys = ["ts", "device", "site_type", "entity", "entity_type"]
    # aggregate the per-metric value columns onto shared keys
    return df.groupby(keys, dropna=False).first().reset_index()


def _flow_bucketed(start, end, step):
    """Aggregate nfacctd flows per device into step-aligned ts buckets."""
    rows = sources.flow_rows(limit=5000)
    recs = []
    for r in rows:
        ts = r.get("ts")
        dev = r.get("device")
        if not ts or not dev:
            continue
        try:  # nfacctd stamp: "YYYY-MM-DD HH:MM:SS" (UTC)
            epoch = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        if epoch < start or epoch > end:
            continue
        bucket = int(epoch // step) * step
        recs.append({"ts": _iso(bucket), "device": dev,
                     "flow_bytes": r.get("bytes") or 0, "flow_packets": r.get("packets") or 0})
    if not recs:
        return pd.DataFrame(columns=["ts", "device", "flow_bytes", "flow_packets"])
    df = pd.DataFrame(recs)
    return df.groupby(["ts", "device"], dropna=False).sum().reset_index()


def _apply_labels(df):
    """LEFT-join the fault timeline on device + ts-in-[t_start,t_end]."""
    labels = sources.label_rows()
    df["is_fault"] = False
    for c in ["scenario_id", "fault_type", "severity"]:
        df[c] = pd.NA
    df["lead_time_s"] = pd.NA
    df["time_to_impact_s"] = pd.NA
    if df.empty or not labels:
        return df

    df["_epoch"] = df["ts"].map(_parse_iso)
    for lab in labels:
        dev = lab.get("device")
        try:
            t0 = _parse_iso(lab["t_start"])
            t_end = _parse_iso(lab["t_end"])
            t_imp = _parse_iso(lab["t_impact"])
        except (KeyError, ValueError):
            continue
        mask = (df["device"] == dev) & (df["_epoch"] >= t0) & (df["_epoch"] <= t_end)
        if not mask.any():
            continue
        df.loc[mask, "is_fault"] = True
        df.loc[mask, "scenario_id"] = lab.get("scenario_id")
        df.loc[mask, "fault_type"] = lab.get("type")
        df.loc[mask, "severity"] = lab.get("severity")
        df.loc[mask, "lead_time_s"] = lab.get("lead_time")
        # time_to_impact_s: seconds from this bucket until t_impact (>=0 before impact)
        df.loc[mask, "time_to_impact_s"] = (t_imp - df.loc[mask, "_epoch"]).round(1)
    df.drop(columns=["_epoch"], inplace=True)
    return df


def build_dataset(start: int, end: int, step: int = 30) -> str:
    """Build the joined labeled Parquet for [start,end]; return its path."""
    iface = _collect(_IF_METRICS, "interface", "interface", start, end, step)
    tunnel = _collect(_TUN_METRICS, "tunnel", "tunnel", start, end, step)
    # device-scoped rows: the entity label IS the device name
    dev = _collect(_DEV_METRICS, "device", "device", start, end, step)
    parts = [d for d in (iface, tunnel, dev) if len(d)]
    base = pd.concat(parts, ignore_index=True) if parts \
        else pd.DataFrame(columns=["ts", "device", "site_type", "entity", "entity_type"])

    # flows attach per (device, ts-bucket); merge onto interface rows of that device/ts
    flows = _flow_bucketed(start, end, step)
    if not base.empty and not flows.empty:
        base = base.merge(flows, on=["ts", "device"], how="left")

    # vrf is not on the live series; left null (nullable per spec). entity carries
    # the interface/tunnel id which is what models key on.
    if "vrf" not in base.columns:
        base["vrf"] = pd.NA

    base = _apply_labels(base)

    # ensure all canonical columns exist, in order
    for c in COLUMNS:
        if c not in base.columns:
            base[c] = pd.NA
    base = base[COLUMNS].sort_values(["ts", "device", "entity"]).reset_index(drop=True)

    fname = f"dataset_{start}_{end}_{step}s.parquet"
    path = os.path.join(DATASETS_DIR, fname)
    base.to_parquet(path, index=False)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int)
    ap.add_argument("--end", type=int)
    ap.add_argument("--step", type=int, default=30)
    ap.add_argument("--minutes", type=int, help="shortcut: last N minutes")
    args = ap.parse_args()

    if args.minutes:
        end = int(time.time())
        start = end - args.minutes * 60
    else:
        end = args.end or int(time.time())
        start = args.start or end - 3600

    path = build_dataset(start, end, args.step)
    df = pd.read_parquet(path)
    print(f"wrote {path}")
    print(f"rows={len(df)} cols={len(df.columns)}")
    print(f"fault_rows={int(df['is_fault'].sum())}")
    print(df.columns.tolist())


if __name__ == "__main__":
    main()
