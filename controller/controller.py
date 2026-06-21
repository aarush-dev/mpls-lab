#!/usr/bin/env python3
"""Simulated SD-WAN controller.

Holds overlay policy (each spoke is dual-homed to both hubs; a per-VRF path
preference picks the primary hub), derives per-tunnel metrics, does latency/loss
path selection with failover, and exposes everything as Prometheus text on HTTP
so Telegraf (Phase 2) scrapes it directly — no extra dependency.

# ponytail: tunnel metrics are MODELLED (baseline + diurnal congestion + optional
#   live netem state read from the host), NOT measured by pinging inside containers.
#   This is the simpler, air-gap-safe choice and still fault-responsive: when the
#   fault orchestrator (later phase) runs `containerlab tools netem set` on a CE
#   uplink, we read that netem delay/loss back via `tc` and fold it into the signal,
#   so injected faults visibly perturb the emitted telemetry.
#   Ceiling: modelled jitter/loss are statistical, not the exact dataplane behaviour.
#   Upgrade path: swap _read_netem()/_model_tunnel() for real RTT (ping the WG peer
#   IP from inside the spoke container via `docker exec`) if higher fidelity is needed.

Run:
  python3 controller.py                 # serve Prometheus metrics on :9362, also log JSON events to stdout
  python3 controller.py --port 9362
  python3 controller.py --once          # print one scrape to stdout and exit
  python3 controller.py --selftest      # validate exposition + path logic, exit nonzero on failure
"""
import argparse
import json
import math
import os
import random
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "trafficgen"))
import diurnal  # noqa: E402  (shared utilization model)
from topo import build_model  # noqa: E402

# --- Policy: per-VRF preferred hub. VOICE/CORP prefer hub1 (primary), GUEST hub2.
# Path selection may override this on degradation (failover).
VRF_PREFERRED_HUB = {"CORP": "ce_hub1", "VOICE": "ce_hub1", "GUEST": "ce_hub2"}

# --- Per-tunnel baseline RTT (ms). Hub1 is the "closer/better" primary.
HUB_BASELINE_MS = {"ce_hub1": 12.0, "ce_hub2": 22.0}

# Degradation thresholds for failover (loss% or latency ms over baseline).
FAILOVER_LOSS_PCT = 5.0
FAILOVER_LATENCY_MULT = 3.0

PERIOD_SECONDS = float(os.environ.get("DIURNAL_PERIOD", "3600"))  # 24h compressed to 1h


