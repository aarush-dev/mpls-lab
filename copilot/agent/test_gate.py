"""Assert-based tests / self-check for the I4a quality gate (ADR-0008 stage 1).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Seam under test: the pure gate fns -- pre_gate / citation_check / extract_entities --
over structured `Cite`s (spec #3 §Testing; the deterministic half, no LLM).
Run:  python3 -m copilot.agent.test_gate
"""
from copilot.agent.gate import (
    GateResult, citation_check, extract_entities, pre_gate, run_gate, tool_calls_ok,
)
from copilot.tools import Cite
from copilot.window import WindowContext

WINDOW = WindowContext(100, 200)


def _c(id, source="metrics", device="r1", ts=150):
    return Cite(id=id, source=source, device=device, ts=ts)


def test_extract_entities_pulls_device_names():
    assert extract_entities("why is r1 slow?") == frozenset({"r1"})
    assert extract_entities("bgp neighbor down on pe1 and ce2?") == frozenset({"pe1", "ce2"})
    assert extract_entities("is the network ok?") == frozenset()
    # protocol / interface tokens are NOT devices -> not required entities (else unanswerable)
    assert extract_entities("bgp as65001 flapping on ge0 for v4?") == frozenset()


def test_tool_calls_ok_flags_failed_calls():
    assert tool_calls_ok(()).ok
    r = tool_calls_ok(("error: over-broad: specify a device",))
    assert not r.ok and any("failed tool call" in m for m in r.missing)


def test_pre_gate_passes_sufficient_in_window_on_topic():
    r = pre_gate((_c("metrics:0"), _c("metrics:1")),
                 window=WINDOW, entities=frozenset({"r1"}), min_evidence=2)
    assert r.ok and r.missing == ()


def test_pre_gate_blocks_thin_evidence():
    r = pre_gate((_c("metrics:0"),), window=WINDOW,
                 entities=frozenset({"r1"}), min_evidence=2)
    assert not r.ok and any("thin" in m for m in r.missing)


def test_pre_gate_blocks_out_of_window():
    r = pre_gate((_c("metrics:0", ts=150), _c("metrics:1", ts=9999)),
                 window=WINDOW, entities=frozenset({"r1"}), min_evidence=2)
    assert not r.ok and any("out-of-window" in m and "metrics:1" in m for m in r.missing)


def test_pre_gate_blocks_off_topic_unsupported_entity():
    # question about r1, evidence only about r9 -> r1 unsupported -> off-topic block
    r = pre_gate((_c("metrics:0", device="r9"), _c("metrics:1", device="r9")),
                 window=WINDOW, entities=frozenset({"r1"}), min_evidence=2)
    assert not r.ok and any("off-topic" in m and "r1" in m for m in r.missing)


def test_pre_gate_blocks_windowed_evidence_off_question_entity():
    # reverse topicality (ADR-0008): a live read on a device the question never named -> off-topic
    r = pre_gate((_c("metrics:0", device="r9"), _c("metrics:1", device="r1")),
                 window=WINDOW, entities=frozenset({"r1"}), min_evidence=2)
    assert not r.ok and any("metrics:0 on r9" in m for m in r.missing)


def test_pre_gate_blocks_windowed_null_ts():
    # a windowed item with no ts can't be proven in-window -> fail, don't skip (ADR-0008)
    r = pre_gate((_c("metrics:0", ts=None), _c("metrics:1")),
                 window=WINDOW, entities=frozenset({"r1"}), min_evidence=2)
    assert not r.ok and any("out-of-window" in m and "metrics:0" in m for m in r.missing)


def test_pre_gate_exempts_kb_and_topology_from_window():
    # KB docs carry a historical ts, topology nodes none -> never window-rejected (ADR-0008),
    # else valid past-incident / structural evidence would be wrongly blocked.
    r = pre_gate((Cite("rb-bgp", "runbook", "r1", 1000), Cite("topo:r2", "topo", "r2", None)),
                 window=WINDOW, entities=frozenset({"r1"}), min_evidence=2)
    assert r.ok, r.missing


