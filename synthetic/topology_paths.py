"""G3: topology_edges.parquet + paths.parquet for the synthetic dataset.

Two temporal tables the RouteNet-style model consumes:

* topology_edges — the static graph (adjacencies, sessions, srlg groups) with
  interval validity. A link flap is encoded as two rows for the same edge_id:
  one that ends at t_impact, one that re-ups at t_end.
* paths — ORDERED hop sequences (order is load-bearing for link<->path message
  passing). path_type is deliberately encapsulation-agnostic: a WireGuard tunnel
  is a path over links, an OSPF SPF path is a path over links. No ldp_lsp — the
  host kernel has no MPLS (see G11 decision).

Deterministic: ids are stable hashes, no random/uuid. Stdlib + pandas only.
"""
from __future__ import annotations

import hashlib
import heapq
from typing import Iterable

import pandas as pd

# ponytail: VRF -> SLA class is the whole business-policy table. Three rows.
VRF_SLA = {"CORP": "business", "VOICE": "strict", "GUEST": "best-effort"}
IGP_INTRA = 10.0   # intra-POP adjacency cost
IGP_INTER = 100.0  # inter-POP (backbone) adjacency cost
# fault_types that take a graph edge DOWN for [t_impact, t_end]:
DOWNING_FAULTS = {
    "node_failure", "p_node_failure", "pop_isolation",
    "core_partition", "srlg_cut",
}
# DEFECT 2: fault_types whose degradation drives a wg hub failover (a reroute the
# controller would trigger) over [t_impact, t_end]. Hard negatives excluded.
FAILOVER_FAULTS = {
    "hub_spoke_congest", "path_asymmetry", "tunnel_degrade", "asymmetric_loss",
    "brownout", "core_congestion", "congestion",
}


def _hid(*parts) -> str:
    """Stable short id from a tuple — deterministic, no uuid."""
    return hashlib.blake2s("|".join(map(str, parts)).encode(), digest_size=8).hexdigest()


def _ts(sec):
    """epoch seconds (or None) -> UTC Timestamp / NaT."""
    return pd.NaT if sec is None else pd.Timestamp(float(sec), unit="s", tz="UTC")


# --------------------------------------------------------------------------- #
# static graph
# --------------------------------------------------------------------------- #
def _static_edges(topology_id, inventory, meta):
    """Build the raw edge dicts (pre-interval). Each carries internal _devices /
    _ifaces sets used only for fault matching; stripped before the DataFrame."""
    pops = meta.get("pops", {})            # {pop_name: [p_router, ...]}
    pe_pop = meta.get("pe_pop", {})        # {pe: pop_index}
    srlgs = meta.get("srlgs", {})          # {name: [[dev, iface], ...]}
    inter = meta.get("inter_pop_links", [])

    # device tiers: prefer inventory site_type, fall back to meta.
    p_routers = {d for d, v in inventory.items() if v.get("site_type") == "core"} \
        or {p for ps in pops.values() for p in ps}
    pes = {d for d, v in inventory.items() if v.get("site_type") == "pe"} or set(pe_pop)
    ces = [d for d, v in inventory.items()
           if v.get("site_type") in ("branch", "dc", "hub")]

    # pop membership + area map (area = pop index; backbone/inter-POP = 0).
    pop_area = {}
    p_area = {}
    for i, (pop, members) in enumerate(sorted(pops.items()), start=1):
        pop_area[pop] = i
        for p in members:
            p_area[p] = i

    edges = []

    def add(src, dst, relation, *, igp=None, area=0, srlg=None,
            devices=None, ifaces=None):
        edges.append({
            "edge_id": _hid(topology_id, relation, src, dst, srlg),
            "topology_id": topology_id,
            "src_entity": src, "dst_entity": dst, "relation": relation,
            "igp_cost": igp, "srlg_group": srlg, "area_id": int(area),
            "_devices": set(devices or [src, dst]),
            "_ifaces": set(ifaces or []),
        })

    # belongs_to (iface->device) + incident_to (device->iface) for every iface.
    for dev, v in inventory.items():
        for iface in v.get("interfaces", []):
            eid = f"{dev}:{iface}"
            add(eid, dev, "belongs_to", devices=[dev], ifaces=[(dev, iface)])
            add(dev, eid, "incident_to", devices=[dev], ifaces=[(dev, iface)])

    # ospf_adj + ldp_session: intra-POP P-P mesh and P-PE (cost 10, pop area).
    for pop, members in pops.items():
        area = pop_area[pop]
        ms = sorted(members)
        for i in range(len(ms)):
            for j in range(i + 1, len(ms)):
                add(ms[i], ms[j], "ospf_adj", igp=IGP_INTRA, area=area)
                add(ms[i], ms[j], "ldp_session")
        # PEs homed in this pop attach to every P in the pop.
        pop_idx = area
        for pe in sorted(p for p, idx in pe_pop.items() if idx == pop_idx):
            for p in ms:
                add(pe, p, "ospf_adj", igp=IGP_INTRA, area=area)
                add(pe, p, "ldp_session")

    # inter-POP P-P links (cost 100, backbone area 0). links list = pairs.
    for link in inter:
        pairs = link.get("links", [])
        srlg = link.get("srlg")
        for k in range(0, len(pairs) - 1, 2):
            (da, ia), (db, ib) = pairs[k], pairs[k + 1]
            add(da, db, "ospf_adj", igp=IGP_INTER, area=0,
                ifaces=[(da, ia), (db, ib)])
            add(da, db, "ldp_session", ifaces=[(da, ia), (db, ib)])

    # ibgp_session (PE<->PE loopbacks) — RR-aware if meta advertises reflectors,
    # else full mesh.
    rr = (meta.get("route_reflectors") or meta.get("rr")
          or (meta.get("knobs") or {}).get("rr") or [])
    pe_list = sorted(pes)
    if rr:
        for pe in pe_list:
            for r in rr:
                if pe != r:
                    add(pe, r, "ibgp_session")
    else:
        for i in range(len(pe_list)):
            for j in range(i + 1, len(pe_list)):
                add(pe_list[i], pe_list[j], "ibgp_session")

    # wg_tunnel edges from inventory tunnels ("src-dst").
    for dev in ces:
        for t in inventory[dev].get("tunnels", []):
            src, _, dst = t.partition("-")
            add(src, dst, "wg_tunnel",
                ifaces=[(src, "wg0"), (dst, "wg0")], devices=[src, dst])

    # shares_srlg — pairwise among members of each srlg group.
    for name, members in srlgs.items():
        ids = [f"{d}:{i}" for d, i in members]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                add(ids[i], ids[j], "shares_srlg", srlg=name,
                    devices=[members[i][0], members[j][0]],
                    ifaces=[tuple(members[i]), tuple(members[j])])

    return edges


