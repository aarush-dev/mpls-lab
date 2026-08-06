"""Assert-based tests / self-check for the investigation tool registry (I1).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Seam under test: the registry -- TOOLS / TOOL_SPECS / dispatch(name, args, adapter,
window) -> (observation, cites) -- with a canned StubAdapter (spec #3 §Testing).
`cites` is the structured evidence channel the I4a gate consumes; n_rows = len(cites).
Run:  python3 -m copilot.tools.test_tools
"""
import atexit
import shutil
import tempfile

from copilot.adapter import StubAdapter
from copilot.retrieval import Doc, HashEmbedder, LanceRetriever
from copilot.tools import Cite, RETRIEVAL_SPECS, RETRIEVAL_TOOLS, TOOLS, TOOL_SPECS, dispatch
from copilot.window import WindowContext

WINDOW = WindowContext(100, 200)
METRICS = [{"device": "r1", "ts": 100 + i, "cpu": 90 + i} for i in range(3)]
LOGS = [{"device": "r1", "ts": 100 + i, "msg": f"link flap {i}"} for i in range(2)]
FLOWS = [{"device": "r1", "ts": 100 + i, "bytes": 1000 + i} for i in range(4)]

# a line topology r1-r2-r3-r4 so hop distance from r1 is unambiguous.
TOPOLOGY = {"nodes": [{"id": n} for n in ("r1", "r2", "r3", "r4")],
            "links": [{"source": "r1", "target": "r2"},
                      {"source": "r2", "target": "r3"},
                      {"source": "r3", "target": "r4"}]}

# KB fixture: a runbook + two incidents. inc-far's text is the BETTER match for the
# bgp query, so a post-filter-over-top-k would surface it first then drop it; only a
# prefilter keeps the nearer-but-weaker inc-near. Real corpus = S1/S2.
KB = [
    Doc(id="rb-bgp", text="bgp neighbor flap runbook check hold timer session reset",
        source="runbook", node="r1", ts=1000),
    Doc(id="inc-near", text="interface congestion queue drops incident", source="incident",
        node="r2", ts=1001),
    Doc(id="inc-far", text="bgp session reset hold timer expiry incident", source="incident",
        node="r4", ts=1002),
]


def _adapter():
    return StubAdapter(metrics_rows=METRICS, events_rows=LOGS, flows_rows=FLOWS,
                       topology=TOPOLOGY)


def _retriever():
    d = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    r = LanceRetriever(HashEmbedder(), d)
    r.add(KB)
    return r


def test_registry_covers_read_and_retrieval_tools():
    # I1 read tools (adapter methods) + I2b retrieval tools (the KB retriever).
    assert set(TOOLS) == {"query_metrics", "search_logs", "flows"}
    assert set(RETRIEVAL_TOOLS) == {"search_runbooks", "search_incidents"}
    names = {s["name"] for s in TOOL_SPECS}
    assert names == set(TOOLS) | {"walk_topology_graph"}, \
        "read tools + topology walk advertised unconditionally in TOOL_SPECS"
    # #122: retrieval tools split into RETRIEVAL_SPECS -- the loop advertises them only when
    # a retriever is wired (mirrors BASH_SPEC/PRESENT_SPEC), never dead-probed otherwise.
    assert {s["name"] for s in RETRIEVAL_SPECS} == set(RETRIEVAL_TOOLS)
    # read tools expose narrowing args only -- NOT start/end (loop owns the window, ADR-0002)
    specs = {s["name"]: set(s["parameters"]["properties"]) for s in TOOL_SPECS}
    for name in TOOLS:
        # query_metrics adds the #56 ranged trend flag; the rest expose narrowing args only.
        expected = {"device", "pattern", "limit", "offset"}
        assert specs[name] == (expected | {"ranged"} if name == "query_metrics" else expected), name
    # retrieval tools take a query (+ k); incidents adds the hop-filter narrowing.
    rspecs = {s["name"]: set(s["parameters"]["properties"]) for s in RETRIEVAL_SPECS}
    assert rspecs["search_runbooks"] == {"query", "k"}
    assert rspecs["search_incidents"] == {"query", "k", "device", "hops"}
    # I3 walk: a focus device (+ hop radius) -- no window (loop owns it, ADR-0002).
    assert specs["walk_topology_graph"] == {"device", "hops"}


