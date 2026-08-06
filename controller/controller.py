#!/usr/bin/env python3
"""Simulated SD-WAN controller.

Holds overlay policy (each spoke is dual-homed to both hubs; a per-VRF path
preference picks the primary hub), derives per-tunnel metrics, does latency/loss
path selection with failover, and exposes everything as Prometheus text on HTTP
so Telegraf (Phase 2) scrapes it directly — no extra dependency.

WHAT IS AND IS NOT MEASURED (the emitted series are SIMULATED, see
render_prometheus HELP text): the 4 tunnel signals (latency, jitter, loss,
rekeys) are drawn ANALYTICALLY per tick — a per-site_type calibrated baseline
(synthetic/profile.json) + a diurnal bump — COPIED from the dataset generator
(synthetic/generate.py:_gen_tunnels) so live telemetry sits in the training
distribution. Nothing here is a dataplane measurement. The FAULT term is the
netem impairment read back out of the site uplink's qdisc *config* (_read_netem),
suppressed while a calibrated overlay is active (no double-count). Fault scenarios
that threshold on these series are thresholding a modelled value.

# ponytail: the 4 tunnel signals mirror the generator's formulas exactly (same
#   baselines, same diurnal, no EMA) so sim == training distribution. SOURCE OF
#   TRUTH: synthetic/generate.py:326-334 + :71-82; guarded by
#   controller/test_tunnel_model.py. The old wg0-ping RTT path is gone.
#   Ceiling: the draw is statistical, not real dataplane behaviour; that is the
#   deliberate trade to match the (also-analytic) training data.

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
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np  # already in the image (signatures.py is pure-numpy)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "faults"))
import signatures  # noqa: E402  -- ONE ramp table, shared with the dataset generator
from topo import build_model  # noqa: E402


def _diurnal(epoch):
    """0.15..1.0 business-hours load multiplier (peak ~15:00 UTC, trough ~03:00).

    COPIED byte-for-byte from the dataset generator so the live tunnel series sit in
    the same distribution as training. SOURCE OF TRUTH: synthetic/generate.py:_diurnal
    (71-82). Uses np.cos (not math.cos) so it matches the generator to the last bit --
    controller/test_tunnel_model.py cross-checks the two. Real UTC wall-clock (per the
    build decision): a full cycle spans 24h, NOT the compressed DIURNAL_PERIOD.
    """
    dt = datetime.fromtimestamp(epoch, timezone.utc)
    h = dt.hour + dt.minute / 60.0
    day = 0.5 - 0.5 * np.cos((h - 3) / 24.0 * 2 * np.pi)  # 0 at 03:00, 1 at 15:00
    weekend = 0.7 if dt.weekday() >= 5 else 1.0
    return float(0.15 + 0.85 * day * weekend)  # never fully idle

OVERLAY_STEP = 5.0  # prog() dur-floor; matches the default tick interval
OVERLAY_SEVMUL = {"low": 0.5, "medium": 0.8, "high": 1.0}

# p_cross = fraction of the calibrated peak at which the SLA is breached = t_impact.
# The generator (generate.py:630-641) sets this per episode, so its POSITIVE (pre-
# impact) windows sit at a SUBTLE precursor level (congestion ~36 ms / 1% loss) while
# the full peak (102 ms / 5.7%) is only reached at t_impact+0.3*dur, POST-impact. The
# live overlay hardcoded p_cross=1.0 -> full peak AT impact -> the served window is the
# out-of-distribution peaked state, so the model mis-types it. Reproduce the generator
# so the served precursor matches training. VOICE is the strictest (binding) SLA on
# every spoke tunnel; profile tunnel_baseline means = healthy floor.
_SLA_THETA_LAT, _SLA_THETA_LOSS = 150.0, 1.0          # VOICE (faults/leadpriors THETA_SLA)
_BASE_LAT_MEAN, _BASE_LOSS_MEAN = 29.06, 0.44         # synthetic/profile.json tunnel_baseline


def _p_cross(sig: dict, sevmul: float) -> float:
    """SLA-crossing fraction of peak, per generator generate.py:635-641 (min of the
    latency/loss crossings). Clamped to [0.02, 1.0]."""
    p_lat = (_SLA_THETA_LAT - _BASE_LAT_MEAN) / max(1e-6, (sig["lat_peak"] - _BASE_LAT_MEAN) * sevmul)
    p_loss = (_SLA_THETA_LOSS - _BASE_LOSS_MEAN) / max(1e-6, sig["loss_peak"] * sevmul)
    return float(min(1.0, max(0.02, min(p_lat, p_loss))))
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


# Healthy tunnel baselines the analytic per-bucket draw samples around. SOURCE OF
# TRUTH: synthetic/generate.py:_gen_tunnels (326-334) + these numbers from
# synthetic/profile.json. The fallback below is LOAD-BEARING: the deployed image now
# ships profile.json, but --selftest / any image without it must still emit the real
# distribution, so the calibrated means/stds are baked here (verbatim from the profile).
_FALLBACK_STEP_S = 30
_FALLBACK_BASELINES = {
    "tunnel_baseline": {
        "tunnel_latency_ms": {"mean": 29.0625, "std": 4.6911, "p50": 28.7545, "min": 16.3218, "max": 58.3231},
        "tunnel_jitter_ms":  {"mean": 2.9498, "std": 0.4007, "p50": 2.9267, "min": 2.1014, "max": 6.9712},
        "tunnel_loss_pct":   {"mean": 0.4382, "std": 0.5420, "p50": 0.0801, "min": 0.0003, "max": 5.6996},
        "tunnel_rekeys":     {"mean": 8.3193, "std": 4.2892, "p50": 8.0, "min": 1.0, "max": 17.0},
    },
    "tunnel_baseline_by_site": {
        "branch": {
            "tunnel_latency_ms": {"mean": 29.0070, "std": 4.7451, "p50": 28.6633, "min": 16.3218, "max": 58.3231},
            "tunnel_jitter_ms":  {"mean": 2.9423, "std": 0.4121, "p50": 2.9244, "min": 2.1014, "max": 6.9712},
            "tunnel_loss_pct":   {"mean": 0.4419, "std": 0.5482, "p50": 0.0828, "min": 0.0003, "max": 5.6996},
            "tunnel_rekeys":     {"mean": 8.3492, "std": 4.3017, "p50": 8.0, "min": 1.0, "max": 17.0},
        },
        "dc": {
            "tunnel_latency_ms": {"mean": 29.3905, "std": 4.3465, "p50": 29.1070, "min": 17.8530, "max": 37.9685},
            "tunnel_jitter_ms":  {"mean": 2.9944, "std": 0.3217, "p50": 2.9420, "min": 2.3156, "max": 3.7178},
            "tunnel_loss_pct":   {"mean": 0.4165, "std": 0.5032, "p50": 0.0709, "min": 0.0005, "max": 1.5108},
            "tunnel_rekeys":     {"mean": 8.1433, "std": 4.2116, "p50": 8.0, "min": 1.0, "max": 15.0},
        },
    },
}


def _load_tunnel_baselines():
    """(step_s, baselines) from synthetic/profile.json, falling back to the baked
    calibration. Same file the generator reads; one extra key read, no new dep."""
    try:
        with open(_PROFILE) as fh:
            prof = json.load(fh)
        gb = prof["tunnel_baseline"]  # KeyError -> fallback
        return int(prof.get("step_s", _FALLBACK_STEP_S)), {
            "tunnel_baseline": gb,
            "tunnel_baseline_by_site": prof.get("tunnel_baseline_by_site", {}),
        }
    except Exception:
        return _FALLBACK_STEP_S, _FALLBACK_BASELINES


STEP_S, _BASELINES = _load_tunnel_baselines()


def _baseline_for(site_type):
    """Per-site_type tunnel baseline, each field falling back to the global baseline
    (matches synthetic/generate.py:320-324)."""
    gb = _BASELINES["tunnel_baseline"]
    tb = _BASELINES["tunnel_baseline_by_site"].get(site_type, gb)
    return {sig: tb.get(sig, gb[sig]) for sig in
            ("tunnel_latency_ms", "tunnel_jitter_ms", "tunnel_loss_pct", "tunnel_rekeys")}


# --- Policy: per-VRF preferred hub. VOICE/CORP prefer hub1 (primary), GUEST hub2.
# Path selection may override this on degradation (failover).
VRF_PREFERRED_HUB = {"CORP": "ce_hub1", "VOICE": "ce_hub1", "GUEST": "ce_hub2"}

# ----------------------------------------------------------------------------
# The 4 tunnel signals are now drawn ANALYTICALLY per tick from the calibrated
# per-site_type baselines + the diurnal bump, copied from the dataset generator
# (synthetic/generate.py:_gen_tunnels) so live telemetry sits in the training
# distribution. No ping, no M/M/1 queue, no AR(1) walk — see update().
# ----------------------------------------------------------------------------

# Degradation thresholds for failover (loss% or latency ms over baseline).
FAILOVER_LOSS_PCT = 5.0
FAILOVER_LATENCY_MULT = 3.0


class TunnelState:
    """Per-tunnel analytic telemetry: each tick draws the 4 signals around the
    calibrated baseline + diurnal bump (copied from the dataset generator)."""

    def __init__(self, spec, baseline):
        self.tunnel = spec["tunnel"]
        self.site = spec["site"]
        self.site_type = spec["site_type"]
        self.hub = spec["hub"]
        self.vrfs = spec["vrfs"]
        # Calibrated per-site_type baselines the analytic draw samples around
        # (synthetic/generate.py:320-334). lat/jit/loss use mean+std; rekeys min/max.
        self._lat = baseline["tunnel_latency_ms"]
        self._jit = baseline["tunnel_jitter_ms"]
        self._loss = baseline["tunnel_loss_pct"]
        rk = baseline["tunnel_rekeys"]
        # Deterministic per-tunnel RNG so the noise realization is stable per
        # tunnel AND across restarts. NOT hash(): CPython randomises str hashing
        # per process unless PYTHONHASHSEED is set, which reseeded every tunnel
        # on every controller restart. crc32 is stable by definition.
        self._rng = random.Random(zlib.crc32(self.tunnel.encode()))
        # Failover-latency baseline = the healthy mean (was the measured RTT).
        self.base_ms = self._lat["mean"]
        # Emitted metrics, seeded plausibly at baseline.
        self.latency_ms = self._lat["mean"]
        self.jitter_ms = self._jit["mean"]
        self.loss_pct = 0.0
        # rekeys: a per-tunnel running counter seeded ONCE from the baseline range
        # (generate.py:326), then bumped spontaneously (generate.py:333) — inert,
        # NOT loss-coupled (the fault ramp never touches rekeys, so neither do we).
        self.rekeys = float(self._rng.randint(int(rk["min"]), int(rk["max"])))
        self._last_tick = None  # previous update() `now`, for the rekey rate

    # Set True by --selftest so the model is exercised hermetically (no docker
    # exec round-trips, which are slow and environment-dependent). Live runs leave
    # it False so injected netem still folds into the telemetry.
    _SKIP_NETEM = False

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

    def update(self, now, netem=None, overlay=None, d=None):
        """Recompute the tunnel series for this tick.

        The 4 signals are drawn ANALYTICALLY around the calibrated per-site_type
        baseline + a diurnal bump, COPIED from the dataset generator
        (synthetic/generate.py:_gen_tunnels 326-334) so live telemetry sits in the
        training distribution. `d` is _diurnal(now); hoisted by Controller.tick()
        (identical across all tunnels at a given `now`), computed here if None.

        `netem` is the (delay_ms, loss_pct) eth1 readback for this tunnel's SITE
        (the FAULT term), hoisted by tick(); None falls back to reading it here.

        `overlay` is the active fault-overlay record for this tunnel's SITE (or
        None). When present it is AUTHORITATIVE: the series ramp toward the
        calibrated signature peak on `signatures.prog`, and the netem readback is
        suppressed so a simultaneous real netem does NOT double-count.
        """
        if d is None:
            d = _diurnal(now)

        # eth1 readback = the FAULT term (a configured impairment, NOT a measurement;
        # the wg0 ping path is gone). See render_prometheus() HELP text.
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

        # --- Analytic healthy draw (copied from generate.py:330-332) --------------
        # per-site baseline normal + diurnal bump; the eth1 fault term rides on top
        # of latency/loss (0 when healthy).
        lat, jit, loss = self._lat, self._jit, self._loss
        target_lat = max(1.0, self._rng.gauss(lat["mean"], lat["std"] * 0.4) + d * 8.0) \
            + netem_delay
        target_jit = max(0.1, self._rng.gauss(jit["mean"], jit["std"] * 0.5) + d * 0.5)
        target_loss = max(0.0, self._rng.gauss(loss["mean"], max(loss["std"], 0.05) * 0.5)) \
            + d * 0.02 + netem_loss

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

        # NO smoothing: the generator draws each bucket independently, so an EMA
        # would shrink the marginal variance below the training distribution.
        self.latency_ms = max(0.1, target_lat)
        self.jitter_ms = max(0.0, target_jit)
        self.loss_pct = max(0.0, target_loss)

        # --- Rekeys: inert running counter (generate.py:326,333) -------------------
        # Seeded once in __init__; bumped spontaneously at the generator's per-bucket
        # 0.002 rate, RESCALED to the actual tick elapsed / STEP_S (dataset buckets
        # are STEP_S apart; the live tick is shorter). No loss coupling.
        elapsed = 0.0 if self._last_tick is None else max(0.0, now - self._last_tick)
        self._last_tick = now
        fired = False
        if self._rng.random() < 0.002 * (elapsed / STEP_S):
            self.rekeys += 1
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
        self.tunnels = [TunnelState(t, _baseline_for(t["site_type"]))
                        for t in model["tunnels"]]
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
            "p_cross": _p_cross(sig, OVERLAY_SEVMUL.get(str(severity), 1.0)), "expires": t_end,
        }
        self._overlay[site] = rec
        return rec

    def clear_overlay(self, site):
        self._overlay.pop(site, None)

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
        # Diurnal load is identical across every tunnel at a given `now` — compute once.
        d = _diurnal(now)
        rekeys = []
        for t in self.tunnels:
            if t.site not in netem_by_site:
                netem_by_site[t.site] = t._read_netem()
            if t.update(now, netem=netem_by_site[t.site],
                        overlay=self._overlay.get(t.site), d=d):
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

        # HONESTY (do not soften): these are SIMULATED series, drawn ANALYTICALLY
        #   from the calibrated per-site_type baseline + diurnal bump (copied from
        #   the dataset generator so sim == training distribution). Nothing here is
        #   a dataplane measurement. The FAULT term is the netem impairment READ
        #   BACK OUT OF THE QDISC CONFIG on the site's eth1 (per-SITE, so every
        #   tunnel/VRF of a site carries the identical addend), suppressed while a
        #   calibrated overlay ramps. A fault label whose t_impact is a threshold
        #   crossing of these series is a crossing of a MODELLED series.
        metric("sdwan_tunnel_latency_ms",
               "SIMULATED per-tunnel latency (ms): calibrated baseline + diurnal bump "
               "+ netem delay read back from the site uplink qdisc config", "gauge")
        for t in self.tunnels:
            lines.append(_m("sdwan_tunnel_latency_ms", t, t.latency_ms))
        metric("sdwan_tunnel_jitter_ms",
               "SIMULATED per-tunnel jitter (ms): calibrated baseline + diurnal bump", "gauge")
        for t in self.tunnels:
            lines.append(_m("sdwan_tunnel_jitter_ms", t, t.jitter_ms))
        metric("sdwan_tunnel_loss_pct",
               "SIMULATED per-tunnel loss percent: calibrated baseline + diurnal bump "
               "+ netem loss read back from the site uplink qdisc config", "gauge")
        for t in self.tunnels:
            lines.append(_m("sdwan_tunnel_loss_pct", t, t.loss_pct))
        metric("sdwan_tunnel_rekeys_total", "SIMULATED cumulative WireGuard rekey counter "
               "(baseline seed + spontaneous rate; inert, not loss-coupled)", "counter")
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
                # sevmul is exposed so the sidecar ramps optics/thermal at the SAME
                # severity as the tunnels (a low fault is 0.5, not a full 1.0).
                self._send_json({site: {k: ov[k] for k in
                                        ("fault_type", "t_start", "t_impact",
                                         "t_end", "expires", "sevmul")}
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
    import statistics
    TunnelState._SKIP_NETEM = True   # hermetic: model-only, no docker exec
    ctrl = Controller()
    n = len(ctrl.tunnels)
    # ponytail: dynamic — accept any positive count so selftest survives rescaling.
    assert n > 0, f"unexpected tunnel count {n}"

    # base_ms is the healthy latency mean (the failover baseline), not a ping.
    gb = _BASELINES["tunnel_baseline"]["tunnel_latency_ms"]
    assert all(t.base_ms == t._lat["mean"] for t in ctrl.tunnels), \
        "base_ms should seed at the analytic latency mean"

    # --- Distribution match: pool the 4 signals across a simulated DAY and hold them
    # to the SAME bar synthetic/check.py:80-89 holds the dataset to. This IS the
    # "closely match the dataset" proof.
    lat_all, jit_all, loss_all = [], [], []
    day_start = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc).timestamp()  # Monday
    for i in range(480):                       # 480 * 180s = 24h
        ctrl.tick(now=day_start + i * 180.0)
        for t in ctrl.tunnels:
            lat_all.append(t.latency_ms); jit_all.append(t.jitter_ms)
            loss_all.append(t.loss_pct)
    assert all(math.isfinite(x) and x > 0 for x in lat_all), "latency not finite/positive"
    for name, vals in (("jitter", jit_all), ("loss", loss_all)):
        assert min(vals) >= 0.0, f"{name} went negative"
    assert all(t.rekeys >= 0 and math.isfinite(t.rekeys) for t in ctrl.tunnels), "bad rekeys"
    # latency median within +/-2 sigma of the calibrated p50, and non-constant.
    lat_med = statistics.median(lat_all)
    assert abs(lat_med - gb["p50"]) <= 2.0 * gb["std"], \
        f"latency median {lat_med:.2f} off calibrated p50 {gb['p50']:.2f} (+/-{2*gb['std']:.2f})"
    assert statistics.pstdev(lat_all) > 0.15 * gb["std"], "latency series too flat"

    # --- Diurnal coupling: latency higher at 15:00 UTC peak than 03:00 trough.
    # No EMA now -> each update() is an independent draw, so AVERAGE many draws.
    sample = ctrl.tunnels[0]
    def _mean_lat_at(hour, k=300):
        ep = datetime(2026, 6, 15, hour, 0, tzinfo=timezone.utc).timestamp()
        return sum((sample.update(ep) or True) and sample.latency_ms
                   for _ in range(k)) / k
    peak_lat = _mean_lat_at(15)
    trough_lat = _mean_lat_at(3)
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
          f"lat_median={lat_med:.2f} (p50 {gb['p50']:.2f}) "
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