def _edge_hit(edge, device, entity) -> bool:
    """Does a downing episode on (device, entity) take this edge down?"""
    if entity is not None:
        eid = f"{device}:{entity}"
        return (eid in (edge["src_entity"], edge["dst_entity"])
                or (device, entity) in edge["_ifaces"])
    # node/pop failure (no iface): any edge touching the device.
    return device in edge["_devices"]


def _intervals(downs, capture_start, capture_end):
    """Downtime windows -> complementary UP intervals. Last interval ends NULL."""
    cur = capture_start
    out = []
    for imp, end in sorted(downs):
        if imp > cur:
            out.append((cur, imp))       # up until the flap
        cur = max(cur, end)
    out.append((cur, None))              # still up -> NULL valid_to
    return out


def build_edges(topology_id, inventory, meta, ledger, capture_start, capture_end):
    edges = _static_edges(topology_id, inventory, meta)

    # collect downtime windows per edge_id from the ledger.
    downs = {}
    for ep in ledger or []:
        if ep.get("topology_id") not in (None, topology_id):
            continue
        downing = ep.get("kind") == "iface_down" or ep.get("fault_type") in DOWNING_FAULTS
        if not downing:
            continue
        imp = ep.get("t_impact")
        imp = ep.get("t_start") if imp is None else imp
        end = ep.get("t_end")
        if imp is None or end is None:
            continue
        dev, ent = ep.get("device"), ep.get("entity")
        for e in edges:
            if _edge_hit(e, dev, ent):
                downs.setdefault(e["edge_id"], []).append((float(imp), float(end)))

    rows = []
    for e in edges:
        e = {k: v for k, v in e.items() if not k.startswith("_")}
        for vf, vt in (_intervals(downs[e["edge_id"]], capture_start, capture_end)
                       if e["edge_id"] in downs else [(capture_start, None)]):
            rows.append({**e, "valid_from": _ts(vf), "valid_to": _ts(vt)})

    return _edges_frame(rows)