def test_read_tool_unknown_device_is_distinguished_from_no_data():
    # #119: "no rows" used to mean unknown device / no data this window / malformed pattern
    # indistinguishably -- runs wasted 2-6 reads against a misspelled device before finally
    # calling walk_topology_graph (the only tool that gave this signal). Read tools now check
    # device existence against the same topology the walk already uses.
    obs, cites = dispatch("query_metrics", {"device": "ghost"}, _adapter(), WINDOW)
    assert cites == () and obs.startswith("error: unknown device") and "ghost" in obs


def test_read_tool_known_device_no_data_echoes_the_effective_filters():
    # a known device with genuinely no rows this window is a clean negative -- but the filters
    # that produced it are echoed so a malformed pattern (or the wrong device) is visible
    # instead of silently indistinguishable from "nothing happened".
    obs, cites = dispatch("query_metrics", {"device": "r4"}, _adapter(), WINDOW)   # r4: no metrics
    assert cites == () and obs == "no rows for device='r4'"


def test_dispatch_surfaces_structured_cites_for_the_gate():
    # I4a: dispatch returns structured Cites (id + provenance) so the gate never re-parses
    # rendered text. Read cites carry a live ts; KB cites the historical doc ts; topo none.
    _, reads = dispatch("query_metrics", {"device": "r1"}, _adapter(), WINDOW)
    assert reads[0] == Cite(id="metrics:0", source="metrics", device="r1", ts=100)
    _, walk = dispatch("walk_topology_graph", {"device": "r1", "hops": 1}, _adapter(), WINDOW)
    assert walk[0] == Cite(id="topo:r1", source="topo", device="r1", ts=None)
    _, kb = dispatch("search_runbooks", {"query": "bgp neighbor flap"},
                     _adapter(), WINDOW, _retriever())
    assert any(c.id == "rb-bgp" and c.source == "runbook" and c.ts == 1000 for c in kb)
    # error paths surface no cites (the gate reads that as thin/failed)
    assert dispatch("query_metrics", {}, _adapter(), WINDOW)[1] == ()


def test_call_index_namespaces_read_ids_across_tool_calls():
    # #124: id=f"{source}:{i}" restarts at 0 every call -> two query_metrics calls in the same
    # investigation both cite "metrics:0", ambiguous which call's row a citation means. A
    # `call_index` namespaces them; omitted (None), the id is unchanged (back-compat).
    _, first = dispatch("query_metrics", {"device": "r1"}, _adapter(), WINDOW, call_index=0)
    _, second = dispatch("query_metrics", {"device": "r1"}, _adapter(), WINDOW, call_index=1)
    assert first[0].id != second[0].id
    assert first[0].id == "metrics@0:0" and second[0].id == "metrics@1:0"


def test_walk_topology_graph_returns_enriched_subgraph():
    # I3 acceptance: blast-radius from a focus device -> the correct hop-ordered subgraph,
    # each node enriched with live status from /metrics. Line r1-r2-r3-r4; metrics only on r1.
    obs, cites = dispatch("walk_topology_graph", {"device": "r1", "hops": 2}, _adapter(), WINDOW)
    assert len(cites) == 3, "r1 + 2 hops = r1,r2,r3"
    # each node cited by a [topo:<node>] id (the I4a gate checks citations)
    assert "[topo:r1] hop 0: cpu=92" in obs, "focus cited + enriched with its latest metric"
    assert "[topo:r2] hop 1: no metrics" in obs and "[topo:r3] hop 2: no metrics" in obs
    assert "r4" not in obs, "beyond the hop radius"


