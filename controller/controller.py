#!/usr/bin/env python3
"""Simulated SD-WAN controller.

Holds overlay policy (each spoke is dual-homed to both hubs; a per-VRF path
preference picks the primary hub), derives per-tunnel metrics, does latency/loss
path selection with failover, and exposes everything as Prometheus text on HTTP
so Telegraf (Phase 2) scrapes it directly — no extra dependency.

WHAT IS AND IS NOT MEASURED (the emitted series are SIMULATED, see
render_prometheus HELP text): the wg0 RTT/loss is measured; the congestion,
jitter and loss-burst terms are modelled; and the FAULT term is neither — it is
the netem impairment read back out of the site uplink's qdisc *config*, because
the wg0 ping does not traverse that uplink. Fault scenarios that threshold on
these series are thresholding a modelled value.

# ponytail: tunnel latency is MEASURED — a background pool pings the hub's wg0 IP
#   from inside each spoke (`docker exec ... ping -I wg0`) on a ~45s cadence and
#   caches min/avg/max/loss; update() reads the cache each tick and layers the
#   tuned congestion/jitter/loss model on top. Propagation comes from the per-site
#   eth0 netem the generator emits (geography formula lives there, ONE source).
#   Faults still inject on eth1 (wg0 ping won't see them), so _read_netem(eth1)
#   stays as the fault term. Gated behind MEASURE_RTT (off in --selftest/no-lab →
#   graceful fallback to a 1ms floor + the existing layers).
#   Ceiling: modelled jitter/loss are statistical, not the exact dataplane behaviour.

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
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "trafficgen"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "faults"))
import diurnal  # noqa: E402  (shared utilization model)
import signatures  # noqa: E402  -- ONE ramp table, shared with the dataset generator
from topo import build_model  # noqa: E402

OVERLAY_STEP = 5.0  # prog() dur-floor; matches the default tick interval
OVERLAY_SEVMUL = {"low": 0.5, "medium": 0.8, "high": 1.0}
# Fallback bases for signatures.default_signatures() when the calibration profile
# is absent (e.g. --selftest, no dataset). The relative-peak tunnel_ramp faults
# (asymmetric_loss, gray_failure) DO scale off these, so an overlay posted without
# a profile emits a weaker-but-present signal; the profile path below is exact.
OVERLAY_BASE_LAT, OVERLAY_BASE_LOSS, OVERLAY_BASE_JIT = 30.0, 0.3, 3.0
_PROFILE = os.path.join(os.path.dirname(__file__), "..", "synthetic", "profile.json")


def _load_fault_signatures():
    """The fault->signature table the overlay ramps toward.

    PREFERS the calibration artifact (synthetic/profile.json 'fault_signatures'):
    it carries the REAL-derived peaks + lead_s the dataset generator itself uses
    (synthetic/generate.py:391), so a live overlay is in-distribution with training
    by construction -- the whole point of #59. Falls back to the shared defaults so
    the controller still runs with no dataset present.
    """
    try:
        with open(_PROFILE) as fh:
            fs = json.load(fh)["fault_signatures"]
        if fs:
            return fs
    except Exception:
        pass
    return signatures.default_signatures(
        OVERLAY_BASE_LAT, OVERLAY_BASE_LOSS, OVERLAY_BASE_JIT)

# --- Policy: per-VRF preferred hub. VOICE/CORP prefer hub1 (primary), GUEST hub2.
# Path selection may override this on degradation (failover).
VRF_PREFERRED_HUB = {"CORP": "ce_hub1", "VOICE": "ce_hub1", "GUEST": "ce_hub2"}

# ----------------------------------------------------------------------------
# Per-tunnel propagation latency is now MEASURED (ping over wg0), not modelled —
# the geography formula lives once in the generator (site_netem(), emitted as a
# per-site eth0 root netem). The controller measures the real RTT and layers the
# tuned congestion/jitter/loss model on top. See _measure_rtt() + update().
# ----------------------------------------------------------------------------
# Per-site-type queue sensitivity multiplier (branches have thinner uplinks, so
# congestion bites harder -> steeper queue climb).
SITE_QUEUE_MULT = {"dc": 0.6, "hub": 0.8, "branch": 1.3}

# VOICE-class paths are policed tighter and are more loss/jitter sensitive; this
# scales the modelled jitter/loss on a tunnel that carries VOICE.
VOICE_SENSITIVITY = 1.4

# Minimal latency floor (ms) used until the measured-RTT cache is populated (or
# when MEASURE_RTT is off / a ping fails). NOT the old geography model — just a
# small positive seed so series start plausible.
MEASURE_FLOOR_MS = 1.0


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
        self.hub_wg = spec["hub_wg"]
        self.vrfs = spec["vrfs"]
        # Propagation reference (ms): seeded at a minimal floor, refreshed to the
        # MEASURED avg RTT once the ping cache fills. Used for series seeding and as
        # the failover-latency baseline. NO geography model here — it's measured.
        self.base_ms = MEASURE_FLOOR_MS
        # Per-site-type queue sensitivity (thin branch uplinks congest harder).
        self.queue_mult = SITE_QUEUE_MULT.get(self.site_type, 1.0)
        # VOICE-carrying tunnels are more loss/jitter sensitive.
        self.voice = "VOICE" in self.vrfs
        # smoothed metrics, seeded at baseline so series start plausible
        self.latency_ms = self.base_ms
        self.jitter_ms = 0.5 + self.base_ms * 0.04
        self.loss_pct = 0.0
        self.rekeys = 0  # cumulative WireGuard rekey counter
        # Deterministic per-tunnel RNG so the noise realization is stable per
        # tunnel AND across restarts. NOT hash(): CPython randomises str hashing
        # per process unless PYTHONHASHSEED is set, which reseeded every tunnel
        # on every controller restart. crc32 is stable by definition.
        self._rng = random.Random(zlib.crc32(self.tunnel.encode()))
        # Correlated-jitter state: an AR(1) random walk (not white noise) so jitter
        # wanders the way real path jitter does, with brief excursions.
        self._jit_walk = 0.0
        # Micro-burst loss state: a countdown of remaining "burst" ticks; when >0
        # the tunnel is in a transient loss event (brief, occasional).
        self._burst_ticks = 0
        self._burst_loss = 0.0
        self._last_rekey = time.time()
        self._rekey_debt = 0  # queued clustered rekeys (handshake retries under stress)
        # Measured-RTT cache: (avg_ms, jitter_ms, loss_pct) or None. Written by the
        # Controller's background ping pool (~45s cadence); read each 5s tick. None
        # until the first refresh / when MEASURE_RTT off → falls back to floor.
        self._measured = None

    # Set True by --selftest so the model is exercised hermetically (no docker
    # exec round-trips, which are slow and environment-dependent). Live runs leave
    # it False so injected netem still folds into the telemetry.
    _SKIP_NETEM = False

    # Measured-RTT gate: on (env MEASURE_RTT) only in the deployed container; off
    # in --selftest/--once/no-lab so the controller still runs without pinging.
    _MEASURE_RTT = os.environ.get("MEASURE_RTT", "") not in ("", "0", "false")

    def _measure_rtt(self):
        """Ping the hub's wg0 overlay IP from inside this spoke over wg0.

        Returns (avg_ms, jitter_ms, loss_pct), or None when the ping could not be
        run/parsed at all (graceful fallback). busybox ping: `round-trip
        min/avg/max = a/b/c ms` + `N% packet loss`; no mdev, so jitter = max-min.

        At 100% loss there is NO round-trip line, so avg_ms is None — that is a
        REAL result (the tunnel is dead) and must not be discarded, otherwise the
        cache freezes at the last healthy values and the telemetry keeps
        reporting a pre-outage RTT through a total outage.
        """
        cname = f"clab-sdwan_mpls_noc-{self.site}"
        try:
            out = subprocess.run(
                ["docker", "exec", cname, "ping", "-c2", "-q", "-W1",
                 "-I", "wg0", self.hub_wg],
                capture_output=True, text=True, timeout=5,
            ).stdout
        except Exception:
            return None
        avg = mn = mx = None
        loss = 0.0
        for ln in out.splitlines():
            if "packet loss" in ln:
                for tok in ln.split(","):
                    if "packet loss" in tok:
                        loss = _parse_pct(tok.strip().split()[0])
            if "min/avg/max" in ln and "=" in ln:
                nums = ln.split("=", 1)[1].strip().split()[0].split("/")
                if len(nums) >= 3:
                    mn, avg, mx = (float(nums[0]), float(nums[1]), float(nums[2]))
        if avg is None:
            # Unreachable (total loss) vs unparseable output: only the former is
            # a measurement worth caching.
            return (None, 0.0, loss) if loss >= 100.0 else None
        return avg, max(0.0, mx - mn), loss

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
        if TunnelState._SKIP_NETEM:
            return 0.0, 0.0
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

    def update(self, now, netem=None, overlay=None):
        """Recompute modelled metrics for this tick, coupled to the diurnal curve.

        `netem` is the (delay_ms, loss_pct) readback for this tunnel's SITE,
        hoisted by Controller.tick() so it costs one docker exec per site per
        tick instead of one per tunnel (6 tunnels share every spoke's uplink).
        Passing None falls back to reading it here.

        `overlay` is the active fault-overlay record for this tunnel's SITE (or
        None). When present it is AUTHORITATIVE: the tunnel series ramp toward the
        calibrated signature peak on `signatures.prog(elapsed vs the drawn lead)`,
        and the netem readback addend is suppressed so a simultaneous real netem
        does NOT double-count. On clear the overlay is dropped and the series glide
        back to baseline through the existing exponential smoothing.

        The same curve that drives offered load (diurnal.util) drives congestion
        here, so telemetry and traffic move together. A nonlinear M/M/1-style
        queue term makes latency/jitter climb sharply as utilization -> 1, and a
        weekly envelope makes weekends visibly calmer.
        """
        hod = diurnal.hour_of_cycle(now, PERIOD_SECONDS)
        wk = diurnal.week_scale(now, PERIOD_SECONDS)
        # Congestion proxy: max VRF utilization on this tunnel drives queueing,
        # scaled by the weekly envelope (weekend tunnels sit less congested).
        cong = max((diurnal.util(hod, v) for v in self.vrfs), default=0.3) * wk
        cong = max(0.0, min(0.985, cong))  # cap below 1 so the queue term stays finite

        # eth1 readback = the FAULT term. NOT a measurement: it is the impairment
        # someone CONFIGURED on the uplink, read back out of the qdisc, because
        # the wg0 ping does not traverse eth1. See render_prometheus() HELP text.
        netem_delay, netem_loss = netem if netem is not None else self._read_netem()

        # Overlay is authoritative: while active, the calibrated ramp is the ONLY
        # fault term -- zero the netem readback so the real tc action (which still
        # installs at impact) is not added on top of the signature (no double-count).
        ov_p = 0.0
        if overlay is not None:
            ov_p = float(signatures.prog(
                now, overlay["t_start"], overlay["t_impact"], overlay["t_end"],
                overlay["dur"], overlay["sevmul"], OVERLAY_STEP, overlay["p_cross"]))
            netem_delay = netem_loss = 0.0

        # --- Measured propagation (ping over wg0, cached) -------------------------
        # The per-site eth0 netem the generator emits is what the ping actually sees.
        # Cache is refreshed by the Controller's background pool; here we just read
        # it. Empty/None → minimal floor (NOT the deleted geography model).
        if self._measured is not None:
            meas_avg, meas_jit, meas_loss = self._measured
            if meas_avg is None:
                # Tunnel unreachable (100% loss): no RTT to report. Hold the last
                # latency baseline and let the measured 100% loss dominate, so a
                # total outage is visible instead of looking pre-outage healthy.
                meas_avg = self.base_ms
            else:
                self.base_ms = meas_avg      # keep the failover baseline on real RTT
        else:
            meas_avg, meas_jit, meas_loss = MEASURE_FLOOR_MS, 0.0, 0.0

        # --- Nonlinear queueing delay (M/M/1: wait ~ rho/(1-rho)) -----------------
        # As utilization (rho) approaches 1 the queue blows up; multiplied by the
        # per-site-type sensitivity (thin branch uplinks feel it more). Capped so a
        # near-saturation tick can't emit absurd RTT.
        rho = cong
        queue_ms = min(60.0, self.queue_mult * 9.0 * rho / (1.0 - rho))

        # --- Correlated jitter: AR(1) walk, not white noise -----------------------
        # Jitter is the short-term variance of the queue. We drive an AR(1) process
        # (memory 0.85) so it wanders smoothly with occasional excursions, and its
        # amplitude grows with congestion + injected delay variance.
        amp = 0.25 + 1.6 * rho + netem_delay * 0.12
        self._jit_walk = 0.85 * self._jit_walk + 0.15 * self._rng.gauss(0, amp)
        voice_k = VOICE_SENSITIVITY if self.voice else 1.0
        # jitter = measured (max-min) + AR(1) congestion walk.
        target_jit = max(0.0, meas_jit + (0.4 + abs(self._jit_walk) + 0.4 * rho) * voice_k)

        # --- Loss: mostly 0-0.3%, congestion-driven tail, plus micro-bursts -------
        # Baseline loss is a small noisy floor (link is healthy ~0-0.3%). A gentle
        # congestion tail kicks in only when the queue is deep. On top, rare brief
        # micro-bursts (buffer overrun / transient reroute) spike loss for a few
        # ticks then clear — the kind of transient the ML team needs to see.
        floor_loss = max(0.0, self._rng.gauss(0.08, 0.06))      # ~0-0.3% healthy floor
        cong_tail = max(0.0, (rho - 0.80)) ** 2 * 22.0          # only deep queues lose
        if self._burst_ticks > 0:
            self._burst_ticks -= 1
        else:
            # Micro-burst probability rises with congestion; small even when calm.
            p_burst = 0.004 + rho * 0.05
            if self._rng.random() < p_burst:
                self._burst_ticks = self._rng.randint(1, 4)
                self._burst_loss = self._rng.uniform(0.6, 3.5) * voice_k
        burst_loss = self._burst_loss if self._burst_ticks > 0 else 0.0
        # loss = max(measured, modelled floor) + congestion tail + bursts + fault.
        modelled_loss = (floor_loss + cong_tail) * voice_k
        target_loss = max(meas_loss, modelled_loss) + burst_loss + netem_loss

        # latency = measured avg + modelled congestion queue + fault (eth1) + noise.
        target_lat = meas_avg + queue_ms + netem_delay + self._rng.gauss(0, 0.4)

        # --- Calibrated overlay ramp (authoritative fault term) -------------------
        # Move each target toward the signature peak by the ramp fraction, exactly
        # as the dataset generator does (signatures.tunnel_ramp_targets + loss bump).
        # Same shared math -> live telemetry is in-distribution with training.
        if overlay is not None:
            sig = overlay["sig"]
            lat_t, jit_t = signatures.tunnel_ramp_targets(sig, target_lat, target_jit)
            target_lat = target_lat + ov_p * (float(lat_t) - target_lat)
            target_jit = target_jit + ov_p * (float(jit_t) - target_jit)
            target_loss = target_loss + ov_p * sig["loss_peak"]

        # Exponential smoothing so the series looks like a real time-series. Loss is
        # smoothed lightly so micro-bursts stay visibly spiky rather than averaged out.
        a = 0.3
        self.latency_ms = max(0.1, (1 - a) * self.latency_ms + a * target_lat)
        self.jitter_ms = max(0.0, (1 - a) * self.jitter_ms + a * target_jit)
        self.loss_pct = max(0.0, 0.45 * self.loss_pct + 0.55 * target_loss)

        # --- Rekey cadence + clustering (flap precursor) --------------------------
        # WireGuard rekeys ~every 2 min of real time. Under stress (high loss),
        # handshakes retry and rekeys CLUSTER: we accrue "debt" that drains as a
        # burst of rekeys over the next few ticks — a precursor the ML can learn.
        rekey_interval = 120.0 / (1.0 + self.loss_pct * 0.5)
        fired = False
        if self.loss_pct > 2.0 and self._rng.random() < (self.loss_pct - 2.0) * 0.04:
            self._rekey_debt += self._rng.randint(1, 3)  # stress-induced retry cluster
        if self._rekey_debt > 0:
            self.rekeys += 1
            self._rekey_debt -= 1
            self._last_rekey = now
            fired = True
        elif now - self._last_rekey >= rekey_interval:
            self.rekeys += 1
            self._last_rekey = now
            fired = True
        return fired  # signals a rekey event this tick


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
        self._drift = {}  # {site: {"latency_threshold_mult": float, "expires": float|None}}
        # {site: overlay record}. Set by set_overlay(); folded into each tunnel's
        # metrics while active; pruned in tick() when past its episode end.
        self._overlay = {}
        self._sigs = _load_fault_signatures()  # calibrated fault->signature table
        self._sites = {t.site for t in self.tunnels}  # valid overlay targets
        for t in self.tunnels:
            for v in t.vrfs:
                self.active.setdefault((t.site, v), VRF_PREFERRED_HUB.get(v, t.hub))

    def set_overlay(self, site, fault_type, lead_s=None, duration=60.0,
                    severity="high", t_start=None):
        """Register a fault overlay for a site (buildup -> peak -> hold episode).

        Mirrors the generator's episode: ramp over the drawn `lead_s` to t_impact,
        peak at t_impact+0.3*dur, decay to baseline by t_end. Returns the record.
        `lead_s=None` uses the signature's calibrated lead. Raises KeyError if
        fault_type is unknown (caller maps to HTTP 400).
        """
        sig = self._sigs[fault_type]
        lead_s = sig["lead_s"] if lead_s is None else float(lead_s)
        t0 = time.time() if t_start is None else float(t_start)
        t_impact = t0 + lead_s
        t_end = t_impact + float(duration)
        rec = {
            "fault_type": fault_type, "sig": sig,
            "t_start": t0, "t_impact": t_impact, "t_end": t_end,
            "dur": float(duration), "sevmul": OVERLAY_SEVMUL.get(str(severity), 1.0),
            "p_cross": 1.0, "expires": t_end,
        }
        self._overlay[site] = rec
        return rec

    def clear_overlay(self, site):
        self._overlay.pop(site, None)

    def refresh_measured(self, workers=16):
        """Refresh every tunnel's measured-RTT cache via a stdlib thread pool.

        Propagation is ~constant, so this runs on a slow (~45s) cadence — NOT every
        5s tick. Called once at startup (warm the cache) then by a background thread.
        Best-effort: a tunnel whose ping fails keeps its previous cache (or None).
        """
        if not TunnelState._MEASURE_RTT:
            return
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = pool.map(lambda t: (t, t._measure_rtt()), self.tunnels)
            for t, r in results:
                if r is not None:
                    t._measured = r

    def _measure_loop(self, period=45.0):
        while True:
            time.sleep(period)
            try:
                self.refresh_measured()
            except Exception:
                pass  # never let the ping pool kill the controller

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
                drift = self._drift.get(site, {})
                eff_mult = drift.get("latency_threshold_mult", FAILOVER_LATENCY_MULT)
                degraded = cur_t is None or (
                    cur_t.loss_pct >= FAILOVER_LOSS_PCT
                    or cur_t.latency_ms >= cur_t.base_ms * eff_mult
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
        # Prune expired entries IN PLACE (pop), never rebind from a snapshot: both
        # dicts are mutated by HTTP handler threads, and rebinding self._x = {...}
        # would silently drop a POST/clear that landed between the snapshot and the
        # assignment. Snapshot only the key list (avoids "dict changed size during
        # iteration"); each pop is atomic under the GIL.
        for k, v in list(self._drift.items()):
            if v["expires"] is not None and v["expires"] <= now:
                self._drift.pop(k, None)
        for k, v in list(self._overlay.items()):
            if v["expires"] is not None and v["expires"] <= now:
                self._overlay.pop(k, None)
        # One netem readback per SITE per tick (all of a site's tunnels share the
        # uplink), not one per tunnel: 28 docker execs instead of 168.
        netem_by_site = {}
        rekeys = []
        for t in self.tunnels:
            if t.site not in netem_by_site:
                netem_by_site[t.site] = t._read_netem()
            if t.update(now, netem=netem_by_site[t.site],
                        overlay=self._overlay.get(t.site)):
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

        # HONESTY (do not soften): these are SIMULATED series. Each is
        #   measured wg0 RTT/loss + a modelled congestion term + the netem
        #   impairment READ BACK OUT OF THE QDISC CONFIG on the site's eth1.
        # That last term is a configured value, not an observation: the wg0 ping
        # does not traverse eth1, so nothing here measures the injected fault.
        # It is also per-SITE, so every tunnel and every VRF of a site carries
        # the identical addend. A fault label whose t_impact is a threshold
        # crossing of these series is a crossing of a MODELLED series.
        metric("sdwan_tunnel_latency_ms",
               "SIMULATED per-tunnel RTT (ms): measured wg0 RTT + modelled congestion "
               "+ netem delay read back from the site uplink qdisc config", "gauge")
        for t in self.tunnels:
            lines.append(_m("sdwan_tunnel_latency_ms", t, t.latency_ms))
        metric("sdwan_tunnel_jitter_ms",
               "SIMULATED per-tunnel jitter (ms): measured wg0 max-min + modelled "
               "congestion walk", "gauge")
        for t in self.tunnels:
            lines.append(_m("sdwan_tunnel_jitter_ms", t, t.jitter_ms))
        metric("sdwan_tunnel_loss_pct",
               "SIMULATED per-tunnel loss percent: measured wg0 loss + modelled floor/"
               "bursts + netem loss read back from the site uplink qdisc config", "gauge")
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
        # NOTE: fabric-wide and UNLABELLED — it cannot be attributed to a device,
        # and the modelled micro-burst RNG moves it on its own with no fault
        # injected. Not usable as fault-impact evidence.
        metric("sdwan_path_changes_total",
               "Cumulative path-selection changes across the whole fabric "
               "(unlabelled; also moves from the modelled loss bursts)", "counter")
        lines.append(f"sdwan_path_changes_total {self.path_changes}")

        for site in list(self._drift):
            lines.append(f'sdwan_controller_drift_active{{site="{site}"}} 1')

        metric("sdwan_overlay_active",
               "1 while a calibrated fault overlay is ramping the tunnel series "
               "for this site (authoritative fault term; suppresses netem readback)",
               "gauge")
        for site, ov in sorted(self._overlay.items()):
            lines.append(f'sdwan_overlay_active{{site="{site}",'
                         f'fault_type="{ov["fault_type"]}"}} 1')

        return "\n".join(lines) + "\n"


def _m(name, t, val):
    lbl = (f'{{device="{t.site}",tunnel="{t.tunnel}",site="{t.site}",site_type="{t.site_type}",'
           f'hub="{t.hub}"}}')
    return f"{name}{lbl} {val:.4f}" if isinstance(val, float) else f"{name}{lbl} {val}"


def _handler_factory(ctrl):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.rstrip("/") == "/fault/overlay":
                # Live overlay registry -- the env-metrics sidecar reads this to
                # drive the same ramp fraction into optics/thermal (#59 T3).
                self._send_json({site: {k: ov[k] for k in
                                        ("fault_type", "t_start", "t_impact",
                                         "t_end", "expires")}
                                 for site, ov in ctrl._overlay.items()})
                return
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

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except Exception:
                self.send_response(400); self.end_headers()
                return
            path = self.path.rstrip("/")
            if path == "/fault/drift":
                site = data.get("site")
                mult = float(data.get("latency_threshold_mult", 2.0))
                ttl = data.get("ttl_s")
                expires = (time.time() + float(ttl)) if ttl else None
                ctrl._drift[site] = {"latency_threshold_mult": mult, "expires": expires}
                self._send_json({"ok": True, "site": site, "mult": mult})
            elif path == "/fault/drift/clear":
                site = data.get("site")
                ctrl._drift.pop(site, None)
                self._send_json({"ok": True, "cleared": site})
            elif path == "/fault/overlay":
                site = data.get("site")
                ft = data.get("fault_type")
                sig = ctrl._sigs.get(ft) if isinstance(ft, str) else None
                sev = data.get("severity", "high")
                try:
                    lead_s = (sig["lead_s"] if data.get("lead_s") is None
                              else float(data["lead_s"]))
                    dur = float(data.get("duration", 60.0))
                except (TypeError, ValueError):
                    sig = None
                # Validate at the trust boundary: unknown site/fault, a non-tunnel
                # fault (only tunnel_ramp posts an overlay), a negative lead, a
                # duration too short to keep prog's knots monotonic, or an unknown
                # severity are all 400 -- never a phantom/garbage overlay.
                if (site not in ctrl._sites or sig is None
                        or sig.get("kind") != "tunnel_ramp"
                        or lead_s < 0 or dur < 2 * OVERLAY_STEP
                        or sev not in OVERLAY_SEVMUL):
                    self.send_response(400); self.end_headers()
                    return
                rec = ctrl.set_overlay(site, ft, lead_s=lead_s, duration=dur,
                                       severity=sev, t_start=data.get("t_start"))
                self._send_json({"ok": True, "site": site,
                                 "fault_type": rec["fault_type"],
                                 "t_impact": rec["t_impact"], "t_end": rec["t_end"]})
            elif path == "/fault/overlay/clear":
                site = data.get("site")
                ctrl.clear_overlay(site)
                self._send_json({"ok": True, "cleared": site})
            else:
                self.send_response(404); self.end_headers()

        def _send_json(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass  # quiet; events go to stdout as JSON
    return H


def serve(ctrl, port, interval):
    httpd = ThreadingHTTPServer(("0.0.0.0", port), _handler_factory(ctrl))
    import threading
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    # Warm the measured-RTT cache, then refresh it in the background (~45s). No-op
    # when MEASURE_RTT is off (refresh_measured returns immediately).
    ctrl.refresh_measured()
    threading.Thread(target=ctrl._measure_loop, daemon=True).start()
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
    TunnelState._SKIP_NETEM = True   # hermetic: model-only, no docker exec
    TunnelState._MEASURE_RTT = False  # no pinging in selftest (mirror _SKIP_NETEM)
    ctrl = Controller()
    n = len(ctrl.tunnels)
    # ponytail: dynamic — accept any positive count so selftest survives rescaling.
    assert n > 0, f"unexpected tunnel count {n}"

    # --- Measured-RTT cache: fallback (no measurement) seeds the floor, and an
    # injected measurement flows into latency. Geography is now MEASURED (the
    # formula lives in the generator's eth0 netem), so no tier check here.
    assert all(t.base_ms == MEASURE_FLOOR_MS for t in ctrl.tunnels), \
        "base_ms should seed at the measure floor when cache empty"
    probe = ctrl.tunnels[0]
    probe._measured = (42.0, 1.5, 0.2)  # simulate a cached ping result
    for j in range(40):
        probe.update(time.time() + j * 5.0)
    assert probe.base_ms == 42.0, "measured avg did not become the latency baseline"
    assert probe.latency_ms > 30.0, f"measured RTT not reflected: {probe.latency_ms}"
    probe._measured = None  # reset so it doesn't skew the bulk ticks below

    # Drive several ticks at a BUSY hour so congestion/queueing is exercised.
    busy = PERIOD_SECONDS * (14.0 / 24.0)
    for i in range(60):
        ctrl.tick(now=busy + i * 5.0)
    lat = [t.latency_ms for t in ctrl.tunnels]
    assert all(math.isfinite(x) and x > 0 for x in lat), "latency not finite/positive"
    assert all(t.loss_pct >= 0 for t in ctrl.tunnels), "negative loss"
    # Loss bounded: healthy fabric, no tunnel should be catastrophically lossy here.
    assert all(t.loss_pct < 40.0 for t in ctrl.tunnels), "loss unbounded"
    # Jitter present and positive somewhere (not a flat zero series).
    assert any(t.jitter_ms > 0.1 for t in ctrl.tunnels), "no jitter present"

    # --- Diurnal coupling: a sample tunnel is more congested at peak than trough -
    sample = ctrl.tunnels[0]
    peak_t = PERIOD_SECONDS * (14.0 / 24.0)
    trough_t = PERIOD_SECONDS * (3.0 / 24.0)
    # Re-derive latency cleanly at each hour (reset smoothing toward each target).
    def settle(t0):
        sample.latency_ms = sample.base_ms
        for j in range(40):
            sample.update(t0 + j * 5.0)
        return sample.latency_ms
    peak_lat = settle(peak_t)
    trough_lat = settle(trough_t)
    assert peak_lat > trough_lat, \
        f"no diurnal latency coupling: peak {peak_lat:.1f} !> trough {trough_lat:.1f}"

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
    assert n_series >= n * 4, f"too few series: {n_series}"

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

    # drift suppresses failover: a high latency_threshold_mult raises the failover
    # bar so a latency excursion on the preferred hub no longer trips failover.
    ctrl._drift[site] = {"latency_threshold_mult": 99.0, "expires": None}
    bad.loss_pct = 0.0
    bad.latency_ms = bad.base_ms * 5  # would trip default 3x, but drift 99x suppresses
    ctrl.select_paths()
    assert ctrl.active[(site, "CORP")] == pref, "drift did not suppress failover"
    ctrl._drift.clear()
    print("selftest: drift OK")

    print(f"controller selftest OK  tunnels={n} series={n_series} "
          f"measured_fallback_floor={MEASURE_FLOOR_MS} "
          f"peak_lat={peak_lat:.1f} trough_lat={trough_lat:.1f} "
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
