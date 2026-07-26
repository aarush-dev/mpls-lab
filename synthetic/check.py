"""check.py -- runnable assert-based gate for the synthetic dataset.

Asserts:
  0. the Parquet declares itself synthetic in its file metadata
  1. schema columns == dataapi canonical (export.COLUMNS), exact order
  2. output non-empty
  3. fault fraction in a sane band (not zero, not absurd)
  4. precursor rows exist (is_fault & time_to_impact_s > 0) -- the ML signal
  5. metric DISTRIBUTIONS track the real calibration profile: latency median
     within 2 sigma of the real p50, latency not near-constant, no negative
     jitter/loss/octets/queue, octet counters monotonic AND advancing
  6. real + synthetic Parquets concatenate with IDENTICAL dtypes on shared cols
  7. device-health features carry signal, not constants:
     - all three entity_type row kinds present
     - power is idle-dominated and role-scaled (Vishwanath 2014)
     - temperature moves and is spatially correlated per POP
     - error/discard/queue counters rise under fault
     - gray_failure shows the optical divergence (rx down, laser bias up)
  8. every tunnel_ramp fault type's precursor rows actually ramp (pre-impact
     latency mean above the healthy mean), not just carry the label

Run:  python3 check.py            # newest synthetic in output/
      python3 check.py <path>
"""
import glob
import json
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "dataapi"))
from export import COLUMNS

OUTDIR = os.path.join(HERE, "output")
DATASETS = os.path.join(HERE, "..", "dataapi", "datasets")