def test_walk_topology_graph_emits_a_mechanically_checkable_total_header():
    # #117: runs in the audit reported invented totals ("46 devices" for an actual 38) even
    # though the walk enumerates every node one-per-line. Emit a `total=N (hop_i=count)` header
    # so a count claim can be checked mechanically instead of trusted.
    obs, cites = dispatch("walk_topology_graph", {"device": "r1", "hops": 2}, _adapter(), WINDOW)
    header = obs.splitlines()[0]
    assert header == f"total={len(cites)} (hop0=1 hop1=1 hop2=1)"


def test_walk_topology_graph_unknown_device_reports_guidance():
    obs, cites = dispatch("walk_topology_graph", {"device": "ghost"}, _adapter(), WINDOW)
    assert cites == () and obs.startswith("error:") and "topology" in obs


def test_walk_topology_graph_identical_with_kg_off():
    # ADR-0007 acceptance: the KG is additive, never load-bearing. The subgraph + live
    # status must be byte-identical with the curated KG off; KG only APPENDS a hint.
    args = {"device": "r1", "hops": 1}
    off, _ = dispatch("walk_topology_graph", args, _adapter(), WINDOW, kg=None)
    on, _ = dispatch("walk_topology_graph", args, _adapter(), WINDOW,
                     kg={"r2": "curated: flaps under load"})
    for line in off.splitlines():                          # the load-bearing core is unchanged
        assert line in on, f"kg on dropped/altered a core line: {line!r}"
    assert "curated: flaps under load" in on and "curated" not in off, "kg is additive-only"


def test_walk_topology_graph_missing_device_reports_guidance():
    obs, cites = dispatch("walk_topology_graph", {"hops": 2}, _adapter(), WINDOW)
    assert cites == () and obs.startswith("error:") and "device" in obs


def test_walk_topology_graph_bad_hops_reports_guidance_not_crash():
    obs, cites = dispatch("walk_topology_graph", {"device": "r1", "hops": None}, _adapter(), WINDOW)
    assert cites == () and obs.startswith("error:")


def test_walk_topology_graph_hops_is_ceilinged():
    # #125: hops had no ceiling -- hops=1000 on the real 148-node topology dumped every node
    # in one ~44.7KB observation (a 79s response). A long chain here stands in for that: an
    # oversized hops request must still stop well short of the whole graph.
    n = 20
    ids = [f"c{i}" for i in range(n)]
    chain = {"nodes": [{"id": i} for i in ids],
             "links": [{"source": ids[i], "target": ids[i + 1]} for i in range(n - 1)]}
    a = StubAdapter(topology=chain)
    obs, cites = dispatch("walk_topology_graph", {"device": "c0", "hops": 1000}, a, WINDOW)
    assert 0 < len(cites) < n, f"hops=1000 must be clamped, got {len(cites)} of {n} nodes"


def test_search_runbooks_routes_to_retriever_with_full_provenance():
    obs, cites = dispatch("search_runbooks", {"query": "bgp neighbor flap"},
                          _adapter(), WINDOW, _retriever())
    assert len(cites) >= 1
    assert "[rb-bgp]" in obs, "hit cited by its doc id (gate needs the citation)"
    # full provenance triple rides the observation (ADR-0006 / I4a gate): source, node, ts.
    assert "source=runbook" in obs and "node=r1" in obs and "ts=1000" in obs


def test_search_incidents_hop_filter_narrows_to_nearby_devices():
    # acceptance: hop-filter narrows incidents to devices near the focus (r1, hops<=2).
    obs, cites = dispatch("search_incidents",
                          {"query": "interface congestion drops", "device": "r1", "hops": 2},
                          _adapter(), WINDOW, _retriever())
    assert "[inc-near]" in obs, "1-hop incident kept"
    assert "[inc-far]" not in obs, "3-hop incident filtered out"


