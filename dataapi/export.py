"""export.py -- the core join. Produces the ML-ready labeled Parquet table.

Pulls, for a time window:
  - interface metrics  (per device+interface  -> entity_type=interface)
  - tunnel metrics     (per device+tunnel     -> entity_type=tunnel)
  - flow aggregates    (per device, bucketed) -> flow_bytes/flow_packets
  - events             (not columns; available via /events -- kept thin)
  - fault labels       (LEFT-joined so each row is tagged)

Alignment: one row per (device, entity, entity_type, ts-bucket). ts buckets are
`step`-second aligned UTC. Fault labels are LEFT-joined on device + whether the
bucket interval [ts, ts+step) OVERLAPS any [t_start, t_end] window for that
device; matched rows get is_fault=true + scenario fields + lead_time_s +
time_to_impact_s. Interface-scoped labels only mark their own interface row.

Canonical columns are fixed (see schema/) so downstream stays stable.

Run:  python3 export.py --start <epoch> --end <epoch> [--step 30]
      python3 export.py --minutes 60          # last 60 min
"""
import argparse
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
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
    # DEFECT 5b/6a: these four keep their original names and positions and now
    # hold the PRIMARY (highest-severity) episode; the full concurrent set is in
    # the list columns at the end. `severity` is an ordinal float, not a string
    # -- the string is kept in `severity_label`.
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
    # --- DEFECT 5b: concurrent-fault supervision -------------------------------
    # Overlapping episodes on one row are all emitted instead of collapsing to
    # the highest-severity one. Every list is index-aligned across the five, and
    # element 0 is the primary (the scalars above).
    "fault_types", "severities", "scenario_ids", "impact_methods", "n_concurrent",
    # DEFECT 6a: string severity kept for humans; explicit primary aliases so a
    # reader does not have to know that the legacy scalar names ARE the primary.
    "severity_label",
    "fault_type_primary", "severity_primary", "scenario_id_primary",
    # DEFECT 2b: which VRF's SLA governed t_impact on a multi-VRF tunnel ramp,
    # index-aligned with fault_types (strictest VRF present; null off ramp_derived).
    "sla_binding_vrf",
    # --- G1: which topology this row came from (leave-one-topology-out needs it) ---
    "topology_id",
    # --- G6: Stream F (fault-dense) vs Stream N (fault-free + hard-negatives) ---
    "stream",
    # --- G4: near-miss rows -- look like a fault, are not one. is_fault stays
    # False; t_impact/severity null; the sampler weights these so the model does
    # not learn "busy => fault". ---
    "is_hard_negative",
    # --- G7: root/affected dual-head supervision (what to fix, not just what hurts) ---
    "is_root", "cascade_parent_id", "cascade_depth", "cascade_motif_id",
    "affected_entity_count",
    # --- G8: per-fault RNG draw -- the only way to reproduce one scenario in an
    # air-gap (nothing can be re-pulled). ---
    "injection_seed",
]

# DEFECT 6a: ordinal severity. A magnitude the loss function can use, with the
# convention that severity is NULL for scenarios whose injector ignores it
# (faults/orchestrator.py:_label_row) preserved -- null stays null.
SEVERITY_ORDINAL = {"low": 0.33, "medium": 0.66, "high": 1.0}

# DEFECT 1 (severity regression): the scenarios whose injector (ProcessKill /
# MultiLinkFault link-set) has no severity concept. Their `severity` MUST be null
# in every row -- orchestrator.py sets it via spec["severity_inert"], but the
# synthetic path (generate.py) draws a severity for every episode and so silently
# populated it. Canonical set = the scen_* builders in faults/orchestrator.py that
# return "severity_inert": True. check.py cross-checks this against orchestrator.
SEVERITY_INERT_FAULTS = frozenset({
    "node_failure", "mpls_underlay_failure", "p_node_failure", "pop_isolation",
    "core_partition", "srlg_cut", "rr_failure",
})

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
                "vrf": m.get("vrf"), "entity": entity, "entity_type": entity_type,
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
    keys = ["ts", "device", "site_type", "vrf", "entity", "entity_type"]
    # aggregate the per-metric value columns onto shared keys
    return df.groupby(keys, dropna=False).first().reset_index()


def _flow_bucketed(start, end, step):
    """Aggregate nfacctd flows per device into step-aligned ts buckets."""
    rows = sources.flow_rows(limit=5000, since=start, until=end)
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
        # keep missing counters as NaN -- "not reported" is not "measured zero"
        recs.append({"ts": _iso(bucket), "device": dev,
                     "flow_bytes": r.get("bytes"), "flow_packets": r.get("packets")})
    if not recs:
        print(f"WARN: no flow records in [{_iso(start)}, {_iso(end)}] -- "
              "flow_bytes/flow_packets will be null", file=sys.stderr)
        return pd.DataFrame(columns=["ts", "device", "flow_bytes", "flow_packets"])
    df = pd.DataFrame(recs)
    return df.groupby(["ts", "device"], dropna=False).sum(min_count=1).reset_index()