def _edges_frame(rows):
    cols = ["edge_id", "topology_id", "src_entity", "dst_entity", "relation",
            "valid_from", "valid_to", "igp_cost", "srlg_group", "area_id"]
    df = pd.DataFrame(rows, columns=cols)
    df["igp_cost"] = df["igp_cost"].astype("float32")
    df["srlg_group"] = df["srlg_group"].astype("object")
    df["area_id"] = df["area_id"].fillna(0).astype("int8")
    for c in ("valid_from", "valid_to"):
        # pd.to_datetime handles all-NaT columns (astype can't localise those).
        df[c] = pd.to_datetime(df[c], utc=True).astype("datetime64[us, UTC]")
    for c in ("edge_id", "topology_id", "src_entity", "dst_entity", "relation"):
        df[c] = df[c].astype("string")
    return df


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def _ospf_graph(inventory, meta):
    """Undirected weighted adjacency over P/PE nodes for SPF (from ospf_adj)."""
    g = {}
    for e in _static_edges("_", inventory, meta):
        if e["relation"] == "ospf_adj":
            a, b, w = e["src_entity"], e["dst_entity"], e["igp_cost"]
            g.setdefault(a, {})[b] = min(w, g.get(a, {}).get(b, w))
            g.setdefault(b, {})[a] = min(w, g.get(b, {}).get(a, w))
    return g


def _dijkstra(g, src):
    """Cheapest paths from src; returns {node: [ordered hops]}."""
    dist = {src: 0.0}
    prev = {}
    pq = [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, float("inf")):
            continue
        for v, w in g.get(u, {}).items():
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v], prev[v] = nd, u
                heapq.heappush(pq, (nd, v))
    paths = {}
    for node in dist:
        hops, cur = [], node
        while cur is not None:
            hops.append(cur)
            cur = prev.get(cur)
        paths[node] = list(reversed(hops))
    return paths


def _merge_windows(wins):
    """Union overlapping [a, b] windows -> sorted disjoint list (mirrors the
    interval union that build_edges' _intervals does over downtime)."""
    out = []
    for a, b in sorted(wins):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def build_paths(topology_id, inventory, meta, ledger, capture_start, capture_end):
    # DEFECT 2: paths.parquet is the INTERVAL-ENCODED path-SELECTION history, not
    # a static catalog. ponytail: the synthetic generator never runs
    # controller.py, so failover is DERIVED FROM THE FAULT LEDGER — the ground
    # truth of which tunnels degraded, i.e. exactly what the controller reroutes
    # on. Same interval style as build_edges: closed rows for reroute windows,
    # valid_to=NULL for the still-active tail.
    rows = []

    def add(pid, hops, ptype, vrf, vf, vt):
        rows.append({
            "path_id": pid,
            "topology_id": topology_id, "hop_sequence": list(hops),
            "path_type": ptype, "vrf": vrf, "sla_class": VRF_SLA[vrf],
            "valid_from": _ts(vf), "valid_to": _ts(vt),
        })

    # failover windows per CE device, harvested from the ledger.
    fail = {}
    for ep in ledger or []:
        if ep.get("topology_id") not in (None, topology_id):
            continue
        if ep.get("is_hard_negative"):           # near-miss stays under SLA -> no reroute
            continue
        inducing = (ep.get("kind") == "tunnel_ramp"
                    or ep.get("fault_type") in FAILOVER_FAULTS)
        if not inducing:
            continue
        imp = ep.get("t_impact")
        imp = ep.get("t_start") if imp is None else imp
        end = ep.get("t_end")
        if imp is None or end is None:
            continue
        fail.setdefault(ep.get("device"), []).append((float(imp), float(end)))

    # one logical wg path per (ce, vrf); path_id STABLE per (topology_id, ce, vrf)
    # (no hub in the id) so a tunnel's whole failover history groups by path_id.
    for dev, v in inventory.items():
        if v.get("site_type") not in ("branch", "dc", "hub"):
            continue
        dsts = [t.partition("-")[2] for t in v.get("tunnels", [])]
        if not dsts:
            continue
        preferred, alternates = dsts[0], dsts[1:]
        vrfs = [i.split("vrf_")[1] for i in v.get("interfaces", [])
                if i.startswith("vrf_") and i.split("vrf_")[1] in VRF_SLA] or ["CORP"]

        # clamp + merge this CE's reroute windows into the capture range.
        wins = _merge_windows([
            (max(a, capture_start), min(b, capture_end))
            for a, b in fail.get(dev, [])
            if min(b, capture_end) > max(a, capture_start)
        ])

        # timeline of (vf, vt, hub): on-preferred outside windows, rotate onto an
        # alternate hub inside each window. Last segment runs to capture_end.
        segs, cur, ai = [], capture_start, 0
        for ws, we in wins:
            if ws > cur:
                segs.append((cur, ws, preferred))
            alt = alternates[ai % len(alternates)] if alternates else preferred
            ai += 1
            segs.append((ws, we, alt))
            cur = we
        if cur < capture_end or not segs:
            segs.append((cur, capture_end, preferred))
        segs[-1] = (segs[-1][0], None, segs[-1][2])   # still active -> NULL valid_to

        for vrf in vrfs:
            pid = _hid(topology_id, "wg_tunnel", vrf, dev)
            for vf, vt, hub in segs:
                add(pid, [dev, hub], "wg_tunnel", vrf, vf, vt)

    # ospf_spf_path — cheapest PE->PE over the IGP. Underlay carries CORP. Lazy:
    # no IGP reroute modelling, so one still-active row per pair (valid_to=NULL).
    g = _ospf_graph(inventory, meta)
    pes = sorted(p for p in g if str(p).startswith("pe"))
    for i, a in enumerate(pes):
        sp = _dijkstra(g, a)
        for b in pes[i + 1:]:
            hops = sp.get(b)
            if hops and len(hops) >= 2:
                add(_hid(topology_id, "ospf_spf_path", "CORP", *hops),
                    hops, "ospf_spf_path", "CORP", capture_start, None)

    return _paths_frame(rows)


