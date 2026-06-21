"""check.py -- runnable assert-based gate for the synthetic dataset.

Asserts:
  1. schema columns == dataapi canonical (export.COLUMNS), exact order
  2. output non-empty
  3. fault fraction in a sane band (not zero, not absurd)
  4. precursor rows exist (is_fault & time_to_impact_s > 0) -- the ML signal
  5. metric ranges OVERLAP the real calibration profile (not wildly off)
  6. real + synthetic Parquets concatenate (same columns/dtypes)

Run:  python3 check.py            # newest synthetic in output/
      python3 check.py <path>
"""
import glob
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "dataapi"))
from export import COLUMNS

OUTDIR = os.path.join(HERE, "output")
DATASETS = os.path.join(HERE, "..", "dataapi", "datasets")


def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        files = sorted(glob.glob(os.path.join(OUTDIR, "*.parquet")))
        assert files, "no synthetic Parquet -- run generate.py first"
        path = files[-1]
    df = pd.read_parquet(path)
    prof = json.load(open(os.path.join(HERE, "profile.json")))

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
    # 5. ranges overlap real calibration
    tb = prof["tunnel_baseline"]["tunnel_latency_ms"]
    lat = df["tunnel_latency_ms"].dropna()
    assert lat.min() <= tb["max"] and lat.max() >= tb["min"], \
        f"tunnel latency range {lat.min():.1f}-{lat.max():.1f} doesn't overlap real {tb['min']:.1f}-{tb['max']:.1f}"
    # interface octets overlap real per-site magnitude (loose: same order)
    real = pd.read_parquet(sorted(glob.glob(os.path.join(DATASETS, "*.parquet")))[-1])
    rin = real["if_in_octets"].dropna()
    sin = df["if_in_octets"].dropna()
    assert sin.max() >= rin.min() and sin.min() <= rin.max(), \
        "synthetic octet range doesn't overlap real"
    # 6. concat-compatibility
    cat = pd.concat([real[COLUMNS], df[COLUMNS]], ignore_index=True)
    assert len(cat) == len(real) + len(df), "concat row count mismatch"
    assert list(cat.columns) == COLUMNS, "concat columns drifted"

    print(f"OK {os.path.basename(path)}")
    print(f"  rows={len(df)} cols={len(df.columns)} fault%={frac*100:.2f} precursors={prec}")
    print(f"  tunnel_latency_ms {lat.min():.1f}-{lat.max():.1f} (real {tb['min']:.1f}-{tb['max']:.1f})")
    print(f"  concat real+synth -> {len(cat)} rows, schema stable")


if __name__ == "__main__":
    main()
