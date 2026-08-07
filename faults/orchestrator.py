#!/usr/bin/env python3
"""Fault orchestrator + ground-truth label writer.

Schedules named scenarios, drives the injectors, and writes the LABELS TIMELINE
-- the ground truth the ML team trains on. For every scenario instance it records
t_start / t_impact / t_end / lead_time, joinable to telemetry on device + time.

stdlib only (urllib for VictoriaMetrics). All timestamps are UTC ISO-8601 (Z).

t_impact derivation (documented per the brief):
  Where a telemetry metric directly reflects the fault, we POLL VictoriaMetrics
  for a THRESHOLD CROSSING and use the first crossing time as t_impact (method
  "vm_threshold"). The metric+threshold per scenario are in SCENARIOS below.
  Where no clean metric exists (e.g. a transient BGP clear), we fall back to a
  MODELLED delay t_start + impact_delay_s (method "modelled") -- the controller
  smooths metrics with EMA so the dataplane effect surfaces a few seconds later.
  # ponytail: polling VM beats instrumenting every injector; the metric IS the
  #   observable, and the AI team consumes the same metric, so the label aligns.

Label schema is defined in README.md (the data-API + ML contract).

CLI:
  python3 orchestrator.py --list
  python3 orchestrator.py --scenario congestion --target ce_branch1 [--severity high] [--duration 90]
  python3 orchestrator.py --demo          # short congestion ramp on ce_branch1, end-to-end
"""
import argparse
import json
import os
import random
import signal
import threading
import time
import urllib.parse
import urllib.request
import sys
import uuid
from datetime import datetime, timezone

# Works both as a script (`python3 orchestrator.py`, cwd=faults/) and as a module
# (`python3 -c "import faults.orchestrator"` from the repo root).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import injectors as inj  # noqa: E402
import leadpriors  # noqa: E402  -- lead priors shared with synthetic/generate.py
import events_push  # noqa: E402  -- thin Loki push for FRR control-plane precursors (#65)

VM_URL = os.environ.get("VM_URL", "http://172.20.20.50:8428")
LABELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "labels")
LABELS_FILE = os.path.join(LABELS_DIR, "labels.jsonl")


# --------------------------------------------------------------------------- time
def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- VM
def vm_instant(query):
    """Run an instant PromQL query; return float value of first result or None.

    None means "no value": either the selector matched nothing OR the query
    failed. The two are NOT the same, so a transport/parse failure is logged as
    a probe_error event instead of being swallowed silently.
    """
    url = f"{VM_URL}/api/v1/query?" + urllib.parse.urlencode({"query": query})
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.load(r)
        res = data.get("data", {}).get("result", [])
        if not res:
            return None
        return float(res[0]["value"][1])
    except Exception as e:
        print(json.dumps({"event": "probe_error", "query": query,
                          "error": f"{type(e).__name__}: {e}"}), flush=True)
        return None


def poll_threshold(query, threshold, baseline=None, timeout_s=120, interval_s=3):
    """Poll `query` until it crosses `threshold` (relative to baseline if given).

    Returns (t_impact_dt, observed_value, saw_value). On timeout t_impact is
    None; saw_value is False when the probe returned NOTHING for the whole
    window (VM down / metric absent / bad selector) — a different condition from
    "the metric was there and simply never crossed".
    If baseline is given, crossing = value >= baseline + threshold.
    """
    deadline = time.time() + timeout_s
    last = None
    saw = False
    target = (baseline + threshold) if baseline is not None else threshold
    while time.time() < deadline:
        v = vm_instant(query)
        last = v
        if v is not None:
            saw = True
            if v >= target:
                return now_utc(), v, True
        time.sleep(interval_s)
    return None, last, saw


# --------------------------------------------------------------------------- labels
def write_label(row):
    os.makedirs(LABELS_DIR, exist_ok=True)
    with open(LABELS_FILE, "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


# --------------------------------------------------------------------------- topology meta
# POP / area / SRLG map emitted by generator/generate.py -> topology/topology-meta.json.
# The core/catastrophic scenarios compute their link-sets from this (nothing hardcoded).
_META = None


def meta():
    global _META
    if _META is None:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "topology", "topology-meta.json")
        with open(p) as f:
            _META = json.load(f)
    return _META


def _p_inter_ifaces(p):
    """Inter-POP (area-0 backbone) ifaces on P router `p` (empty if not an ABR)."""
    out = []
    for rec in meta()["inter_pop_links"]:
        out += [i for (d, i) in rec["links"] if d == p]
    return out


def _backbone_iface(p):
    """One representative backbone iface on `p`: an inter-POP link if it's an ABR,
    else its last core iface (a P-PE link)."""
    inter = _p_inter_ifaces(p)
    return inter[0] if inter else meta()["p_core_ifaces"][p][-1]


def _pe_primary_p_loopback(pe):
    """Loopback IP of the PE's primary (PE-facing) P uplink, from the POP map."""
    m = meta()
    pop = m["pe_pop"][pe]
    internals = m["pops"][f"pop{pop}"][2:]          # PE-facing P in this POP
    i = int(pe.replace("pe", ""))
    primary = internals[(i - 1) % len(internals)]    # matches generator attachment
    return f"10.255.1.{int(primary.replace('p', ''))}"


# --------------------------------------------------------------------------- scenarios
# Each scenario is a builder: given target+severity+duration it returns a dict
# with the injector, the t_impact probe (PromQL + threshold), and metadata.
# Severity maps to impairment magnitude.

SEVERITY = {"low": 0.4, "medium": 0.7, "high": 1.0}


def _ce_uplink(target):
    """CE uplink interface toward PE = eth1 (verified on the live lab)."""
    return "eth1"


def scen_congestion(target, severity, duration):
    """(a) Link/interface CONGESTION buildup: netem delay+loss RAMP on a CE
    uplink. Precursor = latency/jitter creep before loss. Probe = tunnel latency
    on this site rising over baseline."""
    s = SEVERITY[severity]
    iface = _ce_uplink(target)
    injector = inj.NetemImpair(target, iface,
                               delay_ms=80 * s, jitter_ms=20 * s, loss_pct=6 * s)
    probe = f'max(sdwan_tunnel_latency_ms{{device="{target}"}})'
    return {
        "type": "congestion",
        "target": {"device": target, "interface": iface},
        "injector": injector, "ramp": True, "duration": duration, "overlay": True,
        "probe": probe, "threshold": 8.0, "impact_method": "vm_threshold",
        "signature": "latency+jitter creep then loss on the affected site's tunnels",
    }


def scen_bgp_flap(target, severity, duration):
    """(b) BGP/OSPF adjacency FLAP / instability. Repeated session resets ->
    ADJCHANGE churn (Loki) + route reconvergence. Transient: modelled impact."""
    s = SEVERITY[severity]
    count = max(2, int(4 * s))
    injector = inj.BgpFlap(target, count=count, gap_seconds=6.0)
    return {
        "type": "bgp_flap",
        "target": {"device": target, "neighbor": "all"},
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "impact_delay_s": 2, "impact_method": "modelled",
        "signature": "BGP ADJCHANGE bursts in Loki; transient prefix withdrawal/relearn",
    }


