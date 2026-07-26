"""calibrate.py -- derive synthetic/profile.json from the REAL captured Parquet.

We measure, from the real labeled dataset, the things the generator needs so it
doesn't hardcode magic numbers:
  - per site_type interface octet rate (bytes/step) -> traffic plateau
  - tunnel latency / jitter / loss / rekeys baseline distributions
  - per fault_type perturbation signatures (peak metric vs baseline) + lead_time
  - the device/entity inventory (so synthetic naming == lab naming)

The real sample is THIN (~15 min, no full diurnal cycle, flows all null). Where a
statistic can't be derived from it we fall back to a sane default and mark it
with a ponytail comment + "_src":"default" in the JSON so it's auditable.

Run:  python3 calibrate.py [REAL_PARQUET]   (default: newest in dataapi/datasets)
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATASETS = os.path.join(HERE, "..", "dataapi", "datasets")
STEP = 30  # canonical export step (s); the real capture is 30s-bucketed


def _newest_real():
    files = sorted(glob.glob(os.path.join(DATASETS, "*.parquet")))
    assert files, "no real Parquet in dataapi/datasets -- run dataapi/export.py first"
    return files[-1]


def _octet_rate(iface):
    """bytes/step per site_type from cumulative counters (diff within entity)."""
    i = iface.sort_values(["device", "entity", "ts"]).copy()
    i["rate_in"] = i.groupby(["device", "entity"])["if_in_octets"].diff()
    i["rate_out"] = i.groupby(["device", "entity"])["if_out_octets"].diff()
    out = {}
    for st, g in i.groupby("site_type", dropna=False):
        ri = g["rate_in"].dropna()
        ri = ri[ri > 0]  # drop counter resets / idle
        ro = g["rate_out"].dropna()
        ro = ro[ro > 0]
        # ponytail: thin sample -> if a site_type has <3 positive diffs, fall
        # back to a per-site default rate (bytes/step) rather than trust noise.
        defaults = {"branch": 1.2e6, "hub": 5e6, "dc": 3e6, "pe": 2e7, "core": 1e7}
        d = defaults.get(str(st), 1e6)
        out[str(st)] = {
            "rate_in_median": float(ri.median()) if len(ri) >= 3 else d,
            "rate_out_median": float(ro.median()) if len(ro) >= 3 else d * 0.6,
            "_src": "real" if len(ri) >= 3 else "default",
        }
    return out


def _tunnel_baseline(tun_nf):
    """Global baseline (backward-compat) + per-site_type breakdown.

    ponytail: the new 34-device lab has measurable dc<branch latency tiers;
    the prior version averaged across site_types and lost the tier signal.
    Now we emit tunnel_baseline (global, as before) AND tunnel_baseline_by_site
    so generate.py can preserve dc<branch in synthetic data.
    """
    cols_defaults = [
        ("tunnel_latency_ms", (20.0, 7.0)),
        ("tunnel_jitter_ms", (2.0, 0.7)),
        ("tunnel_loss_pct", (0.05, 0.1)),
        ("tunnel_rekeys", (7.0, 2.0)),
    ]
    # global (unchanged shape for backward compat)
    out = {}
    for col, default in cols_defaults:
        s = tun_nf[col].dropna()
        if len(s) >= 5:
            out[col] = {"mean": float(s.mean()), "std": float(s.std()),
                        "p50": float(s.median()), "min": float(s.min()),
                        "max": float(s.max()), "_src": "real"}
        else:  # ponytail: too few tunnel rows -> sane SD-WAN defaults
            m, sd = default
            out[col] = {"mean": m, "std": sd, "p50": m, "min": max(0.0, m - 2 * sd),
                        "max": m + 4 * sd, "_src": "default"}

    # per-site_type tiers -- dc has lower latency than branch
    # ponytail: only tunnel-bearing site_types (branch/dc); hub/pe rarely have tunnels
    site_defaults = {
        "branch": {"tunnel_latency_ms": (33.0, 21.0), "tunnel_jitter_ms": (2.5, 0.7),
                   "tunnel_loss_pct": (0.33, 0.57)},
        "dc":     {"tunnel_latency_ms": (26.0, 8.0),  "tunnel_jitter_ms": (2.5, 0.73),
                   "tunnel_loss_pct": (0.29, 0.53)},
    }
    by_site = {}
    for st, g in tun_nf.groupby("site_type", dropna=False):
        st = str(st)
        by_site[st] = {}
        for col, _ in cols_defaults:
            s = g[col].dropna()
            sd_dft = site_defaults.get(st, {}).get(col, (out[col]["mean"], out[col]["std"]))
            if len(s) >= 5:
                by_site[st][col] = {
                    "mean": float(s.mean()), "std": float(s.std()),
                    "p50": float(s.median()), "min": float(s.min()),
                    "max": float(s.max()), "_src": "real",
                }
            else:
                m, sd = sd_dft
                by_site[st][col] = {"mean": m, "std": sd, "p50": m,
                                    "min": max(0.0, m - 2 * sd),
                                    "max": m + 4 * sd, "_src": "default"}
    return out, by_site


def _fault_signatures(f, tun_nf):
    """Peak perturbation per fault_type relative to baseline, + lead_time."""
    base_lat = tun_nf["tunnel_latency_ms"].median()
    base_loss = tun_nf["tunnel_loss_pct"].median()
    base_jit = tun_nf["tunnel_jitter_ms"].median()
    # ponytail: defaults capture the qualitative signature for fault types the
    # thin real sample doesn't contain (tunnel_degrade, node_failure, etc.).
    defaults = {
        "congestion":     {"lat_peak": 60.0, "loss_peak": 3.0, "jit_peak": 8.0, "lead_s": 50.0, "kind": "tunnel_ramp"},
        "bgp_flap":       {"lat_peak": base_lat, "loss_peak": 0.3, "jit_peak": base_jit, "lead_s": 2.0, "kind": "iface_churn"},
        "tunnel_degrade": {"lat_peak": 35.0, "loss_peak": 5.0, "jit_peak": 12.0, "lead_s": 40.0, "kind": "tunnel_ramp"},
        "policy_drift":   {"lat_peak": 28.0, "loss_peak": 0.35, "jit_peak": 3.0, "lead_s": 3.0, "kind": "iface_churn"},
        "node_failure":   {"lat_peak": base_lat, "loss_peak": 1.0, "jit_peak": base_jit, "lead_s": 1.0, "kind": "iface_down"},
        "asymmetric_loss": {"lat_peak": base_lat * 1.1, "loss_peak": 4.0, "jit_peak": base_jit * 1.5, "lead_s": 30.0, "kind": "tunnel_ramp"},
        "brownout":       {"lat_peak": 45.0, "loss_peak": 1.5, "jit_peak": 6.0, "lead_s": 55.0, "kind": "tunnel_ramp"},
        # --- core / catastrophic / correlated (faults/orchestrator.py SCENARIOS) ---
        # ponytail: these were missing, so the synthetic set only ever contained
        #   7 of the 21 implemented scenario types and no core faults at all.
        #   lead_s values track the per-scenario table in DOCS/03.
        "p_node_failure":  {"lat_peak": base_lat * 1.3, "loss_peak": 2.0, "jit_peak": base_jit * 2.0, "lead_s": 5.0, "kind": "iface_down"},
        "pop_isolation":   {"lat_peak": base_lat * 1.6, "loss_peak": 6.0, "jit_peak": base_jit * 2.5, "lead_s": 3.0, "kind": "iface_down"},
        "core_partition":  {"lat_peak": base_lat * 1.8, "loss_peak": 8.0, "jit_peak": base_jit * 3.0, "lead_s": 3.0, "kind": "iface_down"},
        "srlg_cut":        {"lat_peak": base_lat * 1.4, "loss_peak": 4.0, "jit_peak": base_jit * 2.2, "lead_s": 2.0, "kind": "iface_down"},
        "core_congestion": {"lat_peak": 55.0, "loss_peak": 2.5, "jit_peak": 10.0, "lead_s": 45.0, "kind": "tunnel_ramp"},
        "ospf_area_flap":  {"lat_peak": base_lat * 1.2, "loss_peak": 1.0, "jit_peak": base_jit * 2.0, "lead_s": 4.0, "kind": "iface_churn"},
        "path_asymmetry":  {"lat_peak": 30.0, "loss_peak": 0.4, "jit_peak": 4.0, "lead_s": 20.0, "kind": "tunnel_ramp"},
        "rr_failure":      {"lat_peak": base_lat, "loss_peak": 1.2, "jit_peak": base_jit, "lead_s": 3.0, "kind": "iface_churn"},
        # gray_failure is the long-precursor, weak-signal case: barely visible in
        # tunnel metrics, but it degrades the optics (rx power sags, laser bias
        # climbs) with NO link-down event. That optical divergence is the only
        # early signal, which is exactly why the DOM columns were added.
        "gray_failure":    {"lat_peak": base_lat * 1.15, "loss_peak": 1.8, "jit_peak": base_jit * 1.3, "lead_s": 90.0, "kind": "tunnel_ramp"},
        "mpls_underlay_failure": {"lat_peak": base_lat * 1.3, "loss_peak": 2.0, "jit_peak": base_jit * 1.8, "lead_s": 4.0, "kind": "iface_down"},
        "ldp_session_flap":      {"lat_peak": base_lat * 1.1, "loss_peak": 0.8, "jit_peak": base_jit * 1.6, "lead_s": 3.0, "kind": "iface_churn"},
        "hub_spoke_congest":     {"lat_peak": 70.0, "loss_peak": 5.0, "jit_peak": 14.0, "lead_s": 40.0, "kind": "tunnel_ramp"},
        "bgp_cascade":           {"lat_peak": base_lat * 1.2, "loss_peak": 1.5, "jit_peak": base_jit * 2.0, "lead_s": 3.0, "kind": "iface_churn"},
        "controller_drift":      {"lat_peak": 38.0, "loss_peak": 3.0, "jit_peak": 5.0, "lead_s": 25.0, "kind": "tunnel_ramp"},
    }
    out = {}
    for ft, dft in defaults.items():
        g = f[f["fault_type"] == ft]
        sig = dict(dft)
        if len(g):
            gt = g[g["entity_type"] == "tunnel"]
            if len(gt) >= 2 and not gt["tunnel_latency_ms"].isna().all():
                sig["lat_peak"] = float(gt["tunnel_latency_ms"].max())
                sig["loss_peak"] = float(gt["tunnel_loss_pct"].max())
                sig["jit_peak"] = float(gt["tunnel_jitter_ms"].max())
            lt = g["lead_time_s"].dropna()
            if len(lt):
                sig["lead_s"] = float(lt.median())
            sig["_src"] = "real"
        else:
            sig["_src"] = "default"
        out[ft] = sig
    return out


def _device_health(df):
    """Baselines for the device-health / error-counter features.

    These columns only exist in captures taken after the device-health feature
    set landed, so every statistic falls back to a documented default and is
    marked "_src":"default" when the source capture predates them.

    Error/discard rates are expressed per 30s step (the counters are cumulative;
    what the generator needs is the increment). Defaults describe a HEALTHY
    fabric: errors are rare, discards near-zero, queues shallow.
    """
    defaults = {
        "err_rate_per_step":      0.004,   # ifInErrors increments per step
        "discard_rate_per_step":  0.02,    # ifInDiscards increments per step
        "q_backlog_bytes":        900.0,   # shallow standing queue
        "q_drops_per_step":       0.01,
        "cpu_pct":                4.0,     # FRR control plane at rest
        "mem_pct":                2.5,
        "bgp_msg_per_step":       6.0,     # keepalives dominate when stable
        "rib_routes":             120.0,
        "ospf_lsa_count":         90.0,
    }
    out = {}
    for key, dflt in defaults.items():
        col = {"cpu_pct": "cpu_pct", "mem_pct": "mem_pct",
               "rib_routes": "rib_routes",
               "ospf_lsa_count": "ospf_lsa_count"}.get(key)
        s = df[col].dropna() if (col and col in df.columns) else pd.Series(dtype=float)
        if len(s) >= 5:
            out[key] = {"mean": float(s.mean()), "std": float(s.std()), "_src": "real"}
        else:
            out[key] = {"mean": dflt, "std": dflt * 0.35, "_src": "default"}
    return out


def _inventory(df):
    """device -> {site_type, interfaces[], tunnels[]} so naming matches the lab."""
    inv = {}
    for dev, g in df.groupby("device"):
        st = g["site_type"].dropna()
        ifaces = sorted(g[g.entity_type == "interface"]["entity"].unique().tolist())
        tuns = sorted(g[g.entity_type == "tunnel"]["entity"].unique().tolist())
        inv[dev] = {
            "site_type": str(st.iloc[0]) if len(st) else None,
            "interfaces": ifaces,
            "tunnels": tuns,
        }
    return inv


def build_profile(real_path):
    df = pd.read_parquet(real_path)
    iface = df[df.entity_type == "interface"]
    tun = df[df.entity_type == "tunnel"]
    tun_nf = tun[~tun.is_fault]
    f = df[df.is_fault]

    tun_baseline, tun_baseline_by_site = _tunnel_baseline(tun_nf)
    profile = {
        "source_parquet": os.path.basename(real_path),
        "source_rows": int(len(df)),
        "step_s": STEP,
        "octet_rate_by_site": _octet_rate(iface[~iface.is_fault]),
        "tunnel_baseline": tun_baseline,
        "tunnel_baseline_by_site": tun_baseline_by_site,
        "fault_signatures": _fault_signatures(f, tun_nf),
        "device_health": _device_health(df[~df.is_fault]),
        "real_fault_fraction": float(df.is_fault.mean()),
        "inventory": _inventory(df),
        # octet counter seed: real counters start large (mid-run capture). Seed
        # synthetic counters from the observed per-site median absolute value so
        # ranges OVERLAP the real data (the check asserts this).
        "octet_seed_by_site": {
            str(st): float(g["if_in_octets"].median())
            for st, g in iface[~iface.is_fault].groupby("site_type", dropna=False)
        },
    }
    return profile


def main():
    real = sys.argv[1] if len(sys.argv) > 1 else _newest_real()
    profile = build_profile(real)
    out = os.path.join(HERE, "profile.json")
    with open(out, "w") as fh:
        json.dump(profile, fh, indent=2)
    print(f"wrote {out} from {os.path.basename(real)}")
    print(f"  devices={len(profile['inventory'])} "
          f"fault_types={list(profile['fault_signatures'])} "
          f"real_fault_frac={profile['real_fault_fraction']:.4f}")


if __name__ == "__main__":
    main()