def _paths_frame(rows):
    cols = ["path_id", "topology_id", "hop_sequence", "path_type", "vrf",
            "sla_class", "valid_from", "valid_to"]
    df = pd.DataFrame(rows, columns=cols)
    for c in ("path_id", "topology_id", "path_type", "vrf", "sla_class"):
        df[c] = df[c].astype("string")
    for c in ("valid_from", "valid_to"):
        df[c] = pd.to_datetime(df[c], utc=True).astype("datetime64[us, UTC]")
    return df


# --------------------------------------------------------------------------- #
# writer
# --------------------------------------------------------------------------- #
def write_topology_and_paths(topologies_with_ledgers, edges_path, paths_path,
                             capture_start, capture_end):
    """Concat every topology's edges + paths, write both parquet files."""
    edge_frames, path_frames = [], []
    for topology_id, inventory, meta, ledger in topologies_with_ledgers:
        edge_frames.append(
            build_edges(topology_id, inventory, meta, ledger, capture_start, capture_end))
        path_frames.append(
            build_paths(topology_id, inventory, meta, ledger, capture_start, capture_end))
    pd.concat(edge_frames, ignore_index=True).to_parquet(edges_path, index=False)
    pd.concat(path_frames, ignore_index=True).to_parquet(paths_path, index=False)
    return str(edges_path), str(paths_path)