class TunnelState:
    """Holds per-tunnel modelled metrics + smoothing so values evolve, not jump."""

    def __init__(self, spec):
        self.tunnel = spec["tunnel"]
        self.site = spec["site"]
        self.site_type = spec["site_type"]
        self.hub = spec["hub"]
        self.spoke_wg = spec["spoke_wg"]
        self.hub_wg = spec["hub_wg"]
        self.vrfs = spec["vrfs"]
        self.base_ms = HUB_BASELINE_MS.get(self.hub, 20.0)
        # smoothed metrics
        self.latency_ms = self.base_ms
        self.jitter_ms = 1.0
        self.loss_pct = 0.0
        self.rekeys = 0  # cumulative WireGuard rekey counter
        self._rng = random.Random(hash(self.tunnel) & 0xFFFFFFFF)
        self._last_rekey = time.time()

    def _read_netem(self):
        """Read injected netem delay/loss on the spoke's uplink, if present.

        Returns (extra_delay_ms, extra_loss_pct). Best-effort: returns (0, 0)
        if the lab is not deployed or docker.sock is not mounted.

        # ponytail: use `docker exec <clab-container> tc ...` via the mounted
        #   /var/run/docker.sock rather than `ip netns exec` — the netns path
        #   requires host-net privileges and silently fails inside a container.
        #   docker.sock is cheaper: mount it read-only and shell out to the
        #   docker CLI already in PATH (added to image). Best-effort; any
        #   exception returns (0, 0) so the controller still runs without a lab.
        """
        cname = f"clab-sdwan_mpls_noc-{self.site}"
        try:
            out = subprocess.run(
                ["docker", "exec", cname, "tc", "qdisc", "show", "dev", "eth1"],
                capture_output=True, text=True, timeout=2,
            ).stdout
        except Exception:
            return 0.0, 0.0
        delay_ms = loss_pct = 0.0
        if "netem" in out:
            toks = out.split()
            for i, tk in enumerate(toks):
                if tk == "delay" and i + 1 < len(toks):
                    delay_ms = _parse_time_ms(toks[i + 1])
                if tk == "loss" and i + 1 < len(toks):
                    loss_pct = _parse_pct(toks[i + 1])
        return delay_ms, loss_pct

    def update(self, now):
        """Recompute modelled metrics for this tick."""
        hod = diurnal.hour_of_cycle(now, PERIOD_SECONDS)
        # Congestion proxy: max VRF utilization on this tunnel drives queueing.
        cong = max((diurnal.util(hod, v) for v in self.vrfs), default=0.3)

        netem_delay, netem_loss = self._read_netem()

        # Latency = baseline + congestion queueing delay + injected netem + noise.
        target_lat = (self.base_ms
                      + cong ** 2 * 18.0       # nonlinear queue buildup near saturation
                      + netem_delay
                      + self._rng.gauss(0, 0.6))
        # Jitter grows with congestion and with injected delay variance.
        target_jit = 0.5 + cong * 3.0 + netem_delay * 0.15 + abs(self._rng.gauss(0, 0.3))
        # Loss: near-zero until congestion is high; netem loss adds directly.
        target_loss = max(0.0, (cong - 0.75) * 8.0) + netem_loss + max(0, self._rng.gauss(0, 0.05))

        # Exponential smoothing so the series looks like a real time-series.
        a = 0.3
        self.latency_ms = max(0.1, (1 - a) * self.latency_ms + a * target_lat)
        self.jitter_ms = max(0.0, (1 - a) * self.jitter_ms + a * target_jit)
        self.loss_pct = max(0.0, (1 - a) * self.loss_pct + a * target_loss)

        # Rekey: WireGuard rekeys ~every 2 min of real time; emit as an event/counter.
        # Under heavy loss, rekeys cluster (handshake retries) — a flap precursor signal.
        rekey_interval = 120.0 / (1.0 + self.loss_pct * 0.5)
        if now - self._last_rekey >= rekey_interval:
            self.rekeys += 1
            self._last_rekey = now
            return True  # signals a rekey event this tick
        return False


def _parse_time_ms(s):
    s = s.strip()
    try:
        if s.endswith("ms"):
            return float(s[:-2])
        if s.endswith("us"):
            return float(s[:-2]) / 1000.0
        if s.endswith("s"):
            return float(s[:-1]) * 1000.0
        return float(s)
    except ValueError:
        return 0.0


def _parse_pct(s):
    try:
        return float(s.rstrip("%"))
    except ValueError:
        return 0.0


