"""events.py -- synthetic DISCRETE control-plane events for a fault ledger.

The main synthetic table (generate.py) is 30 s bucketed telemetry. Real NOC
signal also arrives as discrete, exactly-timestamped log events (Drain/Spell
templates: a session drop, an LSA flood, a link down). A flap can begin and
recover *inside one 30 s bucket* -- invisible in the bucketed metrics but loud
in the event stream. This module emits that stream from the same ledger the
generator already builds, so events and metrics join on scenario_id/device.

events_for_ledger(ledger, rng) -> DataFrame   # >=1 event per mapped episode
write_events(ledger, out_path, rng=None) -> path to events.parquet (pyarrow)

Events carry Drain-style (template_id, params) -- NOT raw log text: the template
is the constant skeleton, params the extracted variables. Timestamps are exact
(never bucketed). event_id is a deterministic hash, so a fixed --seed reproduces
the whole stream. This module NEVER owns an RNG (reproducibility): the caller
passes generate.py's seeded Generator, or None for no jitter.
"""
import hashlib
import json

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# ts is µs-precision UTC; every text column is a plain string.
SCHEMA = pa.schema([
    ("event_id", pa.string()),
    ("ts", pa.timestamp("us", "UTC")),
    ("device", pa.string()),
    ("entity", pa.string()),
    ("event_type", pa.string()),
    ("severity", pa.string()),
    ("template_id", pa.string()),
    ("params", pa.string()),
    ("scenario_id", pa.string()),
    ("topology_id", pa.string()),
])
_COLS = [f.name for f in SCHEMA]

# Template registry: template_id -> event_type. Drain/Spell-style stable ids,
# one per (event_type, source). params (extracted below) carry the variables.
TEMPLATES = {
    "FRR-BGP-NBR-DOWN":       "bgp_session_down",
    "FRR-BGP-NBR-UP":         "bgp_session_up",
    "FRR-BGP-HOLD-EXPIRE":    "bgp_hold_expired",
    "FRR-OSPF-ADJ-DOWN":      "ospf_adj_down",
    "FRR-OSPF-ADJ-UP":        "ospf_adj_up",
    "FRR-OSPF-LSA-FLOOD":     "ospf_lsa_flood",
    "FRR-LDP-NBR-DOWN":       "ldp_session_down",
    "FRR-LDP-NBR-UP":         "ldp_session_up",
    "FRR-LDP-LABEL-WITHDRAW": "ldp_label_withdraw",
    "KERN-LINK-DOWN":         "link_down",
    "KERN-LINK-UP":           "link_up",
    "WG-HANDSHAKE":           "wg_handshake",
    "WG-REKEY":               "wg_rekey",
    "SYS-PROC-RESTART":       "process_restart",
    "FRR-ROUTE-WITHDRAW":     "route_withdraw",
    "FRR-VRF-ROUTE-CHANGE":   "vrf_route_change",
}

_BGP_CHURN = {"bgp_flap", "bgp_cascade", "rr_failure"}
_LDP = {"ldp_session_flap", "mpls_underlay_failure"}
_TUNNEL_DEG = {"tunnel_degrade", "asymmetric_loss"}
_CONGEST = {"congestion", "core_congestion", "hub_spoke_congest", "brownout"}

# Each entry: (template_id, anchor, offset_s). anchor is "impact" or "end"; the
# small per-template offset staggers same-anchor events so they stay sub-bucket,
# keep a stable order (down before up), and never collide on event_id.


