"""generate.py -- emit a large LABELED multivariate time-series Parquet in the
EXACT dataapi canonical schema, calibrated to the real lab capture.

Reads synthetic/profile.json (run calibrate.py first). For each device/entity in
the real inventory it walks `--days` of `step`-second buckets producing:
  - interface rows: cumulative octet counters driven by a DIURNAL curve
    (business-hours peak), seeded to overlap the real per-site octet ranges.
  - tunnel rows: latency/jitter/loss sampled around the calibrated baseline,
    with diurnal congestion adding latency at peak; rekeys as a slow counter.
Then it injects labeled fault EPISODES (the same scenario TYPES as faults/) with
correct lead_time/time_to_impact semantics: a precursor is visible BEFORE
t_impact (metric ramps up while time_to_impact_s decreases to 0), which is the
entire point for the predictive ML team. Lead time is floored at 4*step so the
ramp always spans several buckets -- several calibrated lead_s values are ~2 s,
which is shorter than one bucket and would make the precursor a label with no
signal behind it.

Schema == dataapi/export.COLUMNS, dtypes matched to the real Parquet, so real +
synthetic are concatenable/interchangeable for training. THIS DATA IS SYNTHETIC:
the Parquet carries `synthetic=true` in its file-level key/value metadata and the
filename is prefixed `synthetic_`, so it can never be mistaken for a capture.

Run (demo):   python3 generate.py
Run (scale):  python3 generate.py --days 7 --scale 4 --step 30
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "dataapi"))
sys.path.insert(0, os.path.join(HERE, "..", "telemetry"))
from export import COLUMNS  # canonical column list/order -- single source of truth
import envmodel  # chassis/optical models, shared with the live sidecar

OUTDIR = os.path.join(HERE, "output")
os.makedirs(OUTDIR, exist_ok=True)


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _diurnal(epoch):
    """0..1 business-hours load multiplier (peak ~14:00 UTC, trough ~03:00).

    ponytail: the real 15-min capture shows no full daily cycle, so the diurnal
    SHAPE is a sane modelled curve (sinusoid + small weekend dip), not derived.
    Amplitude/baseline ARE calibrated (octet seeds, tunnel baseline).
    """
    dt = datetime.fromtimestamp(epoch, timezone.utc)
    h = dt.hour + dt.minute / 60.0
    day = 0.5 - 0.5 * np.cos((h - 3) / 24.0 * 2 * np.pi)  # 0 at 03:00, 1 at 15:00
    weekend = 0.7 if dt.weekday() >= 5 else 1.0
    return 0.15 + 0.85 * day * weekend  # never fully idle


def _pop_of(dev):
    """POP index for a device -- drives the SHARED per-POP ambient temperature.

    Mirrors telemetry/env-metrics.py's fallback: 4 P per POP, 2 PE per POP, and
    all customer sites in one ambient zone.
    """
    d = str(dev)
    try:
        if d.startswith("pe"):
            return (int(d[2:]) - 1) // 2 + 1
        if d.startswith("p"):
            return (int(d[1:]) - 1) // 4 + 1
    except ValueError:
        pass
    return 1


def _has_optic(ent):
    """Only physical uplinks carry a transceiver -- not lo, VRF devices or wg0."""
    return ent.startswith("eth")


def _sid_hex(rng):
    """8 hex chars from the seeded RNG, not uuid4 -- scenario_id has to be
    reproducible or --seed cannot define a stable train/holdout split."""
    return f"{int(rng.integers(0, 1 << 32)):08x}"


def _gen_interfaces(rng, inv, prof, times):
    """Cumulative octet counters per (device, interface), diurnal-driven.

    Also emits the interface-scoped health features: IF-MIB error/discard
    counters (cumulative, like the octet counters), qdisc queue depth/drops, and
    SFF-8472 transceiver readings on physical uplinks.
    """
    rate = prof["octet_rate_by_site"]
    seed = prof["octet_seed_by_site"]
    dh = prof.get("device_health", {})

    def dh_mean(k, dflt):
        return float(dh.get(k, {}).get("mean", dflt))

    err_rate = dh_mean("err_rate_per_step", 0.004)
    disc_rate = dh_mean("discard_rate_per_step", 0.02)
    q_base = dh_mean("q_backlog_bytes", 900.0)
    q_drop_rate = dh_mean("q_drops_per_step", 0.01)

    # The calibrated rate is bytes per prof['step_s'] bucket; if the caller asked
    # for a different bucket size the increment has to be rescaled or the
    # synthetic byte rate silently drifts from the real lab's.
    step = (times[1] - times[0]) if len(times) > 1 else float(prof.get("step_s", 30))
    step_ratio = step / float(prof.get("step_s", step) or step)

    rows = []
    for dev, meta in inv.items():
        st = meta["site_type"] or "branch"
        r = rate.get(st, rate.get("branch"))
        rin0 = r["rate_in_median"] * step_ratio
        rout0 = r["rate_out_median"] * step_ratio
        ambient = envmodel.pop_ambient_c(_pop_of(dev))
        for ent in meta["interfaces"]:
            # lo / vrf_* / wg0 / bare-named VRF devices (CORP, VOICE, GUEST on the
            # PEs) carry little data -> scale down (keeps realism)
            scale = 0.05 if (ent in ("lo", "wg0") or ent.startswith("vrf_")
                             or ent.isupper()) else 1.0
            cin = float(seed.get(st, 1e5)) * rng.uniform(0.5, 1.5)
            cout = cin * 0.6
            # cumulative error/discard/drop counters, seeded near zero
            e_in = float(rng.integers(0, 3))
            e_out = float(rng.integers(0, 3))
            d_in = float(rng.integers(0, 8))
            d_out = float(rng.integers(0, 8))
            q_drops = float(rng.integers(0, 5))
            optic = _has_optic(ent)
            # per-optic service-life offset so lasers do not all age in lockstep
            age0 = rng.uniform(0.0, 0.5)
            n_t = max(1, len(times))
            for k, ep in enumerate(times):
                d = _diurnal(ep)
                jit = rng.uniform(0.8, 1.2)
                cin += rin0 * scale * d * jit
                cout += rout0 * scale * d * rng.uniform(0.8, 1.2)
                # Errors/discards: rare Poisson events whose rate rises with load.
                e_in += float(rng.poisson(err_rate * scale * (0.4 + d)))
                e_out += float(rng.poisson(err_rate * 0.6 * scale * (0.4 + d)))
                d_in += float(rng.poisson(disc_rate * scale * (0.3 + d)))
                d_out += float(rng.poisson(disc_rate * scale * (0.3 + d)))
                # Queue: shallow standing occupancy that grows nonlinearly with load.
                backlog = q_base * scale * (0.2 + d ** 2 * 2.2) * rng.uniform(0.7, 1.3)
                q_drops += float(rng.poisson(q_drop_rate * scale * max(0.0, d - 0.55) * 6))
                row = {
                    "ts": _iso(ep), "device": dev, "site_type": st,
                    "entity": ent, "entity_type": "interface",
                    "if_in_octets": round(cin, 1), "if_out_octets": round(cout, 1),
                    "if_oper_status": 1.0,
                    "if_in_errors": e_in, "if_out_errors": e_out,
                    "if_in_discards": d_in, "if_out_discards": d_out,
                    "q_backlog_bytes": round(backlog, 1), "q_drops": q_drops,
                }
                if optic:
                    # Chassis temp is regenerated on the device rows; here we only
                    # need a plausible local temperature to drive the optic.
                    approx_t = ambient + envmodel.K_LOAD_C * d
                    age = min(1.0, age0 + 0.5 * k / n_t)
                    xt, xr, xb = envmodel.optical(approx_t, age, 0.0, rng.normal())
                    row["xcvr_temp_c"] = round(xt, 3)
                    row["xcvr_rx_power_dbm"] = round(xr, 3)
                    row["xcvr_tx_bias_ma"] = round(xb, 3)
                rows.append(row)
    return rows


def _gen_devices(rng, inv, prof, times):
    """Device-scoped rows (entity_type='device', entity == device name).

    Whole-box signals: control-plane CPU/memory, routing-table and LSDB size,
    BGP message counters, and the modelled chassis sensors. Temperature carries
    thermal mass, so it is walked forward in time per device rather than sampled
    independently -- that lag is what makes it a precursor rather than a
    restatement of load.
    """
    dh = prof.get("device_health", {})

    def dh_ms(k, m, s):
        e = dh.get(k, {})
        return float(e.get("mean", m)), float(e.get("std", s))

    cpu_m, cpu_s = dh_ms("cpu_pct", 4.0, 1.4)
    mem_m, mem_s = dh_ms("mem_pct", 2.5, 0.9)
    bgp_m, _ = dh_ms("bgp_msg_per_step", 6.0, 2.0)
    rib_m, rib_s = dh_ms("rib_routes", 120.0, 40.0)
    lsa_m, lsa_s = dh_ms("ospf_lsa_count", 90.0, 25.0)

    rows = []
    for dev, meta in inv.items():
        st_raw = meta["site_type"]
        st = st_raw or "branch"
        # Role drives chassis power/thermal class. Pass the RAW site_type: the
        # "branch" default above would otherwise size a P router like a CPE.
        role = envmodel.role_of(dev, st_raw)
        ambient = envmodel.pop_ambient_c(_pop_of(dev))
        temp = ambient + rng.uniform(0.0, 2.0)
        # Routing state only exists on provider nodes; CEs run BGP but no OSPF.
        is_provider = role in ("core", "pe")
        rib = max(1.0, rng.normal(rib_m, rib_s) * (2.5 if is_provider else 1.0))
        lsa = max(0.0, rng.normal(lsa_m, lsa_s)) if is_provider else 0.0
        bgp_rx = float(rng.integers(50, 400))
        bgp_tx = float(rng.integers(50, 400))
        for ep in times:
            d = _diurnal(ep)
            # CPU tracks offered load; the control plane is busier under churn.
            cpu = max(0.1, rng.normal(cpu_m, cpu_s) + d * cpu_m * 1.6)
            mem = max(0.1, rng.normal(mem_m, mem_s) + d * 0.4)
            # Chassis heat and power are driven by FORWARDING load, not by
            # control-plane CPU. A router's ASICs do the work; the CPU sits near
            # idle even at line rate, so cpu/100 would leave temp and power
            # almost flat and the features useless.
            util = d
            temp = envmodel.temp_c(temp, ambient, util, 0.0, rng.normal())
            bgp_rx += max(0.0, rng.normal(bgp_m, bgp_m * 0.4))
            bgp_tx += max(0.0, rng.normal(bgp_m, bgp_m * 0.4))
            rows.append({
                "ts": _iso(ep), "device": dev, "site_type": st,
                "entity": dev, "entity_type": "device",
                "cpu_pct": round(cpu, 3), "mem_pct": round(mem, 3),
                "bgp_msg_rx": round(bgp_rx, 1), "bgp_msg_tx": round(bgp_tx, 1),
                "rib_routes": round(rib, 1), "ospf_lsa_count": round(lsa, 1),
                "device_temp_c": round(temp, 3),
                "device_power_watts": round(
                    envmodel.power_watts(role, util, rng.normal()), 3),
                "device_fan_rpm": round(envmodel.fan_rpm(temp), 1),
                "device_psu_voltage_v": round(
                    envmodel.psu_voltage_v(util, rng.normal()), 4),
            })
    return rows


def _gen_tunnels(rng, inv, prof, times):
    # ponytail: use per-site_type baseline if available (preserves dc<branch tier);
    # fall back to global for site_types without tunnels in the real capture.
    global_tb = prof["tunnel_baseline"]
    by_site = prof.get("tunnel_baseline_by_site", {})
    rows = []
    for dev, meta in inv.items():
        st = meta["site_type"] or "branch"
        # pick site-specific or global baseline
        tb = by_site.get(st, global_tb)
        lat = tb.get("tunnel_latency_ms", global_tb["tunnel_latency_ms"])
        jit = tb.get("tunnel_jitter_ms", global_tb["tunnel_jitter_ms"])
        loss = tb.get("tunnel_loss_pct", global_tb["tunnel_loss_pct"])
        rk = global_tb["tunnel_rekeys"]  # rekeys same across sites
        for ent in meta["tunnels"]:
            rekeys = float(rng.integers(int(rk["min"]), int(rk["max"]) + 1))
            for ep in times:
                d = _diurnal(ep)
                # latency: site-tier baseline + diurnal congestion bump
                l = max(1.0, rng.normal(lat["mean"], lat["std"] * 0.4) + d * 8.0)
                j = max(0.1, rng.normal(jit["mean"], jit["std"] * 0.5) + d * 0.5)
                lo = max(0.0, rng.normal(loss["mean"], max(loss["std"], 0.05) * 0.5)) + d * 0.02
                if rng.random() < 0.002:  # occasional spontaneous rekey
                    rekeys += 1
                rows.append({
                    "ts": _iso(ep), "device": dev, "site_type": st,
                    "entity": ent, "entity_type": "tunnel",
                    "tunnel_latency_ms": round(l, 4), "tunnel_jitter_ms": round(j, 4),
                    "tunnel_loss_pct": round(lo, 4), "tunnel_rekeys": rekeys,
                })
    return rows


def _inject_faults(rng, df, inv, prof, times, step, scale):
    """Overlay labeled fault episodes; set precursor ramp + label columns.

    Semantics (matches export.py / faults README):
      t_start  = ramp begins (precursor visible)
      t_impact = t_start + lead_time  (effect crosses threshold)
      t_end    = t_impact + recovery
      time_to_impact_s = t_impact - bucket_ts  (>0 BEFORE impact, <0 after)
    During [t_start, t_impact] metrics RAMP up so the model can predict; after
    impact they peak then decay. Only rows in [t_start, t_end] are is_fault.

    ponytail: old version had a per-row Python loop over ~17M rows (O(n*ep),
    very slow). Replaced with fully vectorized numpy masking per episode;
    prog ramp computed as array slice, metrics updated in-place on numpy arrays
    then written back once — O(rows_in_window) per episode, no Python for-loop.
    """
    sigs = prof["fault_signatures"]
    ce_devs = [d for d, m in inv.items() if d.startswith("ce_")]
    pe_devs = [d for d in inv if d.startswith(("pe", "p"))]
    span = times[-1] - times[0]
    n_ep = max(4, int(scale * len(inv) * span / 3600 / 8))

    df = df.reset_index(drop=True)
    # ponytail: pandas datetime64[us] -> int64 is microseconds, divide by 1e6 not 1e9
    epoch = pd.to_datetime(df["ts"], utc=True).astype("int64").to_numpy() // 10**6

    # extract metric columns as mutable numpy arrays; .copy() makes them writable
    lat_arr  = df["tunnel_latency_ms"].to_numpy(dtype=float, na_value=np.nan).copy()
    jit_arr  = df["tunnel_jitter_ms"].to_numpy(dtype=float, na_value=np.nan).copy()
    loss_arr = df["tunnel_loss_pct"].to_numpy(dtype=float, na_value=np.nan).copy()
    iin_arr  = df["if_in_octets"].to_numpy(dtype=float, na_value=np.nan).copy()
    iout_arr = df["if_out_octets"].to_numpy(dtype=float, na_value=np.nan).copy()
    ops_arr  = df["if_oper_status"].to_numpy(dtype=float, na_value=np.nan).copy()
    # device-health arrays perturbed by _apply_env below
    err_arr  = df["if_in_errors"].to_numpy(dtype=float, na_value=np.nan).copy()
    disc_arr = df["if_in_discards"].to_numpy(dtype=float, na_value=np.nan).copy()
    qb_arr   = df["q_backlog_bytes"].to_numpy(dtype=float, na_value=np.nan).copy()
    qd_arr   = df["q_drops"].to_numpy(dtype=float, na_value=np.nan).copy()
    xrx_arr  = df["xcvr_rx_power_dbm"].to_numpy(dtype=float, na_value=np.nan).copy()
    xb_arr   = df["xcvr_tx_bias_ma"].to_numpy(dtype=float, na_value=np.nan).copy()
    temp_arr = df["device_temp_c"].to_numpy(dtype=float, na_value=np.nan).copy()
    pow_arr  = df["device_power_watts"].to_numpy(dtype=float, na_value=np.nan).copy()
    fan_arr  = df["device_fan_rpm"].to_numpy(dtype=float, na_value=np.nan).copy()
    cpu_arr  = df["cpu_pct"].to_numpy(dtype=float, na_value=np.nan).copy()

    dev_arr  = df["device"].to_numpy()
    etype    = df["entity_type"].to_numpy()
    ent_arr  = df["entity"].to_numpy()

    # label columns as object arrays
    fault_col = np.full(len(df), False, dtype=bool)
    sid_col   = np.full(len(df), None, dtype=object)
    ftype_col = np.full(len(df), None, dtype=object)
    sev_col   = np.full(len(df), None, dtype=object)
    lead_col  = np.full(len(df), np.nan)
    tti_col   = np.full(len(df), np.nan)

    # Fault types whose signature is control-plane churn (CPU burns during
    # reconvergence) vs dataplane congestion (queues fill, errors climb).
    _CHURN = {"bgp_flap", "policy_drift", "node_failure", "rr_failure",
              "ospf_area_flap", "p_node_failure", "srlg_cut", "pop_isolation",
              "core_partition", "bgp_cascade", "ldp_session_flap",
              "mpls_underlay_failure"}
    _CONGEST = {"congestion", "core_congestion", "brownout", "tunnel_degrade",
                "hub_spoke_congest", "asymmetric_loss"}

    def _prog(mask, t_start, t_impact, lead, dur, sevmul):
        """Ramp-to-impact then decay, evaluated on an arbitrary row mask."""
        ep = epoch[mask].astype(float)
        p = np.where(ep <= t_impact,
                     (ep - t_start) / max(lead, step),
                     np.maximum(0.0, 1.0 - (ep - t_impact) / max(dur, step)))
        return np.clip(p, 0.0, 1.0) * sevmul

    def _counter_adjust(arr, mask, adj):
        """Add adj * per-bucket-increment to a CUMULATIVE counter, integrated.

        A fault must perturb the RATE, not the accumulated counter. Scaling the
        absolute counter (the old `counter * (1 + p*0.4)`) makes it step
        BACKWARDS when the window ends, and every rate derivation -- including
        this repo's calibrate._octet_rate -- reads that as a counter reset.
        `mask` must select every row of the affected entities (generation order
        is time-ascending within an entity) so the extra/missing bytes carry
        forward after the window, which is what a real counter does.
        """
        sub = arr[mask]
        if not len(sub):
            return
        ent = ent_arr[mask]
        start = np.empty(len(sub), dtype=bool)
        start[0] = True
        start[1:] = ent[1:] != ent[:-1]
        inc = np.diff(sub, prepend=sub[0])
        inc[start] = 0.0
        extra = np.cumsum(inc * adj)
        seg = np.flatnonzero(start)
        off = np.repeat(extra[seg], np.diff(np.append(seg, len(sub))))
        arr[mask] = np.round(sub + extra - off, 1)

    def _iface_churn(target, t_start, t_impact, t_end, lead, dur, sevmul):
        """Churn = up to 40% extra bytes per bucket while the episode runs."""
        amask = (dev_arr == target) & (etype == "interface")
        if not amask.any():
            return
        ep_a = epoch[amask].astype(float)
        p = np.where(ep_a <= t_impact,
                     (ep_a - t_start) / max(lead, step),
                     np.maximum(0.0, 1.0 - (ep_a - t_impact) / max(dur, step)))
        adj = np.where((ep_a >= t_start) & (ep_a <= t_end),
                       np.clip(p, 0.0, 1.0) * sevmul * 0.4, 0.0)
        _counter_adjust(iin_arr, amask, adj)

    def _iface_down(target, t_impact, t_end):
        """Down = oper_status 0 AND both octet counters frozen (no forwarding)."""
        amask = (dev_arr == target) & (etype == "interface") & (ent_arr != "lo")
        if not amask.any():
            return
        down = amask & (epoch > t_impact) & (epoch <= t_end)
        if not down.any():
            return
        ops_arr[down] = 0.0
        adj = np.where(down[amask], -1.0, 0.0)
        _counter_adjust(iin_arr, amask, adj)
        _counter_adjust(iout_arr, amask, adj)

    def _tunnel_ramp(tmask, sig, p_t):
        """Ramp latency/jitter/loss toward the signature peak.

        The peak is floored at 1.15x the healthy value: several calibrated
        lat_peak/jit_peak values sit BELOW the generated healthy mean (the
        diurnal congestion bump raises it), so ramping straight at them made a
        labelled degradation ramp DOWNWARD -- a fault that looks healthier than
        healthy. A peak must be a peak.
        """
        lat_t = np.maximum(sig["lat_peak"], lat_arr[tmask] * 1.15)
        jit_t = np.maximum(sig["jit_peak"], jit_arr[tmask] * 1.15)
        lat_arr[tmask]  = np.round(lat_arr[tmask]  + p_t * (lat_t - lat_arr[tmask]), 4)
        jit_arr[tmask]  = np.round(jit_arr[tmask]  + p_t * (jit_t - jit_arr[tmask]), 4)
        loss_arr[tmask] = np.round(loss_arr[tmask] + p_t * sig["loss_peak"], 4)

    def _apply_env(win, ft, t_start, t_impact, lead, dur, sevmul):
        """Perturb the device-health features inside one fault window.

        Every fault leaves a thermal/power trace (envmodel.fault_heat_c); which
        of the other features move depends on whether the fault is control-plane
        churn or dataplane congestion. gray_failure additionally degrades the
        optics -- that is the whole point of the scenario: physical-layer decay
        with no link-down event to trigger on.
        """
        heat = envmodel.fault_heat_c(ft)

        dmask = win & (etype == "device")
        if dmask.any():
            p = _prog(dmask, t_start, t_impact, lead, dur, sevmul)
            if heat:
                temp_arr[dmask] = np.round(temp_arr[dmask] + p * heat, 3)
                fan_arr[dmask] = np.round(
                    np.maximum(envmodel.FAN_BASE_RPM,
                               fan_arr[dmask] + p * heat * envmodel.FAN_RPM_PER_C), 1)
            # Power follows load: churn and congestion both add work; a killed
            # daemon does less (negative heat marks exactly those scenarios).
            pw = pow_arr[dmask]
            pow_arr[dmask] = np.round(pw * (1 + p * (0.05 if heat >= 0 else -0.04)), 3)
            if ft in _CHURN:
                cpu_arr[dmask] = np.round(cpu_arr[dmask] * (1 + p * 3.0), 3)

        imask = win & (etype == "interface")
        if imask.any():
            p = _prog(imask, t_start, t_impact, lead, dur, sevmul)
            if ft in _CONGEST:
                qb_arr[imask] = np.round(qb_arr[imask] * (1 + p * 9.0), 1)
                qd_arr[imask] = np.round(qd_arr[imask] + p * 40.0, 1)
                err_arr[imask] = np.round(err_arr[imask] + p * 6.0, 1)
                disc_arr[imask] = np.round(disc_arr[imask] + p * 25.0, 1)
            elif ft in _CHURN:
                disc_arr[imask] = np.round(disc_arr[imask] + p * 4.0, 1)
            if ft == "gray_failure":
                # Received power sags and laser bias climbs to compensate: the
                # SFF-8472 laser-ageing signature, with no oper-status change.
                valid = ~np.isnan(xrx_arr[imask])
                rx = xrx_arr[imask].copy()
                bi = xb_arr[imask].copy()
                rx[valid] = np.round(rx[valid] - p[valid] * 7.0, 3)
                bi[valid] = np.round(bi[valid] + p[valid] * 5.0, 3)
                xrx_arr[imask] = rx
                xb_arr[imask] = bi
                err_arr[imask] = np.round(err_arr[imask] + p * 12.0, 1)

    ftypes = list(sigs)
    targets = list(inv.keys())
    times_arr = np.array(times, dtype=np.int64)
    for _ in range(n_ep):
        ft = rng.choice(ftypes)
        sig = sigs[ft]
        kind = sig["kind"]
        target = rng.choice(pe_devs if ft in ("bgp_flap", "node_failure") and pe_devs else ce_devs)
        # Gamma(k=2, scale=lead_s/2) → mean≈lead_s, CV≈0.71; always positive.
        # Floored at 4 buckets: several calibrated lead_s are ~2 s, shorter than
        # one bucket, which yields prog==0 at the only precursor bucket -- a
        # time_to_impact_s>0 label with a bit-identical-to-baseline metric.
        lead = max(float(rng.gamma(2.0, max(sig["lead_s"] / 2.0, 0.5))), 4.0 * step)
        dur_impact = float(rng.uniform(60, 240))
        t_start = float(rng.choice(times_arr[: max(1, len(times_arr) - 1)]))
        t_impact = t_start + lead
        t_end = t_impact + dur_impact
        sid = f"{ft}-{target}-{_sid_hex(rng)}"
        sev = rng.choice(["low", "medium", "high"], p=[0.3, 0.4, 0.3])
        sevmul = {"low": 0.5, "medium": 0.8, "high": 1.0}[str(sev)]

        # vectorized window mask
        win = (dev_arr == target) & (epoch >= t_start) & (epoch <= t_end)
        if not win.any():
            continue
        # Overlapping episodes on one device would silently overwrite the
        # earlier episode's scenario_id/lead_time/time_to_impact while its ramp
        # stays in the metrics -- labels desynced from signal. Drop the clash.
        if (fault_col & win).any():
            continue

        # Label ONLY rows a perturbation actually reaches. The window is
        # device-wide but each `kind` moves one entity_type, so labelling the
        # whole window marked ~45% of is_fault rows that are byte-identical to
        # baseline. Device rows always move (_apply_env touches power/temp).
        iface_sig = (kind in ("iface_churn", "iface_down")
                     or ft in _CONGEST or ft in _CHURN or ft == "gray_failure")
        lab = win & (etype == "device")
        if kind == "tunnel_ramp":
            lab = lab | (win & (etype == "tunnel"))
        if iface_sig:
            lab = lab | (win & (etype == "interface"))

        fault_col[lab] = True
        sid_col[lab]   = sid
        ftype_col[lab] = ft
        sev_col[lab]   = str(sev)
        lead_col[lab]  = lead
        tti_col[lab]   = np.round(t_impact - epoch[lab], 1)

        if kind == "tunnel_ramp":
            tmask = win & (etype == "tunnel")
            if tmask.any():
                # ponytail: recompute prog for tmask slice (subset of win)
                ep_t = epoch[tmask].astype(float)
                p_t = np.where(ep_t <= t_impact,
                               (ep_t - t_start) / max(lead, step),
                               np.maximum(0.0, 1.0 - (ep_t - t_impact) / max(dur_impact, step)))
                p_t = np.clip(p_t, 0.0, 1.0) * sevmul
                _tunnel_ramp(tmask, sig, p_t)
        elif kind == "iface_churn":
            _iface_churn(target, t_start, t_impact, t_end, lead, dur_impact, sevmul)
        elif kind == "iface_down":
            _iface_down(target, t_impact, t_end)

        # device-health features move for EVERY fault type, not just the three
        # metric "kinds" above (a fault the tunnel metrics barely register can
        # still be loud in CPU, queue depth or optical power).
        _apply_env(win, ft, t_start, t_impact, lead, dur_impact, sevmul)

        # ponytail: cascade — 12% of episodes trigger a second fault
        if rng.random() < 0.12 and len(targets) > 1:
            target2 = rng.choice([t for t in targets if t != target])
            cascade_sig_key = rng.choice(list(sigs.keys()))
            cascade_sig = sigs[cascade_sig_key]
            cascade_t_start = t_start + lead * 0.5
            cascade_lead = max(float(rng.gamma(2.0, max(cascade_sig["lead_s"] / 2.0, 0.5))),
                               4.0 * step)
            cascade_dur = float(rng.uniform(60, 240))
            cascade_t_impact = cascade_t_start + cascade_lead
            cascade_t_end = cascade_t_impact + cascade_dur
            cascade_sid = f"{cascade_sig_key}-{target2}-{_sid_hex(rng)}-casc"
            cascade_sev = rng.choice(["low", "medium", "high"], p=[0.3, 0.4, 0.3])
            cascade_sevmul = {"low": 0.5, "medium": 0.8, "high": 1.0}[str(cascade_sev)]
            cascade_kind = cascade_sig["kind"]
            cwin = (dev_arr == target2) & (epoch >= cascade_t_start) & (epoch <= cascade_t_end)
            if cwin.any() and not (fault_col & cwin).any():
                c_iface_sig = (cascade_kind in ("iface_churn", "iface_down")
                               or cascade_sig_key in _CONGEST
                               or cascade_sig_key in _CHURN
                               or cascade_sig_key == "gray_failure")
                clab = cwin & (etype == "device")
                if cascade_kind == "tunnel_ramp":
                    clab = clab | (cwin & (etype == "tunnel"))
                if c_iface_sig:
                    clab = clab | (cwin & (etype == "interface"))
                fault_col[clab] = True
                sid_col[clab]   = cascade_sid
                ftype_col[clab] = cascade_sig_key
                sev_col[clab]   = str(cascade_sev)
                lead_col[clab]  = cascade_lead
                tti_col[clab]   = np.round(cascade_t_impact - epoch[clab], 1)
                if cascade_kind == "tunnel_ramp":
                    ctmask = cwin & (etype == "tunnel")
                    if ctmask.any():
                        ep_ct = epoch[ctmask].astype(float)
                        p_ct = np.where(ep_ct <= cascade_t_impact,
                                        (ep_ct - cascade_t_start) / max(cascade_lead, step),
                                        np.maximum(0.0, 1.0 - (ep_ct - cascade_t_impact) / max(cascade_dur, step)))
                        p_ct = np.clip(p_ct, 0.0, 1.0) * cascade_sevmul
                        _tunnel_ramp(ctmask, cascade_sig, p_ct)
                elif cascade_kind == "iface_churn":
                    _iface_churn(target2, cascade_t_start, cascade_t_impact,
                                 cascade_t_end, cascade_lead, cascade_dur, cascade_sevmul)
                elif cascade_kind == "iface_down":
                    _iface_down(target2, cascade_t_impact, cascade_t_end)
                _apply_env(cwin, cascade_sig_key, cascade_t_start, cascade_t_impact,
                           cascade_lead, cascade_dur, cascade_sevmul)

    # write arrays back to df once
    df["tunnel_latency_ms"] = lat_arr
    df["tunnel_jitter_ms"]  = jit_arr
    df["tunnel_loss_pct"]   = loss_arr
    df["if_in_octets"]      = iin_arr
    df["if_out_octets"]     = iout_arr
    df["if_oper_status"]    = ops_arr
    df["if_in_errors"]      = err_arr
    df["if_in_discards"]    = disc_arr
    df["q_backlog_bytes"]   = qb_arr
    df["q_drops"]           = qd_arr
    df["xcvr_rx_power_dbm"] = xrx_arr
    df["xcvr_tx_bias_ma"]   = xb_arr
    df["device_temp_c"]     = temp_arr
    df["device_power_watts"] = pow_arr
    df["device_fan_rpm"]    = fan_arr
    df["cpu_pct"]           = cpu_arr
    df["is_fault"]          = fault_col
    df["scenario_id"]       = sid_col
    df["fault_type"]        = ftype_col
    df["severity"]          = sev_col
    df["lead_time_s"]       = lead_col
    df["time_to_impact_s"]  = tti_col
    return df


def generate(days, step, scale, profile_path, seed=42):
    with open(profile_path) as fh:
        prof = json.load(fh)
    inv = prof["inventory"]
    rng = np.random.default_rng(seed)

    start = datetime(2026, 6, 15, tzinfo=timezone.utc).timestamp()  # a Monday
    n = int(days * 86400 / step)
    times = [int(start + k * step) for k in range(n)]

    rows = (_gen_interfaces(rng, inv, prof, times)
            + _gen_tunnels(rng, inv, prof, times)
            + _gen_devices(rng, inv, prof, times))
    df = pd.DataFrame(rows)

    # init missing canonical columns. The six label columns are NOT initialised
    # here: _inject_faults assigns all of them unconditionally on every path.
    df["vrf"] = pd.NA
    for c in ["flow_bytes", "flow_packets"]:
        # KNOWN GAP: the flow pillar is not synthesised. The real capture DOES
        # carry sparse flow rows (~3% non-null), so this is an omission, not a
        # match to the real export. Calibrating flow_bytes/flow_packets per
        # site_type from those rows is the fix; until then they are null.
        df[c] = pd.NA

    df = _inject_faults(rng, df, inv, prof, times, step, scale)

    # exact canonical order + dtypes matching the real Parquet
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[COLUMNS].sort_values(["ts", "device", "entity"]).reset_index(drop=True)
    df["is_fault"] = df["is_fault"].astype(bool)
    for c in ["if_in_octets", "if_out_octets", "if_oper_status",
              "tunnel_latency_ms", "tunnel_jitter_ms", "tunnel_loss_pct", "tunnel_rekeys",
              "flow_bytes", "flow_packets", "lead_time_s", "time_to_impact_s",
              "if_in_errors", "if_in_discards", "if_out_errors", "if_out_discards",
              "q_backlog_bytes", "q_drops",
              "xcvr_temp_c", "xcvr_rx_power_dbm", "xcvr_tx_bias_ma",
              "cpu_pct", "mem_pct", "bgp_msg_rx", "bgp_msg_tx",
              "rib_routes", "ospf_lsa_count",
              "device_temp_c", "device_power_watts", "device_fan_rpm",
              "device_psu_voltage_v"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    for c in ["scenario_id", "fault_type", "severity"]:
        df[c] = df[c].astype("object")

    # ponytail: seed omitted from the name at 42 so existing filenames stay valid
    sfx = "" if seed == 42 else f"_seed{seed}"
    fname = f"synthetic_{int(start)}_d{days}_s{step}_x{scale}{sfx}.parquet"
    path = os.path.join(OUTDIR, fname)
    # Mark it synthetic IN THE FILE, not just in the filename: a renamed or
    # re-exported copy must still be distinguishable from a real capture.
    tbl = pa.Table.from_pandas(df, preserve_index=False)
    tbl = tbl.replace_schema_metadata({
        **(tbl.schema.metadata or {}),
        b"synthetic": b"true",
        b"generator": b"synthetic/generate.py",
        b"seed": str(seed).encode(),
        b"calibrated_from": str(prof.get("source_parquet", "")).encode(),
    })
    pq.write_table(tbl, path)
    return path, df


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=float, default=2.0, help="days of telemetry (demo default 2)")
    ap.add_argument("--step", type=int, default=30, help="bucket seconds (match export, default 30)")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="fault-episode density multiplier (ML-scale: raise days+scale)")
    ap.add_argument("--profile", default=os.path.join(HERE, "profile.json"))
    ap.add_argument("--seed", type=int, default=42,
                    help="RNG seed; change it for an independent holdout sample")
    args = ap.parse_args()
    assert os.path.exists(args.profile), "profile.json missing -- run calibrate.py first"

    path, df = generate(args.days, args.step, args.scale, args.profile, args.seed)
    print(f"wrote {path}")
    print(f"rows={len(df)} cols={len(df.columns)} "
          f"fault_rows={int(df.is_fault.sum())} ({df.is_fault.mean()*100:.2f}%) "
          f"precursor_rows={int(((df.is_fault) & (df.time_to_impact_s > 0)).sum())}")


if __name__ == "__main__":
    main()
