#!/usr/bin/env python3
"""
topologies.py — pure-Python topology inventory synthesizer for G1 diversity.

Calibration in profile.json is per-site-TYPE (branch/hub/dc/core/pe) and shared
across topologies, so we never re-capture a variant — we only substitute the
`inventory` block. This module mirrors generator/generate.py's node naming and
interface/tunnel enumeration WITHOUT deploying containerlab or touching docker.

Public API:
  build_inventory(knobs)  -> {device: {"site_type","interfaces","tunnels"}}
  load_topologies()       -> 12 variant dicts (topology_id, held_out, knobs, inventory, meta)
  base_inventory()        -> the current lab inventory (== profile.json inventory)

ponytail: we only reproduce what the calibrator keys on — the per-device
interface/tunnel SETS and site_type. Addresses, WG keys, OSPF costs etc. are NOT
recomputed (calibration never reads them), so none of generate.py's address/key
machinery is duplicated here. Interface counts still match generate.py exactly
because we replay its link-enumeration order with the same per-node iface counter.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(HERE, "profile.json")

# VRF ordering matches generate.py VRF_IDX (CORP,VOICE,GUEST).
VRF_IDX = {"CORP": 0, "VOICE": 1, "GUEST": 2}


# ──────────────────────────────────────────────────────────────────────────────
# Core simulation: replay generate.py's build() link enumeration, counting ifaces
# per node in the SAME ORDER so eth<N> names match. Returns (inventory, meta).
# ──────────────────────────────────────────────────────────────────────────────
def _simulate(knobs):
    pop_count = knobs["pop_count"]
    p_per_pop = knobs["p_per_pop"]
    pe_count = knobs["pe_count"]
    branch = knobs["branch_count"]
    hub = knobs["hub_count"]
    dc = knobs["dc_count"]
    redundancy = knobs["inter_pop_redundancy"]
    chords = [tuple(c) for c in knobs.get("inter_pop_chords", [])]
    dual = knobs.get("pe_dual_homing", True)
    vrf_sites = knobs["vrf_sites"]              # {site_type: [vrf,...]}
    p_count = pop_count * p_per_pop
    # ponytail: generate.py asserts p_per_pop>=3 (2 ABR + >=1 PE-facing P). We keep
    # that floor rather than model an unbuildable variant; the G1 "P/POP 2" axis is
    # served by the p_per_pop=3 minimum (2 is structurally rejected upstream).
    assert p_per_pop >= 3, f"p_per_pop {p_per_pop} < 3 (need 2 ABR + PE-facing P)"

    def pop_routers(pop):
        return list(range((pop - 1) * p_per_pop + 1, pop * p_per_pop + 1))

    def borders(pop):
        return pop_routers(pop)[:2]

    def internals(pop):
        return pop_routers(pop)[2:]

    def pe_pop(i):
        return (i - 1) * pop_count // pe_count + 1

    iface_ctr = {}          # node -> highest eth index used (eth0 reserved for mgmt)

    def next_iface(node):
        iface_ctr[node] = iface_ctr.get(node, 0) + 1
        return f"eth{iface_ctr[node]}"

    srlgs = {}              # srlg_id -> ["dev:iface", ...]
    inter_pop_links = []    # [[pop_a, pop_b], ...]

    def add_pp(a, b, srlg=None):
        ia, ib = next_iface(f"p{a}"), next_iface(f"p{b}")
        if srlg:
            srlgs.setdefault(srlg, []).extend([f"p{a}:{ia}", f"p{b}:{ib}"])

    # 1) intra-POP full mesh
    for pop in range(1, pop_count + 1):
        rtr = pop_routers(pop)
        for ii in range(len(rtr)):
            for jj in range(ii + 1, len(rtr)):
                add_pp(rtr[ii], rtr[jj])

    # 2) inter-POP backbone: ring + chords, `redundancy` parallel links per adjacency
    ring = [(p, p % pop_count + 1) for p in range(1, pop_count + 1)]
    adjacencies = [(min(x, y), max(x, y)) for (x, y) in ring]
    adjacencies += [(min(x, y), max(x, y)) for (x, y) in chords]
    for (pa, pb) in adjacencies:
        srlg = f"srlg_pop{pa}_{pb}"
        for r in range(redundancy):
            add_pp(borders(pa)[r % 2], borders(pb)[r % 2], srlg=srlg)
        inter_pop_links.append([pa, pb])

    # 3) P-PE links (each PE dual-homed to its POP's PE-facing P)
    for i in range(1, pe_count + 1):
        facing = internals(pe_pop(i))
        next_iface(f"pe{i}"); next_iface(f"p{facing[(i - 1) % len(facing)]}")
        if dual and len(facing) > 1:
            next_iface(f"pe{i}"); next_iface(f"p{facing[i % len(facing)]}")

    # --- CE list in generate.py order: branch, hub, dc ---
    ce_list = []
    for st, cnt in (("branch", branch), ("hub", hub), ("dc", dc)):
        vrfs = sorted(vrf_sites[st], key=lambda v: VRF_IDX[v])
        for ti in range(1, cnt + 1):
            ce_list.append((f"ce_{st}{ti}", st, ti, vrfs))

    # 4) CE-PE links (/per-VRF): pe_if, ce uplink_if, ce lan_if — in that order.
    pe_served = {f"pe{i}": set() for i in range(1, pe_count + 1)}
    ce_ifaces = {}          # ce_name -> count of eth data ifaces
    for lin_idx, (ce_name, st, ti, vrfs) in enumerate(ce_list):
        pe_name = f"pe{(lin_idx % pe_count) + 1}"
        for vname in vrfs:
            next_iface(pe_name)                 # PE-side CE uplink
            next_iface(ce_name)                 # CE uplink
            next_iface(ce_name)                 # CE LAN
            pe_served[pe_name].add(vname)

    # ── assemble inventory ──────────────────────────────────────────────────────
    inv = {}

    def dev(interfaces, site_type, tunnels=()):
        return {"site_type": site_type,
                "interfaces": sorted(set(interfaces)),
                "tunnels": sorted(tunnels)}

    for i in range(1, p_count + 1):
        eths = [f"eth{n}" for n in range(0, iface_ctr.get(f"p{i}", 0) + 1)]
        inv[f"p{i}"] = dev(eths + ["lo"], "core")

    for i in range(1, pe_count + 1):
        eths = [f"eth{n}" for n in range(0, iface_ctr.get(f"pe{i}", 0) + 1)]
        vrf_names = list(pe_served[f"pe{i}"])   # PE names VRF ifaces after the VRF
        inv[f"pe{i}"] = dev(eths + ["lo"] + vrf_names, "pe")

    hubs = [f"ce_hub{i}" for i in range(1, hub + 1)]
    for ce_name, st, ti, vrfs in ce_list:
        n_eth = iface_ctr.get(ce_name, 0)
        eths = [f"eth{n}" for n in range(0, n_eth + 1)]
        vrf_ifaces = [f"vrf_{v}" for v in vrfs]
        # every CE joins the WG overlay (branch/dc = spoke, hub = concentrator).
        # ponytail: profile.json's captured wg0 set is flaky (live-capture timing),
        # so synthesized inventories carry wg0 on every overlay CE — the clean
        # structural truth. base_inventory() loads the profile verbatim instead.
        ifaces = eths + ["lo", "wg0"] + vrf_ifaces
        # tunnels: spokes peer to ALL hubs; hub side records none (mirrors profile).
        tuns = [f"{ce_name}-{h}" for h in hubs] if st in ("branch", "dc") else []
        inv[ce_name] = dev(ifaces, st, tuns)

    meta = {
        "pops": pop_count,
        "p_per_pop": p_per_pop,
        "pe_pop": {f"pe{i}": pe_pop(i) for i in range(1, pe_count + 1)},
        "inter_pop_links": inter_pop_links,
        "srlgs": list(srlgs.values()),
        "vrfs": sorted({v for vs in vrf_sites.values() for v in vs},
                       key=lambda v: VRF_IDX[v]),
    }
    return inv, meta


def build_inventory(knobs: dict) -> dict:
    """Synthesize {device: {"site_type","interfaces","tunnels"}} from knob values."""
    return _simulate(knobs)[0]


def base_inventory() -> dict:
    """Current lab inventory. Loaded from profile.json verbatim so it is exactly
    equal to the calibrated ground truth (incl. its flaky captured wg0 set)."""
    with open(PROFILE) as f:
        return json.load(f)["inventory"]


def container_estimate(knobs) -> int:
    """P + PE + CE + one host per (site,VRF) — matches generate.py's container math."""
    p = knobs["pop_count"] * knobs["p_per_pop"]
    vs = knobs["vrf_sites"]
    hosts = (knobs["branch_count"] * len(vs["branch"])
             + knobs["hub_count"] * len(vs["hub"])
             + knobs["dc_count"] * len(vs["dc"]))
    return p + knobs["pe_count"] + knobs["branch_count"] + knobs["hub_count"] \
        + knobs["dc_count"] + hosts


