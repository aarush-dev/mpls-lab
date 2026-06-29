#!/usr/bin/env python3
"""
generate.py — topology generator for the air-gapped SD-WAN-over-MPLS NOC lab.

Reads topology-spec.yaml, derives EVERY address from node indices per
DOCS/SPEC-NOTES.md (nothing hardcoded per-node), renders Jinja2 templates, and
writes clab.yml + per-node config dirs to /root/LAB/topology/.

Run:   python3 generate.py
Check: python3 generate.py --check   (asserts no address collisions / missing files)

Deps: jinja2, PyYAML (stdlib otherwise).
"""
import os
import sys
import json
import subprocess

import yaml
from jinja2 import Environment, FileSystemLoader

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "..", "topology-spec.yaml")
TEMPLATES = os.path.join(HERE, "templates")
OUT = os.path.join(HERE, "..", "topology")
# ponytail: persist WG keypairs so re-runs are idempotent (keys stable across
# regeneration for an unchanged spec — avoids re-keying live tunnels on re-run).
WG_KEYS_FILE = os.path.join(HERE, ".wg-keys.json")

VRF_IDX = {"CORP": 0, "VOICE": 1, "GUEST": 2}
# ponytail: VRF table numbers match VRF routing table IDs.
# CORP=10, VOICE=20, GUEST=30 — matches rd_community last octet and is the
# canonical table scheme for this lab (so `ip route show table 10` == CORP).
VRF_TABLE = {"CORP": 10, "VOICE": 20, "GUEST": 30}
DSCP_VAL = {"EF": 46, "AF31": 26, "BE": 0}


# ──────────────────────────────────────────────────────────────────────────────
# Per-site WAN geography → eth0 netem (SINGLE SOURCE of the latency/jitter/loss
# model). One per-site root netem on each CE's mgmt/transport veth (eth0) delays
# BOTH the WG tunnels AND the telemetry transport — realistic, since NOC telemetry
# rides the same WAN. The controller MEASURES the resulting RTT (ping over wg0)
# instead of re-deriving it, so this formula must live in exactly one place: here.
#
# One-way baseline = site_floor[site_type] (access tier: dc near, hub mid, branch
# far) + deterministic per-spoke spread (golden-ratio low-discrepancy, so sites
# differ but are stable across regenerations). Jitter and loss scale off delay.
#
# ponytail: a closed-form geography proxy, not a geocoded fiber matrix. Bounds are
#   enforced so SNMP/IPFIX/syslog still flow (small scrape gaps are realistic).
# ──────────────────────────────────────────────────────────────────────────────
NETEM_FLOOR_MS = {"dc": 5.0, "hub": 8.0, "branch": 18.0}
NETEM_SPREAD_MS = {"dc": 12.0, "hub": 14.0, "branch": 38.0}


def site_netem(site_type, idx):
    """Return (delay_ms, jitter_ms, loss_pct) for a CE's eth0 root netem.

    Bounded: delay ≤ 60ms, jitter ≤ 0.3*delay, loss ≤ 1.0% (so telemetry on the
    same transport keeps flowing). Deterministic from (site_type, idx).
    """
    floor = NETEM_FLOOR_MS.get(site_type, 12.0)
    spread = NETEM_SPREAD_MS.get(site_type, 20.0)
    frac = (idx * 0.6180339887) % 1.0          # golden-ratio low-discrepancy spread
    delay = min(60.0, floor + frac * spread)
    jitter = min(0.3 * delay, 0.12 * delay + 0.3)  # ~12% of delay, capped at 0.3*d
    loss = min(1.0, 0.02 + frac * 0.4)          # tiny, site-varying, ≤1.0%
    return delay, jitter, loss


# ──────────────────────────────────────────────────────────────────────────────
# WireGuard key generation — shell out to `wg` binary (correctness-critical).
# ponytail: pure-python x25519 produced wrong pubkeys (RFC 7748 vector 2 fail).
# `wg` is not on the host; run it inside frr-node:latest (already pulled).
# Keys persisted to .wg-keys.json so re-runs are idempotent.
# ──────────────────────────────────────────────────────────────────────────────
def _wg_genkey_via_docker():
    """Generate a WG keypair using frr-node:latest container."""
    priv = subprocess.check_output(
        ["docker", "run", "--rm", "frr-node:latest", "wg", "genkey"],
        stderr=subprocess.DEVNULL,
    ).decode().strip()
    pub = subprocess.check_output(
        ["docker", "run", "--rm", "frr-node:latest", "sh", "-c",
         f"echo '{priv}' | wg pubkey"],
        stderr=subprocess.DEVNULL,
    ).decode().strip()
    return priv, pub


def wg_keypair(node_name: str, cache: dict):
    """Return (priv, pub) for node_name, generating+caching if missing."""
    if node_name not in cache:
        priv, pub = _wg_genkey_via_docker()
        cache[node_name] = {"priv": priv, "pub": pub}
    return cache[node_name]["priv"], cache[node_name]["pub"]


def load_wg_cache():
    if os.path.isfile(WG_KEYS_FILE):
        with open(WG_KEYS_FILE) as f:
            return json.load(f)
    return {}


def save_wg_cache(cache):
    with open(WG_KEYS_FILE, "w") as f:
        json.dump(cache, f, indent=2)