def scen_tunnel_degrade(target, severity, duration):
    """(c) SD-WAN TUNNEL degradation: jitter/loss decay on the CE uplink +
    WireGuard rekey anomaly. Probe = tunnel loss% rising; rekey clustering in
    controller events."""
    s = SEVERITY[severity]
    iface = _ce_uplink(target)
    injector = inj.NetemImpair(target, iface,
                               delay_ms=30 * s, jitter_ms=40 * s, loss_pct=10 * s)
    rekey = inj.WgRekeyAnomaly(target, count=max(2, int(4 * s)))
    probe = f'max(sdwan_tunnel_loss_pct{{device="{target}"}})'
    return {
        "type": "tunnel_degrade",
        "target": {"device": target, "interface": iface, "tunnel": f"{target}-*"},
        "injector": injector, "extra": rekey, "ramp": True, "duration": duration,
        "overlay": True,
        "probe": probe, "threshold": 2.0, "impact_method": "vm_threshold",
        "signature": "tunnel jitter+loss climb; WireGuard rekey clustering (handshake retries)",
    }


def scen_policy_drift(target, severity, duration):
    """(d) Policy / route DRIFT: a CE VRF route-map lowers local-preference,
    drifting path selection off policy. Observable in show bgp + Loki soft-clear.
    Transient/structural: modelled impact."""
    s = SEVERITY[severity]
    lp = int(100 - 60 * s)  # higher severity -> lower local-pref -> bigger drift
    injector = inj.PolicyDrift(target, vrf="vrf_CORP", local_pref=lp)
    return {
        "type": "policy_drift",
        "target": {"device": target, "vrf": "vrf_CORP"},
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "impact_delay_s": 3, "impact_method": "modelled",
        "signature": "BGP local-pref shift on CORP; route-selection drift, soft-clear ADJ event",
    }


# --- Adversarial extras --------------------------------------------------------
def scen_node_failure(target, severity, duration):
    """Extra: node/process failure (kill bgpd; watchfrr respawns). VPN routes
    drop until recovery. Probe = path_changes counter increment (controller
    reacts), else modelled."""
    injector = inj.ProcessKill(target, proc="bgpd")
    return {
        "type": "node_failure",
        "target": {"device": target, "process": "bgpd"},
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "impact_delay_s": 1, "impact_method": "modelled",
        "severity_inert": True,
        "signature": "bgpd gap -> prefix withdrawal until watchfrr restart (~recoverable)",
    }


def scen_asymmetric_loss(target, severity, duration):
    """Extra: asymmetric loss -- loss only on the egress (uplink) direction, so
    return path is clean. Hard-to-diagnose signature. Probe = tunnel loss%."""
    s = SEVERITY[severity]
    iface = _ce_uplink(target)
    injector = inj.NetemImpair(target, iface, loss_pct=12 * s)  # egress-only
    probe = f'max(sdwan_tunnel_loss_pct{{device="{target}"}})'
    return {
        "type": "asymmetric_loss",
        "target": {"device": target, "interface": iface},
        "injector": injector, "ramp": False, "duration": duration, "overlay": True,
        "probe": probe, "threshold": 2.0, "impact_method": "vm_threshold",
        "signature": "one-directional loss; loss% up with latency near-normal (asymmetric)",
    }


def scen_brownout(target, severity, duration):
    """Extra: brownout -- a hard rate cap on the uplink (bandwidth starvation),
    no added delay/loss at netem level but queueing builds. Probe = tunnel
    latency (queue delay) rising."""
    s = SEVERITY[severity]
    iface = _ce_uplink(target)
    rate = int(2000 * (1.1 - s))  # high severity -> tighter cap (kbit)
    # A `rate` cap alone is invisible: the controller's _read_netem parses only
    # the delay/loss tokens, and the wg0 RTT does not traverse eth1, so a pure
    # rate cap never shows up in tunnel telemetry. Pair it with a small
    # delay+loss (the queueing the cap induces) so the impairment is REAL and
    # observable beside the calibrated overlay.
    injector = inj.NetemImpair(target, iface, delay_ms=15 * s, loss_pct=1.5 * s,
                               rate_kbit=rate)
    return {
        "type": "brownout",
        "target": {"device": target, "interface": iface, "rate_kbit": rate},
        "injector": injector, "ramp": False, "duration": duration, "overlay": True,
        "probe": None, "threshold": None, "impact_method": "modelled",
        "impact_delay_s": 4,
        "signature": "bandwidth starvation on the uplink (rate cap + queueing delay/loss)",
    }


def scen_mpls_underlay_failure(target, severity, duration):
    """Bring down a P-router core interface toward a PE; LDP reconverges via dual-homing."""
    # P-PE links are appended last, but ONLY on the POP-internal Ps — ABRs get
    # inter-POP backbone links there instead, so [-1] on an ABR is not a P-PE
    # link at all. Reject ABRs rather than mislabel the failure domain.
    if target in meta()["abrs"]:
        raise SystemExit(f"{target} is an ABR and has no P-PE link; "
                         f"use an internal P (see topology-meta abrs)")
    iface = meta()["p_core_ifaces"][target][-1]
    injector = inj.MplsUnderlayFailure(target, iface)
    return {
        "type": "mpls_underlay_failure",
        "target": {"device": target, "interface": iface},
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "threshold": None, "impact_method": "modelled",
        "severity_inert": True,
        "signature": "P-PE link down; LDP must reconverge to secondary path (~1s with BFD)",
    }


def scen_ldp_session_flap(target, severity, duration):
    """Flap an LDP session on a PE; self-recovers; generates LDP events in Loki."""
    sev_count = {"low": 1, "medium": 2, "high": 3}.get(str(severity), 1)
    # LDP peer = the PE's primary P uplink loopback (from the POP map, so it's a
    # real neighbour for any PE in any POP — not a hardcoded p1).
    neighbor_ip = _pe_primary_p_loopback(target)
    injector = inj.LdpSessionFlap(target, neighbor_ip, count=sev_count, gap_seconds=6.0)
    return {
        "type": "ldp_session_flap",
        "target": {"device": target, "neighbor": neighbor_ip, "count": sev_count},
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "threshold": None, "impact_method": "modelled",
        "signature": "LDP session cleared N times; session self-recovers; Loki logs ldp_event=Down/Up",
    }


def scen_hub_spoke_congest(target, severity, duration):
    """Heavy spoke-uplink congestion: netem delay+jitter+loss RAMP on a SPOKE's
    eth1 (a spoke peers every hub, so this degrades all of its tunnels at once — a
    hub-side cap would be invisible, the controller folds netem per spoke site).
    Higher calibrated peak than plain `congestion` (signatures.py). Injected on the
    spoke because that is the only place tunnel telemetry can observe it."""
    sev_kwargs = {
        "low":    {"delay_ms": 20,  "jitter_ms": 4,  "loss_pct": 0.5},
        "medium": {"delay_ms": 80,  "jitter_ms": 15, "loss_pct": 2.0},
        "high":   {"delay_ms": 200, "jitter_ms": 40, "loss_pct": 8.0},
    }.get(str(severity), {"delay_ms": 80, "jitter_ms": 15, "loss_pct": 2.0})
    injector = inj.NetemImpair(target, "eth1", **sev_kwargs)
    probe = f'max(sdwan_tunnel_latency_ms{{device="{target}"}})'
    return {
        "type": "hub_spoke_congest",
        "target": {"device": target, "interface": "eth1"},
        "injector": injector, "ramp": True, "duration": duration, "overlay": True,
        "probe": probe, "threshold": 8.0, "impact_method": "vm_threshold",
        "signature": "heavy spoke-uplink congestion; latency+loss climb across all of the spoke's tunnels",
    }


