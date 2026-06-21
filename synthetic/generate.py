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
    tb = prof["tunnel_baseline"]
    lat = tb["tunnel_latency_ms"]; jit = tb["tunnel_jitter_ms"]
    loss = tb["tunnel_loss_pct"]; rk = tb["tunnel_rekeys"]
    rows = []
    for dev, meta in inv.items():
        st = meta["site_type"] or "branch"
        for ent in meta["tunnels"]:
            rekeys = float(rng.integers(int(rk["min"]), int(rk["max"]) + 1))
            for ep in times:
                d = _diurnal(ep)
                # latency: baseline + diurnal congestion bump; jitter/loss small
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
    """
    sigs = prof["fault_signatures"]
    ce_devs = [d for d, m in inv.items() if d.startswith("ce_")]
    pe_devs = [d for d in inv if d.startswith(("pe", "p"))]
    span = times[-1] - times[0]
    # episode budget scales with span & --scale; ~one episode per device-hour/8
    n_ep = max(4, int(scale * len(inv) * span / 3600 / 8))

    # index rows for fast masked update
    df = df.reset_index(drop=True)
    epoch = df["ts"].map(lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
                         .replace(tzinfo=timezone.utc).timestamp()).to_numpy()

    ftypes = list(sigs)
    for _ in range(n_ep):
        ft = rng.choice(ftypes)
        sig = sigs[ft]
        kind = sig["kind"]
        target = rng.choice(pe_devs if ft in ("bgp_flap", "node_failure") and pe_devs else ce_devs)
        lead = float(sig["lead_s"])
        dur_impact = rng.uniform(60, 240)  # how long the impact phase lasts
        t_start = float(rng.choice(times[: max(1, len(times) - 1)]))
        t_impact = t_start + lead
        t_end = t_impact + dur_impact
        sid = f"{ft}-{target}-{uuid.uuid4().hex[:8]}"
        sev = rng.choice(["low", "medium", "high"], p=[0.3, 0.4, 0.3])
        sevmul = {"low": 0.5, "medium": 0.8, "high": 1.0}[str(sev)]

        win = (df["device"] == target) & (epoch >= t_start) & (epoch <= t_end)
        if not win.any():
            continue
        df.loc[win, "is_fault"] = True
        df.loc[win, "scenario_id"] = sid
        df.loc[win, "fault_type"] = ft
        df.loc[win, "severity"] = str(sev)
        df.loc[win, "lead_time_s"] = lead
        tti = t_impact - epoch[win.to_numpy()]
        df.loc[win, "time_to_impact_s"] = np.round(tti, 1)

        # perturb metrics: ramp 0->1 across [t_start,t_impact], decay after
        idx = np.where(win.to_numpy())[0]
        for i in idx:
            e = epoch[i]
            if e <= t_impact:
                prog = (e - t_start) / max(lead, step)        # 0..1 precursor ramp
            else:
                prog = max(0.0, 1.0 - (e - t_impact) / max(dur_impact, step))  # decay
            prog = float(np.clip(prog, 0.0, 1.0)) * sevmul
            row = df.iloc[i]
            if kind == "tunnel_ramp" and row["entity_type"] == "tunnel":
                df.at[i, "tunnel_latency_ms"] = round(row["tunnel_latency_ms"] +
                    prog * (sig["lat_peak"] - row["tunnel_latency_ms"]), 4)
                df.at[i, "tunnel_jitter_ms"] = round(row["tunnel_jitter_ms"] +
                    prog * (sig["jit_peak"] - row["tunnel_jitter_ms"]), 4)
                df.at[i, "tunnel_loss_pct"] = round(row["tunnel_loss_pct"] +
                    prog * sig["loss_peak"], 4)
            elif kind == "iface_churn" and row["entity_type"] == "interface":
                # churn = transient traffic spike on the device interfaces
                if pd.notna(row["if_in_octets"]):
                    df.at[i, "if_in_octets"] = round(row["if_in_octets"] * (1 + prog * 0.4), 1)
            elif kind == "iface_down" and row["entity_type"] == "interface":
                if e > t_impact and row["entity"] not in ("lo",):
                    df.at[i, "if_oper_status"] = 0.0
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
