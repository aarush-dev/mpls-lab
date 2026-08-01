#!/usr/bin/env python3
"""generate_fixtures.py -- builds the frontend's mock data layer.

Reads the committed sample Parquet captures (real + synthetic) and the
topology spec, and emits deterministic, compact JSON fixtures into
`frontend/plugin/src/fixtures/`. A `MockDataClient` (frontend/plugin/src/data,
not this script's concern) reads these files at runtime.

Determinism contract: no `random` without a fixed seed, no `datetime.now()`,
no dict/set iteration that depends on hash-seed ordering (always sort keys
before iterating/emitting). Rerunning this script must produce byte-identical
JSON files.

Run:  python3 frontend/scripts/generate_fixtures.py
"""
import hashlib
import json
import os
import sys

import pandas as pd
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATAAPI_DIR = os.path.join(REPO_ROOT, "dataapi")
sys.path.insert(0, DATAAPI_DIR)
from export import COLUMNS, precursor_mask, SEVERITY_ORDINAL  # noqa: E402

FIXTURES_DIR = os.path.join(REPO_ROOT, "frontend", "plugin", "src", "fixtures")
REAL_PARQUET = os.path.join(DATAAPI_DIR, "datasets", "dataset_1785032386_1785033870_30s.parquet")
SYNTH_PARQUET = os.path.join(REPO_ROOT, "synthetic", "output", "synthetic_1781481600_d1.0_s30_x3.0.parquet")
TOPOLOGY_SPEC = os.path.join(REPO_ROOT, "topology-spec.yaml")

BUCKET_MS = 30000
WINDOW_BUCKETS = 50
FAULT_BUCKETS_PER_EPISODE = 4  # cap on how many fault-active buckets we lift from a synthetic episode
PAD_BUCKETS = 2  # calm buckets appended before/after each lifted episode

SEV_LABEL = {v: k for k, v in SEVERITY_ORDINAL.items()}  # 0.33->low, 0.66->medium, 1.0->high

# fault type -> ragcorpus citation bucket. Every one of the 21 possible fault
# types (10 real + 11 synthetic-only) must map somewhere; only cites files
# that exist under ragcorpus/.
BGP_ROUTING_TYPES = {
    "bgp_flap", "node_failure", "rr_failure", "bgp_cascade", "controller_drift",
    "ospf_area_flap", "p_node_failure", "ldp_session_flap", "gray_failure",
}
TUNNEL_TYPES = {
    "congestion", "tunnel_degrade", "asymmetric_loss", "brownout",
    "hub_spoke_congest", "core_congestion", "mpls_underlay_failure",
    "path_asymmetry", "srlg_cut", "pop_isolation", "core_partition", "policy_drift",
}

ROOT_CAUSE_HYPOTHESES = {
    "congestion": ["Sustained traffic surge exceeding provisioned tunnel bandwidth",
                   "Possible mis-provisioned QoS class letting best-effort traffic starve VOICE/CORP"],
    "tunnel_degrade": ["WireGuard tunnel path experiencing steady latency/jitter buildup",
                        "Underlying transport congestion or a marginal link on the tunnel's egress path"],
    "asymmetric_loss": ["Loss concentrated in one direction of the tunnel, suggesting a one-way path issue",
                         "Possible asymmetric routing or a half-duplex/negotiation mismatch upstream"],
    "brownout": ["Slow, sustained degradation short of a hard outage",
                 "Gradual resource exhaustion (bandwidth, queue, or CPU) on the affected site"],
    "bgp_flap": ["Repeated BGP session churn (clear bgp / neighbor reset loop)",
                 "Flapping physical/logical link causing adjacency bounce"],
    "node_failure": ["bgpd or the device process crashed and is awaiting watchfrr respawn",
                      "Hard device failure on the affected node"],
    "rr_failure": ["Route-reflector (pe1/pe2) session loss collapsing iBGP for its clients",
                   "RR process restart or control-plane resource exhaustion"],
    "bgp_cascade": ["Initial BGP event cascading into dependent sessions/prefixes",
                     "Downstream churn triggered by an upstream adjacency flap"],
    "controller_drift": ["SD-WAN controller state diverged from device-reported state",
                          "Stale controller policy push not yet reconciled with the data plane"],
    "ospf_area_flap": ["OSPF adjacency flapping within a POP area, forcing repeated SPF runs",
                        "MTU mismatch or unstable link triggering neighbor state churn"],
    "p_node_failure": ["Core P router failure removing LDP/OSPF transit capacity",
                        "Provider-core hardware or process fault"],
    "ldp_session_flap": ["LDP session instability disrupting label bindings on a P-P or P-PE link",
                          "Underlying OSPF instability cascading into LDP"],
    "gray_failure": ["Device reports healthy but is silently dropping/degrading a subset of traffic",
                      "Partial hardware fault not surfaced by standard health checks"],
    "hub_spoke_congest": ["WireGuard hub concentrator saturated by aggregate spoke traffic",
                           "Hub uplink undersized for current spoke fan-in"],
    "core_congestion": ["Provider-core link(s) saturated by inter-POP transit load",
                         "Traffic-engineering imbalance concentrating load on one core path"],
    "mpls_underlay_failure": ["LDP/label-switched path failure in the MPLS underlay",
                               "Underlay OSPF instability breaking label bindings"],
    "path_asymmetry": ["Forward and return paths diverging across the core, producing inconsistent latency",
                        "ECMP hashing sending flows over paths with different costs"],
    "srlg_cut": ["Shared-risk link group (fiber conduit) cut taking down all its member links at once",
                 "Simultaneous loss of redundant inter-POP links sharing one physical conduit"],
    "pop_isolation": ["A POP's inter-POP backbone links all down, isolating it from the rest of the core",
                       "Cascading SRLG/core failures leaving one POP unreachable"],
    "core_partition": ["Provider core split into two reachability partitions",
                        "Multiple concurrent core link failures exceeding redundancy"],
    "policy_drift": ["Deployed QoS/routing policy diverged from the intended controller policy",
                      "Manual or partial config change not reconciled with the controller"],
}