def scen_bgp_cascade(target, severity, duration):
    """Cascade BGP flaps on a hub CE; forces multiple path-switches; stresses RIB churn."""
    sev_count = {"low": 1, "medium": 3, "high": 5}.get(str(severity), 3)
    injector = inj.BgpFlap(target, count=sev_count, gap_seconds=8.0)
    # NO PROBE: sdwan_path_changes_total is a single unlabelled fabric-wide
    # counter that the controller increments from its own micro-burst RNG, and
    # nothing in the controller reads BGP state — a crossing cannot be
    # attributed to this fault. Modelled until a real BGP metric is scoped by
    # device (see the FRR/SNMP pillar).
    return {
        "type": "bgp_cascade",
        "target": {"device": target, "count": sev_count},
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "threshold": None, "impact_method": "modelled",
        "impact_delay_s": 2,
        "signature": "repeated BGP session clears on a hub CE; RIB churn (Loki ADJCHANGE)",
    }


CTRL_URL = "http://172.20.20.56:9362"  # controller HTTP (shared by both injectors)


class _OverlayInjector:
    """Post/clear a calibrated tunnel-ramp overlay on the controller (the visible
    buildup precursor). Modeled on _DriftInjector; idempotent both ways -- a
    re-post just resets the record, a clear on a missing site is a no-op."""

    def __init__(self, site, fault_type, lead_s, duration, severity):
        self.site = site
        self.fault_type = fault_type
        self.lead_s = lead_s
        self.duration = duration
        self.severity = severity

    def apply(self):
        import json as _json, urllib.request as _req
        body = _json.dumps({"site": self.site, "fault_type": self.fault_type,
                            "lead_s": self.lead_s, "duration": self.duration,
                            "severity": self.severity}).encode()
        _req.urlopen(f"{CTRL_URL}/fault/overlay", data=body, timeout=5)
        return {"applied": "overlay", "site": self.site, "fault_type": self.fault_type}

    def revert(self):
        import json as _json, urllib.request as _req
        body = _json.dumps({"site": self.site}).encode()
        _req.urlopen(f"{CTRL_URL}/fault/overlay/clear", data=body, timeout=5)
        return {"reverted": "overlay", "site": self.site}


class _DriftInjector:
    """Inline injector for controller drift (no new dep — uses urllib.request)."""

    def __init__(self, site, mult, ttl_s):
        self.site = site
        self.mult = mult
        self.ttl_s = ttl_s

    def apply(self):
        import json as _json, urllib.request as _req
        body = _json.dumps({"site": self.site, "latency_threshold_mult": self.mult,
                            "ttl_s": self.ttl_s}).encode()
        _req.urlopen(f"{CTRL_URL}/fault/drift", data=body, timeout=5)
        return {"applied": "controller_drift", "site": self.site, "mult": self.mult}

    def revert(self):
        import json as _json, urllib.request as _req
        body = _json.dumps({"site": self.site}).encode()
        _req.urlopen(f"{CTRL_URL}/fault/drift/clear", data=body, timeout=5)
        return {"reverted": "controller_drift", "site": self.site}


def scen_controller_drift(target, severity, duration):
    """Post drift suppression to the SD-WAN controller; prevents failover for the
    site AND carries a calibrated overlay so the site's tunnel metric moves (the
    failover-suppression is the fault; the overlay is the visible degradation the
    suppressed failover fails to fix). Both key on the SPOKE site (controller.py
    _drift / _sites), so the target is a spoke, not a hub."""
    mult = {"low": 5.0, "medium": 10.0, "high": 99.0}.get(str(severity), 10.0)
    injector = _DriftInjector(target, mult=mult, ttl_s=duration + 30)
    return {
        "type": "controller_drift",
        "target": {"device": target, "latency_threshold_mult": mult},
        "injector": injector, "ramp": False, "duration": duration, "overlay": True,
        "probe": None, "threshold": None, "impact_method": "modelled",
        "signature": "controller drift suppresses failover; overlay ramps the tunnel metric; sdwan_controller_drift_active rises",
    }


# --- Core / catastrophic / correlated scenarios (POP-structured MPLS backbone) -
# Link-sets are computed from topology-meta.json (no hardcoded interfaces). These
# target the P core — the part of the network the predictive NOC most needs to see.
def scen_p_node_failure(target, severity, duration):
    """Catastrophic: down ALL core ifaces of one P router (full node loss). The
    redundant POP mesh + PE dual-homing must reroute around it."""
    links = [(target, i) for i in meta()["p_core_ifaces"][target]]
    injector = inj.MultiLinkFault(links)
    return {
        "type": "p_node_failure", "device": target,
        "target": {"device": target, "n_links": len(links), "links": links},
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "impact_delay_s": 2, "impact_method": "modelled",
        "severity_inert": True,
        "signature": "full P-router loss; OSPF/LDP reconverge around it via POP mesh + dual-homing",
    }


def scen_pop_isolation(target, severity, duration):
    """Catastrophic: down every inter-POP backbone link of one POP -> the region
    is cut off (only intra-POP connectivity remains); its PEs go unreachable."""
    links = [tuple(l) for l in meta()["pop_inter_links"][target]]
    abr0 = meta()["pops"][target][0]
    injector = inj.MultiLinkFault(links)
    return {
        "type": "pop_isolation", "device": abr0,
        "target": {"device": abr0, "pop": target, "n_links": len(links),
                   "links": links},
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "impact_delay_s": 2, "impact_method": "modelled",
        "severity_inert": True,
        "signature": f"{target} isolated: all inter-POP links down; in-POP PEs unreachable from other POPs",
    }