class Controller:
    def __init__(self, spec=None):
        model = build_model(spec)
        self.tunnels = [TunnelState(t) for t in model["tunnels"]]
        # active path per (site, vrf) -> hub node. Seeded from policy.
        self.active = {}
        self.path_changes = 0
        for t in self.tunnels:
            for v in t.vrfs:
                self.active.setdefault((t.site, v), VRF_PREFERRED_HUB.get(v, t.hub))

    def _tunnels_for(self, site, hub):
        for t in self.tunnels:
            if t.site == site and t.hub == hub:
                return t
        return None

    def select_paths(self):
        """For each (site, vrf), pick the best hub by loss then latency, with
        hysteresis: only leave the preferred hub when it is clearly degraded.
        Returns list of change events emitted this round."""
        events = []
        # group vrfs by site
        site_vrfs = {}
        for t in self.tunnels:
            for v in t.vrfs:
                site_vrfs.setdefault(t.site, set()).add(v)

        for site, vrfs in site_vrfs.items():
            for v in vrfs:
                pref = VRF_PREFERRED_HUB.get(v)
                cur = self.active.get((site, v))
                # candidate tunnels for this site (both hubs)
                cands = [t for t in self.tunnels if t.site == site]
                if not cands:
                    continue
                # score: lower is better
                def score(t):
                    return t.loss_pct * 10.0 + t.latency_ms
                best = min(cands, key=score)
                cur_t = self._tunnels_for(site, cur) if cur else None
                # Failover only if current path is degraded beyond thresholds AND
                # the best alternative is meaningfully better (hysteresis 15%).
                degraded = cur_t is None or (
                    cur_t.loss_pct >= FAILOVER_LOSS_PCT
                    or cur_t.latency_ms >= cur_t.base_ms * FAILOVER_LATENCY_MULT
                )
                if best.hub != cur and degraded and score(best) < score(cur_t) * 0.85:
                    self.active[(site, v)] = best.hub
                    self.path_changes += 1
                    events.append({
                        "event": "path_change", "site": site, "vrf": v,
                        "from": cur, "to": best.hub,
                        "reason": "degradation",
                        "loss_pct": round(cur_t.loss_pct, 2) if cur_t else None,
                        "latency_ms": round(cur_t.latency_ms, 2) if cur_t else None,
                    })
                # Recover to preference when it is healthy again.
                elif cur != pref and pref is not None:
                    pref_t = self._tunnels_for(site, pref)
                    if pref_t and pref_t.loss_pct < FAILOVER_LOSS_PCT and \
                       pref_t.latency_ms < pref_t.base_ms * FAILOVER_LATENCY_MULT:
                        self.active[(site, v)] = pref
                        self.path_changes += 1
                        events.append({
                            "event": "path_change", "site": site, "vrf": v,
                            "from": cur, "to": pref, "reason": "recovery",
                        })
        return events

    def tick(self, now=None):
        """Advance the model one step; return (rekey_events, path_events)."""
        now = now or time.time()
        rekeys = []
        for t in self.tunnels:
            if t.update(now):
                rekeys.append({"event": "rekey", "tunnel": t.tunnel,
                               "site": t.site, "hub": t.hub, "count": t.rekeys})
        path_events = self.select_paths()
        return rekeys, path_events

    def render_prometheus(self):
        """Prometheus text exposition. Telegraf scrapes this verbatim.

        Every metric is tagged tunnel/site/site_type/hub; per-VRF policy metrics
        add vrf. See README.md for the full schema.
        """
        lines = []

        def metric(name, help_, typ):
            lines.append(f"# HELP {name} {help_}")
            lines.append(f"# TYPE {name} {typ}")

        metric("sdwan_tunnel_latency_ms", "Modelled per-tunnel one-way-ish RTT in ms", "gauge")
        for t in self.tunnels:
            lines.append(_m("sdwan_tunnel_latency_ms", t, t.latency_ms))
        metric("sdwan_tunnel_jitter_ms", "Modelled per-tunnel jitter in ms", "gauge")
        for t in self.tunnels:
            lines.append(_m("sdwan_tunnel_jitter_ms", t, t.jitter_ms))
        metric("sdwan_tunnel_loss_pct", "Modelled per-tunnel packet loss percent", "gauge")
        for t in self.tunnels:
            lines.append(_m("sdwan_tunnel_loss_pct", t, t.loss_pct))
        metric("sdwan_tunnel_rekeys_total", "Cumulative WireGuard rekey events", "counter")
        for t in self.tunnels:
            lines.append(_m("sdwan_tunnel_rekeys_total", t, t.rekeys))

        # Per-(site,vrf) policy state: which hub is active (1 on the active tunnel).
        metric("sdwan_path_active", "1 if this hub is the active path for site/vrf", "gauge")
        for (site, vrf), hub in sorted(self.active.items()):
            st = next((t.site_type for t in self.tunnels if t.site == site), "")
            lbl = f'{{device="{site}",site="{site}",site_type="{st}",vrf="{vrf}",hub="{hub}"}}'
            lines.append(f"sdwan_path_active{lbl} 1")
        metric("sdwan_path_changes_total", "Cumulative path-selection changes", "counter")
        lines.append(f"sdwan_path_changes_total {self.path_changes}")

        return "\n".join(lines) + "\n"