def test_pre_gate_allows_blast_radius_neighbours():
    # blast-radius (ADR-0007) surfaces neighbour devices the question never named; the gate
    # must NOT block them (only asserts every NAMED entity is supported, not the reverse).
    r = pre_gate((Cite("topo:r1", "topo", "r1", None), Cite("topo:r2", "topo", "r2", None)),
                 window=WINDOW, entities=frozenset({"r1"}), min_evidence=2)
    assert r.ok, r.missing


def test_citation_check_passes_fully_cited_answer():
    r = citation_check("r1 cpu is pegged [metrics:0]", {"metrics:0"})
    assert r.ok and r.missing == ()


def test_citation_check_rejects_uncited_claim():
    # a device-anchored sentence with no [id] -> rejected (acceptance #2)
    r = citation_check("r1 is down. r5 recovered [metrics:0].", {"metrics:0"})
    assert not r.ok and any("uncited claim" in m for m in r.missing)


def test_citation_check_rejects_fabricated_id():
    r = citation_check("r1 cpu pegged [metrics:9]", {"metrics:0"})
    assert not r.ok and any("fabricated" in m for m in r.missing)


def test_citation_check_rejects_answer_with_no_citations():
    r = citation_check("everything looks fine", set())
    assert not r.ok


def test_run_gate_combines_tool_calls_pre_gate_and_citation():
    cites = (_c("metrics:0"), _c("metrics:1"))
    ok = run_gate("r1 cpu pegged [metrics:0]", cites, window=WINDOW,
                  question="why is r1 slow?", min_evidence=2)
    assert ok.ok, ok.missing
    # a failed tool call blocks even a well-cited, sufficient answer (ADR-0008 check 1)
    bad = run_gate("r1 cpu pegged [metrics:0]", cites, window=WINDOW,
                   question="why is r1 slow?", min_evidence=2, tool_errors=("error: x",))
    assert not bad.ok and any("failed tool call" in m for m in bad.missing)


def test_abstain_softens_sufficiency_but_not_integrity():
    # ADR-0008 §Nuances: a Prediction Record's abstain==true makes "anomalous, no confident call,
    # here's the evidence" a valid answer -> the SAME thin-evidence answer blocks without it, passes
    # with it. R4a acceptance #3.
    thin = (_c("metrics:0"),)                             # 1 item < min_evidence 2
    q = "why is r1 slow?"
    blocked = run_gate("r1 looks anomalous [metrics:0]", thin, window=WINDOW,
                       question=q, min_evidence=2)
    assert not blocked.ok and any("thin evidence" in m for m in blocked.missing)
    softened = run_gate("r1 looks anomalous [metrics:0]", thin, window=WINDOW,
                        question=q, min_evidence=2, abstain=True)
    assert softened.ok, softened.missing
    # integrity still bites under abstain: a fabricated citation is never licensed.
    forged = run_gate("r1 is down [metrics:9]", thin, window=WINDOW,
                      question=q, min_evidence=2, abstain=True)
    assert not forged.ok and any("fabricated citation" in m for m in forged.missing)


def _run():
    test_extract_entities_pulls_device_names()
    test_tool_calls_ok_flags_failed_calls()
    test_pre_gate_passes_sufficient_in_window_on_topic()
    test_pre_gate_blocks_thin_evidence()
    test_pre_gate_blocks_out_of_window()
    test_pre_gate_blocks_off_topic_unsupported_entity()
    test_pre_gate_blocks_windowed_evidence_off_question_entity()
    test_pre_gate_blocks_windowed_null_ts()
    test_pre_gate_exempts_kb_and_topology_from_window()
    test_pre_gate_allows_blast_radius_neighbours()
    test_citation_check_passes_fully_cited_answer()
    test_citation_check_rejects_uncited_claim()
    test_citation_check_rejects_fabricated_id()
    test_citation_check_rejects_answer_with_no_citations()
    test_run_gate_combines_tool_calls_pre_gate_and_citation()
    test_abstain_softens_sufficiency_but_not_integrity()
    print("copilot.agent.gate self-check OK")


if __name__ == "__main__":
    _run()