_SEV_RANK = {"low": 1, "medium": 2, "high": 3}

# DEFECT 3: the VRF a VRF-scoped interface belongs to. The SNMP pillar carries no
# `vrf` label, so the identity has to come from the interface name -- CEs run
# `vrf_CORP`-style kernel VRF devices, PEs name theirs after the VRF itself. It
# used to leak into `entity` while the dedicated column stayed 100% null.
_VRF_NAMES = ("CORP", "VOICE", "GUEST")


def vrf_of_entity(entity):
    """VRF for an interface-like entity name, or None where VRF has no meaning
    (physical eth*, lo, wg0, and every P-router interface)."""
    e = str(entity)
    if e.startswith("vrf_") and e[4:] in _VRF_NAMES:
        return e[4:]
    return e if e in _VRF_NAMES else None


def tunnel_vrf_set(vrfs):
    """VRF label for a tunnel row.

    A WireGuard tunnel is shared: every VRF of the site rides it, and under
    failover a non-preferred hub carries them too (controller.VRF_PREFERRED_HUB
    is a preference, not a binding). So the honest value is the SET --
    `["CORP","VOICE"]` on a branch, `["CORP","GUEST","VOICE"]` on a hub/dc.
    Naming one VRF per tunnel would be a fabrication, and splitting tunnel rows
    per VRF would change the row-count arithmetic (899 keys/bucket) consumers
    depend on.

    DEFECT 2a: returned as a list<string>, matching fault_types/severities --
    a `"+"`-joined scalar was one opaque categorical a consumer could not
    decompose back into member VRFs.
    """
    present = sorted(v for v in _VRF_NAMES if v in set(vrfs or ()))
    return present or None


def _as_vrf_list(v):
    """DEFECT 2a: normalise any `vrf` value to list<string> | None so the column
    is one Arrow type across tunnels (multi-VRF), interfaces (single), and device
    rows (null). Also migrates legacy `"CORP+VOICE"` delimited strings."""
    if isinstance(v, (list, tuple, np.ndarray)):
        return list(v) or None
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    return v.split("+")  # scalar VRF name, or a legacy "+"-joined string


def _apply_labels(df, step):
    """LEFT-join the fault timeline on device + bucket-interval overlap.

    A row covers [ts, ts+step); it is faulty if that interval overlaps the
    label's [t_start, t_end]. Instant containment (ts in [t_start, t_end])
    would drop every window narrower than `step` -- most of the label file.

    DEFECT 5b: every overlapping label is emitted, not just the highest-severity
    one. Collapsing to a single winner was deterministic but threw away the
    concurrency the consumer's multi-label head is built to learn.
    """
    labels = sources.label_rows()
    n = len(df)
    acc = [[] for _ in range(n)]  # per-row list of episode dicts
    if not df.empty and labels:
        epoch = df["ts"].map(_parse_iso).to_numpy()
        for lab in labels:
            dev = lab.get("device")
            try:
                t0 = _parse_iso(lab["t_start"])
                t_end = _parse_iso(lab["t_end"])
                t_imp = _parse_iso(lab["t_impact"])
            except (KeyError, ValueError):
                continue
            mask = (df["device"] == dev) & (epoch + step > t0) & (epoch <= t_end)
            # entity-scoped faults hit their own interface, not every interface on
            # the box; tunnel/device rows stay in scope (genuinely affected).
            # target is either a dict {device, interface|neighbor|vrf} or a bare
            # device-name string (13 of the labels); only the former narrows scope.
            target = lab.get("target")
            tgt = target.get("interface") if isinstance(target, dict) else None
            if tgt:
                mask &= (df["entity"] == tgt) | (df["entity_type"] != "interface")
            idx = mask.to_numpy().nonzero()[0]
            if not len(idx):
                continue
            sev = lab.get("severity")
            for i in idx:
                acc[i].append({
                    "scenario_id": lab.get("scenario_id"),
                    "fault_type": lab.get("type"),
                    "severity": SEVERITY_ORDINAL.get(sev),
                    "severity_label": sev,
                    "rank": _SEV_RANK.get(sev, 0),
                    "lead_time_s": lab.get("lead_time"),
                    "time_to_impact_s": round(t_imp - float(epoch[i]), 1),
                    "impact_method": lab.get("impact_method"),
                    "sla_binding_vrf": lab.get("sla_binding_vrf"),  # DEFECT 2b
                })
    return attach_labels(df, acc)


