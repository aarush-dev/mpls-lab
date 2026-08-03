"""Assert self-check for the retrieval spine (I2a).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Seam under test: the Retriever -- add(docs) + search(query,k) -> [Hit(doc, score)]
with provenance -- over embedded LanceDB, plus the config-only embedder swap
(ADR-0006). Real embedders (nim/local) need a model/endpoint, so the ranking test
rides HashEmbedder (deterministic, no deps/net); the swap test only checks dispatch.
Run:  python3 -m copilot.retrieval.test_retrieval
"""
import atexit
import os
import shutil
import tempfile
import types

from copilot.config import Config
from copilot.retrieval import (
    Doc, Embedder, HashEmbedder, Hit, LanceRetriever, LocalEmbedder, NimEmbedder,
    Retriever, make_embedder,
)

# fixture corpus (real content = S1/S2 seeding). provenance on every doc.
CORPUS = [
    Doc(id="rb-bgp", text="bgp neighbor flap troubleshooting: check hold timer and session resets",
        source="runbook", node="pe3", ts=1000),
    Doc(id="rb-tunnel", text="sdwan tunnel degrade runbook: latency jitter loss rekey",
        source="runbook", node="ce_branch1", ts=1001),
    Doc(id="inc-cong", text="past incident congestion on interface eth1 queue backlog drops",
        source="incident", node="ce_branch1", ts=1002),
]


def _retriever():
    d = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return LanceRetriever(HashEmbedder(), d)


def test_add_then_search_returns_ranked_hits_with_provenance():
    r = _retriever()
    r.add(CORPUS)
    hits = r.search("bgp neighbor session flap", k=3)
    assert hits and isinstance(hits[0], Hit)
    assert hits[0].doc.id == "rb-bgp", f"most relevant doc ranks first, got {hits[0].doc.id}"
    # provenance rides on the hit (source, node, ts) -- required by the gate
    top = hits[0].doc
    assert top.source == "runbook" and top.node == "pe3" and top.ts == 1000


def test_append_batch_with_null_provenance_round_trips():
    # exercises the open_table append path (not just create_table) AND that a doc with
    # null node/ts survives lancedb's inferred schema.
    r = _retriever()
    r.add(CORPUS)
    r.add([Doc(id="rb-ospf", text="ospf lsa flood adjacency", source="runbook")])
    ids = {h.doc.id for h in r.search("ospf adjacency", k=5)}
    assert "rb-ospf" in ids, "appended doc is searchable"


def test_node_filter_on_all_none_node_corpus_does_not_crash():
    # REGRESSION (found by E1/#42 over the real seeded KB): the S1/S2 seeder sets no node, so a
    # real corpus has node=None on EVERY doc -> lancedb inferred a Null-typed `node` column and
    # search_incidents' `node IN (...)` prefilter crashed ("could not convert to literal of type
    # 'Null'"). The fixture CORPUS sets node= so it never reproduced. Pin the schema -> the filter
    # is valid (and simply matches nothing) on an all-None column.
    r = _retriever()
    r.add([Doc(id="inc-a", text="tunnel degrade latency", source="incident"),
           Doc(id="inc-b", text="bgp flap reset", source="incident")])  # node=None on both
    hits = r.search("tunnel latency", k=5, source="incident", nodes={"pe3", "ce_branch1"})
    assert hits == [], "node filter over an all-None-node corpus matches nothing, does not crash"
    assert r.search("tunnel latency", k=5, source="incident"), "unfiltered search still returns"


def test_search_source_filter_returns_only_that_source():
    # I2b: search_incidents/search_runbooks scope by provenance. A query whose best
    # match is a runbook, filtered to source="incident", must skip the runbook.
    r = _retriever()
    r.add(CORPUS)
    hits = r.search("tunnel latency jitter", k=5, source="incident")
    assert hits, "an incident is returned"
    assert all(h.doc.source == "incident" for h in hits), \
        f"source filter leaked non-incident: {[h.doc.source for h in hits]}"


def test_k_caps_result_count():
    r = _retriever()
    r.add(CORPUS)
    assert len(r.search("tunnel", k=1)) == 1


def test_search_before_add_is_empty_not_crash():
    assert _retriever().search("anything", k=3) == []


def test_store_and_embedder_satisfy_the_protocols():
    # same house-style structural check as adapter/llm siblings: the concrete impls
    # ride the seam, so swapping them is config-only.
    assert isinstance(HashEmbedder(), Embedder)
    assert isinstance(_retriever(), Retriever)


def test_embedder_swap_is_config_only_and_lazy():
    # dispatch on cfg.embed_profile -- swap = one config line.
    assert isinstance(make_embedder(Config(embed_profile="nim")), NimEmbedder)
    local = make_embedder(Config(embed_profile="unsloth-local"))
    assert isinstance(local, LocalEmbedder)
    # construction must NOT load the model (lazy) -- so selection is testable air-gapped.
    assert local._model is None
    # unknown profile -> ValueError (defense-in-depth; Config normally validates the enum).
    try:
        make_embedder(types.SimpleNamespace(embed_profile="gpt4-embed"))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError on unknown embed_profile")


def test_nim_embedder_body_gates_input_type_on_env(monkeypatch=None):
    # NVIDIA-hosted nv-embedqa REJECTS the plain OpenAI body -- it needs input_type (+truncate).
    # Env-gated: set -> the params ride the body; unset -> plain OpenAI shape (local/plain server).
    import copilot.retrieval.embedder as emb

    captured = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"data": [{"embedding": [0.0]}]}

    def _fake_post(url, json=None, headers=None, timeout=None):
        captured.update(json)
        return _Resp()

    import httpx
    orig, os_env = httpx.post, dict(os.environ)
    httpx.post = _fake_post
    try:
        os.environ["COPILOT_EMBED_INPUT_TYPE"] = "passage"
        os.environ["COPILOT_EMBED_TRUNCATE"] = "END"
        emb.NimEmbedder(Config(embed_profile="nim")).encode(["hi"])
        assert captured.get("input_type") == "passage" and captured.get("truncate") == "END"
        captured.clear()
        os.environ.pop("COPILOT_EMBED_INPUT_TYPE")
        os.environ.pop("COPILOT_EMBED_TRUNCATE")
        emb.NimEmbedder(Config(embed_profile="nim")).encode(["hi"])
        assert "input_type" not in captured and "truncate" not in captured, "unset -> plain body"
    finally:
        httpx.post = orig
        os.environ.clear(); os.environ.update(os_env)


def _run():
    test_add_then_search_returns_ranked_hits_with_provenance()
    test_node_filter_on_all_none_node_corpus_does_not_crash()
    test_append_batch_with_null_provenance_round_trips()
    test_search_source_filter_returns_only_that_source()
    test_k_caps_result_count()
    test_search_before_add_is_empty_not_crash()
    test_store_and_embedder_satisfy_the_protocols()
    test_embedder_swap_is_config_only_and_lazy()
    test_nim_embedder_body_gates_input_type_on_env()
    print("copilot.retrieval self-check OK")


if __name__ == "__main__":
    _run()
