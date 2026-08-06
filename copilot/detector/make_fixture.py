"""One-time sampler: carve a small, committed TDD corpus out of the (gitignored)
full synthetic run.

The detector is tested against REAL generator output, not hand-rolled numbers, so
its thresholds track the actual signatures. The full run is 241 MB / 17 M rows and
gitignored -- this samples short per-device TIME WINDOWS (a detector needs history:
"down" = oper down SUSTAINED, which one bucket cannot show) covering every fault
type's episode, the hard-negative traps, and clean baseline, into
fixtures/corpus.parquet (committed).

Each emitted window carries `window_id` + withheld truth (`truth_kind`,
`truth_fault_type`) so the test groups by window and grades detect() directly.

Reproduce: python3 -m copilot.detector.make_fixture [SRC.parquet]
Default SRC = synthetic/output/synthetic_multi_d0.2_s30_x3.0_n12.parquet
"""
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

HERE = os.path.dirname(__file__)
DEFAULT_SRC = os.path.join(HERE, "..", "..", "synthetic", "output",
                           "synthetic_multi_d0.2_s30_x3.0_n12.parquet")
OUT = os.path.join(HERE, "fixtures", "corpus.parquet")

# detector-visible columns (what a real telemetry reader would have) + the keys/labels
# the sampler needs. The committed fixture keeps the detector cols + window_id + truth.
DETECT_COLS = ["device", "entity", "entity_type", "ts", "if_oper_status",
               "if_in_octets", "if_out_octets", "tunnel_loss_pct", "tunnel_latency_ms",
               "tunnel_jitter_ms", "cpu_pct", "q_backlog_bytes", "if_out_discards",
               "xcvr_rx_power_dbm", "xcvr_tx_bias_ma"]
SAMPLER_COLS = ["topology_id", "scenario_id", "fault_type", "is_fault",
                "is_hard_negative"]

PER_FAULT = 30     # episodes sampled per fault_type
N_HARD_NEG = 250   # hard-negative windows (the false-positive traps)
N_CLEAN = 250      # clean baseline windows
PAD = 2            # buckets of context padded around a hard-neg run
CLEAN_LEN = 12     # bucket span of a clean window
SEED = 7


def _s0(x):
    if isinstance(x, (list, tuple, np.ndarray)):
        return x[0] if len(x) else None
    return x


def _device_span(df, topo, device, t0, t1):
    """All rows for one device over [t0, t1] on the grid -- the detector's window."""
    m = (df.topology_id == topo) & (df.device == device) & (df.ts >= t0) & (df.ts <= t1)
    return df.loc[m]


def main(src=DEFAULT_SRC):
    assert os.path.exists(src), f"source run missing: {src} -- generate it first"
    df = pq.read_table(src, columns=DETECT_COLS + SAMPLER_COLS).to_pandas()
    for c in ("fault_type", "scenario_id"):
        df[c] = df[c].map(_s0)
    rng = np.random.default_rng(SEED)
    step = np.median(np.diff(np.sort(df.ts.unique())))  # Timedelta (grid bucket)
    windows = []
    wid = 0

    # --- fault windows: one per episode (scenario_id), labelled by its fault_type ---
    fault = df[df.is_fault & df.scenario_id.notna()]
    for ft, g in fault.groupby("fault_type"):
        sids = g.scenario_id.unique()
        for sid in rng.permutation(sids)[:PER_FAULT]:
            ep = g[g.scenario_id == sid]
            dev, topo = ep.device.iloc[0], ep.topology_id.iloc[0]
            w = _device_span(df, topo, dev, ep.ts.min() - PAD * step, ep.ts.max() + PAD * step).copy()
            w["window_id"], w["truth_kind"], w["truth_fault_type"] = wid, "fault", ft
            windows.append(w); wid += 1

    # --- hard-negative windows: contiguous is_hard_negative runs per device ---
    hn = df[df.is_hard_negative].sort_values(["topology_id", "device", "ts"])
    hn_windows = []
    for (topo, dev), g in hn.groupby(["topology_id", "device"]):
        ts = np.sort(g.ts.unique())
        brk = np.where(np.diff(ts) > step)[0] + 1
        for run in np.split(ts, brk):
            hn_windows.append((topo, dev, run.min() - PAD * step, run.max() + PAD * step))
    for i in rng.permutation(len(hn_windows))[:N_HARD_NEG]:
        topo, dev, t0, t1 = hn_windows[i]
        w = _device_span(df, topo, dev, t0, t1).copy()
        w["window_id"], w["truth_kind"], w["truth_fault_type"] = wid, "hard_neg", None
        windows.append(w); wid += 1

    # --- clean windows: devices/spans with no fault and no hard-negative row ---
    dirty = set(map(tuple, df.loc[df.is_fault | df.is_hard_negative,
                                  ["topology_id", "device"]].drop_duplicates().values))
    clean_dev = [t for t in map(tuple, df[["topology_id", "device"]].drop_duplicates().values)
                 if t not in dirty]
    for i in rng.permutation(len(clean_dev))[:N_CLEAN]:
        topo, dev = clean_dev[i]
        ts = np.sort(df[(df.topology_id == topo) & (df.device == dev)].ts.unique())
        if len(ts) < 2:
            continue
        start = ts[rng.integers(0, max(1, len(ts) - CLEAN_LEN))]
        w = _device_span(df, topo, dev, start, start + CLEAN_LEN * step).copy()
        w["window_id"], w["truth_kind"], w["truth_fault_type"] = wid, "clean", None
        windows.append(w); wid += 1

    out = pd.concat(windows, ignore_index=True)[
        DETECT_COLS + ["window_id", "truth_kind", "truth_fault_type"]]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    out.to_parquet(OUT, index=False)
    k = out.drop_duplicates("window_id").truth_kind.value_counts().to_dict()
    print(f"wrote {OUT}: {len(out)} rows, {wid} windows {k}, "
          f"{out.truth_fault_type.nunique(dropna=True)} fault types")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC)
