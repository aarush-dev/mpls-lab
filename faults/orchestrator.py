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
import uuid
from datetime import datetime, timezone

import injectors as inj

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
    """Run an instant PromQL query; return float value of first result or None."""
    url = f"{VM_URL}/api/v1/query?" + urllib.parse.urlencode({"query": query})
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.load(r)
        res = data.get("data", {}).get("result", [])
        if not res:
            return None
        return float(res[0]["value"][1])
    except Exception:
        return None


def poll_threshold(query, threshold, baseline=None, timeout_s=120, interval_s=3):
    """Poll `query` until it crosses `threshold` (relative to baseline if given).

    Returns (t_impact_dt, observed_value) at first crossing, or (None, last) on
    timeout. If baseline is given, crossing = value >= baseline + threshold.
    """
    deadline = time.time() + timeout_s
    last = None
    target = (baseline + threshold) if baseline is not None else threshold
    while time.time() < deadline:
        v = vm_instant(query)
        last = v
        if v is not None and v >= target:
            return now_utc(), v
        time.sleep(interval_s)
    return None, last


# --------------------------------------------------------------------------- labels
def write_label(row):
    os.makedirs(LABELS_DIR, exist_ok=True)
    with open(LABELS_FILE, "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


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
        "injector": injector, "ramp": True, "duration": duration,
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
        "injector": injector, "ramp": False, "duration": duration,
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
    injector = inj.NetemImpair(target, iface, rate_kbit=rate)
    probe = f'max(sdwan_tunnel_latency_ms{{device="{target}"}})'
    return {
        "type": "brownout",
        "target": {"device": target, "interface": iface, "rate_kbit": rate},
        "injector": injector, "ramp": False, "duration": duration,
        "probe": probe, "threshold": 6.0, "impact_method": "vm_threshold",
        "signature": "bandwidth starvation; queueing latency climbs under load, loss late",
    }


def scen_mpls_underlay_failure(target, severity, duration):
    """Bring down a P-router core interface toward a PE; LDP reconverges via dual-homing."""
    from faults.injectors import MplsUnderlayFailure
    # P routers have PE-facing ifaces after P-P links.
    # At p_count=8: first PE-facing iface is eth8 (eth1..eth7 = P-P links + loopback).
    # ponytail: use eth8 as a safe default; P1 connects to PE1 on eth8.
    iface = "eth8"
    injector = MplsUnderlayFailure(target, iface, down_seconds=float(duration))
    return {
        "type": "mpls_underlay_failure", "target": target, "severity": severity,
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "threshold": None, "impact_method": "modelled",
        "signature": "P-PE link down; LDP must reconverge to secondary path (~1s with BFD)",
    }


def scen_ldp_session_flap(target, severity, duration):
    """Flap an LDP session on a PE; self-recovers; generates LDP events in Loki."""
    from faults.injectors import LdpSessionFlap
    sev_count = {"low": 1, "medium": 2, "high": 3}.get(str(severity), 1)
    # PE loopback peer: pe1 -> 10.255.1.1 (p-router side). Use first P loopback.
    neighbor_ip = "10.255.1.1"
    injector = LdpSessionFlap(target, neighbor_ip, count=sev_count, gap_seconds=6.0)
    return {
        "type": "ldp_session_flap", "target": target, "severity": severity,
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "threshold": None, "impact_method": "modelled",
        "signature": "LDP session cleared N times; session self-recovers; Loki logs ldp_event=Down/Up",
    }


def scen_hub_spoke_congest(target, severity, duration):
    """Ramp netem congestion on hub uplink; all spokes routed through this hub degrade."""
    from faults.injectors import NetemImpair
    sev_kwargs = {
        "low":    {"delay_ms": 20,  "jitter_ms": 4,  "loss_pct": 0.5},
        "medium": {"delay_ms": 80,  "jitter_ms": 15, "loss_pct": 2.0},
        "high":   {"delay_ms": 200, "jitter_ms": 40, "loss_pct": 8.0},
    }.get(str(severity), {"delay_ms": 80, "jitter_ms": 15, "loss_pct": 2.0})
    injector = NetemImpair(target, "eth1", **sev_kwargs)
    probe = f'max(sdwan_tunnel_latency_ms{{source="{target}"}})'
    return {
        "type": "hub_spoke_congest", "target": target, "severity": severity,
        "injector": injector, "ramp": True, "duration": duration,
        "probe": probe, "threshold": 50.0, "impact_method": "vm_threshold",
        "signature": "hub uplink congestion; all spoke tunnel latencies rise",
    }


def scen_bgp_cascade(target, severity, duration):
    """Cascade BGP flaps on a hub CE; forces multiple path-switches; stresses RIB churn."""
    from faults.injectors import BgpFlap
    sev_count = {"low": 1, "medium": 3, "high": 5}.get(str(severity), 3)
    injector = BgpFlap(target, count=sev_count, gap_seconds=8.0)
    probe = "sdwan_path_changes_total"
    return {
        "type": "bgp_cascade", "target": target, "severity": severity,
        "injector": injector, "ramp": False, "duration": duration,
        "probe": probe, "threshold": 1.0, "impact_method": "vm_threshold",
        "signature": "repeated BGP session clears; multiple path-switches; sdwan_path_changes_total increments",
    }


class _DriftInjector:
    """Inline injector for controller drift (no new dep — uses urllib.request)."""
    CTRL_URL = "http://172.20.20.56"

    def __init__(self, site, mult, ttl_s):
        self.site = site
        self.mult = mult
        self.ttl_s = ttl_s

    def apply(self):
        import json as _json, urllib.request as _req
        body = _json.dumps({"site": self.site, "latency_threshold_mult": self.mult,
                            "ttl_s": self.ttl_s}).encode()
        _req.urlopen(f"{self.CTRL_URL}/fault/drift", data=body,
                     timeout=5)
        return {"applied": "controller_drift", "site": self.site, "mult": self.mult}

    def revert(self):
        import json as _json, urllib.request as _req
        body = _json.dumps({"site": self.site}).encode()
        _req.urlopen(f"{self.CTRL_URL}/fault/drift/clear", data=body, timeout=5)
        return {"reverted": "controller_drift", "site": self.site}


def scen_controller_drift(target, severity, duration):
    """Post drift suppression to the SD-WAN controller; prevents failover for the site."""
    mult = {"low": 5.0, "medium": 10.0, "high": 99.0}.get(str(severity), 10.0)
    injector = _DriftInjector(target, mult=mult, ttl_s=duration + 30)
    return {
        "type": "controller_drift", "target": target, "severity": severity,
        "injector": injector, "ramp": False, "duration": duration,
        "probe": None, "threshold": None, "impact_method": "modelled",
        "signature": "controller drift suppresses failover; sdwan_controller_drift_active rises",
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
}


# --------------------------------------------------------------------------- run
def run_scenario(name, target, severity="medium", duration=90, ramp_steps=6,
                 dry_run=False):
    """Execute one scenario end-to-end and write a label row. Returns the row."""
    if name not in SCENARIOS:
        raise SystemExit(f"unknown scenario '{name}'. choices: {list(SCENARIOS)}")
    spec = SCENARIOS[name](target, severity, duration)
    injector = spec["injector"]
    scenario_id = f"{name}-{target}-{uuid.uuid4().hex[:8]}"

    # Baseline read for vm_threshold scenarios (so we measure the *delta*).
    baseline = None
    if spec.get("probe"):
        baseline = vm_instant(spec["probe"])
        print(json.dumps({"event": "baseline", "scenario_id": scenario_id,
                          "probe": spec["probe"], "baseline": baseline}), flush=True)

    t_start = now_utc()
    print(json.dumps({"event": "inject", "scenario_id": scenario_id,
                      "type": spec["type"], "t_start": iso(t_start),
                      "dry_run": dry_run}), flush=True)

    if not dry_run:
        if spec.get("ramp"):
            injector.ramp(steps=ramp_steps,
                          step_seconds=max(3.0, duration / (ramp_steps * 2)))
        else:
            injector.apply()
        if spec.get("extra"):
            spec["extra"].apply()

    # --- t_impact ---
    if spec["impact_method"] == "vm_threshold" and spec.get("probe") and not dry_run:
        t_impact, observed = poll_threshold(
            spec["probe"], spec["threshold"], baseline=baseline,
            timeout_s=int(duration), interval_s=3)
        if t_impact is None:  # fell back: never crossed -> model it
            t_impact = t_start
            impact_method = "modelled_fallback"
        else:
            impact_method = "vm_threshold"
    else:
        impact_method = "modelled"
        t_impact = t_start  # offset applied below
        observed = None

    # apply modelled offset
    if impact_method.startswith("modelled"):
        delay = spec.get("impact_delay_s", 2)
        t_impact = datetime.fromtimestamp(t_start.timestamp() + delay, tz=timezone.utc)

    print(json.dumps({"event": "impact", "scenario_id": scenario_id,
                      "t_impact": iso(t_impact), "method": impact_method,
                      "observed": observed}), flush=True)

    # --- hold for the rest of the duration ---
    elapsed = time.time() - t_start.timestamp()
    remaining = duration - elapsed
    if remaining > 0 and not dry_run:
        time.sleep(remaining)

    # --- revert ---
    if not dry_run:
        if spec.get("extra"):
            spec["extra"].revert()
        injector.revert()
    t_end = now_utc()
    print(json.dumps({"event": "revert", "scenario_id": scenario_id,
                      "t_end": iso(t_end)}), flush=True)

    lead_time = round((t_impact - t_start).total_seconds(), 1)

    row = {
        "scenario_id": scenario_id,
        "type": spec["type"],
        "target": spec["target"],
        "severity": severity,
        "t_start": iso(t_start),
        "t_impact": iso(t_impact),
        "t_end": iso(t_end),
        "lead_time": lead_time,
        "impact_method": impact_method,
        "probe": spec.get("probe"),
        "baseline_value": baseline,
        "impact_value": observed,
        "signature": spec["signature"],
        "device": target,
    }
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
    row = run_scenario("congestion", target, severity="high", duration=60,
                       ramp_steps=5)
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
_PE_ALL      = [f"pe{i}"        for i in range(1, 11)]   # 10 PEs
_P_ALL       = [f"p{i}"         for i in range(1, 9)]    # 8 P routers

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
    "mpls_underlay_failure": _P_ALL,
    "ldp_session_flap":      _PE_ALL,
    "hub_spoke_congest":     _CE_HUBS,
    "bgp_cascade":           _CE_HUBS,
    "controller_drift":      _CE_HUBS,
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
}