# --------------------------------------------------------------------------- #
# self-check
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import math
    import tempfile
    import os

    START, END = 1_700_000_000.0, 1_700_003_600.0
    T_IMPACT, T_END = 1_700_001_000.0, 1_700_001_600.0

    inventory = {
        "p1": {"site_type": "core", "interfaces": ["eth1", "eth4", "lo"], "tunnels": []},
        "p2": {"site_type": "core", "interfaces": ["eth1", "eth4", "lo"], "tunnels": []},
        "p5": {"site_type": "core", "interfaces": ["eth1", "eth4", "lo"], "tunnels": []},
        "p6": {"site_type": "core", "interfaces": ["eth1", "eth4", "lo"], "tunnels": []},
        "pe1": {"site_type": "pe", "interfaces": ["eth1", "lo"], "tunnels": []},
        "pe3": {"site_type": "pe", "interfaces": ["eth1", "lo"], "tunnels": []},
        "ce_branch1": {"site_type": "branch",
                       "interfaces": ["eth1", "wg0", "vrf_CORP", "vrf_VOICE"],
                       "tunnels": ["ce_branch1-ce_hub1", "ce_branch1-ce_hub2"]},
        "ce_hub1": {"site_type": "hub", "interfaces": ["eth1", "wg0"], "tunnels": []},
        "ce_hub2": {"site_type": "hub", "interfaces": ["eth1", "wg0"], "tunnels": []},
    }
    meta = {
        "pops": {"pop1": ["p1", "p2"], "pop2": ["p5", "p6"]},
        "pe_pop": {"pe1": 1, "pe3": 2},
        "srlgs": {"srlg_x": [["p1", "eth4"], ["p5", "eth4"]]},
        "inter_pop_links": [{"pop_a": 1, "pop_b": 2, "srlg": "srlg_x",
                             "links": [["p1", "eth4"], ["p5", "eth4"]]}],
    }
    # one iface_down on the inter-POP link iface p1/eth4 (drives an edge flap),
    # one tunnel_degrade on ce_branch1 (drives a wg hub failover), and one hard
    # negative that must be ignored.
    ledger = [
        {"topology_id": "T", "kind": "iface_down", "fault_type": "link_flap",
         "device": "p1", "entity": "eth4",
         "t_start": T_IMPACT, "t_impact": T_IMPACT, "t_end": T_END},
        {"topology_id": "T", "kind": "tunnel_ramp", "fault_type": "tunnel_degrade",
         "device": "ce_branch1", "entity": "wg0", "is_hard_negative": False,
         "t_start": T_IMPACT, "t_impact": T_IMPACT, "t_end": T_END},
        {"topology_id": "T", "kind": "tunnel_ramp", "fault_type": "tunnel_degrade",
         "device": "ce_branch1", "entity": "wg0", "is_hard_negative": True,
         "t_start": START + 100, "t_impact": START + 100, "t_end": START + 200},
    ]

    edges = build_edges("T", inventory, meta, ledger, START, END)
    paths = build_paths("T", inventory, meta, ledger, START, END)

    # schema/type asserts
    assert {"valid_from", "valid_to"} <= set(edges.columns)
    assert str(edges["valid_from"].dtype) == "datetime64[us, UTC]"
    assert str(edges["valid_to"].dtype) == "datetime64[us, UTC]"

    # interval-flap encoding: p1/eth4 ospf_adj went down -> ends at t_impact + re-up row.
    downed = edges[(edges["src_entity"] == "p1:eth4")
                   | (edges["dst_entity"] == "p1:eth4")]
    ended = downed[downed["valid_to"].notna()]
    assert len(ended) >= 1, "no closed interval for downed edge"
    for eid in ended["edge_id"].unique():
        grp = edges[edges["edge_id"] == eid]
        assert grp["valid_to"].isna().any(), f"no re-up row for {eid}"
        reup = grp[grp["valid_from"] == _ts(T_END)]
        assert len(reup) >= 1, f"re-up row not at t_end for {eid}"

    # igp_cost domain
    for v in edges["igp_cost"].dropna().unique():
        assert v in (10.0, 100.0), v
    assert edges["igp_cost"].isna().any()  # NULL for non-igp relations

    # paths: ordered list hops, restricted path_type, sla mapping
    assert (paths["path_type"].isin(["wg_tunnel", "ospf_spf_path"])).all()
    for hs in paths["hop_sequence"]:
        assert isinstance(hs, list) and len(hs) >= 2
    assert (paths["path_type"] == "ospf_spf_path").any(), "no SPF path built"
    assert (paths["path_type"] == "wg_tunnel").any(), "no tunnel path built"

    # DEFECT 2: interval-encoded SELECTION history, not a static catalog.
    assert paths["valid_to"].isna().any(), "no still-active (NULL valid_to) row"
    assert paths["valid_to"].notna().any(), "no closed interval (real valid_to)"
    assert paths["path_id"].duplicated().any(), "no path_id grouping >1 row"
    # the rerouted group must change hub across its interval rows.
    dup_id = paths["path_id"][paths["path_id"].duplicated()].iloc[0]
    grp = paths[paths["path_id"] == dup_id]
    assert len({hs[1] for hs in grp["hop_sequence"]}) >= 2, "reroute did not change hub"
    # closed-interval valid_to values are real UTC timestamps.
    assert paths.loc[paths["valid_to"].notna(), "valid_to"].map(
        lambda t: isinstance(t, pd.Timestamp)).all()

    # write + reread parquet
    d = tempfile.mkdtemp()
    ep, pp = os.path.join(d, "e.parquet"), os.path.join(d, "p.parquet")
    write_topology_and_paths([("T", inventory, meta, ledger)], ep, pp, START, END)
    e2, p2 = pd.read_parquet(ep), pd.read_parquet(pp)
    assert len(e2) == len(edges) and len(p2) == len(paths)
    assert isinstance(p2["hop_sequence"].iloc[0].tolist()
                      if hasattr(p2["hop_sequence"].iloc[0], "tolist")
                      else p2["hop_sequence"].iloc[0], list)

    print("edge relation counts:")
    print(edges["relation"].value_counts().to_string())
    print("\npath_type counts:")
    print(paths["path_type"].value_counts().to_string())
    print(f"\ninterval-flap OK: {len(ended)} closed row(s), "
          f"{len(edges[edges['valid_to'].isna()])} still-up rows.")
    print(f"path selection: {int(paths['valid_to'].isna().sum())} still-active (NULL) / "
          f"{int(paths['valid_to'].notna().sum())} closed reroute row(s). self-check PASS")