def test_hop_filter_prefilters_rather_than_trimming_top_k():
    # the far incident is the STRONGER match for this query, so a post-filter over a
    # top-1 global search would surface inc-far then drop it -> "no matches". A prefilter
    # searches WITHIN the near set and returns the weaker-but-nearby inc-near.
    obs, cites = dispatch("search_incidents",
                          {"query": "bgp session reset hold timer expiry", "device": "r1",
                           "hops": 2, "k": 1}, _adapter(), WINDOW, _retriever())
    assert "[inc-near]" in obs and len(cites) == 1, f"prefilter kept the nearby incident, got: {obs}"
    assert "no matches" not in obs


def test_search_incidents_without_device_skips_hop_filter():
    obs, cites = dispatch("search_incidents", {"query": "incident"},
                          _adapter(), WINDOW, _retriever())
    assert "[inc-near]" in obs and "[inc-far]" in obs, "no focus device -> no hop narrowing"


def test_retrieval_tool_missing_query_reports_guidance_not_crash():
    obs, cites = dispatch("search_runbooks", {}, _adapter(), WINDOW, _retriever())
    assert cites == () and obs.startswith("error:") and "query" in obs


def test_retrieval_tool_null_k_reports_guidance_not_crash():
    # a weak model may emit k/hops as null (not just a bad string) -> TypeError, which must
    # still come back AS guidance (ADR-0015), never crash the loop/stream.
    obs, cites = dispatch("search_runbooks", {"query": "x", "k": None},
                          _adapter(), WINDOW, _retriever())
    assert cites == () and obs.startswith("error:")
    obs2, cites2 = dispatch("search_incidents", {"query": "x", "device": "r1", "hops": None},
                            _adapter(), WINDOW, _retriever())
    assert cites2 == () and obs2.startswith("error:")


def test_retrieval_tool_without_retriever_reports_guidance_not_crash():
    obs, cites = dispatch("search_incidents", {"query": "x"}, _adapter(), WINDOW)  # no retriever
    assert cites == () and obs.startswith("error:")


def test_search_logs_routes_to_events():
    obs, cites = dispatch("search_logs", {"device": "r1"}, _adapter(), WINDOW)
    assert len(cites) == 2, "search_logs served the events rows"
    assert "[events:0]" in obs and "link flap 0" in obs


def test_flows_routes_to_flows():
    obs, cites = dispatch("flows", {"device": "r1"}, _adapter(), WINDOW)
    assert len(cites) == 4
    assert "[flows:0]" in obs and "bytes=1000" in obs


def test_query_metrics_still_routes_to_metrics():
    obs, cites = dispatch("query_metrics", {"device": "r1"}, _adapter(), WINDOW)
    assert len(cites) == 3 and "[metrics:0]" in obs


def test_query_metrics_ranged_flag_reaches_adapter():
    # #56: the `ranged` arg must flow through dispatch into Filters.ranged so the adapter can
    # return a trend series. Capture the Filters the adapter is called with.
    seen = []

    class Recorder:
        def metrics(self, filters):
            seen.append(filters)
            return _adapter().metrics(filters)

    dispatch("query_metrics", {"device": "r1", "ranged": True}, Recorder(), WINDOW)
    assert seen and seen[0].ranged is True
    # default (no flag) stays latest-per-series -- ranged False.
    dispatch("query_metrics", {"device": "r1"}, Recorder(), WINDOW)
    assert seen[1].ranged is False


