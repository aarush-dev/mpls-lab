"""PA-G1: emit topology_edges.parquet for the LIVE lab, schema-matched to the
predictive-analysis model's GraphSnapshotCache (bah_predictive_analysis
.../src/data/graph.py).

Why this exists: the graph model trained on 12 synthetic topologies; the live lab
(24 P / 12 PE / 6 POP) is a DIFFERENT topology_id, absent from the training
`topology_edges.parquet`. Without a matching edge set, `snapshot_edges()` returns
empty and the graph encoder no-ops -> the graph-fused heads get no neighbour
context. This regenerates the live topology's device-level edges so the model can
message-pass over the real lab.

Reuse, not reinvent: the synthetic dataset's own edge builder
(`synthetic/topology_paths._static_edges`) already emits the exact relation
vocabulary + column schema the model reads. We drive it with the live lab's
`topology/topology-meta.json` (which already carries pops / pe_pop / srlgs /
inter_pop_links in the shape `_static_edges` wants) and a minimal device
inventory. The graph is STATIC here (no fault-flap intervals): every edge is
valid_from=epoch0, valid_to=NULL (open) -> always valid at live `now`, so the
snapshot query at any live timestamp resolves the full topology.

ponytail: only device->device relations survive snapshot_edges (it collapses
`dev:iface`->dev and drops self-loops), so belongs_to/incident_to and per-iface
lists are irrelevant to the served graph -- the minimal inventory below is enough.
wg_tunnel (CE<->hub) edges need a CE/tunnel inventory the meta doesn't carry; the
core relations that drive the lab's fault types (ospf/ldp/ibgp/srlg) are complete.
Upgrade path: pass a CE inventory to add wg_tunnel edges if CE-side faults matter.

Run:  python3 dataapi/topology_edges.py            # -> dataapi/graph_data/topology_edges.parquet
Self-check: python3 dataapi/topology_edges.py --check
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "synthetic"))
import topology_paths as tp  # noqa: E402  (the synthetic edge builder we reuse)

META_PATH = os.path.join(ROOT, "topology", "topology-meta.json")
OUT_DIR = os.path.join(HERE, "graph_data")
OUT_PATH = os.path.join(OUT_DIR, "topology_edges.parquet")

# Stable id for the single live topology. The copilot passes this same string as
# `topology_id` on every snapshot request (one lab = one topology).
TOPOLOGY_ID = "live_lab"

# valid_from for the static graph: the model compares valid_from(us) <= ts_us; use
# the parquet's own [us,UTC] cast (via _edges_frame) so units match the query
# (PA-G6). epoch 2000-01-01 is safely before any live capture; valid_to=NULL open.
_VALID_FROM = pd.Timestamp("2000-01-01", tz="UTC")


def _inventory(meta: dict) -> dict:
    """Device inventory for `_static_edges`: site_type per device (+ CE tunnels).

    P routers = core, PE = pe drive ospf/ldp/ibgp/srlg. CE spokes/hubs + their
    wg_tunnel edges are ADDED here (PA-#131): the model trained with wg_tunnel in
    its relation set, so without CE<->hub edges every live CE/tunnel entity collapses
    to a device node with ZERO edges -> the graph refinement gives CE windows no
    neighbour context (self-pool only), unlike training. Reuse controller/topo to
    derive the exact live spoke<->hub tunnels (same node naming the generator uses)."""
    inv: dict[str, dict] = {}
    for members in meta.get("pops", {}).values():
        for p in members:
            inv[p] = {"site_type": "core"}
    for pe in meta.get("pe_pop", {}):
        inv[pe] = {"site_type": "pe"}
    # CE spokes + hubs + wg_tunnel edges (spoke<->every hub). _static_edges reads
    # inventory[dev]["tunnels"] as "spoke-hub" strings for the branch/dc/hub tier.
    try:
        sys.path.insert(0, os.path.join(ROOT, "controller"))
        import topo as _topo
        model = _topo.build_model()
        hubs = [h["node"] for h in model["hubs"]]
        for h in model["hubs"]:
            inv[h["node"]] = {"site_type": "hub", "tunnels": []}
        for sp in model["spokes"]:
            inv[sp["node"]] = {"site_type": sp["site_type"],
                               "tunnels": [f'{sp["node"]}-{hub}' for hub in hubs]}
    except Exception as e:  # core edges still valid; CE nodes just stay edgeless
        print(f"warn: CE inventory skipped ({e!r}); wg_tunnel edges absent", file=sys.stderr)
    return inv


def build_live_edges(meta_path: str = META_PATH, out_path: str = OUT_PATH,
                     topology_id: str = TOPOLOGY_ID) -> pd.DataFrame:
    """Build + write the live topology's device-level edge set. Returns the frame."""
    meta = json.load(open(meta_path))
    inventory = _inventory(meta)
    raw = tp._static_edges(topology_id, inventory, meta)   # reuse the generator
    rows = []
    for e in raw:
        e = {k: v for k, v in e.items() if not k.startswith("_")}  # strip fault-match sets
        e["valid_from"] = _VALID_FROM
        e["valid_to"] = pd.NaT                              # open interval -> always valid
        rows.append(e)
    df = tp._edges_frame(rows)                              # canonical cols + dtypes
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_parquet(out_path, index=False)
    return df


def _check():
    """Self-check: build, then confirm GraphSnapshotCache resolves live devices at
    a live 'now'. Proves the edges are non-empty and the interval/unit math holds."""
    df = build_live_edges()
    dev_rel = df[df["src_entity"].map(lambda s: ":" not in str(s))
                 & df["dst_entity"].map(lambda s: ":" not in str(s))]
    assert len(dev_rel) > 0, "no device-device edges produced"
    assert set(df["relation"]) >= {"ospf_adj", "ldp_session", "ibgp_session"}, \
        f"missing core relations: {set(df['relation'])}"
    # simulate a snapshot query at a live 'now' the way GraphSnapshotCache does.
    import numpy as np
    vf = df["valid_from"].astype("int64").to_numpy()
    now_us = int(pd.Timestamp.utcnow().value // 1000)      # us
    assert (vf <= now_us).all(), "some edges not yet valid at now (unit/interval bug)"
    print(f"OK topology_id={TOPOLOGY_ID} rows={len(df)} "
          f"device-device={len(dev_rel)} relations={sorted(set(df['relation']))}")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    if "--check" in sys.argv:
        _check()
    else:
        d = build_live_edges()
        print(f"wrote {OUT_PATH} rows={len(d)} relations={sorted(set(d['relation']))}")
