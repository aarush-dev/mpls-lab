"""Assert-based tests / self-check for the investigation tool registry (I1).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Seam under test: the registry -- TOOLS / TOOL_SPECS / dispatch(name, args, adapter,
window) -> (observation, n) -- with a canned StubAdapter (spec #3 §Testing).
Run:  python3 -m copilot.tools.test_tools
"""
import atexit
import shutil
import tempfile

from copilot.adapter import StubAdapter
from copilot.retrieval import Doc, HashEmbedder, LanceRetriever
from copilot.tools import RETRIEVAL_TOOLS, TOOLS, TOOL_SPECS, dispatch

WINDOW = (100, 200)
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
    assert names == set(TOOLS) | set(RETRIEVAL_TOOLS) | {"walk_topology_graph"}, \
        "every tool advertised in TOOL_SPECS"
    # read tools expose narrowing args only -- NOT start/end (loop owns the window, ADR-0002)
    specs = {s["name"]: set(s["parameters"]["properties"]) for s in TOOL_SPECS}
    for name in TOOLS:
        assert specs[name] == {"device", "pattern", "limit", "offset"}, name
    # retrieval tools take a query (+ k); incidents adds the hop-filter narrowing.
    assert specs["search_runbooks"] == {"query", "k"}
    assert specs["search_incidents"] == {"query", "k", "device", "hops"}
    # I3 walk: a focus device (+ hop radius) -- no window (loop owns it, ADR-0002).
    assert specs["walk_topology_graph"] == {"device", "hops"}


def test_walk_topology_graph_returns_enriched_subgraph():
    # I3 acceptance: blast-radius from a focus device -> the correct hop-ordered subgraph,
    # each node enriched with live status from /metrics. Line r1-r2-r3-r4; metrics only on r1.
    obs, n = dispatch("walk_topology_graph", {"device": "r1", "hops": 2}, _adapter(), WINDOW)
    assert n == 3, "r1 + 2 hops = r1,r2,r3"
    # each node cited by a [topo:<node>] id (the I4a gate checks citations)
    assert "[topo:r1] hop 0: cpu=92" in obs, "focus cited + enriched with its latest metric"
    assert "[topo:r2] hop 1: no metrics" in obs and "[topo:r3] hop 2: no metrics" in obs
    assert "r4" not in obs, "beyond the hop radius"


def test_walk_topology_graph_unknown_device_reports_guidance():
    obs, n = dispatch("walk_topology_graph", {"device": "ghost"}, _adapter(), WINDOW)
    assert n == 0 and obs.startswith("error:") and "topology" in obs


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
    obs, n = dispatch("walk_topology_graph", {"hops": 2}, _adapter(), WINDOW)
    assert n == 0 and obs.startswith("error:") and "device" in obs


def test_walk_topology_graph_bad_hops_reports_guidance_not_crash():
    obs, n = dispatch("walk_topology_graph", {"device": "r1", "hops": None}, _adapter(), WINDOW)
    assert n == 0 and obs.startswith("error:")


def test_search_runbooks_routes_to_retriever_with_full_provenance():
    obs, n = dispatch("search_runbooks", {"query": "bgp neighbor flap"},
                      _adapter(), WINDOW, _retriever())
    assert n >= 1
    assert "[rb-bgp]" in obs, "hit cited by its doc id (gate needs the citation)"
    # full provenance triple rides the observation (ADR-0006 / I4a gate): source, node, ts.
    assert "source=runbook" in obs and "node=r1" in obs and "ts=1000" in obs


def test_search_incidents_hop_filter_narrows_to_nearby_devices():
    # acceptance: hop-filter narrows incidents to devices near the focus (r1, hops<=2).
    obs, n = dispatch("search_incidents",
                      {"query": "interface congestion drops", "device": "r1", "hops": 2},
                      _adapter(), WINDOW, _retriever())
    assert "[inc-near]" in obs, "1-hop incident kept"
    assert "[inc-far]" not in obs, "3-hop incident filtered out"