def scen_core_partition(target, severity, duration):
    """Catastrophic: cut the full edge cut-set bisecting the backbone ring into
    two halves (a multi-fibre core partition / area-0 discontiguity)."""
    m = meta(); pc = m["pop_count"]
    n = int(str(target).replace("pop", "")) if "pop" in str(target) else int(target)
    half = {((n - 1 + k) % pc) + 1 for k in range(pc // 2)}
    links = []
    for rec in m["inter_pop_links"]:
        if (rec["pop_a"] in half) != (rec["pop_b"] in half):   # crossing edge
            links += [tuple(l) for l in rec["links"]]
    injector = inj.MultiLinkFault(links)
    dev = links[0][0] if links else "p1"
    return {
        "type": "core_partition", "device": dev,
        "target": {"device": dev, "seam": target, "half": sorted(half),
                   "n_links": len(links), "links": links},
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "impact_delay_s": 2, "impact_method": "modelled",
        "severity_inert": True,
        "signature": "backbone ring bisected; area-0 splits into two islands until restore",
    }


def scen_srlg_cut(target, severity, duration):
    """Correlated fibre cut: down EVERY link in one SRLG conduit at once -- the
    redundant parallel links share a duct, so 'redundancy' doesn't save you."""
    links = [tuple(l) for l in meta()["srlgs"][target]]
    injector = inj.MultiLinkFault(links)
    return {
        "type": "srlg_cut", "device": links[0][0],
        "target": {"device": links[0][0], "srlg": target, "n_links": len(links),
                   "links": links},
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "impact_delay_s": 2, "impact_method": "modelled",
        "severity_inert": True,
        "signature": "shared-risk conduit cut; all parallel inter-POP links drop together",
    }


def scen_core_congestion(target, severity, duration):
    """Transit congestion buildup: netem delay+loss RAMP on a P-P backbone link;
    every LSP transiting it degrades (not just one edge site)."""
    s = SEVERITY[severity]
    iface = _backbone_iface(target)
    injector = inj.NetemImpair(target, iface,
                               delay_ms=60 * s, jitter_ms=15 * s, loss_pct=4 * s)
    return {
        "type": "core_congestion", "device": target,
        "target": {"device": target, "interface": iface},
        "injector": injector, "ramp": True, "duration": duration,
        "probe": None, "impact_delay_s": 4, "impact_method": "modelled",
        "signature": "backbone-link congestion; latency/loss climb on all transiting LSPs",
    }


def scen_ospf_area_flap(target, severity, duration):
    """Routing instability: flap an inter-POP (area-0) adjacency -> SPF churn +
    inter-area reconvergence. Precursor = ospf_spf_* moving + ADJCHANGE in Loki."""
    iface = _backbone_iface(target)
    count = {"low": 1, "medium": 2, "high": 3}.get(str(severity), 2)
    injector = inj.LinkFlap(target, iface, down_seconds=4.0, count=count)
    return {
        "type": "ospf_area_flap", "device": target,
        "target": {"device": target, "interface": iface, "count": count},
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "impact_delay_s": 2, "impact_method": "modelled",
        "signature": "inter-POP adjacency flaps; SPF re-runs, inter-area routes churn",
    }


def scen_path_asymmetry(target, severity, duration):
    """Raise OSPF cost on ONE direction of a backbone link so forward and return
    paths diverge (path asymmetry -- a named precursor in the brief)."""
    iface = _backbone_iface(target)
    cost = int(500 + 1500 * SEVERITY[severity])
    # _backbone_iface returns an inter-POP link on an ABR (igp_cost_inter=100)
    # and an intra-POP/P-PE link otherwise (igp_cost_intra=10). Reverting to a
    # hardcoded 100 would leave a cost-10 link permanently inflated.
    orig = 100 if _p_inter_ifaces(target) else 10
    injector = inj.OspfCostShift(target, iface, cost=cost, orig_cost=orig)
    return {
        "type": "path_asymmetry", "device": target,
        "target": {"device": target, "interface": iface, "cost": cost},
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "impact_delay_s": 3, "impact_method": "modelled",
        "signature": "one-way OSPF cost hike; forward/return paths diverge (asymmetric routing)",
    }


def scen_rr_failure(target, severity, duration):
    """Catastrophic control-plane: kill bgpd on a Route Reflector -> VPNv4 route
    propagation degrades cluster-wide until watchfrr respawns it."""
    injector = inj.ProcessKill(target, proc="bgpd")
    return {
        "type": "rr_failure", "device": target,
        "target": {"device": target, "process": "bgpd", "role": "route_reflector"},
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "impact_delay_s": 3, "impact_method": "modelled",
        "severity_inert": True,
        "signature": "RR bgpd gap; VPNv4 propagation stalls (bgp_peer_established drops) until restart",
    }


def scen_gray_failure(target, severity, duration):
    """Gray failure: sub-BFD intermittent loss (0.5-2%) on a backbone link, NO
    link-down. BFD won't trip; degradation is slow -> high predictive value."""
    s = SEVERITY[severity]
    iface = _backbone_iface(target)
    loss = round(0.5 + 1.5 * s, 2)             # 0.5%..2%, below BFD trip
    injector = inj.NetemImpair(target, iface, loss_pct=loss)
    return {
        "type": "gray_failure", "device": target,
        "target": {"device": target, "interface": iface, "loss_pct": loss},
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "impact_delay_s": 5, "impact_method": "modelled",
        "signature": "low sub-BFD loss on a backbone link; no down event, slow transit degradation",
    }


SCENARIOS = {
    "congestion": scen_congestion,            # (a) mandated
    "bgp_flap": scen_bgp_flap,                # (b) mandated
    "tunnel_degrade": scen_tunnel_degrade,    # (c) mandated
    "policy_drift": scen_policy_drift,        # (d) mandated
    "node_failure": scen_node_failure,        # adversarial extra
    "asymmetric_loss": scen_asymmetric_loss,  # adversarial extra
    "brownout": scen_brownout,                # adversarial extra
    "mpls_underlay_failure": scen_mpls_underlay_failure,
    "ldp_session_flap":      scen_ldp_session_flap,
    "hub_spoke_congest":     scen_hub_spoke_congest,
    "bgp_cascade":           scen_bgp_cascade,
    "controller_drift":      scen_controller_drift,
    # --- core / catastrophic / correlated (POP backbone) ---
    "p_node_failure":  scen_p_node_failure,
    "pop_isolation":   scen_pop_isolation,
    "core_partition":  scen_core_partition,
    "srlg_cut":        scen_srlg_cut,
    "core_congestion": scen_core_congestion,
    "ospf_area_flap":  scen_ospf_area_flap,
    "path_asymmetry":  scen_path_asymmetry,
    "rr_failure":      scen_rr_failure,
    "gray_failure":    scen_gray_failure,
}


# Ground-truth fault type/cause per scenario, available PRE-IMPACT (before the
# t_end /labels row is written). Same value each scen_ fn puts in spec["type"]
# and _label_row writes at t_end -- serving it from /faults/active lets #91 build
# a §3.3 salvage record and a stable base alert_id during buildup. For every
# scenario that value IS the scenario name; test_scenario_types_match_specs guards
# spec["type"] == name so a future divergent scenario fails loudly, not silently.
# ponytail: {name: name}; add an explicit override here only if a scenario's type
# ever stops matching its name.
SCENARIO_TYPES = {name: name for name in SCENARIOS}


# --------------------------------------------------------------------------- run
def draw_ramp_seconds(name, duration, step=30):
    """DEFECT 1b: ramp wall-duration for a ramping scenario = the lead drawn from
    the shared per-type prior, capped so the ramp cannot outlast the fault.

    Returns (ramp_s, capped). The cap bites often at the default 90 s duration --
    the priors are minutes-scale because that is how far ahead the telemetry
    actually sees these failures. Run campaigns with a longer --duration to get
    the untruncated prior.
    """
    lead, _ = leadpriors.draw_lead_s(name, step, random.gauss(0, 1))
    cap = 0.7 * float(duration)
    return (min(lead, cap), lead > cap)


def _resolve_impact(spec, t_start, baseline, duration, dry_run, ramp_s=None):
    """Return (t_impact, observed, impact_method, t_impact_ramp) for one fault.

    Shared by run_scenario and _campaign_fault so both label paths agree.
    impact_method is one of:
      vm_threshold      the probe was read and crossed -> measured t_impact
      ramp_derived      no probe crossing, but the scenario RAMPED: t_impact is
                        the end of the ramp, which is where the impairment
                        reaches the level the SLA is defined against
                        (faults/leadpriors.THETA_SLA)
      modelled_fallback the probe was read but never crossed, and there was no
                        ramp to derive from -> constant impact_delay_s
      probe_unavailable the probe returned NOTHING for the whole window (VM
                        down / metric absent / selector matches no series) ->
                        modelled t_impact, but no telemetry stands behind it
      modelled          the scenario declares no probe at all and does not ramp

    t_impact_ramp is the ramp-derived timestamp whenever a ramp ran, INCLUDING
    when the probe crossed -- recording both is what lets the two methods be
    compared instead of one silently replacing the other.
    """
    t_ramp = (datetime.fromtimestamp(t_start.timestamp() + ramp_s, tz=timezone.utc)
              if ramp_s else None)
    if spec["impact_method"] == "vm_threshold" and spec.get("probe") and not dry_run:
        t_impact, observed, saw = poll_threshold(
            spec["probe"], spec["threshold"], baseline=baseline,
            timeout_s=int(duration), interval_s=3)
        if t_impact is not None:
            return t_impact, observed, "vm_threshold", t_ramp
        method = "modelled_fallback" if saw else "probe_unavailable"
    else:
        observed = None
        method = "modelled"
    if t_ramp is not None and method != "probe_unavailable":
        return t_ramp, observed, "ramp_derived", t_ramp
    delay = spec.get("impact_delay_s", 2)
    return (datetime.fromtimestamp(t_start.timestamp() + delay, tz=timezone.utc),
            observed, method, t_ramp)


def _label_row(spec, scenario_id, name, target, severity, t_start, t_impact,
               t_end, impact_method, baseline, observed, dry_run, error,
               t_impact_ramp=None):
    """Build the ground-truth label row (one schema, both run paths)."""
    return {
        "scenario_id": scenario_id,
        "type": spec["type"] if spec else name,
        "target": spec["target"] if spec else {"device": target},
        # severity is recorded as null for scenarios whose injector ignores it
        # (link-set / process-kill faults) — the column must not carry a value
        # the fault never used.
        "severity": None if (spec and spec.get("severity_inert")) else severity,
        "t_start": iso(t_start),
        "t_impact": iso(t_impact),
        "t_end": iso(t_end),
        "lead_time": round((t_impact - t_start).total_seconds(), 1),
        "impact_method": impact_method,
        # DEFECT 1b: both t_impacts when both exist, so vm_threshold and
        # ramp_derived can be compared instead of one hiding the other.
        "t_impact_ramp": iso(t_impact_ramp) if t_impact_ramp else None,
        "probe": spec.get("probe") if spec else None,
        "baseline_value": baseline,
        "impact_value": observed,
        "signature": spec["signature"] if spec else None,
        "device": (spec.get("device", target) if spec else target),
        "dry_run": dry_run,
        "error": error,
    }


def run_scenario(name, target, severity="medium", duration=90,
                 dry_run=False, cancel=None, status=None):
    """buildup -> impact -> hold -> revert state machine for one live injection.

    Every scenario draws a precursor lead from the shared prior, floored to a
    demo-visible [30,60]s (out-of-distribution for the naturally-fast faults, per
    docs/SPEC-NOTES.md). Overlay-flagged scenarios also post a calibrated
    tunnel-ramp overlay so the precursor is VISIBLE during buildup. Then: wait the
    lead (cancellable) -> fire the real injector at IMPACT -> hold `duration`
    (cancellable) -> guaranteed finally reverts the physical action AND clears the
    overlay. The visible ramp lives in the controller overlay, not a netem ramp,
    so the impairment fires at full magnitude at impact rather than ramping.

    cancel: optional threading.Event. Set during buildup OR hold it wakes the wait
    and falls through to the same revert+clear (early-revert). If it fires before
    impact the physical injector never runs; the overlay is still cleared and a
    label is still written with t_impact = t_start + lead.

    status: optional mutable dict. If given, phase is reported into it at each
    transition -- buildup (with lead + t_impact), impact, reverting -- via plain
    key writes (GIL-atomic, no lock). A caller (faults_api registry) can read it
    concurrently to project the live lifecycle. None => behaviour unchanged."""
    if name not in SCENARIOS:
        raise SystemExit(f"unknown scenario '{name}'. choices: {list(SCENARIOS)}")
    spec = SCENARIOS[name](target, severity, duration)
    injector = spec["injector"]
    scenario_id = f"{name}-{target}-{uuid.uuid4().hex[:8]}"

    # The overlay flag is set only on the tunnel_ramp scenarios whose target is a
    # valid controller overlay site (a spoke CE); iface_down / control-plane /
    # backbone / hub-targeted faults post none. The lead is drawn for ALL of them
    # (and even on a dry run) so the previewed label matches a real run.
    is_overlay = bool(spec.get("overlay"))
    overlay_active = is_overlay  # cleared below if the controller post fails
    # Every overlay scenario injects on its target (a spoke CE), which is the site
    # the tunnel metric folds into — so the overlay is keyed on `target`.
    lead = min(60.0, max(30.0, leadpriors.draw_lead_s(name, 30, random.gauss(0, 1))[0]))
    overlay = (_OverlayInjector(target, spec["type"], lead, duration, severity)
               if is_overlay and not dry_run else None)
    t_start = now_utc()
    t_impact = datetime.fromtimestamp(t_start.timestamp() + lead, tz=timezone.utc)
    impact_method = "overlay_lead" if is_overlay else "modelled"
    print(json.dumps({"event": "inject", "scenario_id": scenario_id,
                      "type": spec["type"], "t_start": iso(t_start),
                      "lead_s": round(lead, 1), "overlay": overlay is not None,
                      "dry_run": dry_run}), flush=True)

    def _status(**kw):
        if status is not None:
            status.update(kw)  # GIL-atomic key writes; no lock

    _status(phase="buildup", lead=round(lead, 1), t_impact=iso(t_impact))

    def _wait(sec):
        cancel.wait(sec) if cancel is not None else time.sleep(sec)

    def _cancelled():
        return cancel is not None and cancel.is_set()

    fired = False
    error = None
    try:
        # Overlay post is best-effort: a controller 400/timeout must NOT abort the
        # real injection, only drop the visible precursor. The buildup wait below
        # is universal, so timing (t_impact = t_start + lead) holds either way.
        if overlay is not None:
            try:
                overlay.apply()
            except Exception as e:
                overlay = None            # nothing to clear later
                overlay_active = False
                impact_method = "modelled"  # no calibrated ramp was emitted
                error = f"overlay_post_failed: {type(e).__name__}: {e}"
                print(json.dumps({"event": "overlay_error", "scenario_id": scenario_id,
                                  "error": error}), flush=True)
        # --- control-plane precursors: push the fault's discrete FRR events into
        # Loki at buildup start so the log-side precursors land BEFORE t_impact
        # (#65). Best-effort: emit_precursors never raises and no-ops if Loki (or
        # pandas, or a discrete signature) is unavailable. Stamped at t_start (the
        # start of buildup), so the whole burst precedes the physical impact.
        # ponytail: an early-revert-before-impact leaves these precursors in Loki
        #   with no impact behind them -- accepted; the label row carries the same
        #   scenario_id with error="early_revert_before_impact", so a consumer
        #   joining on scenario_id sees the cancel. Precursors MUST be emitted
        #   during buildup (that is the point), before cancel is knowable.
        if not dry_run and not _cancelled():
            tgt = spec.get("target", {})
            n_ev = events_push.emit_precursors(
                spec["type"], spec.get("device", target),
                tgt.get("interface") or tgt.get("neighbor") or tgt.get("tunnel"),
                tgt.get("vrf"),
                None if spec.get("severity_inert") else severity,
                scenario_id, "live", t_start.timestamp())
            if n_ev:
                print(json.dumps({"event": "precursors_emitted",
                                  "scenario_id": scenario_id, "count": n_ev}),
                      flush=True)
        # --- buildup: cancellable precursor wait ---
        if not dry_run and not _cancelled():
            _wait(lead)
        # --- impact: fire the real physical action (skipped on early-revert) ---
        if not dry_run and not _cancelled():
            injector.apply()
            if spec.get("extra"):
                spec["extra"].apply()
            fired = True
            _status(phase="impact")
            print(json.dumps({"event": "impact", "scenario_id": scenario_id,
                              "t_impact": iso(t_impact), "method": impact_method}),
                  flush=True)
            # --- hold: cancellable ---
            _wait(duration)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        print(json.dumps({"event": "scenario_error", "scenario_id": scenario_id,
                          "error": error}), flush=True)
    finally:
        _status(phase="reverting")  # covers early-revert-before-impact too
        # Revert the physical action and clear the overlay INDEPENDENTLY: a failed
        # physical revert must never leave the overlay live on the controller.
        if fired:
            try:
                if spec.get("extra"):
                    spec["extra"].revert()
                injector.revert()
            except Exception as e:
                error = (error or "") + f" revert_failed: {type(e).__name__}: {e}"
                print(json.dumps({"event": "revert_error", "scenario_id": scenario_id,
                                  "error": str(e)}), flush=True)
        if overlay is not None:
            try:
                overlay.revert()
            except Exception as e:
                error = (error or "") + f" overlay_clear_failed: {type(e).__name__}: {e}"
                print(json.dumps({"event": "overlay_clear_error", "scenario_id": scenario_id,
                                  "error": str(e)}), flush=True)
        # A fault reverted before it fired never impacted -- flag the label so the
        # row is not mistaken for a real injection at t_start+lead.
        if not fired and not dry_run and error is None:
            error = "early_revert_before_impact"
        t_end = now_utc()
        print(json.dumps({"event": "revert", "scenario_id": scenario_id,
                          "t_end": iso(t_end)}), flush=True)

        row = _label_row(spec, scenario_id, name, target, severity, t_start,
                         t_impact, t_end, impact_method, None, None,
                         dry_run, error, t_impact if overlay_active else None)
        write_label(row)
        print(json.dumps({"event": "label_written", "row": row}), flush=True)
    return row


# --------------------------------------------------------------------------- demo
def demo():
    """Short congestion ramp on ce_branch1 (~60s), end-to-end, with before/after
    VM evidence printed."""
    target = "ce_branch1"
    probe = f'max(sdwan_tunnel_latency_ms{{device="{target}"}})'
    before = vm_instant(probe)
    print(json.dumps({"event": "demo_before", "probe": probe, "value": before}),
          flush=True)
    row = run_scenario("congestion", target, severity="high", duration=60)
    after = vm_instant(probe)
    print(json.dumps({"event": "demo_after", "probe": probe, "value": after,
                      "delta": (after - before) if (after and before) else None}),
          flush=True)
    return row


# --------------------------------------------------------------------------- campaign
# ponytail: Poisson arrivals = expovariate(1/mean_gap). One thread per active
#   fault so concurrent faults on DIFFERENT targets are real (not serialised).
#   Active-target set guards against stacking two faults on the same device.
#   try/finally + SIGINT handler guarantee every injected fault is reverted.

# Valid targets per scenario class.  Non-critical means: not P-core (p1-p5).
_CE_BRANCHES = [f"ce_branch{i}" for i in range(1, 25)]   # 24 branches
_CE_HUBS     = [f"ce_hub{i}"    for i in range(1, 7)]    # 6 hubs
_CE_DCS      = [f"ce_dc{i}"     for i in range(1, 5)]    # 4 DCs
_CE_ALL      = _CE_BRANCHES + _CE_HUBS + _CE_DCS          # 34 CEs
_CE_SPOKES   = _CE_BRANCHES + _CE_DCS                     # 28 spokes (peer the hubs; valid overlay sites)
_PE_ALL      = [f"pe{i}"        for i in range(1, 13)]   # 12 PEs
_P_ALL       = [f"p{i}"         for i in range(1, 25)]   # 24 P routers
# core-fault pools, derived from the topology-meta POP map (single source of truth)
_ABRS  = meta()["abrs"]                                   # ABR (backbone) P routers
# POP-internal (PE-facing) Ps — the only ones that HAVE a P-PE link.
_P_INTERNAL = [p for p in _P_ALL if p not in _ABRS]
_POPS  = list(meta()["pops"].keys())                     # pop1..pop6
_SRLGS = list(meta()["srlgs"].keys())                    # inter-POP conduits
_RR    = ["pe1", "pe2"]                                   # route reflectors

# ponytail: scenario pools defined once here; avoids re-deriving them later.
CAMPAIGN_POOLS = {
    # netem scenarios need an uplink CE
    "congestion":      _CE_ALL,
    "tunnel_degrade":  _CE_ALL,
    "asymmetric_loss": _CE_ALL,
    "brownout":        _CE_ALL,
    # routing scenarios can target CE or PE (kill bgpd is non-destructive on PE)
    "bgp_flap":        _CE_ALL + _PE_ALL,
    "policy_drift":    _CE_ALL,             # CORP VRF only exists on CEs
    # node_failure (bgpd kill) — avoid PE core nodes to keep core stable;
    # actually fine on CEs and PE spokes; skip P-core entirely
    "node_failure":    _CE_ALL + _PE_ALL,
    # only internal Ps have a P-PE link; an ABR target would down an inter-POP
    # backbone conduit while the label claimed a P-PE failure.
    "mpls_underlay_failure": _P_INTERNAL,
    "ldp_session_flap":      _PE_ALL,
    "hub_spoke_congest":     _CE_SPOKES,     # spoke uplink is the observable point
    "bgp_cascade":           _CE_HUBS,
    # drift + overlay both key on a SPOKE site (controller.py _drift/_sites are
    # spokes, never hubs) — a hub target would suppress/ramp nothing.
    "controller_drift":      _CE_SPOKES,
    # core / catastrophic / correlated faults — the chaos the campaign now mixes in.
    # ponytail: pop_isolation + core_partition are EXCLUDED from the random campaign
    #   (whole-region/backbone cuts overlap link-sets; run them explicitly as the
    #   Phase-6 named scenarios). p_node_failure/srlg_cut/rr_failure stay in: each
    #   touches a disjoint, recoverable link-set so the active-target guard suffices.
    "p_node_failure":  _P_ALL,
    "srlg_cut":        _SRLGS,
    "core_congestion": _ABRS,
    "ospf_area_flap":  _ABRS,
    "path_asymmetry":  _ABRS,
    "rr_failure":      _RR,
    "gray_failure":    _ABRS,
}

# Fault duration bounds (seconds) per scenario, independent of --duration.
# ponytail: short enough to keep campaign lively; long enough to get telemetry.
_DURATION_BOUNDS = {
    "congestion":      (30, 90),
    "tunnel_degrade":  (25, 70),
    "asymmetric_loss": (20, 60),
    "brownout":        (20, 60),
    "bgp_flap":        (15, 45),
    "policy_drift":    (20, 60),
    "node_failure":    (10, 30),
    "mpls_underlay_failure": (15, 45),
    "ldp_session_flap":      (10, 30),
    "hub_spoke_congest":     (30, 90),
    "bgp_cascade":           (20, 60),
    "controller_drift":      (60, 180),
    "p_node_failure":        (15, 45),
    "srlg_cut":              (20, 50),
    "core_congestion":       (30, 90),
    "ospf_area_flap":        (15, 45),
    "path_asymmetry":        (30, 90),
    "rr_failure":            (15, 40),
    "gray_failure":          (60, 180),
}


def _merged_seconds(intervals):
    """Total wall seconds covered by [start, end] intervals, overlaps merged."""
    total = 0.0
    end = None
    for a, b in sorted(intervals):
        if end is None or a > end:
            total += b - a
            end = b
        elif b > end:
            total += b - end
            end = b
    return total


# Scenarios that cannot share a device with ANYTHING else: ProcessKill takes the
# routing daemon away, so every scenario that needs vtysh on that box breaks, and
# the label of the other fault would describe a fault it never really injected.
_EXCLUSIVE = {"node_failure", "rr_failure", "bgp_cascade"}


def _lock_key(name, target):
    """(device, resource) the scenario mutates. resource=None means whole-device
    exclusivity. Two scenarios may run on one device iff their keys differ and
    neither is device-exclusive."""
    if name in _EXCLUSIVE:
        return (target, None)
    spec_target = None
    try:
        spec_target = SCENARIOS[name](target, "low", 1).get("target")
    except Exception:
        pass
    if isinstance(spec_target, dict):
        # netem lives on an interface; two installs on one interface conflict
        for k in ("interface", "tunnel", "vrf", "neighbor", "process"):
            if spec_target.get(k):
                return (target, f"{k}:{spec_target[k]}")
    return (target, f"scenario:{name}")


def _lock_selftest():
    """The concurrency rule the dataset's multi-label supervision depends on."""
    d = "ce_branch1"
    assert _lock_key("congestion", d) == _lock_key("tunnel_degrade", d), \
        "two netem installs on one interface must still exclude each other"
    assert _lock_key("policy_drift", d) != _lock_key("congestion", d), \
        "a VRF-scoped drift must be able to run beside an interface impairment"
    assert _lock_key("node_failure", "pe1")[1] is None, \
        "ProcessKill must be device-exclusive"
    # hub_spoke_congest and congestion are both netem on the spoke's eth1, so on
    # one spoke they must exclude each other.
    assert _lock_key("hub_spoke_congest", "ce_branch1") == _lock_key("congestion", "ce_branch1"), \
        "two netem installs on one spoke uplink must exclude each other"
    # the ramp duration must actually vary and stay inside the fault
    spans = {round(draw_ramp_seconds("congestion", 3000)[0], 3) for _ in range(50)}
    assert len(spans) > 40, f"ramp duration is not varying: {len(spans)} distinct"
    # the cap must bind at the default 90s duration -- the priors are minutes
    capped = [draw_ramp_seconds("congestion", 90) for _ in range(20)]
    assert all(s <= 0.7 * 90 + 1e-6 for s, _ in capped), "ramp outlasts the fault"
    assert any(c for _, c in capped), "cap never reported at a 90s duration"
    print("orchestrator selftest OK")


def _campaign_fault(name, target, severity, duration, ramp_steps,
                    campaign_id, active_targets, lock, stats, dry_run):
    """Run one fault in a thread; guard active_targets; always revert."""
    # DEFECT 5a: the lock key was the bare device, so one device could carry at
    # most one fault at a time and the dataset had ZERO within-device
    # concurrency -- nothing for a multi-label head to learn. The key is now the
    # RESOURCE the scenario actually mutates, so a tunnel-scoped degradation and
    # a device-scoped event on the same box run concurrently, while two netem
    # installs on one interface still cannot.
    key = _lock_key(name, target)
    with lock:
        blocked = key in active_targets or (target, None) in active_targets
        if key[1] is None:  # device-exclusive: nothing else may hold this device
            blocked = blocked or any(k[0] == target for k in active_targets)
        if blocked:
            # Another fault already holds this resource — skip silently.
            return
        active_targets.add(key)

    # The target must be released whatever happens (a builder that raises used
    # to leak it for the life of the campaign, so the device was never faulted
    # again). Innermost finally = it can never be skipped.
    try:
        # Nothing below the try can be unbound in the finally: the spec build is
        # inside the guard and every name has a value before the try.
        scenario_id = f"{name}-{target}-{uuid.uuid4().hex[:8]}"
        spec = injector = None
        baseline = observed = error = None
        impact_method = "modelled"
        t_impact_ramp = None
        t_start = t_impact = now_utc()
        try:
            spec = SCENARIOS[name](target, severity, duration)
            injector = spec["injector"]

            if spec.get("probe"):
                baseline = vm_instant(spec["probe"])

            t_start = t_impact = now_utc()
            print(json.dumps({"event": "campaign_inject", "campaign_id": campaign_id,
                              "scenario_id": scenario_id, "type": name, "target": target,
                              "severity": severity, "duration": duration,
                              "t_start": iso(t_start), "dry_run": dry_run}), flush=True)

            ramp_s = draw_ramp_seconds(name, duration)[0] if spec.get("ramp") else None
            if not dry_run:
                if spec.get("ramp"):
                    injector.ramp(steps=ramp_steps, total_seconds=ramp_s)
                else:
                    injector.apply()
                if spec.get("extra"):
                    spec["extra"].apply()

            t_impact, observed, impact_method, t_impact_ramp = _resolve_impact(
                spec, t_start, baseline, duration, dry_run, ramp_s)

            # Hold for remainder of duration
            elapsed = time.time() - t_start.timestamp()
            remaining = duration - elapsed
            if remaining > 0 and not dry_run:
                time.sleep(remaining)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            print(json.dumps({"event": "scenario_error", "scenario_id": scenario_id,
                              "error": error}), flush=True)
        finally:
            # Always revert, even on exception or SIGINT.
            try:
                if injector is not None and not dry_run:
                    if spec.get("extra"):
                        spec["extra"].revert()
                    injector.revert()
            except Exception as e:
                error = (error or "") + f" revert_failed: {type(e).__name__}: {e}"
                print(json.dumps({"event": "revert_error", "scenario_id": scenario_id,
                                  "error": str(e)}), flush=True)

            t_end = now_utc()
            print(json.dumps({"event": "campaign_revert", "campaign_id": campaign_id,
                              "scenario_id": scenario_id, "t_end": iso(t_end)}), flush=True)

            row = _label_row(spec, scenario_id, name, target, severity, t_start,
                             t_impact, t_end, impact_method, baseline, observed,
                             dry_run, error, t_impact_ramp)
            row["campaign_id"] = campaign_id
            write_label(row)
            print(json.dumps({"event": "label_written", "row": row}), flush=True)

            with lock:
                stats["count"] += 1
                stats["by_type"][name] = stats["by_type"].get(name, 0) + 1
                # Intervals, not a running sum: faults overlap by design, so
                # summing durations double-counts wall time (and produced
                # negative healthy_seconds / >100% fault_pct).
                stats["intervals"].append((t_start.timestamp(), t_end.timestamp()))
    finally:
        with lock:
            active_targets.discard(key)


def run_campaign(total_duration, mean_gap=120, seed=None, dry_run=False,
                 ramp_steps=4, campaign_id=None):
    """Drive a Poisson-arrival fault campaign for `total_duration` seconds.

    # ponytail: arrival model = Poisson process with mean_gap seconds between
    #   incidents (inter-arrival ~ Exp(1/mean_gap)). This gives realistic burstiness
    #   vs. a fixed timer. mean_gap=120 → ~1 fault per 2 min on average.
    #   Seed makes runs reproducible for CI/ML dataset versioning.
    """
    rng = random.Random(seed)
    campaign_id = campaign_id or f"campaign-{uuid.uuid4().hex[:12]}"
    deadline = time.time() + total_duration

    lock = threading.Lock()
    active_targets = set()
    threads = []
    stats = {"count": 0, "by_type": {}, "intervals": []}

    # SIGINT handler: join all threads (their finally blocks revert)
    _stop = threading.Event()

    def _sigint(sig, frame):
        print(json.dumps({"event": "campaign_interrupted",
                          "campaign_id": campaign_id}), flush=True)
        _stop.set()

    old_handler = signal.signal(signal.SIGINT, _sigint)

    print(json.dumps({"event": "campaign_start", "campaign_id": campaign_id,
                      "total_duration": total_duration, "mean_gap_s": mean_gap,
                      "seed": seed, "dry_run": dry_run}), flush=True)

    try:
        while not _stop.is_set():
            # ponytail: Exp(1/mean_gap) inter-arrival; clamp to avoid near-zero gaps.
            gap = max(5.0, rng.expovariate(1.0 / mean_gap))
            wake_at = time.time() + gap
            if wake_at >= deadline:
                # No more incidents fit in the window — wait out the remaining time.
                remaining = deadline - time.time()
                if remaining > 0:
                    _stop.wait(timeout=remaining)
                break

            _stop.wait(timeout=max(0, wake_at - time.time()))
            if _stop.is_set():
                break
            if time.time() >= deadline:
                break

            # Pick scenario + target
            name = rng.choice(list(CAMPAIGN_POOLS.keys()))
            pool = CAMPAIGN_POOLS[name]
            # Skip targets already faulted (check without holding lock long)
            with lock:
                available = [t for t in pool if t not in active_targets]
            if not available:
                print(json.dumps({"event": "campaign_skip", "reason": "all_targets_busy",
                                  "scenario": name}), flush=True)
                continue

            target = rng.choice(available)
            severity = rng.choice(list(SEVERITY.keys()))
            lo, hi = _DURATION_BOUNDS[name]
            duration = round(rng.uniform(lo, hi), 1)

            # Spawn thread so concurrent faults on different targets are real
            t = threading.Thread(
                target=_campaign_fault,
                args=(name, target, severity, duration, ramp_steps,
                      campaign_id, active_targets, lock, stats, dry_run),
                daemon=True,
            )
            threads.append(t)
            t.start()

    finally:
        signal.signal(signal.SIGINT, old_handler)
        # Wait for all active faults to revert (their finally blocks run)
        for t in threads:
            t.join(timeout=300)

    fault_seconds = _merged_seconds(stats["intervals"])
    summary = {
        "event": "campaign_summary",
        "campaign_id": campaign_id,
        "total_incidents": stats["count"],
        "by_type": stats["by_type"],
        # union of fault windows (overlapping concurrent faults counted once)
        "fault_seconds": round(fault_seconds, 1),
        # sum of per-fault durations; > fault_seconds when faults overlap
        "concurrent_fault_seconds": round(
            sum(b - a for a, b in stats["intervals"]), 1),
        "healthy_seconds": round(max(0.0, total_duration - fault_seconds), 1),
        "fault_pct": round(100 * min(fault_seconds, total_duration) / total_duration, 1),
    }
    print(json.dumps(summary), flush=True)
    return summary


# --------------------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description="Fault orchestrator + label writer")
    ap.add_argument("--scenario", choices=list(SCENARIOS))
    ap.add_argument("--target", help="device name, e.g. ce_branch1 / pe1")
    ap.add_argument("--severity", choices=list(SEVERITY), default="medium")
    ap.add_argument("--duration", type=float, default=90,
                    help="single-scenario hold duration OR campaign total duration (s)")
    ap.add_argument("--ramp-steps", type=int, default=6)
    ap.add_argument("--list", action="store_true", help="list scenarios and exit")
    ap.add_argument("--selftest", action="store_true",
                    help="check the lock rule + ramp draw, no lab needed")
    ap.add_argument("--demo", action="store_true", help="run a short demo scenario")
    ap.add_argument("--dry-run", action="store_true",
                    help="write a label without touching the lab (schema check); "
                         "the row is flagged dry_run=true")
    # Campaign flags
    ap.add_argument("--campaign", action="store_true",
                    help="run a Poisson-arrival fault campaign for --duration seconds")
    ap.add_argument("--mean-gap", type=float, default=120,
                    help="campaign: mean inter-arrival gap in seconds (default 120)")
    ap.add_argument("--seed", type=int, default=None,
                    help="campaign: RNG seed for reproducibility")
    ap.add_argument("--campaign-id", default=None,
                    help="campaign: explicit campaign tag (auto-generated if omitted)")
    args = ap.parse_args()

    if args.selftest:
        _lock_selftest()
        return
    if args.list:
        for n, fn in SCENARIOS.items():
            print(f"{n:16s} {fn.__doc__.strip().splitlines()[0]}")
        return
    if args.demo:
        demo()
        return
    if args.campaign:
        run_campaign(total_duration=args.duration, mean_gap=args.mean_gap,
                     seed=args.seed, dry_run=args.dry_run,
                     ramp_steps=args.ramp_steps, campaign_id=args.campaign_id)
        return
    if not args.scenario or not args.target:
        ap.error("--scenario and --target are required (or use --demo / --list / --campaign)")
    run_scenario(args.scenario, args.target, severity=args.severity,
                 duration=args.duration, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
