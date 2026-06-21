"""check_dataset.py -- runnable assert-based schema gate.

Fails (AssertionError, non-zero exit) if the latest dataset Parquet is missing,
empty, or does not match the canonical schema. Run after export.py.

Usage:  python3 check_dataset.py            # checks newest dataset in datasets/
        python3 check_dataset.py <path>     # check a specific Parquet
"""
import glob
import os
import sys

import pandas as pd

from export import COLUMNS, DATASETS_DIR


def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        files = sorted(glob.glob(os.path.join(DATASETS_DIR, "*.parquet")))
        assert files, "no dataset Parquet found in datasets/ -- run export.py first"
        path = files[-1]

    df = pd.read_parquet(path)

    assert len(df) > 0, f"dataset is empty: {path}"
    assert list(df.columns) == COLUMNS, (
        f"schema mismatch.\n expected: {COLUMNS}\n got:      {list(df.columns)}")
    # at least one real telemetry value present somewhere
    telem_cols = ["if_in_octets", "if_out_octets", "tunnel_latency_ms", "tunnel_loss_pct"]
    assert df[telem_cols].notna().any().any(), "no telemetry values in dataset"
    # is_fault must be boolean-typed
    assert df["is_fault"].dropna().isin([True, False]).all(), "is_fault not boolean"

    print(f"OK {path}")
    print(f"  rows={len(df)} cols={len(df.columns)} fault_rows={int(df['is_fault'].sum())}")


if __name__ == "__main__":
    main()
