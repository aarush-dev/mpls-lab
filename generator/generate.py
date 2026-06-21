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
import shutil
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

    # --- P routers ---
    for i in range(1, p_count + 1):
        lo = f"10.255.1.{i}"
        nodes[f"p{i}"] = dict(role="P", loopback=lo, core_links=[], ce_links=[])
        reg_ip(lo, f"p{i}.lo")

    # --- PE routers ---
    for i in range(1, pe_count + 1):
        lo = f"10.255.2.{i}"
        nodes[f"pe{i}"] = dict(role="PE", loopback=lo, core_links=[], ce_links=[],
                               vrfs=[])
        reg_ip(lo, f"pe{i}.lo")

    # --- P-P core links (/31), all unordered pairs, sequential from 10.0.0.0 ---
    pp_pairs = [(a, b) for a in range(1, p_count + 1) for b in range(a + 1, p_count + 1)]
    for kk, (a, b) in enumerate(pp_pairs):
        net = 2 * kk
        lo_addr = f"10.0.0.{net}"      # lower-index router (.0 of /31)
        hi_addr = f"10.0.0.{net + 1}"  # higher-index router
        ia, ib = next_iface(f"p{a}"), next_iface(f"p{b}")
        nodes[f"p{a}"]["core_links"].append(dict(iface=ia, addr=lo_addr))
        nodes[f"p{b}"]["core_links"].append(dict(iface=ib, addr=hi_addr))
        links.append(dict(a=f"p{a}:{ia}", b=f"p{b}:{ib}"))
        reg_ip(lo_addr, f"p{a}:{ia}")
        reg_ip(hi_addr, f"p{b}:{ib}")

    # --- P-PE links (/31), PE round-robin to a P, sequential from 10.0.1.0 ---
    for i in range(1, pe_count + 1):
        p_idx = (i - 1) % p_count + 1
        net = 2 * (i - 1)
        pe_addr = f"10.0.1.{net}"
        p_addr = f"10.0.1.{net + 1}"
        ipe, ip = next_iface(f"pe{i}"), next_iface(f"p{p_idx}")
        nodes[f"pe{i}"]["core_links"].append(dict(iface=ipe, addr=pe_addr))
        nodes[f"p{p_idx}"]["core_links"].append(dict(iface=ip, addr=p_addr))
        links.append(dict(a=f"pe{i}:{ipe}", b=f"p{p_idx}:{ip}"))
        reg_ip(pe_addr, f"pe{i}:{ipe}")
        reg_ip(p_addr, f"p{p_idx}:{ip}")

    # --- iBGP full mesh among PEs ---
    for i in range(1, pe_count + 1):
        peers = [f"10.255.2.{j}" for j in range(1, pe_count + 1) if j != i]
        nodes[f"pe{i}"]["ibgp_peers"] = peers

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
        if st == "hub":
            host = ti                # 172.16.0.1, .2
            role = "hub"
        elif st == "branch":
            host = 10 + ti           # .11..
            role = "spoke"
        elif st == "dc":
            host = 20 + ti           # .21..
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

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(os.path.join(OUT, "configs"))

    frr_nodes = []
    for name, n in model["nodes"].items():
        role = n["role"]
        ndir = os.path.join(OUT, "configs", name)
        os.makedirs(ndir)
        core_ifaces = [l["iface"] for l in n["core_links"]]

        frr_txt = t_frr.render(
            hostname=name, role=role, loopback=n["loopback"],
            core_links=n["core_links"], ce_links=n.get("ce_links", []),
            vrfs=n.get("vrfs", []), ibgp_peers=n.get("ibgp_peers", []),
            pe_neighbors=n.get("pe_neighbors", []),
            vrf_ifaces=n.get("vrf_ifaces", []),
            provider_as=provider_as, ce_as=n.get("asn"),
            lans=n.get("lans", []),
        )
        _w(os.path.join(ndir, "frr.conf"), frr_txt)

        _w(os.path.join(ndir, "daemons"), t_daemons.render(
            ospfd=(role in ("P", "PE")), ldpd=(role in ("P", "PE")),
            bgpd=(role in ("PE", "CE")), staticd=False))

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

    # ponytail: pass frr_image="frr-node:latest" directly into the template
    # render instead of the fragile post-render string replace.
    clab_txt = t_clab.render(
        lab_name="sdwan_mpls_noc", frr_image="frr-node:latest",
        host_image=k["host_image"], frr_nodes=frr_nodes,
        host_nodes=model["host_nodes"], links=model["links"])
    _w(os.path.join(OUT, "clab.yml"), clab_txt)


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
    for name, n in model["nodes"].items():
        if n["role"] == "PE":
            assert len(n["ibgp_peers"]) == pe_count - 1, \
                f"{name} iBGP peers {len(n['ibgp_peers'])} != {pe_count-1}"

    # 3. each spoke has exactly len(hubs) wg peers; hub has branch_count+dc_count peers
    k = model["spec"]["knobs"]
    n_hubs = k["hub_count"]
    n_spokes_expected = k["branch_count"] + k["dc_count"]
    for cename, w in model["wg"].items():
        if w["role"] == "spoke":
            assert len(w["peers"]) == n_hubs, \
                f"{cename} wg peers {len(w['peers'])} != {n_hubs} (hub count)"
        else:
            assert len(w["peers"]) == n_spokes_expected, \
                f"{cename} hub wg peers {len(w['peers'])} != {n_spokes_expected}"

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
    print("check: OK — no IP collisions, iBGP mesh + wg peers correct, files present")


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
