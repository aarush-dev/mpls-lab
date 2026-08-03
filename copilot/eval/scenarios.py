"""copilot.eval.scenarios -- S4 (#37) labelled eval-scenario seed (ADR-0017).

A future replay-eval harness (deferred, ADR-0017 §Eval) scores an investigator answer against
`/labels` ground truth on: fault-type, root-cause localization, time-to-impact (+ citation/gate/
abstain axes it derives elsewhere). This module is that ground truth, projected once into the
scoreable expected-answer per scenario and frozen as a committed starter set (`scenarios.jsonl`).

A scenario is a pure PROJECTION of one `faults/labels/labels.jsonl` row (the same row the
PA-emulator consumes, emulate.py) -- expected values ARE the ground-truth label, never re-derived
from copilot output, so scoring can't be tautological:
  fault_type  = label `type`                         (fine expected fault)
  family      = emulator.family(type)                (coarse §2.1 expected; ONE source of truth)
  root_cause  = label `target` {device, interface/neighbor/vrf}   (expected localization)
  tti_s       = label `lead_time` (= t_impact - t_start)          (expected time-to-impact)

Curation (ponytail): the starter set is the lexicographically-first scenario of each distinct
fault `type` -- deterministic (no RNG/wall-clock), covers all 5 families and every fault type,
spans severities naturally. Regenerate with `python3 -m copilot.eval.scenarios --write`; the
committed jsonl is the stable contract the (unknown, deferred) harness reads without importing us.

Self-check:  python3 -m copilot.eval.scenarios
"""
import json
import os

from copilot.emulator import family

# the 5 coarse families (emulate._FAMILY values, ADR-0003/PA.md §2.1) the starter set must cover.
FAMILIES = frozenset({"congestion", "routing-instability", "tunnel-degradation",
                      "device-fault", "link-fault"})

_HERE = os.path.dirname(os.path.abspath(__file__))
SEED_PATH = os.path.join(_HERE, "scenarios.jsonl")
LABELS_PATH = os.path.join(_HERE, "..", "..", "faults", "labels", "labels.jsonl")


def _root_cause(label: dict) -> dict:
    """Normalized expected localization. `target` is a dict in most rows but a bare device
    string in 13 (labels.jsonl inconsistency) -- unify to a {device, ...} dict so a harness
    scores one shape. Device always present: the row's top-level `device` is the base, target
    keys enrich it -- load-bearing for the 4 pop/srlg-level rows whose dict target has no
    `device` key (pop_isolation/core_partition/srlg_cut).

    ponytail: base = top-level device, target `**tgt` wins on conflict. Verified 0 rows where
    target.device != top-level device today, so the precedence is moot in the seed; if a future
    label sets a target.device that differs (a more precise localization than the row's device),
    the target's value is the intended winner. Upgrade to a stated schema only if a harness must
    branch on the keyset."""
    tgt = label["target"]
    if not isinstance(tgt, dict):
        return {"device": tgt}
    return {"device": label["device"], **tgt}


def _scenario(label: dict) -> dict:
    """Project one ground-truth label row -> an eval scenario (expected answer)."""
    return {
        "scenario_id": label["scenario_id"],
        "fault_type": label["type"],
        "family": family(label["type"]),
        "root_cause": _root_cause(label),
        "tti_s": label["lead_time"],
        "severity": label["severity"],
        "signature": label["signature"],
    }


def build_scenarios(labels: list[dict]) -> list[dict]:
    """Curate the starter set from ground-truth labels: the first scenario (by scenario_id) of
    each distinct fault `type`, projected to expected answers, sorted by scenario_id. Pure +
    deterministic -- same labels in, same set out."""
    first: dict[str, dict] = {}
    for lab in labels:
        t = lab["type"]
        if t not in first or lab["scenario_id"] < first[t]["scenario_id"]:
            first[t] = lab
    return sorted((_scenario(lab) for lab in first.values()),
                  key=lambda s: s["scenario_id"])


def _read_jsonl(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def load_seed(path: str = SEED_PATH) -> list[dict]:
    """The committed starter scenario set (what a harness consumes)."""
    return _read_jsonl(path)


def write_seed(labels_path: str = LABELS_PATH, out_path: str = SEED_PATH) -> list[dict]:
    """Regenerate scenarios.jsonl from the labels file. Idempotent for unchanged labels."""
    scenarios = build_scenarios(_read_jsonl(labels_path))
    with open(out_path, "w") as f:
        for s in scenarios:
            f.write(json.dumps(s) + "\n")
    return scenarios


if __name__ == "__main__":
    import sys
    if "--write" in sys.argv:
        n = len(write_seed())
        print(f"wrote {n} scenarios -> {SEED_PATH}")
    else:
        from copilot.eval.test_scenarios import _run
        _run()
