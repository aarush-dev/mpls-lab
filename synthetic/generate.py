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
entire point for the predictive ML team.

Schema == dataapi/export.COLUMNS, dtypes matched to the real Parquet, so real +
synthetic are concatenable/interchangeable for training.

Run (demo):   python3 generate.py
Run (scale):  python3 generate.py --days 7 --scale 4 --step 30
"""
import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "dataapi"))
from export import COLUMNS  # canonical column list/order -- single source of truth

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


def _gen_interfaces(rng, inv, prof, times):
    """Cumulative octet counters per (device, interface), diurnal-driven."""
    rate = prof["octet_rate_by_site"]
    seed = prof["octet_seed_by_site"]
    rows = []
    for dev, meta in inv.items():
        st = meta["site_type"] or "branch"
        r = rate.get(st, rate.get("branch"))
        rin0 = r["rate_in_median"]
        rout0 = r["rate_out_median"]
        for ent in meta["interfaces"]:
            # lo / vrf_* / wg0 carry little data -> scale down (keeps realism)
            scale = 0.05 if (ent == "lo" or ent.startswith("vrf_")) else 1.0
            cin = float(seed.get(st, 1e5)) * rng.uniform(0.5, 1.5)
            cout = cin * 0.6
            for ep in times:
                d = _diurnal(ep)
                jit = rng.uniform(0.8, 1.2)
                cin += rin0 * scale * d * jit
                cout += rout0 * scale * d * rng.uniform(0.8, 1.2)
                rows.append({
                    "ts": _iso(ep), "device": dev, "site_type": st,
                    "entity": ent, "entity_type": "interface",
                    "if_in_octets": round(cin, 1), "if_out_octets": round(cout, 1),
                    "if_oper_status": 1.0,
                })
    return rows


def _gen_tunnels(rng, inv, prof, times):
    # ponytail: use per-site_type baseline if available (preserves dc<branch tier);
    # fall back to global for site_types without tunnels in the real capture.
    global_tb = prof["tunnel_baseline"]
    by_site = prof.get("tunnel_baseline_by_site", {})
    rk_g = global_tb["tunnel_rekeys"]
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
    ops_arr  = df["if_oper_status"].to_numpy(dtype=float, na_value=np.nan).copy()

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

    ftypes = list(sigs)
    times_arr = np.array(times, dtype=np.int64)
    for _ in range(n_ep):
        ft = rng.choice(ftypes)
        sig = sigs[ft]
        kind = sig["kind"]
        target = rng.choice(pe_devs if ft in ("bgp_flap", "node_failure") and pe_devs else ce_devs)
        lead = float(sig["lead_s"])
        dur_impact = float(rng.uniform(60, 240))
        t_start = float(rng.choice(times_arr[: max(1, len(times_arr) - 1)]))
        t_impact = t_start + lead
        t_end = t_impact + dur_impact
        sid = f"{ft}-{target}-{uuid.uuid4().hex[:8]}"
        sev = rng.choice(["low", "medium", "high"], p=[0.3, 0.4, 0.3])
        sevmul = {"low": 0.5, "medium": 0.8, "high": 1.0}[str(sev)]

        # vectorized window mask
        win = (dev_arr == target) & (epoch >= t_start) & (epoch <= t_end)
        if not win.any():
            continue

        fault_col[win] = True
        sid_col[win]   = sid
        ftype_col[win] = ft
        sev_col[win]   = str(sev)
        lead_col[win]  = lead
        tti_col[win]   = np.round(t_impact - epoch[win], 1)

        # vectorized prog ramp (no Python loop)
        ep_win = epoch[win].astype(float)
        prog = np.where(
            ep_win <= t_impact,
            (ep_win - t_start) / max(lead, step),
            np.maximum(0.0, 1.0 - (ep_win - t_impact) / max(dur_impact, step)),
        )
        prog = np.clip(prog, 0.0, 1.0) * sevmul

        if kind == "tunnel_ramp":
            tmask = win & (etype == "tunnel")
            if tmask.any():
                # ponytail: recompute prog for tmask slice (subset of win)
                ep_t = epoch[tmask].astype(float)
                p_t = np.where(ep_t <= t_impact,
                               (ep_t - t_start) / max(lead, step),
                               np.maximum(0.0, 1.0 - (ep_t - t_impact) / max(dur_impact, step)))
                p_t = np.clip(p_t, 0.0, 1.0) * sevmul
                lat_arr[tmask]  = np.round(lat_arr[tmask]  + p_t * (sig["lat_peak"]  - lat_arr[tmask]),  4)
                jit_arr[tmask]  = np.round(jit_arr[tmask]  + p_t * (sig["jit_peak"]  - jit_arr[tmask]),  4)
                loss_arr[tmask] = np.round(loss_arr[tmask] + p_t * sig["loss_peak"],                      4)
        elif kind == "iface_churn":
            imask = win & (etype == "interface")
            if imask.any():
                ep_i = epoch[imask].astype(float)
                p_i = np.where(ep_i <= t_impact,
                               (ep_i - t_start) / max(lead, step),
                               np.maximum(0.0, 1.0 - (ep_i - t_impact) / max(dur_impact, step)))
                p_i = np.clip(p_i, 0.0, 1.0) * sevmul
                valid = ~np.isnan(iin_arr[imask])
                tmp = iin_arr[imask].copy()
                tmp[valid] = np.round(tmp[valid] * (1 + p_i[valid] * 0.4), 1)
                iin_arr[imask] = tmp
        elif kind == "iface_down":
            dmask = win & (etype == "interface") & (epoch > t_impact) & (ent_arr != "lo")
            if dmask.any():
                ops_arr[dmask] = 0.0

    # write arrays back to df once
    df["tunnel_latency_ms"] = lat_arr
    df["tunnel_jitter_ms"]  = jit_arr
    df["tunnel_loss_pct"]   = loss_arr
    df["if_in_octets"]      = iin_arr
    df["if_oper_status"]    = ops_arr
    df["is_fault"]          = fault_col
    df["scenario_id"]       = sid_col
    df["fault_type"]        = ftype_col
    df["severity"]          = sev_col
    df["lead_time_s"]       = lead_col
    df["time_to_impact_s"]  = tti_col
    return df


def generate(days, step, scale, profile_path):
    with open(profile_path) as fh:
        prof = json.load(fh)
    inv = prof["inventory"]
    rng = np.random.default_rng(42)

    start = datetime(2026, 6, 15, tzinfo=timezone.utc).timestamp()  # a Monday
    n = int(days * 86400 / step)
    times = [int(start + k * step) for k in range(n)]

    rows = _gen_interfaces(rng, inv, prof, times) + _gen_tunnels(rng, inv, prof, times)
    df = pd.DataFrame(rows)

    # init label + missing canonical columns
    df["is_fault"] = False
    for c in ["scenario_id", "fault_type", "severity"]:
        df[c] = pd.NA
    df["lead_time_s"] = pd.NA
    df["time_to_impact_s"] = pd.NA
    df["vrf"] = pd.NA
    for c in ["flow_bytes", "flow_packets"]:
        # ponytail: real capture had flows all-null (nfacctd not joined in sample);
        # we leave them null too so synthetic matches what the real export emits.
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
              "flow_bytes", "flow_packets", "lead_time_s", "time_to_impact_s"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    for c in ["scenario_id", "fault_type", "severity"]:
        df[c] = df[c].astype("object")

    fname = f"synthetic_{int(start)}_d{days}_s{step}_x{scale}.parquet"
    path = os.path.join(OUTDIR, fname)
    df.to_parquet(path, index=False)
    return path, df


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=float, default=2.0, help="days of telemetry (demo default 2)")
    ap.add_argument("--step", type=int, default=30, help="bucket seconds (match export, default 30)")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="fault-episode density multiplier (ML-scale: raise days+scale)")
    ap.add_argument("--profile", default=os.path.join(HERE, "profile.json"))
    args = ap.parse_args()
    assert os.path.exists(args.profile), "profile.json missing -- run calibrate.py first"

    path, df = generate(args.days, args.step, args.scale, args.profile)
    print(f"wrote {path}")
    print(f"rows={len(df)} cols={len(df.columns)} "
          f"fault_rows={int(df.is_fault.sum())} ({df.is_fault.mean()*100:.2f}%) "
          f"precursor_rows={int(((df.is_fault) & (df.time_to_impact_s > 0)).sum())}")


if __name__ == "__main__":
    main()