# ──────────────────────────────────────────────────────────────────────────────
# Topology model
# ──────────────────────────────────────────────────────────────────────────────
def build(spec):
    k = spec["knobs"]
    addr = spec["addressing"]
    provider_as = k["provider_as"]
    p_count, pe_count = k["p_count"], k["pe_count"]
    branch, hub, dc = k["branch_count"], k["hub_count"], k["dc_count"]

    # --- POP / multi-area core knobs ---
    pop_count = k.get("pop_count", 1)
    p_per_pop = k.get("p_per_pop", p_count)
    multi_area = k.get("multi_area", False)
    cost_intra = k.get("igp_cost_intra", 10)
    cost_inter = k.get("igp_cost_inter", 100)
    redundancy = k.get("inter_pop_redundancy", 2)
    chords = [tuple(c) for c in k.get("inter_pop_chords", [])]
    assert p_count == pop_count * p_per_pop, \
        f"p_count {p_count} != pop_count {pop_count} * p_per_pop {p_per_pop}"
    assert p_per_pop >= 3, "need >=3 P per POP (2 ABR + >=1 PE-facing)"

    def pop_of(p):                       # 1-based POP id for P router index p
        return (p - 1) // p_per_pop + 1

    def pop_routers(pop):                # P indices in a POP
        return list(range((pop - 1) * p_per_pop + 1, pop * p_per_pop + 1))

    def borders(pop):                    # first 2 P of a POP = ABRs (area-0 facing)
        return pop_routers(pop)[:2]

    def internals(pop):                  # remaining P = PE-facing, pure area-K
        return pop_routers(pop)[2:]

    # area a P-loopback / link belongs to: its POP area (1..pop_count), or 0 if
    # single-area mode. Backbone (inter-POP + ABR-ABR) links are forced to area 0.
    def pop_area(pop):
        return pop if multi_area else 0

    # node name + interface-index bookkeeping. eth index per node, eth0 reserved
    # by clab as mgmt; data links start at eth1.
    iface_ctr = {}

    def next_iface(node):
        iface_ctr[node] = iface_ctr.get(node, 0) + 1
        return f"eth{iface_ctr[node]}"

    nodes = {}     # name -> dict(role, loopback, core_links, ce_links, ...)
    links = []     # clab link endpoint pairs
    all_ips = []   # (ip, owner) for collision check

    def reg_ip(ip, owner):
        all_ips.append((ip, owner))

    def pe_pop(i):                       # contiguous PE→POP assignment (2 per POP @ 12/6)
        return (i - 1) * pop_count // pe_count + 1

    # --- P routers (loopback lives in its POP area) ---
    for i in range(1, p_count + 1):
        lo = f"10.255.1.{i}"
        nodes[f"p{i}"] = dict(role="P", loopback=lo, core_links=[], ce_links=[],
                              pop=pop_of(i), ospf_area=pop_area(pop_of(i)),
                              is_abr=(i in borders(pop_of(i))))
        reg_ip(lo, f"p{i}.lo")

    # --- PE routers (loopback lives in its POP area) ---
    for i in range(1, pe_count + 1):
        lo = f"10.255.2.{i}"
        nodes[f"pe{i}"] = dict(role="PE", loopback=lo, core_links=[], ce_links=[],
                               vrfs=[], pop=pe_pop(i), ospf_area=pop_area(pe_pop(i)))
        reg_ip(lo, f"pe{i}.lo")

    # ── Core link fabric (POP-structured, multi-area) ───────────────────────────
    # P-P /31s sequential from 10.0.0.0; each link tagged with OSPF area, cost,
    # and (inter-POP only) an SRLG conduit id. Intra-POP = cheap area-K mesh; the
    # ABR-ABR pair + all inter-POP links = area 0 backbone (expensive transit).
    pp_net = [0]              # mutable /31 counter for 10.0.0.x
    srlgs = {}               # srlg_id -> [[device, iface], ...]
    inter_pop_links = []     # adjacency records for topology-meta.json

    def add_pp(a, b, area, cost, srlg=None):
        net = 2 * pp_net[0]; pp_net[0] += 1
        a_addr, b_addr = f"10.0.0.{net}", f"10.0.0.{net + 1}"
        ia, ib = next_iface(f"p{a}"), next_iface(f"p{b}")
        nodes[f"p{a}"]["core_links"].append(
            dict(iface=ia, addr=a_addr, area=area, ospf_cost=cost, srlg=srlg))
        nodes[f"p{b}"]["core_links"].append(
            dict(iface=ib, addr=b_addr, area=area, ospf_cost=cost, srlg=srlg))
        links.append(dict(a=f"p{a}:{ia}", b=f"p{b}:{ib}"))
        reg_ip(a_addr, f"p{a}:{ia}"); reg_ip(b_addr, f"p{b}:{ib}")
        if srlg:
            srlgs.setdefault(srlg, []).extend([[f"p{a}", ia], [f"p{b}", ib]])
        return (f"p{a}", ia), (f"p{b}", ib)

    # 1) Intra-POP full mesh. ABR-ABR pair carries backbone → area 0; the rest
    #    stay in the POP area. All intra links are cheap (cost_intra).
    for pop in range(1, pop_count + 1):
        rtr = pop_routers(pop)
        bset = set(borders(pop))
        for ii in range(len(rtr)):
            for jj in range(ii + 1, len(rtr)):
                a, b = rtr[ii], rtr[jj]
                area = 0 if {a, b} == bset else pop_area(pop)
                add_pp(a, b, area, cost_intra)

    # 2) Inter-POP backbone: ring + chords, area 0, expensive. Each adjacency has
    #    `redundancy` parallel links sharing ONE SRLG conduit (fibre cut = all down).
    ring = [(p, p % pop_count + 1) for p in range(1, pop_count + 1)]
    adjacencies = [(min(x, y), max(x, y), "ring") for (x, y) in ring]
    adjacencies += [(min(x, y), max(x, y), "chord") for (x, y) in chords]
    for (pa, pb, kind) in adjacencies:
        srlg = f"srlg_pop{pa}_{pb}"
        eps = []
        for r in range(redundancy):
            ea, eb = add_pp(borders(pa)[r % 2], borders(pb)[r % 2], 0,
                            cost_inter, srlg=srlg)
            eps.extend([ea, eb])
        inter_pop_links.append(dict(pop_a=pa, pop_b=pb, kind=kind, srlg=srlg,
                                    links=[[d, i] for (d, i) in eps]))

    # 3) P-PE links (/31): each PE dual-homed to the 2 PE-facing P in its POP, in
    #    the POP area. Sequential /31s from 10.0.1.0.
    ppe_net = [0]

    def add_ppe(pe_i, p_idx, area):
        net = 2 * ppe_net[0]; ppe_net[0] += 1
        pe_addr, p_addr = f"10.0.1.{net}", f"10.0.1.{net + 1}"
        ipe, ip = next_iface(f"pe{pe_i}"), next_iface(f"p{p_idx}")
        nodes[f"pe{pe_i}"]["core_links"].append(
            dict(iface=ipe, addr=pe_addr, area=area, ospf_cost=cost_intra))
        nodes[f"p{p_idx}"]["core_links"].append(
            dict(iface=ip, addr=p_addr, area=area, ospf_cost=cost_intra))
        links.append(dict(a=f"pe{pe_i}:{ipe}", b=f"p{p_idx}:{ip}"))
        reg_ip(pe_addr, f"pe{pe_i}:{ipe}"); reg_ip(p_addr, f"p{p_idx}:{ip}")

    for i in range(1, pe_count + 1):
        pop = pe_pop(i)
        area = pop_area(pop)
        facing = internals(pop)            # PE-facing P in this POP
        add_ppe(i, facing[(i - 1) % len(facing)], area)   # primary
        if k.get("pe_dual_homing") and len(facing) > 1:
            add_ppe(i, facing[i % len(facing)], area)      # secondary

    # --- topology metadata (consumed by faults/orchestrator.py + dataapi) ---
    topo_meta = dict(
        pop_count=pop_count, p_per_pop=p_per_pop, multi_area=multi_area,
        pops={f"pop{pop}": [f"p{x}" for x in pop_routers(pop)]
              for pop in range(1, pop_count + 1)},
        abrs=[f"p{x}" for pop in range(1, pop_count + 1) for x in borders(pop)],
        pe_pop={f"pe{i}": pe_pop(i) for i in range(1, pe_count + 1)},
        # every core iface of each P (for p_node_failure = down all at once)
        p_core_ifaces={f"p{i}": [l["iface"] for l in nodes[f"p{i}"]["core_links"]]
                       for i in range(1, p_count + 1)},
        srlgs=srlgs,
        inter_pop_links=inter_pop_links,
        # per-POP inter-POP link set (for pop_isolation fault)
        pop_inter_links={
            f"pop{pop}": [lk for rec in inter_pop_links
                          if pop in (rec["pop_a"], rec["pop_b"])
                          for lk in rec["links"]
                          if lk[0] in {f"p{x}" for x in borders(pop)}]
            for pop in range(1, pop_count + 1)},
    )

    # --- iBGP: full-mesh or RR-aware ---
    rr_enabled = k.get("route_reflector", False)
    rr_node_names = set(k.get("rr_nodes", []))
    for i in range(1, pe_count + 1):
        pe_name = f"pe{i}"
        is_rr = rr_enabled and pe_name in rr_node_names
        if rr_enabled and not is_rr:
            peer_list = [
                {"ip": f"10.255.2.{j}", "rr_client": False}
                for j in range(1, pe_count + 1) if f"pe{j}" in rr_node_names
            ]
        else:
            peer_list = [
                {"ip": f"10.255.2.{j}",
                 "rr_client": rr_enabled and f"pe{j}" not in rr_node_names}
                for j in range(1, pe_count + 1) if j != i
            ]
        nodes[pe_name]["ibgp_peers"] = peer_list
        nodes[pe_name]["is_rr"] = is_rr

    # --- CE nodes: build linear list (branch, hub, dc) ---
    ce_list = []  # dict(name, site_type, type_idx, lo, asn, vrfs[], wg)
    lo_octet = {"branch": 3, "hub": 4, "dc": 5}
    asn_base = addr["ce_asn_base"]
    # which VRFs each site type gets:
    site_vrfs = {"branch": [], "hub": [], "dc": []}
    for vname, vdef in spec["vrfs"].items():
        for st in vdef["sites"]:
            site_vrfs[st].append(vname)
    # keep VRF order CORP, VOICE, GUEST (by VRF_IDX)
    for st in site_vrfs:
        site_vrfs[st].sort(key=lambda v: VRF_IDX[v])

    def add_sites(site_type, count):
        for ti in range(1, count + 1):
            name = f"ce_{site_type}{ti}"
            lo = f"10.255.{lo_octet[site_type]}.{ti}"
            asn = asn_base[site_type] + (ti - 1)
            ce_list.append(dict(name=name, site_type=site_type, type_idx=ti,
                                lo=lo, asn=asn, vrfs=site_vrfs[site_type]))
            reg_ip(lo, f"{name}.lo")

    add_sites("branch", branch)
    add_sites("hub", hub)
    add_sites("dc", dc)

    # --- CE-PE links (/30 per VRF), customer LANs, VRF attachment on PE ---
    host_nodes = []
    pe_vrf_map = {}  # (pe_name, vrf) -> list of ce_neighbor dicts
    pe_vrf_attach = {}  # pe_name -> list of (iface, vrf) for clab exec

    for lin_idx, ce in enumerate(ce_list):
        # ponytail: assert subnet math won't overflow a /30 octet.
        # 4 addresses per /30, up to 3 VRFs, site_linear_idx * 4 must fit in octet.
        assert lin_idx * 4 + 2 < 256, \
            f"CE-PE /30 address overflow at site {lin_idx} — reduce site count"
        pe_idx = (lin_idx % pe_count) + 1
        pe_name = f"pe{pe_idx}"
        ce_name = ce["name"]
        nodes[ce_name] = dict(role="CE", loopback=ce["lo"], core_links=[],
                              ce_links=[], pe_neighbors=[], asn=ce["asn"],
                              site_type=ce["site_type"], lin_idx=lin_idx,
                              lans=[], vrf_ifaces=[])
        # one /30 (CE-PE eBGP) AND one /24 customer LAN per VRF.
        for vname in ce["vrfs"]:
            vidx = VRF_IDX[vname]
            # ponytail: assert LAN third-octet doesn't overflow.
            assert lin_idx * 4 + vidx < 256, \
                f"customer LAN /24 third-octet overflow at site {lin_idx} vrf {vname}"
            base = lin_idx * 4
            pe_ip = f"10.1.{vidx}.{base + 1}"
            ce_ip = f"10.1.{vidx}.{base + 2}"
            pe_if = next_iface(pe_name)
            ce_if = next_iface(ce_name)
            nodes[pe_name]["ce_links"].append(dict(iface=pe_if, addr=pe_ip))
            nodes[ce_name]["ce_links"].append(dict(iface=ce_if, addr=ce_ip))

            # per-VRF customer LAN: 192.168.{lin_idx*4 + vrf_idx}.0/24.
            # vrf_idx in {0,1,2}; blocks of 4 third-octets per site (slot 3 spare)
            # → ranges [4i,4i+2] are disjoint across sites, never collide.
            lan_octet = lin_idx * 4 + vidx
            lan_net = f"192.168.{lan_octet}.0/24"
            lan_gw = f"192.168.{lan_octet}.1"
            host_ip = f"192.168.{lan_octet}.10"
            ce_lan_if = next_iface(ce_name)
            nodes[ce_name]["lans"].append(dict(iface=ce_lan_if, gw=lan_gw,
                                               net=lan_net, vrf=vname))
            # (vrf -> its LAN iface + its PE uplink iface) for VRF device binding
            nodes[ce_name]["vrf_ifaces"].append(
                dict(vrf=vname, lan_if=ce_lan_if, uplink_if=ce_if,
                     table=VRF_TABLE[vname]))
            reg_ip(pe_ip, f"{pe_name}:{pe_if}")
            reg_ip(ce_ip, f"{ce_name}:{ce_if}")
            reg_ip(lan_gw, f"{ce_name}.lan.{vname}")
            reg_ip(host_ip, f"host_{ce_name}_{vname}.lan")

            # CE eBGP neighbor toward this PE-VRF.
            # ponytail: with per-VRF bgpd (`router bgp <asn> vrf <VRF>`), the
            # advertisement is scoped to the VRF process — no outbound filters
            # needed. pe_neighbors used by frr.conf.j2 CE vrf-bgp block.
            nodes[ce_name]["pe_neighbors"].append(
                dict(ip=pe_ip, vrf=vname, lan_net=lan_net))
            links.append(dict(a=f"{pe_name}:{pe_if}", b=f"{ce_name}:{ce_if}"))
            pe_vrf_map.setdefault((pe_name, vname), []).append(
                dict(ip=ce_ip, asn=ce["asn"]))
            pe_vrf_attach.setdefault(pe_name, []).append((pe_if, vname))

            # one host container per (site, vrf), each on its own LAN subnet
            hname = f"h_{ce['site_type']}{ce['type_idx']}_{vname.lower()}"
            host_nodes.append(dict(name=hname, addr=host_ip, gw=lan_gw,
                                   ce=ce_name, ce_lan_if=ce_lan_if, vrf=vname))
            links.append(dict(a=f"{hname}:eth1", b=f"{ce_name}:{ce_lan_if}"))

    # --- attach per-PE VRF neighbor lists onto PE nodes ---
    vrf_meta = spec["vrfs"]
    for i in range(1, pe_count + 1):
        pe_name = f"pe{i}"
        vlist = []
        for vname in sorted(vrf_meta, key=lambda v: VRF_IDX[v]):
            neighbors = pe_vrf_map.get((pe_name, vname), [])
            if not neighbors:
                continue  # PE only configures VRFs it actually serves
            rd = vrf_meta[vname]["rd_community"]
            vlist.append(dict(name=vname, rd=rd, rt=rd, ce_neighbors=neighbors))
        nodes[pe_name]["vrfs"] = vlist

    # --- Static mgmt IPs (172.20.20.0/24) ---
    # ponytail: overlay rides the mgmt/transport net — simplification; realistic
    # upgrade = dedicated transport VRF over the WAN underlay.
    # Assign deterministic mgmt IPs starting at .101 (well above clab auto-range
    # which fills from the low end). Index across all node types in declaration order:
    # P(1..p_count), PE(1..pe_count), CE(lin_idx order), host_nodes(in order).
    MGMT_SUBNET = "172.20.20"
    MGMT_START = 101
    mgmt_ip = {}  # node_name -> "172.20.20.X"
    _mgmt_idx = 0

    def assign_mgmt(name):
        nonlocal _mgmt_idx
        ip = f"{MGMT_SUBNET}.{MGMT_START + _mgmt_idx}"
        mgmt_ip[name] = ip
        _mgmt_idx += 1
        return ip

    for i in range(1, p_count + 1):
        assign_mgmt(f"p{i}")
    for i in range(1, pe_count + 1):
        assign_mgmt(f"pe{i}")
    for ce in ce_list:
        assign_mgmt(ce["name"])
    # host_nodes not built yet; assigned during host_nodes population below
    # (host_nodes list is built in the CE-PE links loop above; re-index after)

    # --- WireGuard overlay ---
    wg_subnet_prefix = addr["wg_overlay_subnet"].rsplit(".", 1)[0]  # 172.16.0
    wg_port = addr["wg_port"]
    # ponytail: load persisted keypairs; only generate missing ones (idempotent).
    wg_cache = load_wg_cache()
    wg = {}  # ce_name -> dict(role, addr, priv, pub, endpoint)
    hubs, spokes = [], []
    for ce in ce_list:
        st, ti = ce["site_type"], ce["type_idx"]
        # ponytail: WG /24 host blocks must stay disjoint as counts grow.
        #   hub:    .1  .. .(hub_count)         (hubs, expect <= ~9)
        #   branch: .11 .. .(10+branch_count)   (at 16 branches → .11 .. .27)
        #   dc:     .101.. .(100+dc_count)      (moved above branch block so the
        #           two spoke ranges never overlap even at branch_count up to ~89)
        if st == "hub":
            host = ti                # 172.16.0.1 ..
            role = "hub"
        elif st == "branch":
            host = 10 + ti           # .11 ..
            role = "spoke"
        elif st == "dc":
            host = 100 + ti          # .101 .. (disjoint from branch block)
            role = "spoke"
        else:
            continue
        wgaddr = f"{wg_subnet_prefix}.{host}"
        priv, pub = wg_keypair(ce["name"], wg_cache)
        # endpoint = CE static mgmt IP (172.20.20.x) — always routable between
        # containers; CE loopbacks (10.255.x.x) are NOT routable off-container.
        wg[ce["name"]] = dict(role=role, addr=wgaddr, priv=priv, pub=pub,
                              endpoint=mgmt_ip[ce["name"]], name=ce["name"])
        reg_ip(wgaddr, f"{ce['name']}.wg")
        (hubs if role == "hub" else spokes).append(ce["name"])

    # persist any newly-generated keys
    save_wg_cache(wg_cache)

    # cross-inject peers: spokes peer to BOTH hubs; hubs peer to ALL spokes.
    for ce_name, info in wg.items():
        peers = []
        if info["role"] == "spoke":
            for h in hubs:
                hi = wg[h]
                peers.append(dict(name=h, role="hub", pub_key=hi["pub"],
                                  allowed_ips=f"{hi['addr']}/32",
                                  endpoint=hi["endpoint"]))
        else:  # hub: peer to every spoke
            for s in spokes:
                si = wg[s]
                peers.append(dict(name=s, role="spoke", pub_key=si["pub"],
                                  allowed_ips=f"{si['addr']}/32",
                                  endpoint=si["endpoint"]))
        info["peers"] = peers

    # --- hub-hub WG pairs (adjacent hubs: 0+1, 2+3, ...) ---
    if k.get("hub_hub_wg") and len(hubs) >= 2:
        for hi_idx in range(0, len(hubs) - 1, 2):
            ha, hb = hubs[hi_idx], hubs[hi_idx + 1]
            ia, ib = wg[ha], wg[hb]
            wg[ha]["peers"].append(dict(name=hb, role="hub", pub_key=ib["pub"],
                                        allowed_ips=f"{ib['addr']}/32",
                                        endpoint=ib["endpoint"]))
            wg[hb]["peers"].append(dict(name=ha, role="hub", pub_key=ia["pub"],
                                        allowed_ips=f"{ia['addr']}/32",
                                        endpoint=ia["endpoint"]))

    # assign mgmt IPs to host nodes (built during CE-PE links loop)
    for h in host_nodes:
        assign_mgmt(h["name"])
        h["mgmt_ip"] = mgmt_ip[h["name"]]

    # attach mgmt_ip to each node for clab.yml rendering
    for name in nodes:
        nodes[name]["mgmt_ip"] = mgmt_ip.get(name, "")

    # --- clab exec hooks per node ---
    # PE: reload mpls sysctl, create VRFs (table N), bind CE ifaces, bring up.
    # CE: create VRF devices, bind ifaces, apply qos, retry-bring-up wg.
    # ponytail: VRF table numbers are CORP=10, VOICE=20, GUEST=30 (VRF_TABLE).
    # These match rd_community last octets and `ip route show table N` is intuitive.

    node_exec = {}
    for i in range(1, pe_count + 1):
        pe_name = f"pe{i}"
        ex = ["sysctl -p /etc/sysctl.d/90-mpls.conf"]
        attaches = pe_vrf_attach.get(pe_name, [])
        created = set()
        for (iface, vname) in attaches:
            if vname not in created:
                ex.append(f"ip link add {vname} type vrf table {VRF_TABLE[vname]}")
                ex.append(f"ip link set {vname} up")
                created.add(vname)
            ex.append(f"ip link set {iface} vrf {vname}")
        node_exec[pe_name] = ex

    for i in range(1, p_count + 1):
        node_exec[f"p{i}"] = ["sysctl -p /etc/sysctl.d/90-mpls.conf"]

    for ce in ce_list:
        ce_name = ce["name"]
        vrf_ifaces = nodes[ce_name]["vrf_ifaces"]
        ex = []
        # ponytail: CE VRF isolation is STRUCTURAL — create a vrf device per VRF,
        # bind BOTH the LAN iface AND the PE-uplink iface into it. This gives each
        # VRF its own FIB → cross-VRF forwarding is structurally impossible
        # (kernel drops it, no iptables band-aid needed).
        created_vrfs = set()
        for vi in vrf_ifaces:
            vname = vi["vrf"]
            if vname not in created_vrfs:
                ex.append(f"ip link add vrf_{vname} type vrf table {vi['table']}")
                ex.append(f"ip link set vrf_{vname} up")
                created_vrfs.add(vname)
            ex.append(f"ip link set {vi['uplink_if']} vrf vrf_{vname}")
            ex.append(f"ip link set {vi['lan_if']} vrf vrf_{vname}")
        ex += ["chmod +x /qos.sh", "/qos.sh || true"]
        # ponytail: ONE per-site root netem on eth0 (mgmt/transport veth, plain
        # qdisc → root netem is valid). Delays BOTH the WG tunnels AND telemetry
        # transport — the controller MEASURES this via ping over wg0 instead of
        # modelling it. `replace` is idempotent. Geo formula = site_netem() above.
        d, j, l = site_netem(ce["site_type"], ce["type_idx"])
        ex.append(
            f"tc qdisc replace dev eth0 root netem "
            f"delay {d:.1f}ms {j:.1f}ms loss {l:.2f}%"
        )
        if ce_name in wg:
            # ponytail: WG endpoints are CE loopbacks reachable only AFTER
            # BGP/MPLS converges (~30-90s). Background-retry loop so deploy
            # doesn't block. CONTRACT: frr-node image provides wireguard-go
            # (userspace impl); WG_QUICK_USERSPACE_IMPLEMENTATION=wireguard-go
            # so `wg-quick up` works regardless of host kernel WG module.
            # Loop exits on first success; fails loudly after 30 attempts.
            wg_retry = (
                "bash -c 'for i in $(seq 1 30); do "
                "WG_QUICK_USERSPACE_IMPLEMENTATION=wireguard-go wg-quick up wg0 "
                "&& exit 0; "
                "sleep 5; done; "
                "echo wg0 bring-up failed after 30 attempts >&2; exit 1' &"
            )
            ex.append(wg_retry)
        node_exec[ce_name] = ex

    return dict(
        spec=spec, nodes=nodes, links=links, host_nodes=host_nodes,
        ce_list=ce_list, wg=wg, node_exec=node_exec, all_ips=all_ips,
        provider_as=provider_as, pe_count=pe_count, mgmt_ip=mgmt_ip,
        topo_meta=topo_meta,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Rendering
# ──────────────────────────────────────────────────────────────────────────────
def render(model):
    spec = model["spec"]
    k = spec["knobs"]
    env = Environment(loader=FileSystemLoader(TEMPLATES), trim_blocks=False,
                      lstrip_blocks=False, keep_trailing_newline=True)
    t_frr = env.get_template("frr.conf.j2")
    t_daemons = env.get_template("daemons.j2")
    t_mpls = env.get_template("90-mpls.conf.j2")
    t_snmp = env.get_template("snmpd.conf.j2")
    t_qos = env.get_template("qos.sh.j2")
    t_vtysh = env.get_template("vtysh.conf.j2")
    t_wg = env.get_template("wg0.conf.j2")
    t_clab = env.get_template("clab.yml.j2")

    snmp_community = spec["telemetry"]["snmp"]["community"]
    provider_as = model["provider_as"]

    # qos class table (shared across CEs)
    qcfg = spec["qos"]
    root_rate = qcfg["default_uplink_rate"]
    classid_for = {"VOICE": 10, "CORP": 20, "GUEST": 30}
    qos_classes = []
    for c in qcfg["classes"]:
        dscp = c["dscp"]
        dval = DSCP_VAL[dscp]
        tos = dval << 2
        qos_classes.append(dict(
            vrf=c["vrf"], dscp=dscp, dscp_val=dval, tos=hex(tos),
            classid=classid_for[c["vrf"]],
            prio=spec["vrfs"][c["vrf"]]["qos_priority"],
            rate=_pct_rate(root_rate, c["bandwidth_pct"]),
            ceil=_pct_rate(root_rate, c["bandwidth_pct"] + c["burst_pct"]),
        ))
    # default class = best-effort (GUEST) if present else CORP
    default_classid = classid_for.get("GUEST", classid_for["CORP"])

    # ponytail: exist_ok preserves inodes so Docker bind mounts inside running
    # containers see regenerated content without a container restart.
    os.makedirs(os.path.join(OUT, "configs"), exist_ok=True)

    frr_nodes = []
    for name, n in model["nodes"].items():
        role = n["role"]
        ndir = os.path.join(OUT, "configs", name)
        os.makedirs(ndir, exist_ok=True)
        core_ifaces = [l["iface"] for l in n["core_links"]]

        frr_txt = t_frr.render(
            hostname=name, role=role, loopback=n["loopback"],
            core_links=n["core_links"], ce_links=n.get("ce_links", []),
            vrfs=n.get("vrfs", []), ibgp_peers=n.get("ibgp_peers", []),
            pe_neighbors=n.get("pe_neighbors", []),
            vrf_ifaces=n.get("vrf_ifaces", []),
            provider_as=provider_as, ce_as=n.get("asn"),
            lans=n.get("lans", []),
            bfd_core=k.get("bfd_core", False),
            is_rr=n.get("is_rr", False),
            loopback_area=n.get("ospf_area", 0),
        )
        _w(os.path.join(ndir, "frr.conf"), frr_txt)

        _w(os.path.join(ndir, "daemons"), t_daemons.render(
            ospfd=(role in ("P", "PE")), ldpd=(role in ("P", "PE")),
            bgpd=(role in ("PE", "CE")), staticd=False,
            bfdd=(role in ("P", "PE") and k.get("bfd_core", False))))

        _w(os.path.join(ndir, "snmpd.conf"), t_snmp.render(
            hostname=name, snmp_community=snmp_community))

        _w(os.path.join(ndir, "vtysh.conf"), t_vtysh.render())

        if role in ("P", "PE"):
            _w(os.path.join(ndir, "90-mpls.conf"),
               t_mpls.render(core_ifaces=core_ifaces))

        if role == "CE":
            # ponytail: pass ALL CE uplink ifaces to qos.sh for HTB shaping on
            # each VRF uplink (not just ce_links[0] / CORP only).
            uplinks = [lk["iface"] for lk in n["ce_links"]]
            _w(os.path.join(ndir, "qos.sh"), t_qos.render(
                hostname=name, uplink_ifaces=uplinks, uplink_rate=root_rate,
                classes=qos_classes, default_classid=default_classid))
            if name in model["wg"]:
                w = model["wg"][name]
                _w(os.path.join(ndir, "wg0.conf"), t_wg.render(
                    hostname=name, wg_role=w["role"], wg_addr=w["addr"],
                    wg_port=spec["addressing"]["wg_port"], priv_key=w["priv"],
                    peers=w["peers"]))

        frr_nodes.append(dict(name=name, role=role,
                              has_wg=(name in model["wg"]),
                              mgmt_ip=model["mgmt_ip"].get(name, ""),
                              exec=model["node_exec"].get(name, [])))

    # ponytail: pass frr_image="frr-node:0.1" directly into the template
    # render instead of the fragile post-render string replace.
    clab_txt = t_clab.render(
        lab_name="sdwan_mpls_noc", frr_image="frr-node:0.1",
        host_image=k["host_image"], frr_nodes=frr_nodes,
        host_nodes=model["host_nodes"], links=model["links"])
    _w(os.path.join(OUT, "clab.yml"), clab_txt)

    # topology metadata (POP/area/SRLG map) — consumed by faults + dataapi
    _w(os.path.join(OUT, "topology-meta.json"),
       json.dumps(model["topo_meta"], indent=2) + "\n")

    # emit telemetry node-mappings from the same model (anti-drift)
    emit_telemetry(model)


def _pct_rate(rate_str, pct):
    # rate_str like "1gbit" -> scale by pct. Emit kbit to keep it integer-clean.
    num = "".join(ch for ch in rate_str if ch.isdigit())
    unit = "".join(ch for ch in rate_str if ch.isalpha()).lower()
    mult = {"gbit": 1_000_000, "mbit": 1_000, "kbit": 1}[unit]
    kbit = int(num) * mult * pct // 100
    return f"{kbit}kbit"


def _w(path, text):
    with open(path, "w") as f:
        f.write(text)


# ──────────────────────────────────────────────────────────────────────────────
# Telemetry node-mappings (anti-drift): emit the telegraf SNMP agent list and the
# nfacctd pre_tag_map straight from the SAME node/mgmt-IP model the lab is built
# from, so they can never drift from the deployed node set. A later step points
# telemetry/ at these files (telegraf include + nfacctd pre_tag_map).
# Only FRR nodes (P/PE/CE) are emitted — host containers run no SNMP/IPFIX.
# ──────────────────────────────────────────────────────────────────────────────
def emit_telemetry(model):
    out_dir = os.path.join(OUT, "telemetry")
    os.makedirs(out_dir, exist_ok=True)

    # FRR nodes in declaration order (P, PE, CE) with their mgmt IPs.
    frr = [(name, n["mgmt_ip"]) for name, n in model["nodes"].items()
           if n["role"] in ("P", "PE", "CE")]
    width = max(len(name) for name, _ in frr)

    # 1) Telegraf SNMP agent list — a snippet the telegraf [[inputs.snmp]] block
    #    references for its `agents = [...]` array. Format: one TOML "udp://IP:161"
    #    list element per FRR node (community/version live in telegraf.conf).
    snmp_lines = ["# GENERATED by generator/generate.py — do not edit by hand.",
                  "# Telegraf SNMP agents = all FRR mgmt IPs (community v2c on :161).",
                  "# A later step wires telegraf.conf's `agents` array to this list.",
                  "agents = ["]
    for name, ip in frr:
        snmp_lines.append(f'    "udp://{ip}:161",'.ljust(34) + f"# {name}")
    snmp_lines.append("]")
    _w(os.path.join(out_dir, "snmp_agents.toml"), "\n".join(snmp_lines) + "\n")

    # 2) nfacctd device_map (pmacct pre_tag_map format): mgmt-IP -> device label,
    #    one line per FRR node. Format: `set_label=<device> ip=<mgmtip>`.
    #    nfacctd matches the IPFIX exporter source IP and tags the flow `device`.
    dm_lines = ["! GENERATED by generator/generate.py — do not edit by hand.",
                "! pre_tag_map: IPFIX exporter mgmt IP -> device label.",
                "! Format: set_label=<value> ip=<exporter_ip>", ""]
    for name, ip in frr:
        dm_lines.append(f"set_label={name.ljust(width)}  ip={ip}")
    _w(os.path.join(out_dir, "device_map.txt"), "\n".join(dm_lines) + "\n")

    return [t[0] for t in frr]


# ──────────────────────────────────────────────────────────────────────────────
# Self-test (--check): address collisions + required per-node files present.
# Run BEFORE render() to catch math errors, and again after to check files.
# ──────────────────────────────────────────────────────────────────────────────
def check(model, post_render=False):
    # 0. no duplicate mgmt IPs
    mgmt_seen = {}
    for name, ip in model["mgmt_ip"].items():
        assert ip not in mgmt_seen, \
            f"mgmt IP collision {ip}: {name} vs {mgmt_seen[ip]}"
        mgmt_seen[ip] = name

    # 1. no duplicate IPs
    seen = {}
    for ip, owner in model["all_ips"]:
        assert ip not in seen, f"IP collision {ip}: {owner} vs {seen[ip]}"
        seen[ip] = owner

    # 1b. Option A: no per-(site,VRF) customer /24 collides, and each host's LAN
    #     is genuinely distinct from every other VRF's LAN (the whole point).
    lan_owner = {}
    for name, n in model["nodes"].items():
        for lan in n.get("lans", []):
            net = lan["net"]
            who = f"{name}/{lan['vrf']}"
            assert net not in lan_owner, \
                f"customer LAN collision {net}: {who} vs {lan_owner[net]}"
            lan_owner[net] = who
    # host count must equal sum over sites of #VRFs served (Option A)
    n_hosts_expected = sum(len(n.get("lans", []))
                           for n in model["nodes"].values() if n["role"] == "CE")
    assert len(model["host_nodes"]) == n_hosts_expected, \
        f"host count {len(model['host_nodes'])} != per-(site,VRF) {n_hosts_expected}"

    # 2. every PE has pe_count-1 iBGP peers
    pe_count = model["pe_count"]
    k_check = model["spec"]["knobs"]
    rr_enabled_c = k_check.get("route_reflector", False)
    rr_nodes_c = set(k_check.get("rr_nodes", []))
    for name, n in model["nodes"].items():
        if n["role"] == "PE":
            if rr_enabled_c and name not in rr_nodes_c:
                expected_peers = len(rr_nodes_c)
            else:
                expected_peers = pe_count - 1
            assert len(n["ibgp_peers"]) == expected_peers, (
                f"{name} iBGP peers {len(n['ibgp_peers'])} != {expected_peers}"
            )

    # 3. each spoke has exactly len(hubs) wg peers; hub has branch_count+dc_count peers
    k = model["spec"]["knobs"]
    n_hubs = k["hub_count"]
    n_spokes_expected = k["branch_count"] + k["dc_count"]
    hubs = [nm for nm, w in model["wg"].items() if w["role"] == "hub"]
    for cename, w in model["wg"].items():
        if w["role"] == "spoke":
            assert len(w["peers"]) == n_hubs, \
                f"{cename} wg peers {len(w['peers'])} != {n_hubs} (hub count)"
        else:
            hi_idx = hubs.index(cename) if cename in hubs else -1
            in_pair = k.get("hub_hub_wg") and hi_idx >= 0 and (
                (hi_idx % 2 == 0 and hi_idx + 1 < len(hubs)) or hi_idx % 2 == 1
            )
            expected_hub_peers = n_spokes_expected + (1 if in_pair else 0)
            assert len(w["peers"]) == expected_hub_peers, (
                f"{cename} hub wg peers {len(w['peers'])} != {expected_hub_peers}"
            )

    # 4. required per-node files exist on disk (post-render only)
    if post_render and os.path.isdir(OUT):
        for name, n in model["nodes"].items():
            ndir = os.path.join(OUT, "configs", name)
            required = ["frr.conf", "daemons", "snmpd.conf", "vtysh.conf"]
            if n["role"] in ("P", "PE"):
                required.append("90-mpls.conf")
            if n["role"] == "CE":
                required.append("qos.sh")
                if name in model["wg"]:
                    required.append("wg0.conf")
            for fn in required:
                p = os.path.join(ndir, fn)
                assert os.path.isfile(p), f"missing {p}"
        assert os.path.isfile(os.path.join(OUT, "clab.yml")), "missing clab.yml"

        # 5. telemetry node-mappings emitted, one entry per FRR node (anti-drift)
        n_frr = sum(1 for n in model["nodes"].values()
                    if n["role"] in ("P", "PE", "CE"))
        tdir = os.path.join(OUT, "telemetry")
        snmp_f = os.path.join(tdir, "snmp_agents.toml")
        dm_f = os.path.join(tdir, "device_map.txt")
        assert os.path.isfile(snmp_f), f"missing {snmp_f}"
        assert os.path.isfile(dm_f), f"missing {dm_f}"
        with open(snmp_f) as f:
            n_agents = sum(1 for ln in f if ln.lstrip().startswith('"udp://'))
        with open(dm_f) as f:
            n_labels = sum(1 for ln in f if ln.startswith("set_label="))
        assert n_agents == n_frr, \
            f"telegraf agents {n_agents} != FRR nodes {n_frr}"
        assert n_labels == n_frr, \
            f"device_map labels {n_labels} != FRR nodes {n_frr}"
    print("check: OK — no IP collisions, iBGP mesh + wg peers correct, "
          "telemetry mappings emitted, files present")


def main():
    with open(SPEC) as f:
        spec = yaml.safe_load(f)

    if "--check" in sys.argv:
        # Check pre-render (math/model), then render, then check post-render (files).
        model = build(spec)
        check(model, post_render=False)
        render(model)
        check(model, post_render=True)
        return

    model = build(spec)
    render(model)
    check(model, post_render=True)  # always self-validate after a render
    n_nodes = len(model["nodes"]) + len(model["host_nodes"])
    print(f"rendered {len(model['nodes'])} FRR nodes + "
          f"{len(model['host_nodes'])} hosts ({n_nodes} containers) -> {OUT}")


if __name__ == "__main__":
    main()
