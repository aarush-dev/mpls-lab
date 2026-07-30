"""check_dataset.py -- runnable assert-based schema gate.

Fails (AssertionError, non-zero exit) if the latest dataset Parquet is missing,
empty, does not match the canonical schema, violates schema/dataset.schema.json,
or has no positive labels despite the window overlapping a fault. Run after
export.py.

Usage:  python3 check_dataset.py            # checks newest dataset in datasets/
        python3 check_dataset.py <path>     # check a specific Parquet
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd
from jsonschema import Draft202012Validator

import sources
from export import COLUMNS, DATASETS_DIR, _parse_iso

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "schema", "dataset.schema.json")


def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        files = glob.glob(os.path.join(DATASETS_DIR, "*.parquet"))
        assert files, "no dataset Parquet found in datasets/ -- run export.py first"
        # newest by window END, not the lexicographic sort (which orders by START)
        path = max(files, key=lambda p: int(os.path.basename(p).split("_")[2]))

    df = pd.read_parquet(path)

    assert len(df) > 0, f"dataset is empty: {path}"
    assert list(df.columns) == COLUMNS, (
        f"schema mismatch.\n expected: {COLUMNS}\n got:      {list(df.columns)}")
    # at least one real telemetry value present somewhere
    telem_cols = ["if_in_octets", "if_out_octets", "tunnel_latency_ms", "tunnel_loss_pct"]
    assert df[telem_cols].notna().any().any(), "no telemetry values in dataset"

    # is_fault must actually BE boolean -- .dropna().isin([True, False]) passed
    # for an all-null column and for any 0/1 numeric column.
    assert df["is_fault"].dtype == bool, f"is_fault dtype is {df['is_fault'].dtype}, want bool"

    # enforce the published contract, not just the column list
    validator = Draft202012Validator(json.load(open(SCHEMA_PATH)))
    # The schema describes the JSON projection of a row, so project first:
    # ts is a timestamp on the wire (DEFECT 6b) and the label columns are numpy
    # arrays (DEFECT 5b). NaN is not JSON null either, so nullable fields need
    # the conversion to validate as intended.
    def _json(v):
        if isinstance(v, (list, tuple, np.ndarray)):
            return [None if (x is None or (isinstance(x, float) and pd.isna(x))) else x
                    for x in v]
        if isinstance(v, pd.Timestamp):
            return v.strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(v, (np.integer, np.floating)):
            v = v.item()
        return None if pd.isna(v) else v

    sample = [{k: _json(v) for k, v in rec.items()}
              for rec in df.head(500).to_dict(orient="records")]
    errors = [f"{list(e.path)}: {e.message}"
              for rec in sample for e in validator.iter_errors(rec)]
    assert not errors, f"schema violations ({len(errors)}):\n  " + "\n  ".join(errors[:10])

    # A zero-positive label column is the exact failure this gate exists to
    # catch. Only demand positives when a fault window actually overlaps.
    ts = pd.to_datetime(df["ts"], utc=True)  # timestamp column since DEFECT 6b
    t_lo, t_hi = ts.min().timestamp(), ts.max().timestamp()
    devices = set(df["device"])
    overlapping = [l["scenario_id"] for l in sources.label_rows()
                   if l.get("device") in devices
                   and _parse_iso(l["t_start"]) <= t_hi and _parse_iso(l["t_end"]) >= t_lo]
    if overlapping:
        assert df["is_fault"].any(), (
            f"no fault rows, but {len(overlapping)} labels overlap the window "
            f"({overlapping[:5]}) -- the label join is broken")

    print(f"OK {path}")
    print(f"  rows={len(df)} cols={len(df.columns)} fault_rows={int(df['is_fault'].sum())} "
          f"overlapping_labels={len(overlapping)}")


if __name__ == "__main__":
    main()