def _m(name, t, val):
    lbl = (f'{{device="{t.site}",tunnel="{t.tunnel}",site="{t.site}",site_type="{t.site_type}",'
           f'hub="{t.hub}"}}')
    return f"{name}{lbl} {val:.4f}" if isinstance(val, float) else f"{name}{lbl} {val}"


def _handler_factory(ctrl):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/metrics", "/"):
                self.send_response(404)
                self.end_headers()
                return
            body = ctrl.render_prometheus().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass  # quiet; events go to stdout as JSON
    return H


def serve(ctrl, port, interval):
    httpd = ThreadingHTTPServer(("0.0.0.0", port), _handler_factory(ctrl))
    import threading
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(json.dumps({"event": "controller_up", "port": port,
                      "tunnels": len(ctrl.tunnels), "interval_s": interval}),
          flush=True)
    while True:
        rekeys, paths = ctrl.tick()
        for e in rekeys + paths:
            print(json.dumps(e), flush=True)
        time.sleep(interval)


# ----------------------------------------------------------------------------- selftest
def _selftest():
    ctrl = Controller()
    assert len(ctrl.tunnels) == 12, f"expected 12 tunnels, got {len(ctrl.tunnels)}"

    # Drive several ticks; metrics must move and stay finite/sane.
    for i in range(50):
        ctrl.tick(now=i * 5.0)
    lat = [t.latency_ms for t in ctrl.tunnels]
    assert all(math.isfinite(x) and x > 0 for x in lat), "latency not finite/positive"
    assert all(t.loss_pct >= 0 for t in ctrl.tunnels), "negative loss"

    # Exposition must be well-formed: HELP/TYPE present, label set parseable,
    # values numeric, no NaN/inf tokens.
    text = ctrl.render_prometheus()
    assert "# HELP sdwan_tunnel_latency_ms" in text
    assert "# TYPE sdwan_tunnel_loss_pct gauge" in text
    n_series = 0
    for ln in text.splitlines():
        if ln.startswith("#") or not ln.strip():
            continue
        assert "{" in ln or ln.startswith("sdwan_path_changes_total"), f"bad line: {ln}"
        val = ln.rsplit(" ", 1)[1]
        f = float(val)  # raises if malformed
        assert math.isfinite(f), f"non-finite metric value: {ln}"
        n_series += 1
    assert n_series >= 12 * 4, f"too few series: {n_series}"

    # Path selection: force a degradation on the preferred CORP hub for a site and
    # confirm failover to the other hub, then recovery.
    site = "ce_branch1"
    pref = VRF_PREFERRED_HUB["CORP"]  # ce_hub1
    bad = ctrl._tunnels_for(site, pref)
    bad.loss_pct = 20.0
    bad.latency_ms = bad.base_ms * 5
    ctrl.select_paths()
    assert ctrl.active[(site, "CORP")] != pref, "failover did not occur on degradation"
    # heal
    bad.loss_pct = 0.0
    bad.latency_ms = bad.base_ms
    ctrl.select_paths()
    assert ctrl.active[(site, "CORP")] == pref, "did not recover to preferred hub"

    print(f"controller selftest OK  tunnels={len(ctrl.tunnels)} series={n_series} "
          f"path_changes={ctrl.path_changes}")


def main():
    ap = argparse.ArgumentParser(description="Simulated SD-WAN controller")
    ap.add_argument("--port", type=int, default=9362)
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between ticks")
    ap.add_argument("--once", action="store_true", help="print one scrape and exit")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return
    ctrl = Controller()
    if args.once:
        ctrl.tick()
        sys.stdout.write(ctrl.render_prometheus())
        return
    serve(ctrl, args.port, args.interval)


if __name__ == "__main__":
    main()