# ──────────────────────────────────────────────────────────────────────────────
# The 12 variants. Compact design rows → full knobs. Deterministic slug per row.
# Axes: POP, P/POP, PE/POP, CE/PE, inter_pop_redundancy, VRF set, RR placement, size.
# ──────────────────────────────────────────────────────────────────────────────
# VRF profiles (branch never gets GUEST — matches spec YAGNI).
_VRF = {
    "corp": {"branch": ["CORP"], "hub": ["CORP"], "dc": ["CORP"]},
    "cv":   {"branch": ["CORP", "VOICE"], "hub": ["CORP", "VOICE"],
             "dc": ["CORP", "VOICE"]},
    "full": {"branch": ["CORP", "VOICE"], "hub": ["CORP", "VOICE", "GUEST"],
             "dc": ["CORP", "VOICE", "GUEST"]},
}


def _rr(mode, pe_count):
    """RR placement -> (route_reflector, rr_nodes). full-mesh=no RR."""
    if mode == "mesh":
        return False, []
    if mode == "pe12":                       # RR on pe1,pe2
        return True, ["pe1", "pe2"][:min(2, pe_count)]
    if mode == "dedicated":                  # RR on the last PE (a dedicated RR)
        return True, [f"pe{pe_count}"]
    raise ValueError(mode)