def _spec(ft, kind, hard):
    """Discrete events for one episode. Lazy: only the common types are mapped;
    anything else (path_asymmetry, policy_drift, controller_drift, ...) emits
    nothing -- YAGNI, those have no clean discrete signature."""
    if hard:
        # ponytail: hard-negative sub-type inferred from kind/ft, not a new field.
        if kind in ("iface_down", "iface_churn"):        # self-healing flap
            return [("KERN-LINK-DOWN", "impact", 0.0),
                    ("KERN-LINK-UP", "impact", 1.5)]     # short sub-bucket gap
        if ft == "ospf_area_flap":                        # maintenance drain
            return [("FRR-OSPF-LSA-FLOOD", "impact", 0.0)]
        return []                                         # congestion recedes -> nothing
    if ft in _BGP_CHURN:
        return [("FRR-BGP-NBR-DOWN", "impact", 0.0),
                ("FRR-BGP-HOLD-EXPIRE", "impact", 0.4),
                ("FRR-ROUTE-WITHDRAW", "impact", 0.7),
                ("FRR-BGP-NBR-UP", "end", 0.0)]
    if ft == "ospf_area_flap":
        return [("FRR-OSPF-ADJ-DOWN", "impact", 0.0),
                ("FRR-OSPF-LSA-FLOOD", "impact", 0.5),
                ("FRR-OSPF-ADJ-UP", "end", 0.0)]
    if ft in _LDP:
        return [("FRR-LDP-NBR-DOWN", "impact", 0.0),
                ("FRR-LDP-LABEL-WITHDRAW", "impact", 0.4),
                ("FRR-LDP-NBR-UP", "end", 0.0)]
    if kind == "iface_down":     # node/p_node/pop/core/srlg -- link goes away
        return [("KERN-LINK-DOWN", "impact", 0.0),
                ("FRR-OSPF-ADJ-DOWN", "impact", 0.3),
                ("SYS-PROC-RESTART", "impact", 0.6),
                ("KERN-LINK-UP", "end", 0.0)]
    if ft in _TUNNEL_DEG:
        return [("WG-REKEY", "impact", 0.0),
                ("FRR-VRF-ROUTE-CHANGE", "impact", 0.5)]
    if ft in _CONGEST:           # dataplane: at most one routing wobble, no flap
        return [("FRR-VRF-ROUTE-CHANGE", "impact", 0.0)]
    if ft == "gray_failure":     # the point IS no link event; only an optic-driven rekey
        return [("WG-REKEY", "impact", 0.0)]
    return []


def _params(event_type, device, entity, vrf):
    """Extracted variables for the Drain template (the slots, filled with concrete
    values). ponytail: no neighbour table exists in the ledger, so the peer slot
    reuses the scoped entity -- honest about what we actually know."""
    p = {"device": device}
    if event_type in ("bgp_session_up", "bgp_session_down", "bgp_hold_expired",
                       "ldp_session_up", "ldp_session_down", "ldp_label_withdraw"):
        p["peer"] = entity
    if event_type in ("ospf_adj_up", "ospf_adj_down", "ospf_lsa_flood",
                      "link_up", "link_down", "wg_handshake", "wg_rekey"):
        p["iface"] = entity
    if event_type in ("route_withdraw", "vrf_route_change"):
        p["vrf"] = vrf
    if event_type == "process_restart":
        p["proc"] = "bgpd"
    return p


def _event_id(scenario_id, event_type, ts_us):
    """Deterministic id from (scenario_id, event_type, ts) -- no uuid/random, so
    a fixed seed reproduces the stream. ts_us makes repeated event_types unique."""
    key = f"{scenario_id}|{event_type}|{ts_us}".encode()
    return "ev-" + hashlib.blake2b(key, digest_size=8).hexdigest()


def events_for_ledger(ledger, rng):
    """One or more discrete events per mapped episode, exact (unbucketed) ts."""
    rows = []
    for ep in ledger:
        ft, kind = ep["fault_type"], ep["kind"]
        # t_impact is None for pure-dataplane faults -> anchor on t_start instead.
        t_impact = ep.get("t_impact")
        anchors = {"impact": t_impact if t_impact is not None else ep["t_start"],
                   "end": ep["t_end"]}
        device, entity, vrf = ep["device"], ep.get("entity"), ep.get("vrf")
        for tid, anchor, off in _spec(ft, kind, ep.get("is_hard_negative", False)):
            etype = TEMPLATES[tid]
            # tiny sub-bucket jitter, only if the caller handed us its Generator.
            jit = float(rng.uniform(0.0, 0.9)) if rng is not None else 0.0
            ts_epoch = anchors[anchor] + off + jit
            ts_us = int(round(ts_epoch * 1e6))
            rows.append({
                "event_id": _event_id(ep["scenario_id"], etype, ts_us),
                "ts": ts_us, "device": device, "entity": entity,
                "event_type": etype,
                "severity": ep.get("severity_label"),
                "template_id": tid,
                "params": json.dumps(_params(etype, device, entity, vrf)),
                "scenario_id": ep["scenario_id"], "topology_id": ep["topology_id"],
            })

    df = pd.DataFrame(rows, columns=_COLS)
    # µs epoch int -> tz-aware µs timestamp (exact match to the parquet column).
    df["ts"] = pd.to_datetime(df["ts"], unit="us", utc=True).astype("datetime64[us, UTC]")
    return df