def _campaign_fault(name, target, severity, duration, ramp_steps,
                    campaign_id, active_targets, lock, stats, dry_run):
    """Run one fault in a thread; guard active_targets; always revert."""
    with lock:
        if target in active_targets:
            # Another fault is already running on this target — skip silently.
            return
        active_targets.add(target)

    try:
        spec = SCENARIOS[name](target, severity, duration)
        injector = spec["injector"]
        scenario_id = f"{name}-{target}-{uuid.uuid4().hex[:8]}"

        baseline = None
        if spec.get("probe"):
            baseline = vm_instant(spec["probe"])

        t_start = now_utc()
        print(json.dumps({"event": "campaign_inject", "campaign_id": campaign_id,
                          "scenario_id": scenario_id, "type": name, "target": target,
                          "severity": severity, "duration": duration,
                          "t_start": iso(t_start), "dry_run": dry_run}), flush=True)

        if not dry_run:
            if spec.get("ramp"):
                injector.ramp(steps=ramp_steps,
                              step_seconds=max(2.0, duration / (ramp_steps * 2)))
            else:
                injector.apply()
            if spec.get("extra"):
                spec["extra"].apply()

        # t_impact (same logic as run_scenario)
        if spec["impact_method"] == "vm_threshold" and spec.get("probe") and not dry_run:
            t_impact, observed = poll_threshold(
                spec["probe"], spec["threshold"], baseline=baseline,
                timeout_s=int(duration), interval_s=3)
            impact_method = "vm_threshold" if t_impact else "modelled_fallback"
            if t_impact is None:
                t_impact = t_start
        else:
            impact_method = "modelled"
            t_impact = t_start
            observed = None

        if impact_method.startswith("modelled"):
            delay = spec.get("impact_delay_s", 2)
            t_impact = datetime.fromtimestamp(
                t_start.timestamp() + delay, tz=timezone.utc)

        # Hold for remainder of duration
        elapsed = time.time() - t_start.timestamp()
        remaining = duration - elapsed
        if remaining > 0 and not dry_run:
            time.sleep(remaining)

    finally:
        # Always revert, even on exception or SIGINT (finally fires on Thread.join timeout too)
        try:
            if not dry_run:
                if spec.get("extra"):
                    spec["extra"].revert()
                injector.revert()
        except Exception as e:
            print(json.dumps({"event": "revert_error", "scenario_id": scenario_id,
                              "error": str(e)}), flush=True)

        t_end = now_utc()
        print(json.dumps({"event": "campaign_revert", "campaign_id": campaign_id,
                          "scenario_id": scenario_id, "t_end": iso(t_end)}), flush=True)

        lead_time = round((t_impact - t_start).total_seconds(), 1)
        row = {
            "scenario_id": scenario_id,
            "campaign_id": campaign_id,
            "type": spec["type"],
            "target": spec["target"],
            "severity": severity,
            "t_start": iso(t_start),
            "t_impact": iso(t_impact),
            "t_end": iso(t_end),
            "lead_time": lead_time,
            "impact_method": impact_method,
            "probe": spec.get("probe"),
            "baseline_value": baseline,
            "impact_value": observed,
            "signature": spec["signature"],
            "device": target,
        }
        write_label(row)
        print(json.dumps({"event": "label_written", "row": row}), flush=True)

        with lock:
            active_targets.discard(target)
            stats["count"] += 1
            stats["by_type"][name] = stats["by_type"].get(name, 0) + 1
            stats["fault_seconds"] += (t_end - t_start).total_seconds()


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
    stats = {"count": 0, "by_type": {}, "fault_seconds": 0.0}

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

    healthy_seconds = total_duration - stats["fault_seconds"]
    summary = {
        "event": "campaign_summary",
        "campaign_id": campaign_id,
        "total_incidents": stats["count"],
        "by_type": stats["by_type"],
        "fault_seconds": round(stats["fault_seconds"], 1),
        "healthy_seconds": round(healthy_seconds, 1),
        "fault_pct": round(100 * stats["fault_seconds"] / total_duration, 1),
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
    ap.add_argument("--demo", action="store_true", help="run a short demo scenario")
    ap.add_argument("--dry-run", action="store_true",
                    help="write a label without touching the lab (schema check)")
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
                 duration=args.duration, ramp_steps=args.ramp_steps,
                 dry_run=args.dry_run)


if __name__ == "__main__":
    main()