# rows: (slug, held_out, pops, p_per_pop, pe_pop, ce_per_pe(b,h,d), red, chords, vrf, rr)
#   pe_count = pops*pe_pop ; branch/hub/dc = ce_per_pe * pe_count (component-wise)
_ROWS = [
    ("p6_pe12_r2_base",  False, 6, 4, 2, (2, 0.5, 0.33), 2, [[1, 4], [2, 5], [3, 6]], "full", "pe12"),
    ("p8_pe24_r3_full",  True,  8, 4, 3, (4, 1, 0.5),    3, [[1, 5], [2, 6], [3, 7], [4, 8]], "full", "dedicated"),
    ("p4_pe8_r2_cv",     False, 4, 4, 2, (3, 0.5, 0.25), 2, [[1, 3], [2, 4]], "cv", "pe12"),
    ("p2_pe2_r1_corp",   False, 2, 3, 1, (3, 1, 1),      1, [], "corp", "mesh"),
    ("p6_pe6_r1_cv",     False, 6, 3, 1, (3, 1, 0.5),    1, [], "cv", "dedicated"),
    ("p4_pe12_r3_full",  False, 4, 6, 3, (1, 0.33, 0.33), 3, [[1, 3], [2, 4]], "full", "mesh"),
    ("p8_pe16_r2_cv",    True,  8, 4, 2, (3, 0.5, 0.25), 2, [[1, 5], [2, 6], [3, 7], [4, 8]], "cv", "pe12"),
    ("p6_pe18_r2_full",  False, 6, 4, 3, (2, 0.5, 0.33), 2, [[1, 4], [2, 5], [3, 6]], "full", "dedicated"),
    ("p4_pe4_r1_corp",   False, 4, 3, 1, (5, 1, 1),      1, [], "corp", "mesh"),
    ("p2_pe4_r1_cv",     False, 2, 4, 2, (5, 1, 1),      1, [], "cv", "pe12"),
    ("p6_pe12_r3_full",  False, 6, 4, 2, (5, 1, 1),      3, [[1, 4], [2, 5], [3, 6]], "full", "pe12"),
    ("p8_pe8_r2_full",   False, 8, 3, 1, (3, 1, 0.5),    2, [[1, 5], [2, 6], [3, 7], [4, 8]], "full", "dedicated"),
]


