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


SCENARIOS = {
    "congestion": scen_congestion,            # (a) mandated
    "bgp_flap": scen_bgp_flap,                # (b) mandated
    "tunnel_degrade": scen_tunnel_degrade,    # (c) mandated
    "policy_drift": scen_policy_drift,        # (d) mandated
    "node_failure": scen_node_failure,        # adversarial extra
    "asymmetric_loss": scen_asymmetric_loss,  # adversarial extra
    "brownout": scen_brownout,                # adversarial extra
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


# --------------------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description="Fault orchestrator + label writer")
    ap.add_argument("--scenario", choices=list(SCENARIOS))
    ap.add_argument("--target", help="device name, e.g. ce_branch1 / pe1")
    ap.add_argument("--severity", choices=list(SEVERITY), default="medium")
    ap.add_argument("--duration", type=float, default=90)
    ap.add_argument("--ramp-steps", type=int, default=6)
    ap.add_argument("--list", action="store_true", help="list scenarios and exit")
    ap.add_argument("--demo", action="store_true", help="run a short demo scenario")
    ap.add_argument("--dry-run", action="store_true",
                    help="write a label without touching the lab (schema check)")
    args = ap.parse_args()

    if args.list:
        for n, fn in SCENARIOS.items():
            print(f"{n:16s} {fn.__doc__.strip().splitlines()[0]}")
        return
    if args.demo:
        demo()
        return
    if not args.scenario or not args.target:
        ap.error("--scenario and --target are required (or use --demo / --list)")
    run_scenario(args.scenario, args.target, severity=args.severity,
                 duration=args.duration, ramp_steps=args.ramp_steps,
                 dry_run=args.dry_run)


if __name__ == "__main__":
    main()