def test_hop_filter_prefilters_rather_than_trimming_top_k():
    # the far incident is the STRONGER match for this query, so a post-filter over a
    # top-1 global search would surface inc-far then drop it -> "no matches". A prefilter
    # searches WITHIN the near set and returns the weaker-but-nearby inc-near.
    obs, n = dispatch("search_incidents",
                      {"query": "bgp session reset hold timer expiry", "device": "r1",
                       "hops": 2, "k": 1}, _adapter(), WINDOW, _retriever())
    assert "[inc-near]" in obs and n == 1, f"prefilter kept the nearby incident, got: {obs}"
    assert "no matches" not in obs


def test_search_incidents_without_device_skips_hop_filter():
    obs, n = dispatch("search_incidents", {"query": "incident"},
                      _adapter(), WINDOW, _retriever())
    assert "[inc-near]" in obs and "[inc-far]" in obs, "no focus device -> no hop narrowing"


def test_retrieval_tool_missing_query_reports_guidance_not_crash():
    obs, n = dispatch("search_runbooks", {}, _adapter(), WINDOW, _retriever())
    assert n == 0 and obs.startswith("error:") and "query" in obs


def test_retrieval_tool_null_k_reports_guidance_not_crash():
    # a weak model may emit k/hops as null (not just a bad string) -> TypeError, which must
    # still come back AS guidance (ADR-0015), never crash the loop/stream.
    obs, n = dispatch("search_runbooks", {"query": "x", "k": None},
                      _adapter(), WINDOW, _retriever())
    assert n == 0 and obs.startswith("error:")
    obs2, n2 = dispatch("search_incidents", {"query": "x", "device": "r1", "hops": None},
                        _adapter(), WINDOW, _retriever())
    assert n2 == 0 and obs2.startswith("error:")


def test_retrieval_tool_without_retriever_reports_guidance_not_crash():
    obs, n = dispatch("search_incidents", {"query": "x"}, _adapter(), WINDOW)  # no retriever
    assert n == 0 and obs.startswith("error:")


def test_search_logs_routes_to_events():
    obs, n = dispatch("search_logs", {"device": "r1"}, _adapter(), WINDOW)
    assert n == 2, "search_logs served the events rows"
    assert "[events:0]" in obs and "link flap 0" in obs


def test_flows_routes_to_flows():
    obs, n = dispatch("flows", {"device": "r1"}, _adapter(), WINDOW)
    assert n == 4
    assert "[flows:0]" in obs and "bytes=1000" in obs


def test_query_metrics_still_routes_to_metrics():
    obs, n = dispatch("query_metrics", {"device": "r1"}, _adapter(), WINDOW)
    assert n == 3 and "[metrics:0]" in obs


def test_unfiltered_call_rejected_with_guidance():
    # inherits the F2 mandatory-filter contract: no device/pattern -> guidance, not rows.
    obs, n = dispatch("search_logs", {}, _adapter(), WINDOW)
    assert n == 0 and obs.startswith("error:") and "device" in obs


def test_unknown_tool_reports_error_not_raise():
    obs, n = dispatch("delete_everything", {"device": "r1"}, _adapter(), WINDOW)
    assert n == 0 and obs.startswith("error: unknown tool")


def test_non_int_limit_reports_error_not_raise():
    # a weak model may emit limit/offset as junk; that must come back AS guidance
    # (ADR-0015), never crash the loop/stream.
    obs, n = dispatch("search_logs", {"device": "r1", "limit": "lots"}, _adapter(), WINDOW)
    assert n == 0 and obs.startswith("error:")


def test_window_is_supplied_by_caller_not_model():
    # a tool arg trying to widen the window is ignored -- dispatch only reads device/
    # pattern/limit/offset; start/end come from the loop's window (ADR-0002/0015).
    obs, n = dispatch("flows", {"device": "r1", "start": 0, "end": 9_999_999_999},
                      _adapter(), WINDOW)
    assert n == 4, "extra start/end args do not widen the read"


def _run():
    test_registry_covers_read_and_retrieval_tools()
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
    test_unfiltered_call_rejected_with_guidance()
    test_unknown_tool_reports_error_not_raise()
    test_non_int_limit_reports_error_not_raise()
    test_window_is_supplied_by_caller_not_model()
    print("copilot.tools self-check OK")


if __name__ == "__main__":
    _run()
