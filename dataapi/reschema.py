"""reschema.py -- bring an already-exported Parquet onto the current canonical
schema WITHOUT re-querying the telemetry stack.

Why this exists: the schema changed under a capture that cannot be retaken (the
lab it came from is gone). The metric columns in that file are the measurement
and are copied through untouched; everything this script rewrites is encoding or
a re-derivation from ground truth that is still on disk:

  * ts            string -> timestamp[us, UTC]                 (DEFECT 6b)
  * severity      string -> ordinal float, string kept in severity_label (6a)
  * labels        collapsed scalars -> full concurrent lists     (DEFECT 5b)
                  re-joined from faults/labels/labels.jsonl, not converted from
                  the old single-winner columns, so concurrency that the old
                  collapse discarded comes back
  * vrf           null -> derived where VRF is meaningful        (DEFECT 3)

It invents nothing. If the label file is missing, the label columns come out
empty rather than guessed, and it says so.

Run:  python3 reschema.py <in.parquet> [-o out.parquet] [--step 30]
"""
import argparse
import os
import sys

import pandas as pd

import export
import sources

# Columns the label join owns; dropped before the re-join so a stale value cannot
# survive into the new file.
_LABEL_COLS = ["is_fault", "scenario_id", "fault_type", "severity", "severity_label",
               "lead_time_s", "time_to_impact_s", "fault_types", "severities",
               "scenario_ids", "impact_methods", "n_concurrent",
               "fault_type_primary", "severity_primary", "scenario_id_primary"]


def reschema(path, step=30, out=None):
    df = pd.read_parquet(path)
    before = len(df)
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    df = df.drop(columns=[c for c in _LABEL_COLS if c in df.columns])
    try:
        n_labels = len(sources.label_rows())
    except Exception:
        n_labels = 0
    if not n_labels:
        print("WARN: no label rows found -- label columns will be empty",
              file=sys.stderr)
    df = export._apply_labels(df, step)
    df = export._fill_vrf(df)
    df = export.finalize_schema(df)
    assert len(df) == before, f"row count changed: {before} -> {len(df)}"
    out = out or path
    tmp = f"{out}.{os.getpid()}.tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, out)
    return out, df, n_labels


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path")
    ap.add_argument("-o", "--out", default=None, help="default: rewrite in place")
    ap.add_argument("--step", type=int, default=30)
    args = ap.parse_args()
    out, df, n = reschema(args.path, args.step, args.out)
    print(f"wrote {out}")
    print(f"rows={len(df)} cols={len(df.columns)} labels_applied={n} "
          f"fault_rows={int(df.is_fault.sum())} "
          f"precursor_rows={int(export.precursor_mask(df).sum())} "
          f"max_concurrent={int(df.n_concurrent.max())}")


if __name__ == "__main__":
    main()