def attach_labels(df, acc):
    """Write the label columns from a per-row list of episode dicts.

    Shared with synthetic/generate.py so the two paths cannot produce different
    label schemas. Primary = highest severity, ties broken by the first episode
    seen, and it is placed at index 0 of every list so element 0 is always the
    primary.
    """
    cols = {c: [] for c in ("is_fault", "scenario_id", "fault_type", "severity",
                            "severity_label", "lead_time_s", "time_to_impact_s",
                            "fault_types", "severities", "scenario_ids",
                            "impact_methods", "sla_binding_vrf", "n_concurrent",
                            # G7/G8 primary-episode scalars (None on the real path,
                            # which never sets them -- .get() below keeps it robust)
                            "is_root", "cascade_parent_id", "cascade_depth",
                            "cascade_motif_id", "affected_entity_count",
                            "injection_seed")}
    for eps in acc:
        if not eps:
            cols["is_fault"].append(False)
            for c in ("scenario_id", "fault_type", "severity", "severity_label",
                      "lead_time_s", "time_to_impact_s", "fault_types", "severities",
                      "scenario_ids", "impact_methods", "sla_binding_vrf",
                      "cascade_parent_id", "cascade_depth", "cascade_motif_id",
                      "affected_entity_count", "injection_seed"):
                cols[c].append(None)
            cols["n_concurrent"].append(0)
            cols["is_root"].append(False)
            continue
        eps = sorted(eps, key=lambda e: -e["rank"])
        p = eps[0]
        cols["is_fault"].append(True)
        cols["scenario_id"].append(p["scenario_id"])
        cols["fault_type"].append(p["fault_type"])
        cols["severity"].append(p["severity"])
        cols["severity_label"].append(p["severity_label"])
        cols["lead_time_s"].append(p["lead_time_s"])
        cols["time_to_impact_s"].append([e["time_to_impact_s"] for e in eps])
        cols["fault_types"].append([e["fault_type"] for e in eps])
        cols["severities"].append([e["severity"] for e in eps])
        cols["scenario_ids"].append([e["scenario_id"] for e in eps])
        cols["impact_methods"].append([e["impact_method"] for e in eps])
        # DEFECT 2b: index-aligned with fault_types; None for non-ramp episodes.
        cols["sla_binding_vrf"].append([e.get("sla_binding_vrf") for e in eps])
        cols["n_concurrent"].append(len(eps))
        # G7/G8: primary episode carries the cascade/root/seed scalars. A plain
        # (non-cascade) fault is its own root at depth 0. .get() defaults keep the
        # real export path (which sets none of these) valid.
        cols["is_root"].append(bool(p.get("is_root", True)))
        cols["cascade_parent_id"].append(p.get("cascade_parent_id"))
        cols["cascade_depth"].append(p.get("cascade_depth"))
        cols["cascade_motif_id"].append(p.get("cascade_motif_id"))
        cols["affected_entity_count"].append(p.get("affected_entity_count"))
        cols["injection_seed"].append(p.get("injection_seed"))
    for c, v in cols.items():
        df[c] = v
    df["fault_type_primary"] = df["fault_type"]
    df["severity_primary"] = df["severity"]
    df["scenario_id_primary"] = df["scenario_id"]
    return df


# DEFECT 6b: ts is a real timestamp, not a string. 2.6M ISO strings parsed on
# every training epoch is pure overhead, and a timestamp type lets a loader check
# monotonicity without a parse.
_LIST_COLS = ("fault_types", "severities", "scenario_ids", "impact_methods",
              "time_to_impact_s", "sla_binding_vrf")
_FLOAT_COLS = ("if_in_octets", "if_out_octets", "if_oper_status",
               "tunnel_latency_ms", "tunnel_jitter_ms", "tunnel_loss_pct",
               "tunnel_rekeys", "flow_bytes", "flow_packets", "lead_time_s",
               "severity", "severity_primary",
               "if_in_errors", "if_in_discards", "if_out_errors", "if_out_discards",
               "q_backlog_bytes", "q_drops",
               "xcvr_temp_c", "xcvr_rx_power_dbm", "xcvr_tx_bias_ma",
               "cpu_pct", "mem_pct", "bgp_msg_rx", "bgp_msg_tx",
               "rib_routes", "ospf_lsa_count",
               "device_temp_c", "device_power_watts", "device_fan_rpm",
               "device_psu_voltage_v")


def precursor_mask(df):
    """Rows whose label set contains at least one pre-impact episode.

    `time_to_impact_s` is a LIST per row since DEFECT 5b, so `> 0` no longer
    works on it -- every reader that used to do that needs this instead.
    """
    return df["is_fault"].fillna(False).astype(bool) & df["time_to_impact_s"].map(
        lambda v: bool(v is not None and not isinstance(v, float)
                       and any((x or 0) > 0 for x in v)))