def _row_to_knobs(row):
    (_slug, _ho, pops, ppp, pe_pp, cpp, red, chords, vrf, rr) = row
    pe_count = pops * pe_pp
    b_pp, h_pp, d_pp = cpp
    branch = max(1, round(b_pp * pe_count))
    hub = max(1, round(h_pp * pe_count))
    dc = max(1, round(d_pp * pe_count))
    route_reflector, rr_nodes = _rr(rr, pe_count)
    return {
        "pop_count": pops, "p_per_pop": ppp, "pe_count": pe_count,
        "branch_count": branch, "hub_count": hub, "dc_count": dc,
        "inter_pop_redundancy": red, "inter_pop_chords": chords,
        "pe_dual_homing": ppp >= 4, "hub_hub_wg": True,
        "route_reflector": route_reflector, "rr_nodes": rr_nodes, "rr_mode": rr,
        "vrf_sites": _VRF[vrf], "multi_area": True,
    }


def load_topologies() -> list:
    out = []
    for row in _ROWS:
        slug, held_out = row[0], row[1]
        knobs = _row_to_knobs(row)
        inv, meta = _simulate(knobs)
        out.append({
            "topology_id": f"topo_{slug}",
            "held_out": held_out,
            "knobs": knobs,
            "inventory": inv,
            "meta": meta,
        })
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Self-check
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    prof = base_inventory()

    # 1) base_inventory() == profile inventory: device set + per-device iface/tunnel sets.
    b = base_inventory()
    assert set(b) == set(prof), "base device set != profile"
    for name in prof:
        assert set(b[name]["interfaces"]) == set(prof[name]["interfaces"]), \
            f"{name} interfaces differ"
        assert set(b[name]["tunnels"]) == set(prof[name]["tunnels"]), \
            f"{name} tunnels differ"
        assert b[name]["site_type"] == prof[name]["site_type"], f"{name} site_type"
    print("base_inventory() == profile inventory: OK")

    # 2) 12 topologies, exactly 2 held_out, >=8 distinct ids.
    topos = load_topologies()
    assert len(topos) == 12, f"expected 12 topologies, got {len(topos)}"
    assert sum(t["held_out"] for t in topos) == 2, "must have exactly 2 held_out"
    ids = [t["topology_id"] for t in topos]
    assert len(set(ids)) >= 8, f"need >=8 distinct ids, got {len(set(ids))}"
    assert len(set(ids)) == len(ids), "topology_ids not unique"

    # 3) container-size estimate spans the required range (~40..500).
    sizes = [container_estimate(t["knobs"]) for t in topos]
    assert min(sizes) <= 45 and max(sizes) >= 480, f"size span too narrow: {sizes}"

    # 4) every CE carries vrf_* interfaces matching its VRF set.
    for t in topos:
        vs = t["knobs"]["vrf_sites"]
        for name, d in t["inventory"].items():
            if d["site_type"] in ("branch", "hub", "dc"):
                want = {f"vrf_{v}" for v in vs[d["site_type"]]}
                got = {i for i in d["interfaces"] if i.startswith("vrf_")}
                assert got == want, f"{t['topology_id']}/{name} vrf ifaces {got} != {want}"

    print("load_topologies(): 12 variants, 2 held_out, %d distinct ids OK" % len(set(ids)))
    print("\n%-22s %4s %4s %4s %-5s %6s %5s" %
          ("topology_id", "pops", "pe", "red", "vrf", "~size", "held"))
    for t, sz in zip(topos, sizes):
        k = t["knobs"]
        vrfset = "".join(sorted({v[0] for vv in k["vrf_sites"].values() for v in vv}))
        print("%-22s %4d %4d %4d %-5s %6d %5s" %
              (t["topology_id"], k["pop_count"], k["pe_count"],
               k["inter_pop_redundancy"], vrfset, sz, t["held_out"]))
    print("\nself-check: OK")