RECOMMENDED_ACTIONS = {
    "congestion": [("Check tunnel utilization", "Compare sdwan_tunnel_loss_pct/latency_ms against the VRF's QoS class share."),
                   ("Rebalance or upsize", "Consider raising the CE uplink rate or re-weighting HTB classes.")],
    "tunnel_degrade": [("Inspect wg0 path", "Check tunnel_latency_ms/jitter_ms trend and rekey count for the affected tunnel."),
                        ("Fail over to alternate hub", "If a second hub is reachable, prefer it while the primary path recovers.")],
    "asymmetric_loss": [("Isolate direction", "Compare inbound vs outbound loss to localize which leg is impaired."),
                         ("Check upstream link", "Inspect the egress interface counters on the affected direction.")],
    "brownout": [("Correlate with load", "Check cpu_pct/mem_pct and queue backlog alongside the tunnel metrics."),
                 ("Watch for escalation", "Brownouts can precede a hard outage -- confirm trend direction.")],
    "bgp_flap": [("Check neighbor state", 'Run `show bgp summary` on the device; confirm this is a flap, not a process gap.'),
                 ("Dampen if repeated", "Consider BGP dampening if the flap recurs beyond the SLA window.")],
    "node_failure": [("Confirm process state", "Check whether bgpd/watchfrr is respawning the failed process."),
                      ("Escalate if hard down", "If the node stays unreachable past the respawn window, treat as hardware failure.")],
    "rr_failure": [("Check RR reachability", "Verify pe1/pe2 loopback reachability from clients."),
                    ("Fail over RR role", "If one RR is down, confirm the other RR is still serving all clients.")],
    "bgp_cascade": [("Trace the origin", "Identify the first device in the ADJCHANGE sequence."),
                     ("Contain downstream churn", "Watch dependent VRF sessions for secondary flaps.")],
    "controller_drift": [("Reconcile controller state", "Force a controller policy re-push to the drifted device."),
                          ("Audit recent changes", "Check for manual config changes bypassing the controller.")],
    "ospf_area_flap": [("Check adjacency stability", "Look for repeated neighbor state transitions in the affected area."),
                        ("Verify link quality", "Rule out a marginal physical link or MTU mismatch.")],
    "p_node_failure": [("Verify core redundancy", "Confirm alternate P-P/P-PE paths are carrying rerouted traffic."),
                        ("Escalate to hardware", "Core node failures typically need on-site intervention.")],
    "ldp_session_flap": [("Check LDP neighbor state", "Correlate with the underlying OSPF adjacency for the same link."),
                          ("Verify label bindings", "Confirm LSPs re-establish once the session stabilizes.")],
    "gray_failure": [("Cross-check health vs traffic", "Compare reported health against actual forwarded traffic/errors."),
                      ("Consider proactive reboot", "Gray failures often need a hard reset to clear.")],
    "hub_spoke_congest": [("Check hub aggregate load", "Sum spoke tunnel throughput against the hub's uplink capacity."),
                           ("Redistribute spokes", "Consider moving some spokes to a less-loaded hub.")],
    "core_congestion": [("Check core link utilization", "Identify the saturated inter-POP link(s)."),
                         ("Traffic-engineer around it", "Shift traffic to an alternate core path if available.")],
    "mpls_underlay_failure": [("Check LDP/OSPF underlay", "Confirm label bindings re-establish once OSPF converges."),
                               ("Verify MPLS forwarding", "Confirm LSPs are back before declaring resolved.")],
    "path_asymmetry": [("Compare forward/return paths", "Trace both directions to find the diverging hop."),
                        ("Check ECMP hashing", "Confirm ECMP isn't sending return traffic over a longer path.")],
    "srlg_cut": [("Identify the shared conduit", "Check which SRLG group's links all dropped together."),
                 ("Confirm diverse restoration", "Verify traffic rerouted over links outside the cut conduit.")],
    "pop_isolation": [("Check all inter-POP links for that POP", "Confirm whether it's a full or partial isolation."),
                       ("Prioritize restoration", "POP isolation affects every site behind it -- treat as highest priority.")],
    "core_partition": [("Map the partition boundary", "Determine which POPs are on which side."),
                        ("Restore redundant paths", "Bring up any remaining spare core capacity.")],
    "policy_drift": [("Diff deployed vs intended policy", "Compare the device's active config against the controller's intended state."),
                      ("Re-push controller policy", "Force reconciliation once the drift is confirmed.")],
}