def test_query_metrics_ranged_default_limit_not_clipped_to_ten():
    # #56: opting into a trend but not naming a limit must NOT clip to Filters' default 10 --
    # the samples are the point. 15 in-window samples for one device -> all 15 returned.
    from copilot.adapter import MAX_LIMIT
    rows = [{"device": "r1", "ts": 100 + i, "cpu": 50 + i} for i in range(15)]
    ad = StubAdapter(metrics_rows=rows, topology=TOPOLOGY)
    _, ranged = dispatch("query_metrics", {"device": "r1", "ranged": True}, ad, WINDOW)
    assert len(ranged) == 15 <= MAX_LIMIT           # not truncated to 10
    _, default = dispatch("query_metrics", {"device": "r1"}, ad, WINDOW)
    assert len(default) == 10                        # default read still caps at 10 (unchanged)


def test_query_metrics_advertises_ranged_only_on_metrics():
    # ranged is a metrics-only trend flag; advertising it on search_logs/flows would mislead the
    # model. It appears in query_metrics' schema and nowhere else.
    specs = {s["name"]: s for s in TOOL_SPECS}
    assert "ranged" in specs["query_metrics"]["parameters"]["properties"]
    assert "ranged" not in specs["search_logs"]["parameters"]["properties"]
    assert "ranged" not in specs["flows"]["parameters"]["properties"]


def test_unfiltered_call_rejected_with_guidance():
    # inherits the F2 mandatory-filter contract: no device/pattern -> guidance, not rows.
    obs, cites = dispatch("search_logs", {}, _adapter(), WINDOW)
    assert cites == () and obs.startswith("error:") and "device" in obs


def test_unknown_tool_reports_error_not_raise():
    obs, cites = dispatch("delete_everything", {"device": "r1"}, _adapter(), WINDOW)
    assert cites == () and obs.startswith("error: unknown tool")


def test_non_int_limit_reports_error_not_raise():
    # a weak model may emit limit/offset as junk; that must come back AS guidance
    # (ADR-0015), never crash the loop/stream.
    obs, cites = dispatch("search_logs", {"device": "r1", "limit": "lots"}, _adapter(), WINDOW)
    assert cites == () and obs.startswith("error:")


def test_window_is_supplied_by_caller_not_model():
    # a tool arg trying to widen the window is ignored -- dispatch only reads device/
    # pattern/limit/offset; start/end come from the loop's window (ADR-0002/0015).
    obs, cites = dispatch("flows", {"device": "r1", "start": 0, "end": 9_999_999_999},
                          _adapter(), WINDOW)
    assert len(cites) == 4, "extra start/end args do not widen the read"


def _run():
    test_registry_covers_read_and_retrieval_tools()
    test_dispatch_surfaces_structured_cites_for_the_gate()
    test_walk_topology_graph_returns_enriched_subgraph()
    test_walk_topology_graph_unknown_device_reports_guidance()
    test_walk_topology_graph_identical_with_kg_off()
    test_walk_topology_graph_missing_device_reports_guidance()
    test_walk_topology_graph_bad_hops_reports_guidance_not_crash()
    test_search_runbooks_routes_to_retriever_with_full_provenance()
    test_search_incidents_hop_filter_narrows_to_nearby_devices()
    test_hop_filter_prefilters_rather_than_trimming_top_k()
    test_search_incidents_without_device_skips_hop_filter()
    test_retrieval_tool_missing_query_reports_guidance_not_crash()
    test_retrieval_tool_null_k_reports_guidance_not_crash()
    test_retrieval_tool_without_retriever_reports_guidance_not_crash()
    test_search_logs_routes_to_events()
    test_flows_routes_to_flows()
    test_query_metrics_still_routes_to_metrics()
    test_query_metrics_ranged_flag_reaches_adapter()
    test_query_metrics_ranged_default_limit_not_clipped_to_ten()
    test_query_metrics_advertises_ranged_only_on_metrics()
    test_unfiltered_call_rejected_with_guidance()
    test_unknown_tool_reports_error_not_raise()
    test_non_int_limit_reports_error_not_raise()
    test_window_is_supplied_by_caller_not_model()
    print("copilot.tools self-check OK")


if __name__ == "__main__":
    _run()
