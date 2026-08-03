"""Assert self-check: the runbook corpus names every fault scenario.

Ground truth = faults/orchestrator.py SCENARIOS (the 21 injectable faults), NOT
the stale faults/README.md table. Guards S2's promise that the RAG corpus
covers the whole fault taxonomy: if a scenario is added to the orchestrator and
no runbook mentions it, this fails.

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Run:  python3 ragcorpus/check_corpus.py
"""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))  # repo root, for faults.orchestrator

from faults.orchestrator import SCENARIOS  # noqa: E402

# backtick-wrapped snake_case tokens (`gray_failure`) named anywhere in a runbook
_TOKEN = re.compile(r"`([a-z][a-z0-9_]+)`")


def scenarios_named_in_runbooks():
    named = set()
    for path in glob.glob(os.path.join(HERE, "runbook-*.md")):
        with open(path) as f:
            named |= set(_TOKEN.findall(f.read()))
    return named & set(SCENARIOS)


def test_runbooks_cover_all_scenarios():
    want = set(SCENARIOS)
    got = scenarios_named_in_runbooks()
    missing = want - got
    assert not missing, f"{len(missing)} fault(s) named in no runbook: {sorted(missing)}"


if __name__ == "__main__":
    test_runbooks_cover_all_scenarios()
    print(f"ragcorpus coverage OK: all {len(SCENARIOS)} scenarios named in runbooks")