def citations_for(fault_type):
    cites = []
    if fault_type in BGP_ROUTING_TYPES:
        cites.append({"title": "Runbook: BGP adjacency down / flapping", "href": "ragcorpus/runbook-bgp-adjacency-down.md"})
    if fault_type in TUNNEL_TYPES:
        cites.append({"title": "Runbook: SD-WAN tunnel latency / loss high", "href": "ragcorpus/runbook-tunnel-latency-high.md"})
    cites.append({"title": "Topology map", "href": "ragcorpus/topology-map.md"})
    cites.append({"title": "Incident report template", "href": "ragcorpus/incident-template.md"})
    return cites


def sev_label(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "unknown"
    # nearest ordinal match (values are exactly 0.33/0.66/1.0 in this dataset)
    best = min(SEV_LABEL, key=lambda k: abs(k - x))
    return SEV_LABEL[best]


def ts_to_ms(ts) -> int:
    return int(pd.Timestamp(ts).value // 1_000_000)


# ---------------------------------------------------------------------------
# 1. Topology -- derived purely from topology-spec.yaml knobs (no Parquet dep)
# ---------------------------------------------------------------------------

def build_topology(spec):
    k = spec["knobs"]
    p_count, pe_count, pop_count, p_per_pop = k["p_count"], k["pe_count"], k["pop_count"], k["p_per_pop"]
    branch_n, hub_n, dc_n = k["branch_count"], k["hub_count"], k["dc_count"]
    multi_area = k.get("multi_area", False)
    redundancy = k.get("inter_pop_redundancy", 2)
    chords = [tuple(c) for c in k.get("inter_pop_chords", [])]

    def pop_of(p):
        return (p - 1) // p_per_pop + 1

    def pop_routers(pop):
        return list(range((pop - 1) * p_per_pop + 1, pop * p_per_pop + 1))

    def borders(pop):
        return pop_routers(pop)[:2]

    def internals(pop):
        return pop_routers(pop)[2:]

    def pe_pop(i):
        return (i - 1) * pop_count // pe_count + 1

    nodes = []
    links = []

    for i in range(1, p_count + 1):
        nodes.append({"id": f"p{i}", "role": "p", "pop": f"pop{pop_of(i)}"})
    for i in range(1, pe_count + 1):
        nodes.append({"id": f"pe{i}", "role": "pe", "pop": f"pop{pe_pop(i)}"})

    # P-P intra-POP full mesh
    for pop in range(1, pop_count + 1):
        rtr = pop_routers(pop)
        for ii in range(len(rtr)):
            for jj in range(ii + 1, len(rtr)):
                a, b = rtr[ii], rtr[jj]
                links.append({"source": f"p{a}", "target": f"p{b}", "kind": "physical"})

    # P-P inter-POP: ring + chords, `redundancy` parallel links per adjacency
    ring = [(p, p % pop_count + 1) for p in range(1, pop_count + 1)]
    adjacencies = sorted(set((min(x, y), max(x, y)) for (x, y) in ring) |
                          set((min(x, y), max(x, y)) for (x, y) in chords))
    for (pa, pb) in adjacencies:
        for r in range(redundancy):
            a = borders(pa)[r % 2]
            b = borders(pb)[r % 2]
            links.append({"source": f"p{a}", "target": f"p{b}", "kind": "physical"})

    # P-PE: PE dual-homed to the 2 PE-facing P routers in its POP
    for i in range(1, pe_count + 1):
        pop = pe_pop(i)
        facing = internals(pop)
        links.append({"source": f"pe{i}", "target": f"p{facing[(i - 1) % len(facing)]}", "kind": "physical"})
        if k.get("pe_dual_homing") and len(facing) > 1:
            links.append({"source": f"pe{i}", "target": f"p{facing[i % len(facing)]}", "kind": "physical"})

    # CE sites, in the same linear order the generator uses: branch, hub, dc
    site_vrfs = {"branch": [], "hub": [], "dc": []}
    for vname, vdef in spec["vrfs"].items():
        for st in vdef["sites"]:
            site_vrfs[st].append(vname)
    vrf_idx = {"CORP": 0, "VOICE": 1, "GUEST": 2}
    for st in site_vrfs:
        site_vrfs[st].sort(key=lambda v: vrf_idx[v])

    ce_list = []
    for st, count in (("branch", branch_n), ("hub", hub_n), ("dc", dc_n)):
        for ti in range(1, count + 1):
            ce_list.append({"name": f"ce_{st}{ti}", "site_type": st, "type_idx": ti})

    ce_pop = {}
    for lin_idx, ce in enumerate(ce_list):
        pe_idx = (lin_idx % pe_count) + 1
        pe_name = f"pe{pe_idx}"
        pop = pe_pop(pe_idx)
        ce_pop[ce["name"]] = pop
        role = f"ce_{ce['site_type']}"
        nodes.append({
            "id": ce["name"], "role": role, "siteType": ce["site_type"],
            "pop": f"pop{pop}", "vrfs": list(site_vrfs[ce["site_type"]]),
        })
        links.append({"source": pe_name, "target": ce["name"], "kind": "physical"})

        for vname in site_vrfs[ce["site_type"]]:
            hname = f"h_{ce['site_type']}{ce['type_idx']}_{vname.lower()}"
            nodes.append({
                "id": hname, "role": "host", "siteType": ce["site_type"],
                "pop": f"pop{pop}", "parent": ce["name"], "vrfs": [vname],
            })
            links.append({"source": ce["name"], "target": hname, "kind": "physical"})

    # WireGuard overlay tunnels: every branch/dc spoke <-> every hub, plus hub-hub pairs
    hubs = [ce["name"] for ce in ce_list if ce["site_type"] == "hub"]
    spokes = [ce["name"] for ce in ce_list if ce["site_type"] in ("branch", "dc")]
    for s in spokes:
        for h in hubs:
            links.append({"source": s, "target": h, "kind": "tunnel"})
    if k.get("hub_hub_wg") and len(hubs) >= 2:
        for hi in range(0, len(hubs) - 1, 2):
            links.append({"source": hubs[hi], "target": hubs[hi + 1], "kind": "tunnel"})

    return {"nodes": nodes, "links": links}, ce_list


# ---------------------------------------------------------------------------
# 2. Composite playback tape: real spine (chronological) + short synthetic
#    episodes for fault types the real capture lacks, one per missing type
#    (alphabetical, deterministic), each already calm-fault-calm shaped.
# ---------------------------------------------------------------------------

def build_tape():
    real = pd.read_parquet(REAL_PARQUET)
    real = real[COLUMNS].sort_values(["ts", "device", "entity"]).reset_index(drop=True)

    real_buckets = sorted(real["ts"].unique())
    assert len(real_buckets) == WINDOW_BUCKETS, f"expected {WINDOW_BUCKETS} real buckets, got {len(real_buckets)}"
    real_types = set(real["fault_type"].dropna().unique())

    synth = pd.read_parquet(SYNTH_PARQUET)
    synth = synth[COLUMNS]
    all_types = sorted(synth["fault_type"].dropna().unique())
    missing_types = sorted(t for t in all_types if t not in real_types)

    faults = synth[synth["is_fault"]]
    synth_ts_sorted = sorted(synth["ts"].unique())
    ts_index = {t: i for i, t in enumerate(synth_ts_sorted)}

    episode_frames = []
    episode_provenance = []
    for ft in missing_types:
        scenarios = sorted(faults.loc[faults["fault_type"] == ft, "scenario_id"].unique())
        scen = scenarios[0]  # deterministic: lexicographically first
        scen_ts = sorted(faults.loc[faults["scenario_id"] == scen, "ts"].unique())
        fault_ts = scen_ts[:FAULT_BUCKETS_PER_EPISODE]  # first N fault-active buckets
        first_i, last_i = ts_index[fault_ts[0]], ts_index[fault_ts[-1]]
        lo = max(0, first_i - PAD_BUCKETS)
        hi = min(len(synth_ts_sorted) - 1, last_i + PAD_BUCKETS)
        window_ts = synth_ts_sorted[lo:hi + 1]
        frame = synth[synth["ts"].isin(window_ts)].sort_values(["ts", "device", "entity"]).reset_index(drop=True)
        # This slice of a full synthetic day is dense with OTHER, unrelated
        # concurrent scenarios overlapping the same buckets. Blank out any
        # fault labelling that isn't this episode's own scenario, so the
        # lifted episode reads as a single clean incident, not a pile-up of
        # coincidental background faults. Underlying metric values (octets,
        # cpu, etc.) are untouched -- only the label columns are cleared.
        other = frame["is_fault"] & (frame["scenario_id"] != scen)
        if other.any():
            frame.loc[other, "is_fault"] = False
            for c in ("scenario_id", "fault_type", "severity", "severity_label",
                      "lead_time_s", "fault_type_primary", "severity_primary",
                      "scenario_id_primary"):
                frame.loc[other, c] = None
            for c in ("time_to_impact_s", "fault_types", "severities", "scenario_ids", "impact_methods"):
                frame.loc[other, c] = pd.Series([None] * other.sum(), index=frame.index[other]).astype(object)
            frame.loc[other, "n_concurrent"] = 0
        episode_frames.append(frame)
        episode_provenance.append({
            "faultType": ft, "scenarioId": scen, "bucketCount": len(window_ts),
        })

    # Reassign every bucket a new contiguous ts, in tape order: real spine
    # first (already calm->incident->calm), then episodes alphabetically.
    tape_start = real_buckets[0]
    frames = [real] + episode_frames
    bucket_orig_ts = list(real_buckets)
    for frame in episode_frames:
        bucket_orig_ts.extend(sorted(frame["ts"].unique()))

    tape = pd.concat(frames, ignore_index=True)
    orig_ts_to_idx = {orig: i for i, orig in enumerate(bucket_orig_ts)}
    tape["bucket_index"] = tape["ts"].map(orig_ts_to_idx)

    bucket_count = len(bucket_orig_ts)
    tape_bucket_ms = [ts_to_ms(tape_start) + i * BUCKET_MS for i in range(bucket_count)]

    device_ids = sorted(real["device"].unique())

    meta_provenance = {
        "spine": {
            "kind": "measured",
            "file": os.path.relpath(REAL_PARQUET, REPO_ROOT),
            "bucketRange": [0, WINDOW_BUCKETS - 1],
            "description": "real 148-container lab capture, chronological, already calm->incident->calm",
        },
        "episodes": episode_provenance,
        "episodeSource": {
            "kind": "simulated",
            "file": os.path.relpath(SYNTH_PARQUET, REPO_ROOT),
            "description": "short slices lifted for fault types absent from the real capture; each padded "
                            f"with {PAD_BUCKETS} calm buckets on either side",
        },
    }

    return tape, tape_bucket_ms, device_ids, bucket_count, meta_provenance


# ---------------------------------------------------------------------------
# 3. Per-bucket node health (red/amber) for the 70 FRR devices
# ---------------------------------------------------------------------------

def build_node_states(tape, device_ids):
    def row_precursor(is_fault, tti):
        if not is_fault or tti is None:
            return False
        try:
            return any((x is not None) and x > 0 for x in tti)
        except TypeError:
            return False

    def row_active(is_fault, tti, if_oper_status, entity_type):
        if entity_type == "interface" and if_oper_status == 2.0:
            return True
        if not is_fault:
            return False
        if tti is None:
            return True  # is_fault true, no per-episode detail -> treat as active
        try:
            return any((x is not None) and x <= 0 for x in tti)
        except TypeError:
            return True

    states = {}
    grouped = tape.groupby(["bucket_index", "device"], sort=True)
    for (bidx, device), g in grouped:
        red = False
        amber = False
        for _, row in g.iterrows():
            if row_active(row["is_fault"], row["time_to_impact_s"], row["if_oper_status"], row["entity_type"]):
                red = True
                break
            if row_precursor(row["is_fault"], row["time_to_impact_s"]):
                amber = True
        if red:
            states.setdefault(str(int(bidx)), {})[device] = "red"
        elif amber:
            states.setdefault(str(int(bidx)), {})[device] = "amber"

    # emit sorted for determinism
    out = {}
    for bidx in sorted(states, key=lambda x: int(x)):
        out[bidx] = dict(sorted(states[bidx].items()))
    return out


# ---------------------------------------------------------------------------
# 4. Telemetry: compact per-device MetricSeries over the tape
# ---------------------------------------------------------------------------

# Kept to what the plan says is actually charted: cpu/mem, interface rx/tx,
# errors/discards, queue, xcvr/env, tunnel latency/jitter/loss. Dropped
# bgp_msg_rx/tx, rib_routes, ospf_lsa_count, device_temp/power/fan/psu, and
# tunnel_rekeys to keep the fixture compact -- not charted anywhere in the plan.
DEVICE_METRICS = [
    ("cpu_pct", "CPU", "%", "measured"),
    ("mem_pct", "Memory", "%", "measured"),
]
INTERFACE_METRICS = [
    ("if_in_octets", "Interface RX", "bytes", "measured"),
    ("if_out_octets", "Interface TX", "bytes", "measured"),
    ("if_in_errors", "Interface RX errors", "count", "measured"),
    ("if_in_discards", "Interface RX discards", "count", "measured"),
    ("if_out_errors", "Interface TX errors", "count", "measured"),
    ("if_out_discards", "Interface TX discards", "count", "measured"),
    ("q_backlog_bytes", "Queue backlog", "bytes", "measured"),
    ("q_drops", "Queue drops", "count", "measured"),
    ("xcvr_temp_c", "Transceiver temperature", "C", "modelled"),
    ("xcvr_rx_power_dbm", "Transceiver RX power", "dBm", "modelled"),
    ("xcvr_tx_bias_ma", "Transceiver TX bias", "mA", "modelled"),
]
TUNNEL_METRICS = [
    ("tunnel_latency_ms", "Tunnel latency", "ms", "simulated"),
    ("tunnel_jitter_ms", "Tunnel jitter", "ms", "simulated"),
    ("tunnel_loss_pct", "Tunnel loss", "%", "simulated"),
]


def _round(v):
    if v is None:
        return None
    return round(v, 3)


def _series(rows_by_bucket, bucket_ms, col):
    points = []
    for bidx, ms in enumerate(bucket_ms):
        row = rows_by_bucket.get(bidx)
        v = row[col] if row is not None else None
        if v is not None and isinstance(v, float) and pd.isna(v):
            v = None
        points.append({"tMs": ms, "value": _round(v)})
    return points


def select_telemetry_devices(device_ids, incidents, topology):
    """Down-select from 70 FRR devices to a compact, representative set: every
    device involved in an incident, plus a small deterministic baseline sample
    per role so healthy devices are also chartable (topology click-through)."""
    incident_devices = sorted(set(d for inc in incidents for d in inc["deviceIds"]))
    role_of = {n["id"]: n["role"] for n in topology["nodes"]}
    by_role = {}
    for d in sorted(device_ids):
        by_role.setdefault(role_of.get(d, "unknown"), []).append(d)
    baseline = []
    for role in sorted(by_role):
        extra = [d for d in by_role[role] if d not in incident_devices][:2]
        baseline.extend(extra)
    return sorted(set(incident_devices) | set(baseline))


def build_telemetry(tape, device_ids, bucket_ms):
    out = {}
    dev_rows = tape[tape["entity_type"] == "device"]
    iface_rows = tape[tape["entity_type"] == "interface"]
    tun_rows = tape[tape["entity_type"] == "tunnel"]

    for device in device_ids:
        series_list = []

        d = dev_rows[dev_rows["device"] == device]
        by_bucket = {int(r["bucket_index"]): r for _, r in d.iterrows()}
        for col, label, unit, source in DEVICE_METRICS:
            series_list.append({
                "key": f"{device}:{col}", "label": label, "unit": unit, "source": source,
                "points": _series(by_bucket, bucket_ms, col),
            })

        di = iface_rows[iface_rows["device"] == device]
        if not di.empty:
            rep_entity = sorted(di["entity"].unique())[0]
            de = di[di["entity"] == rep_entity]
            by_bucket = {int(r["bucket_index"]): r for _, r in de.iterrows()}
            for col, label, unit, source in INTERFACE_METRICS:
                series_list.append({
                    "key": f"{device}:{rep_entity}:{col}", "label": f"{label} ({rep_entity})",
                    "unit": unit, "source": source, "points": _series(by_bucket, bucket_ms, col),
                })

        dt = tun_rows[tun_rows["device"] == device]
        if not dt.empty:
            rep_tunnel = sorted(dt["entity"].unique())[0]
            de = dt[dt["entity"] == rep_tunnel]
            by_bucket = {int(r["bucket_index"]): r for _, r in de.iterrows()}
            for col, label, unit, source in TUNNEL_METRICS:
                series_list.append({
                    "key": f"{device}:{rep_tunnel}:{col}", "label": f"{label} ({rep_tunnel})",
                    "unit": unit, "source": source, "points": _series(by_bucket, bucket_ms, col),
                })

        out[device] = series_list
    return out


# ---------------------------------------------------------------------------
# 5. Incidents: group tape fault rows by scenario_id
# ---------------------------------------------------------------------------

def build_incidents(tape, bucket_ms):
    faults = tape[tape["is_fault"]]
    scenario_ids = sorted(faults["scenario_id"].dropna().unique())
    incidents = []
    for scen in scenario_ids:
        rows = faults[faults["scenario_id"] == scen]
        first_row = rows.iloc[0]
        fault_type = first_row["fault_type"]
        severity_val = first_row["severity"]
        device_ids = sorted(rows["device"].unique())
        buckets = sorted(int(b) for b in rows["bucket_index"].unique())
        start_bucket, end_bucket = buckets[0], buckets[-1]

        def is_impact(row):
            tti = row["time_to_impact_s"]
            if tti is None:
                return True
            try:
                return any((x is not None) and x <= 0 for x in tti)
            except TypeError:
                return True

        impact_rows = rows[rows.apply(is_impact, axis=1)]
        impact_bucket = int(impact_rows["bucket_index"].min()) if not impact_rows.empty else end_bucket

        lead_times = sorted(x for x in rows["lead_time_s"].dropna().unique())
        affected_scope = sorted(set(f"{r.device}:{r.entity}" for r in rows.itertuples()))
        if len(affected_scope) > 12:
            affected_scope = affected_scope[:12]

        evidence = [{
            "label": "Fault window",
            "detail": f"{fault_type} active on {', '.join(device_ids)} across buckets {start_bucket}-{end_bucket} "
                      f"(severity {sev_label(severity_val)})",
            "source": "ground_truth",
        }]
        if lead_times:
            evidence.append({
                "label": "Lead time",
                "detail": f"labelled lead_time_s={lead_times[0]}s ahead of impact",
                "source": "ground_truth",
            })
        n_concurrent = int(rows["n_concurrent"].max()) if not rows["n_concurrent"].isna().all() else 1
        if n_concurrent and n_concurrent > 1:
            evidence.append({
                "label": "Concurrent episodes",
                "detail": f"up to {n_concurrent} overlapping fault episodes observed on affected rows",
                "source": "ground_truth",
            })

        incidents.append({
            "id": f"inc-{scen}",
            "status": "unknown",  # derived at runtime from the cursor by the consumer
            "faultType": fault_type,
            "severity": sev_label(severity_val),
            "source": "ground_truth",
            "deviceIds": device_ids,
            "startedAt": pd.Timestamp(bucket_ms[start_bucket], unit="ms", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
            "impactAt": pd.Timestamp(bucket_ms[impact_bucket], unit="ms", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endedAt": pd.Timestamp(bucket_ms[end_bucket], unit="ms", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
            "summary": f"{fault_type.replace('_', ' ')} detected on {device_ids[0]}"
                       + (f" (+{len(device_ids) - 1} more)" if len(device_ids) > 1 else ""),
            "evidence": evidence,
            "affectedScope": affected_scope,
            "rootCauseHypotheses": ROOT_CAUSE_HYPOTHESES.get(fault_type, ["Root cause under investigation"]),
            "recommendedActions": [{"title": t, "detail": d} for t, d in
                                    RECOMMENDED_ACTIONS.get(fault_type, [("Investigate", "Correlate with topology and telemetry.")])],
            "startBucket": start_bucket,
            "impactBucket": impact_bucket,
            "endBucket": end_bucket,
        })
    incidents.sort(key=lambda inc: inc["startBucket"])
    return incidents


# ---------------------------------------------------------------------------
# 6. Predictions: mirror the incidents (fire a few buckets before impact,
#    confidence ramp 0.6->0.95), plus one seeded late call + one false alarm.
# ---------------------------------------------------------------------------

PRED_LEAD_TICKS = 4


def build_predictions(incidents, bucket_ms, device_ids):
    predictions = []
    for inc in incidents:
        impact_bucket = inc["impactBucket"]
        start_bucket = inc["startBucket"]
        device = inc["deviceIds"][0]
        fault_type = inc["faultType"]
        earliest = max(start_bucket, impact_bucket - PRED_LEAD_TICKS)
        tick_buckets = list(range(earliest, impact_bucket))
        if not tick_buckets:
            tick_buckets = [max(0, impact_bucket - 1)]
        n = len(tick_buckets)
        for ti, b in enumerate(tick_buckets):
            frac = ti / (n - 1) if n > 1 else 1.0
            confidence = round(0.6 + frac * (0.95 - 0.6), 3)
            tti_s = (impact_bucket - b) * BUCKET_MS / 1000.0
            predictions.append({
                "id": f"pred-{inc['id']}-{ti}",
                "deviceId": device,
                "faultType": fault_type,
                "confidence": confidence,
                "timeToImpactSeconds": tti_s,
                "source": "mock",
                "issuedAtMs": bucket_ms[b],
            })

    # 1 seeded late call: fires AFTER impact on the incident with the most
    # fault buckets (deterministic pick), confidence lower, "TTI" negative.
    if incidents:
        late_inc = max(incidents, key=lambda i: (i["endBucket"] - i["startBucket"], i["id"]))
        late_bucket = min(late_inc["impactBucket"] + 2, late_inc["endBucket"], len(bucket_ms) - 1)
        late_bucket = max(late_bucket, late_inc["impactBucket"])  # never before impact -- that's the point
        predictions.append({
            "id": f"pred-{late_inc['id']}-late",
            "deviceId": late_inc["deviceIds"][0],
            "faultType": late_inc["faultType"],
            "confidence": 0.55,
            "timeToImpactSeconds": float((late_inc["impactBucket"] - late_bucket) * BUCKET_MS / 1000.0),
            "source": "mock",
            "issuedAtMs": bucket_ms[late_bucket],
        })

    # 1 false alarm: fires on a calm bucket for a device never involved in any
    # incident, and no matching incident ever follows.
    involved = {d for inc in incidents for d in inc["deviceIds"]}
    candidates = sorted(d for d in device_ids if d not in involved)
    fa_device = candidates[0] if candidates else sorted(device_ids)[0]
    fault_buckets = {b for inc in incidents for b in range(inc["startBucket"], inc["endBucket"] + 1)}
    calm_buckets = sorted(b for b in range(len(bucket_ms)) if b not in fault_buckets)
    fa_bucket = calm_buckets[len(calm_buckets) // 2] if calm_buckets else 0
    predictions.append({
        "id": "pred-false-alarm-1",
        "deviceId": fa_device,
        "faultType": "congestion",
        "confidence": 0.62,
        "timeToImpactSeconds": 180.0,
        "source": "mock",
        "issuedAtMs": bucket_ms[fa_bucket],
    })

    predictions.sort(key=lambda p: (p["issuedAtMs"], p["id"]))
    return predictions


# ---------------------------------------------------------------------------
# 7. Events: fabricated routing syslog lines tied to fault episodes
# ---------------------------------------------------------------------------

EVENT_TEMPLATES = {
    "bgp_flap": ["%BGP-5-ADJCHANGE: neighbor Down (BGP Notification received)",
                 "%BGP-5-ADJCHANGE: neighbor Up"],
    "node_failure": ["%DAEMON-3-DOWN: bgpd process exited, watchfrr restarting",
                      "%DAEMON-5-UP: bgpd process respawned by watchfrr"],
    "rr_failure": ["%BGP-5-ADJCHANGE: neighbor (route-reflector) Down (BGP Notification received)"],
    "bgp_cascade": ["%BGP-5-ADJCHANGE: neighbor Down (BGP Notification received) -- cascading churn detected"],
    "controller_drift": ["%SDWAN-4-DRIFT: controller-reported policy state diverges from device state"],
    "ospf_area_flap": ["%OSPF-5-ADJCHG: Neighbor Down (dead timer expired)",
                        "%OSPF-5-ADJCHG: Neighbor Up (full adjacency)"],
    "p_node_failure": ["%SYS-3-NODEDOWN: core node unreachable, LDP/OSPF sessions torn down"],
    "ldp_session_flap": ["%LDP-5-SESSION: session Down (Hold timer expired)",
                          "%LDP-5-SESSION: session Up"],
    "gray_failure": ["%HEALTH-4-PARTIAL: device reports healthy but traffic loss observed"],
    "congestion": ["%QOS-4-CONGEST: queue backlog exceeding threshold on egress interface"],
    "tunnel_degrade": ["%SDWAN-4-TUNNEL: tunnel_latency_ms trending upward past SLA threshold"],
    "asymmetric_loss": ["%SDWAN-4-TUNNEL: asymmetric loss detected between tunnel endpoints"],
    "brownout": ["%SDWAN-4-TUNNEL: sustained sub-SLA degradation short of hard failure"],
    "hub_spoke_congest": ["%SDWAN-4-HUB: hub concentrator uplink saturated by aggregate spoke traffic"],
    "core_congestion": ["%MPLS-4-CORE: inter-POP core link utilization exceeding threshold"],
    "mpls_underlay_failure": ["%LDP-3-SESSION: session Down, label bindings withdrawn"],
    "path_asymmetry": ["%ROUTE-4-PATH: forward/return path divergence detected across core"],
    "srlg_cut": ["%SYS-3-LINKDOWN: multiple links down simultaneously (shared conduit)"],
    "pop_isolation": ["%SYS-3-POPISOLATED: all inter-POP backbone links down for this POP"],
    "core_partition": ["%SYS-3-PARTITION: core reachability partition detected"],
    "policy_drift": ["%SDWAN-4-DRIFT: deployed QoS/routing policy diverges from controller intent"],
}


def build_events(incidents, bucket_ms, tape_source_for_bucket):
    events = []
    for inc in incidents:
        templates = EVENT_TEMPLATES.get(inc["faultType"], ["%SYS-4-FAULT: fault condition detected"])
        device = inc["deviceIds"][0]
        source = tape_source_for_bucket(inc["startBucket"])
        for i, line in enumerate(templates):
            bucket = min(inc["startBucket"] + i, inc["endBucket"])
            events.append({
                "tsMs": bucket_ms[bucket],
                "device": device,
                "app": "bgpd" if inc["faultType"] in BGP_ROUTING_TYPES else "sdwand",
                "severity": inc["severity"],
                "line": line,
                "source": source,
            })
    events.sort(key=lambda e: (e["tsMs"], e["device"] or ""))
    return events


# ---------------------------------------------------------------------------
# 8. Flows: sample from device flow_bytes/flow_packets
# ---------------------------------------------------------------------------

def build_flows(tape, bucket_ms, device_ids):
    dev_rows = tape[(tape["entity_type"] == "device") & tape["flow_bytes"].notna()]
    flows = []
    for device in device_ids:
        d = dev_rows[dev_rows["device"] == device].sort_values("bucket_index")
        # sample every 5th bucket to stay compact
        for _, row in d.iloc[::5].iterrows():
            flows.append({
                "tsMs": bucket_ms[int(row["bucket_index"])],
                "device": device,
                "bytes": None if pd.isna(row["flow_bytes"]) else float(row["flow_bytes"]),
                "packets": None if pd.isna(row["flow_packets"]) else float(row["flow_packets"]),
                "source": "measured",
            })
    flows.sort(key=lambda f: (f["tsMs"], f["device"]))
    return flows


# ---------------------------------------------------------------------------
# 9. Conversations: seed copilot templates keyed by fault type
# ---------------------------------------------------------------------------

def build_conversations(fault_types):
    conversations = []
    for ft in sorted(fault_types):
        conv_id = f"conv-seed-{ft}"
        user_text = f"What's going on with the {ft.replace('_', ' ')} alert?"
        actions = [{"title": t, "detail": d} for t, d in
                   RECOMMENDED_ACTIONS.get(ft, [("Investigate", "Correlate with topology and telemetry.")])]
        response = {
            "summary": f"This looks like a {ft.replace('_', ' ')} event. See the linked runbook for the "
                       "telemetry signature and triage steps.",
            "predictedIssue": ft,
            "affectedScope": [],
            "evidence": [{"label": "Fault type", "detail": f"Matches known signature for {ft}", "source": "ground_truth"}],
            "rootCauseHypotheses": ROOT_CAUSE_HYPOTHESES.get(ft, ["Root cause under investigation"]),
            "recommendedActions": actions,
            "citations": citations_for(ft),
            "disclaimer": "Templated guidance -- confirm against live telemetry before acting.",
        }
        conversations.append({
            "id": conv_id,
            "messages": [
                {"id": f"{conv_id}-u1", "role": "user", "content": user_text,
                 "createdAt": "2026-07-26T02:19:30Z", "state": "complete"},
                {"id": f"{conv_id}-a1", "role": "assistant", "content": response["summary"],
                 "createdAt": "2026-07-26T02:19:31Z", "state": "complete"},
            ],
            "context": {"incidentId": None},
            "seedResponse": response,
            "source": "mock",
        })
    return conversations


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def write_json(name, obj):
    path = os.path.join(FIXTURES_DIR, name)
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2, sort_keys=False, separators=(",", ": "))
        fh.write("\n")
    return path


def main():
    os.makedirs(FIXTURES_DIR, exist_ok=True)

    spec = yaml.safe_load(open(TOPOLOGY_SPEC))
    topology, ce_list = build_topology(spec)

    tape, bucket_ms, device_ids, bucket_count, provenance = build_tape()

    def tape_source_for_bucket(bidx):
        return "ground_truth" if bidx < WINDOW_BUCKETS else "simulated"

    node_states = build_node_states(tape, device_ids)
    incidents = build_incidents(tape, bucket_ms)
    telemetry_devices = select_telemetry_devices(device_ids, incidents, topology)
    telemetry = build_telemetry(tape, telemetry_devices, bucket_ms)
    predictions = build_predictions(incidents, bucket_ms, device_ids)
    events = build_events(incidents, bucket_ms, tape_source_for_bucket)
    flows = build_flows(tape, bucket_ms, telemetry_devices)

    all_fault_types = sorted(set(inc["faultType"] for inc in incidents))
    conversations = build_conversations(all_fault_types)

    meta = {
        "bucketMs": BUCKET_MS,
        "bucketCount": bucket_count,
        "windowBuckets": WINDOW_BUCKETS,
        "startTsMs": bucket_ms[0],
        "buckets": bucket_ms,
        "deviceIds": device_ids,
        "telemetryDeviceIds": telemetry_devices,
        "provenance": provenance,
    }

    write_json("meta.json", meta)
    write_json("topology.json", topology)
    write_json("nodeStates.json", node_states)
    write_json("telemetry.json", telemetry)
    write_json("incidents.json", incidents)
    write_json("predictions.json", predictions)
    write_json("events.json", events)
    write_json("flows.json", flows)
    write_json("conversations.json", conversations)

    print(f"bucketCount={bucket_count} deviceIds={len(device_ids)} incidents={len(incidents)} "
          f"predictions={len(predictions)} faultTypes={len(all_fault_types)} "
          f"topologyNodes={len(topology['nodes'])} topologyLinks={len(topology['links'])}")


if __name__ == "__main__":
    main()
