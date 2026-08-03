"""Assert-based self-check for the S4 (#37) eval-scenario seed (ADR-0017).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Seam under test: build_scenarios (pure curation+projection) + the committed seed (load_seed) --
the contract a future eval harness reads. Expected values must BE the ground-truth label, so the
checks compare the seed back to labels.jsonl (an independent source of truth), never to copilot output.
Run:  python3 -m copilot.eval.test_scenarios
"""
from copilot.eval.scenarios import (
    FAMILIES, LABELS_PATH, _read_jsonl, build_scenarios, load_seed,
)

_LABELS = _read_jsonl(LABELS_PATH)
_BY_ID = {r["scenario_id"]: r for r in _LABELS}


def test_build_is_deterministic_and_one_per_type():
    a = build_scenarios(_LABELS)
    b = build_scenarios(list(reversed(_LABELS)))       # input order must not matter
    assert a == b, "build_scenarios not deterministic wrt input order"
    types = [s["fault_type"] for s in a]
    assert len(types) == len(set(types)), "more than one scenario per fault type"
    assert set(types) == {r["type"] for r in _LABELS}, "not every fault type covered"


def test_build_picks_lexicographically_first_scenario_of_each_type():
    # curation rule is load-bearing: the pick is the min scenario_id per type (reproducible).
    from collections import defaultdict
    want = defaultdict(list)
    for r in _LABELS:
        want[r["type"]].append(r["scenario_id"])
    for s in build_scenarios(_LABELS):
        assert s["scenario_id"] == min(want[s["fault_type"]]), s["fault_type"]


def test_seed_matches_a_fresh_build():
    # the committed jsonl must not drift from the generator (regenerate with --write).
    assert load_seed() == build_scenarios(_LABELS), \
        "scenarios.jsonl stale -- run `python3 -m copilot.eval.scenarios --write`"


def test_seed_covers_every_family():
    fams = {s["family"] for s in load_seed()}
    assert fams == FAMILIES, f"families covered {fams} != {FAMILIES}"


def test_expected_values_are_the_ground_truth_label():
    # every scenario is a faithful projection of a real label -- expected == ground truth, not
    # invented. This is what stops later scoring from being tautological.
    for s in load_seed():
        lab = _BY_ID.get(s["scenario_id"])
        assert lab is not None, f"{s['scenario_id']} not a real label"
        assert s["fault_type"] == lab["type"]
        assert s["tti_s"] == lab["lead_time"]            # expected TTI = label lead_time
        assert isinstance(s["tti_s"], (int, float)) and s["tti_s"] > 0
        # expected localization faithful to the label: device preserved, no target key dropped
        assert s["root_cause"]["device"] == lab["device"], "root cause device != label device"
        if isinstance(lab["target"], dict):
            assert lab["target"].items() <= s["root_cause"].items(), "dropped a target field"


def test_root_cause_fills_device_for_deviceless_dict_targets():
    # load-bearing branch: 4 rows (pop_isolation/core_partition/srlg_cut) carry a dict target with
    # NO device key -- the projection must fill it from the row's top-level `device`, else the
    # harness has no device to localize to. Uses real labels, not a hand-built fixture.
    deviceless = [r for r in _LABELS
                  if isinstance(r["target"], dict) and not r["target"].get("device")]
    assert deviceless, "expected >=1 deviceless dict target in labels (data changed?)"
    by_id = {s["scenario_id"]: s for s in build_scenarios(_LABELS)}
    for lab in deviceless:
        s = by_id.get(lab["scenario_id"])
        if s is None:
            continue                                     # not the per-type pick; the invariant is
        assert s["root_cause"]["device"] == lab["device"]  # filled from top-level device


def _run():
    test_build_is_deterministic_and_one_per_type()
    test_root_cause_fills_device_for_deviceless_dict_targets()
    test_build_picks_lexicographically_first_scenario_of_each_type()
    test_seed_matches_a_fresh_build()
    test_seed_covers_every_family()
    test_expected_values_are_the_ground_truth_label()
    print("copilot.eval.scenarios self-check OK")


if __name__ == "__main__":
    _run()