def finalize_schema(df):
    """Canonical column order + dtypes. Both the real and synthetic paths call
    this, so a dtype can never diverge between them (check.py gate 6)."""
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[COLUMNS].copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True).astype("datetime64[us, UTC]")
    df["is_fault"] = df["is_fault"].fillna(False).astype(bool)
    df["n_concurrent"] = pd.to_numeric(df["n_concurrent"], errors="coerce") \
        .fillna(0).astype("int8")
    # G4/G7: booleans default False; G7 counters/depth are nullable ints (null off
    # a cascade / on non-fault rows); G8 seed is a nullable int64.
    df["is_hard_negative"] = df["is_hard_negative"].fillna(False).astype(bool)
    df["is_root"] = df["is_root"].fillna(False).astype(bool)
    df["cascade_depth"] = pd.to_numeric(df["cascade_depth"], errors="coerce").astype("Int8")
    df["affected_entity_count"] = pd.to_numeric(
        df["affected_entity_count"], errors="coerce").astype("Int16")
    df["injection_seed"] = pd.to_numeric(
        df["injection_seed"], errors="coerce").astype("Int64")
    for c in _FLOAT_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    for c in ("scenario_id", "fault_type", "severity_label", "vrf",
              "fault_type_primary", "scenario_id_primary",
              # G1/G6/G7 string columns
              "topology_id", "stream", "cascade_parent_id", "cascade_motif_id"
              ) + _LIST_COLS:
        df[c] = df[c].astype("object")
    # DEFECT 2a: one Arrow type for vrf -- list<string> | None on every row.
    df["vrf"] = df["vrf"].map(_as_vrf_list)
    return df.sort_values(["ts", "device", "entity"]).reset_index(drop=True)


def _site_vrfs():
    """{device: [vrf, ...]} from the generated topology, for the tunnel rows.
    Empty dict if topology-meta is absent -- then tunnel vrf stays null rather
    than being guessed."""
    try:
        meta = sources.topology_graph()
    except Exception:  # no generated topology (e.g. synthetic-only host)
        return {}
    return {n["id"]: n["vrfs"] for n in (meta or {}).get("nodes", []) if n.get("vrfs")}


def _fill_vrf(df):
    """DEFECT 3: populate `vrf` wherever VRF is meaningful, leave it null where
    it is not (physical interfaces, device rows)."""
    if df.empty:
        return df
    sv = _site_vrfs()
    have = df["vrf"].notna() if "vrf" in df.columns else pd.Series(False, index=df.index)
    iface = (df["entity_type"] == "interface") & ~have
    df.loc[iface, "vrf"] = df.loc[iface, "entity"].map(vrf_of_entity)
    tun = (df["entity_type"] == "tunnel") & ~have
    if tun.any() and sv:
        df.loc[tun, "vrf"] = df.loc[tun, "device"].map(
            lambda d: tunnel_vrf_set(sv.get(d)))
    return df


def build_dataset(start: int, end: int, step: int = 30) -> str:
    """Build the joined labeled Parquet for [start,end]; return its path."""
    iface = _collect(_IF_METRICS, "interface", "interface", start, end, step)
    tunnel = _collect(_TUN_METRICS, "tunnel", "tunnel", start, end, step)
    # device-scoped rows: the entity label IS the device name
    dev = _collect(_DEV_METRICS, "device", "device", start, end, step)
    parts = [d for d in (iface, tunnel, dev) if len(d)]
    base = pd.concat(parts, ignore_index=True) if parts \
        else pd.DataFrame(columns=["ts", "device", "site_type", "vrf", "entity", "entity_type"])

    # Flows are device-wide, so they attach to the device row only: merging into
    # the whole frame would replicate one measurement across every interface and
    # tunnel row of that (device, bucket) and inflate any naive sum ~15x.
    flows = _flow_bucketed(start, end, step)
    if not base.empty and not flows.empty:
        is_dev = base["entity_type"] == "device"
        base = pd.concat([base[~is_dev],
                          base[is_dev].merge(flows, on=["ts", "device"], how="left")],
                         ignore_index=True)

    base = _apply_labels(base, step)
    base = _fill_vrf(base)
    base = finalize_schema(base)

    fname = f"dataset_{start}_{end}_{step}s.parquet"
    path = os.path.join(DATASETS_DIR, fname)
    # atomic: two uvicorn workers can build the same window concurrently, and a
    # half-written Parquet has no footer.
    tmp = f"{path}.{os.getpid()}.tmp"
    base.to_parquet(tmp, index=False)
    os.replace(tmp, path)
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