def write_events(ledger, out_path, rng=None):
    """Write events.parquet with the exact declared schema; return out_path."""
    df = events_for_ledger(ledger, rng)
    tbl = pa.Table.from_pandas(df, schema=SCHEMA, preserve_index=False)
    pq.write_table(tbl, out_path)
    return out_path


def _selfcheck():
    import os
    import tempfile

    import numpy as np
    rng = np.random.default_rng(0)
    t0 = 1_750_000_000.0
    ledger = [
        {"scenario_id": "bgp_flap-ce_branch1-aa", "fault_type": "bgp_flap",
         "kind": "iface_churn", "device": "ce_branch1", "entity": "eth1",
         "t_start": t0, "t_impact": t0 + 40, "t_end": t0 + 160,
         "severity_label": "high", "vrf": ["CORP"], "injection_seed": 1,
         "topology_id": "topo-1", "is_hard_negative": False, "stream": "syn",
         "cascade_parent_id": None, "cascade_depth": 0, "cascade_motif_id": None,
         "is_root": True},
        {"scenario_id": "node_failure-ce_branch2-bb", "fault_type": "node_failure",
         "kind": "iface_down", "device": "ce_branch2", "entity": "eth2",
         "t_start": t0, "t_impact": t0 + 5, "t_end": t0 + 90,
         "severity_label": None, "vrf": None, "injection_seed": 2,
         "topology_id": "topo-1", "is_hard_negative": False, "stream": "syn",
         "cascade_parent_id": None, "cascade_depth": 0, "cascade_motif_id": None,
         "is_root": True},
        {"scenario_id": "node_failure-ce_branch3-cc", "fault_type": "node_failure",
         "kind": "iface_down", "device": "ce_branch3", "entity": "eth0",
         "t_start": t0, "t_impact": t0 + 10, "t_end": t0 + 20,
         "severity_label": None, "vrf": None, "injection_seed": 3,
         "topology_id": "topo-1", "is_hard_negative": True, "stream": "syn",
         "cascade_parent_id": None, "cascade_depth": 0, "cascade_motif_id": None,
         "is_root": True},
        {"scenario_id": "congestion-ce_branch4-dd", "fault_type": "congestion",
         "kind": "tunnel_ramp", "device": "ce_branch4", "entity": None,
         "t_start": t0, "t_impact": None, "t_end": t0 + 120,
         "severity_label": "medium", "vrf": ["VOICE"], "injection_seed": 4,
         "topology_id": "topo-1", "is_hard_negative": False, "stream": "syn",
         "cascade_parent_id": None, "cascade_depth": 0, "cascade_motif_id": None,
         "is_root": True},
    ]
    df = events_for_ledger(ledger, rng)
    assert len(df), "no events emitted"
    assert df["template_id"].notna().all(), "template_id must be non-null on all rows"
    assert set(df["template_id"]) <= set(TEMPLATES), "unknown template_id"
    for p in df["params"]:
        json.loads(p)  # raises if not valid JSON
    assert "us" in str(df["ts"].dtype), f"ts must be µs datetime, got {df['ts'].dtype}"
    assert df["event_id"].is_unique, "event_id must be unique"
    # link_down precedes link_up within every scenario that has both.
    for sid, g in df.groupby("scenario_id"):
        d = g.loc[g.event_type == "link_down", "ts"]
        u = g.loc[g.event_type == "link_up", "ts"]
        if len(d) and len(u):
            assert d.max() < u.min(), f"link_down !< link_up in {sid}"

    tmp = os.path.join(tempfile.gettempdir(), "events_selfcheck.parquet")
    write_events(ledger, tmp, rng=np.random.default_rng(0))
    back = pq.read_table(tmp)
    assert back.schema.equals(SCHEMA), "round-trip schema mismatch"
    assert back.num_rows == len(df), "round-trip row count mismatch"
    os.remove(tmp)
    print(f"events selfcheck OK: {len(df)} events, {df['event_type'].nunique()} types, "
          f"schema round-tripped")


if __name__ == "__main__":
    _selfcheck()