def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        files = glob.glob(os.path.join(OUTDIR, "*.parquet"))
        assert files, "no synthetic Parquet -- run generate.py first"
        # ponytail: newest by mtime. Lexical sort picked "d7.0" over "d0.05" and
        # silently validated a stale file from a previous schema.
        path = max(files, key=os.path.getmtime)
    df = pd.read_parquet(path)
    prof = json.load(open(os.path.join(HERE, "profile.json")))

    # 0. the file must declare itself synthetic. Without this a synthetic export
    # is indistinguishable from a capture once it leaves output/.
    meta = pq.ParquetFile(path).schema_arrow.metadata or {}
    assert meta.get(b"synthetic") == b"true", \
        f"{os.path.basename(path)} carries no synthetic=true Parquet metadata"
    # 1. schema
    assert list(df.columns) == COLUMNS, \
        f"schema mismatch\n exp {COLUMNS}\n got {list(df.columns)}"
    # 2. non-empty
    assert len(df) > 0, "empty dataset"
    # 3. fault fraction sane
    frac = df["is_fault"].mean()
    assert 0.0005 < frac < 0.25, f"fault fraction out of band: {frac:.4f}"
    # 4. precursors exist
    prec = ((df["is_fault"]) & (df["time_to_impact_s"] > 0)).sum()
    assert prec > 0, "no precursor rows (is_fault & time_to_impact_s>0)"
    # 5. distributions match the real calibration.
    # The old gate asserted interval OVERLAP only, which a constant column
    # passes; it could not fail. These compare shape and enforce physics.
    tb = prof["tunnel_baseline"]["tunnel_latency_ms"]
    lat = df["tunnel_latency_ms"].dropna()
    assert abs(lat.median() - tb["p50"]) <= 2.0 * tb["std"], \
        f"tunnel latency median {lat.median():.1f} off real p50 {tb['p50']:.1f} (std {tb['std']:.1f})"
    assert lat.std() > 0.15 * tb["std"], \
        f"tunnel latency is near-constant (std={lat.std():.3f}, real std={tb['std']:.3f})"
    for c in ("tunnel_jitter_ms", "tunnel_loss_pct", "tunnel_rekeys",
              "if_in_octets", "if_out_octets", "q_backlog_bytes", "q_drops"):
        s = df[c].dropna()
        assert len(s) and s.min() >= 0, f"{c} has negative values (min={s.min()})"
    # Cumulative counters: monotonic per (device, entity) AND actually advancing.
    ordered = df[df.entity_type == "interface"].sort_values(["device", "entity", "ts"])
    for c in ("if_in_octets", "if_out_octets"):
        dif = ordered.groupby(["device", "entity"])[c].diff().dropna()
        assert (dif < 0).sum() == 0, \
            f"{c}: {(dif < 0).sum()} backwards steps -- counter is not monotonic"
        assert (dif > 0).mean() > 0.5, \
            f"{c}: only {(dif > 0).mean()*100:.1f}% of buckets advance -- counter is flat"
    # interface octets overlap real per-site magnitude (loose: same order)
    real = pd.read_parquet(max(glob.glob(os.path.join(DATASETS, "*.parquet")),
                               key=os.path.getmtime))
    rin = real["if_in_octets"].dropna()
    sin = df["if_in_octets"].dropna()
    assert sin.max() >= rin.min() and sin.min() <= rin.max(), \
        "synthetic octet range doesn't overlap real"
    # 6. concat-compatibility == identical dtypes on the shared columns. The old
    # gate asserted len(concat)==len(a)+len(b) and that the columns it had just
    # selected were the columns it selected; neither could fail. A capture taken
    # before the device-health columns existed is missing them, so only columns
    # present in BOTH are compared; reindex fills the rest with NA.
    shared = [c for c in COLUMNS if c in real.columns]
    bad = [(c, str(real[c].dtype), str(df[c].dtype))
           for c in shared if real[c].dtype != df[c].dtype]
    assert not bad, f"dtype mismatch real vs synthetic: {bad}"
    cat = pd.concat([real.reindex(columns=COLUMNS), df[COLUMNS]], ignore_index=True)

    # 7. device-health features must carry signal, not be constants -----------
    dev = df[df.entity_type == "device"]
    ifc = df[df.entity_type == "interface"]
    assert set(df.entity_type.unique()) == {"interface", "tunnel", "device"}, \
        f"missing entity_type kinds: {sorted(df.entity_type.unique())}"
    assert len(dev) > 0 and len(ifc) > 0

    # Power idle-dominated AND role-scaled: a core chassis must outdraw a CPE by
    # an order of magnitude (Vishwanath 2014 measured 11.07kW idle / 12.3kW max).
    pw = dev.groupby("site_type")["device_power_watts"].mean()
    assert dev.device_power_watts.min() > 0, "non-positive power"
    if {"core", "branch"} <= set(pw.index):
        assert pw["core"] > 10 * pw["branch"], \
            f"power not role-scaled: core={pw.get('core')} branch={pw.get('branch')}"

    # Temperature must MOVE (a constant column teaches nothing) and stay physical.
    t = dev.device_temp_c.dropna()
    assert t.std() > 0.5, f"device_temp_c is near-constant (std={t.std():.3f})"
    assert 5.0 < t.min() and t.max() < 80.0, f"temp out of physical range: {t.min()}-{t.max()}"

    # Per-POP ambient => provider nodes in different POPs sit at different means.
    prov = dev[dev.device.str.match(r"^(p|pe)\d+$")].copy()
    if len(prov):
        prov["pop"] = [_pop_of(d) for d in prov.device]
        pop_means = prov.groupby("pop")["device_temp_c"].mean()
        if len(pop_means) > 1:
            assert pop_means.max() - pop_means.min() > 0.5, \
                "no per-POP temperature spread (spatial correlation missing)"

    # Fault rows must be louder than healthy on the counters that faults drive.
    if ifc.is_fault.any():
        for col in ("if_in_discards", "q_backlog_bytes", "q_drops"):
            g = ifc.groupby("is_fault")[col].mean()
            assert g.get(True, 0) > g.get(False, 0), \
                f"{col} does not rise under fault: {g.to_dict()}"

    # gray_failure: rx optical power sags while laser bias climbs, with no
    # oper-status change. This divergence is the scenario's only early signal.
    gray = ifc[ifc.fault_type == "gray_failure"]
    healthy = ifc[~ifc.is_fault]
    if len(gray) and gray.xcvr_rx_power_dbm.notna().any():
        assert gray.xcvr_rx_power_dbm.mean() < healthy.xcvr_rx_power_dbm.mean(), \
            "gray_failure did not depress rx optical power"
        assert gray.xcvr_tx_bias_ma.mean() > healthy.xcvr_tx_bias_ma.mean(), \
            "gray_failure did not raise laser bias current"

    # 8. a precursor label must have a precursor SIGNAL behind it: for every
    # tunnel_ramp fault type, pre-impact tunnel latency must already exceed the
    # healthy mean. Calibrated lead_s of ~2s (< one bucket) used to produce
    # time_to_impact_s>0 rows that were bit-identical to baseline.
    tun = df[df.entity_type == "tunnel"]
    base_lat = tun[~tun.is_fault].tunnel_latency_ms.mean()
    for ft, sig in prof["fault_signatures"].items():
        if sig.get("kind") != "tunnel_ramp":
            continue
        pre = tun[(tun.is_fault) & (tun.fault_type == ft) & (tun.time_to_impact_s > 0)]
        if len(pre) < 20:
            continue  # too few draws in a short run to be a meaningful mean
        assert pre.tunnel_latency_ms.mean() > base_lat, \
            f"{ft} precursor rows carry no ramp: {pre.tunnel_latency_ms.mean():.2f} vs healthy {base_lat:.2f}"

    print(f"OK {os.path.basename(path)}")
    print(f"  rows={len(df)} cols={len(df.columns)} fault%={frac*100:.2f} precursors={prec}")
    print(f"  entity_type: {df.entity_type.value_counts().to_dict()}")
    print(f"  tunnel_latency_ms {lat.min():.1f}-{lat.max():.1f} (real {tb['min']:.1f}-{tb['max']:.1f})")
    print(f"  device_temp_c {t.min():.1f}-{t.max():.1f}C  power {dev.device_power_watts.min():.0f}-{dev.device_power_watts.max():.0f}W")
    print(f"  fault_types={df.fault_type.nunique()}  concat real+synth -> {len(cat)} rows, schema stable")


def _pop_of(dev):
    """POP index from device name -- mirrors synthetic/generate.py:_pop_of."""
    d = str(dev)
    try:
        if d.startswith("pe"):
            return (int(d[2:]) - 1) // 2 + 1
        if d.startswith("p"):
            return (int(d[1:]) - 1) // 4 + 1
    except ValueError:
        pass
    return 1


if __name__ == "__main__":
    main()
