"""Assert-based tests / self-check for the signal detector (no framework, repo style).

Grades detect() against the committed real-data corpus (make_fixture.py): every
fault_type's impact window must fire, every hard-negative trap must NOT. Down
detection is checked per server-down fault type individually.

Prior art: copilot/emulator/test_emulate.py (assert + __main__).
Run:  python3 -m copilot.detector.test_detect
"""
import os

import pandas as pd

from copilot.detector import detect, scan

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "corpus.parquet")

# the 6 fault types that physically drop a link (if_oper_status -> down): "server
# going down". Every one must be caught by the down rule.
IFACE_DOWN = {"node_failure", "p_node_failure", "srlg_cut", "pop_isolation",
              "core_partition", "mpls_underlay_failure"}
# signal-starved BY DESIGN (loss below clean p99 / sub-threshold optical drift):
# best-effort per scope -- recorded, not gated. See detect.py.
BEST_EFFORT = {"gray_failure", "path_asymmetry"}

KEY = ["topology_id", "device", "ts"]


def _buckets(df):
    """Yield (label_kind, fault_type, verdict) per device-bucket. label_kind in
    {fault, hard_neg, clean} from the withheld ground truth."""
    for _, g in df.groupby(KEY, sort=False):
        verdict = detect(g)
        if bool(g["is_fault"].any()):
            ft = g.loc[g["is_fault"], "fault_type"].dropna()
            yield "fault", (ft.iloc[0] if len(ft) else None), verdict
        elif bool(g["is_hard_negative"].any()):
            yield "hard_neg", None, verdict
        else:
            yield "clean", None, verdict


def _fires(v):
    return bool(v["down"] or v["faulted"])


def _load():
    assert os.path.exists(FIXTURE), f"{FIXTURE} missing -- run make_fixture.py"
    return pd.read_parquet(FIXTURE)


def test_every_fault_type_detected():
    df = _load()
    hit, tot = {}, {}
    for kind, ft, v in _buckets(df):
        if kind != "fault" or ft is None:
            continue
        tot[ft] = tot.get(ft, 0) + 1
        hit[ft] = hit.get(ft, 0) + _fires(v)
    assert tot, "no fault buckets in fixture"
    for ft in sorted(tot):
        recall = hit[ft] / tot[ft]
        tag = "best-effort" if ft in BEST_EFFORT else "STRONG"
        print(f"  {ft:24s} recall={recall:.2f} ({hit[ft]}/{tot[ft]}) [{tag}]")
        if ft not in BEST_EFFORT:
            assert recall >= 0.90, f"{ft} recall {recall:.2f} < 0.90"
    # best-effort classes still must not be totally blind
    for ft in BEST_EFFORT & set(tot):
        assert hit[ft] > 0, f"{ft} caught nothing at all"


def test_server_down_detected_per_type():
    """Every 'server going down' fault type -> detect().down on its impact buckets."""
    df = _load()
    hit, tot = {}, {}
    for kind, ft, v in _buckets(df):
        if kind == "fault" and ft in IFACE_DOWN:
            tot[ft] = tot.get(ft, 0) + 1
            hit[ft] = hit.get(ft, 0) + bool(v["down"])
    for ft in sorted(IFACE_DOWN & set(tot)):
        recall = hit[ft] / tot[ft]
        print(f"  DOWN {ft:22s} recall={recall:.2f} ({hit[ft]}/{tot[ft]})")
        assert recall >= 0.90, f"down {ft} recall {recall:.2f} < 0.90"


def test_hard_negatives_do_not_fire():
    df = _load()
    fired = tot = 0
    for kind, _, v in _buckets(df):
        if kind == "hard_neg":
            tot += 1
            fired += _fires(v)
    rate = fired / tot if tot else 0.0
    print(f"  hard-neg false-positive rate={rate:.2f} ({fired}/{tot})")
    assert tot and rate <= 0.10, f"hard-neg FP rate {rate:.2f} > 0.10"


def test_clean_baseline_quiet():
    df = _load()
    fired = tot = 0
    for kind, _, v in _buckets(df):
        if kind == "clean":
            tot += 1
            fired += _fires(v)
    rate = fired / tot if tot else 0.0
    print(f"  clean false-positive rate={rate:.2f} ({fired}/{tot})")
    assert tot and rate <= 0.05, f"clean FP rate {rate:.2f} > 0.05"


def test_scan_shape():
    """scan(df) -> one verdict row per device-bucket, with the decision fields."""
    df = _load()
    out = scan(df)
    assert len(out) == df[KEY].drop_duplicates().shape[0]
    for col in ("device", "ts", "down", "faulted", "cause"):
        assert col in out.columns, f"scan output missing {col}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"\n{name}")
            fn()
    print("\nOK")
