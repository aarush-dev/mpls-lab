"""Assert-based self-check for the HTTP tool adapter (A1, #40).

Exercises the shape-mapping (dataapi bodies -> contract) with an INJECTED fetch, so no live
stack is needed; the live-lab path is verified separately (the ticket's acceptance run).
Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Run:  python3 -m copilot.adapter.test_http
"""
import httpx

from copilot.adapter import (
    AdapterError, Evidence, FilterError, Filters, HttpAdapter, NodeState, ToolAdapter,
)
from copilot.adapter.http import _iso_to_epoch, _selector
from copilot.tools import dispatch
from copilot.window import WindowContext

# Two window-covering epochs (event 2026-08-03T05:20:07Z = 1785734407; flow 10:30:31 = 1785753031).
W_START, W_END = 1785730000, 1785760000


def _fetch(routes):
    """Build a fake transport: {path: body} -> fetch(path, params). A body that is an Exception
    is raised (transport fault)."""
    def fetch(path, params):
        body = routes[path]
        if isinstance(body, Exception):
            raise body
        return body
    return fetch


def _adapter(routes):
    return HttpAdapter("http://x", fetch=_fetch(routes))


def _filters(**kw):
    base = dict(start=W_START, end=W_END, device="pe1", limit=10)
    base.update(kw)
    return Filters(**base)


def test_iso_and_stamp_ts_normalise_to_epoch_int():
    # /events ISO ('...Z') and /flows stamp ('YYYY-MM-DD HH:MM:SS', naive UTC) both -> epoch int.
    assert _iso_to_epoch("2026-08-03T05:20:07Z") == 1785734407
    assert _iso_to_epoch("2026-08-03 10:30:31") == 1785753031
    assert _iso_to_epoch("not-a-date") is None       # -> dropped by serve_rows, no TypeError
    assert _iso_to_epoch(None) is None


def test_events_ts_reaches_evidence_as_int_no_typeerror_at_gate():
    routes = {"/events": {"rows": [
        {"ts": "2026-08-03T05:20:07Z", "device": "pe1", "line": "bgp down"},
    ]}}
    ev = _adapter(routes).events(_filters()).evidence[0]
    assert isinstance(ev, Evidence) and ev.source == "events"
    assert ev.ts == 1785734407 and isinstance(ev.ts, int), "ISO string must be epoch int"
    # the gate does `start <= ts <= end` numerically -- prove the int survives that comparison.
    assert W_START <= ev.ts <= W_END


def test_flows_stamp_reaches_evidence_as_int():
    routes = {"/flows": {"rows": [
        {"ts": "2026-08-03 10:30:31", "device": "pe1", "ip_src": "10.0.0.1", "proto": "tcp"},
    ]}}
    ev = _adapter(routes).flows(_filters()).evidence[0]
    assert ev.ts == 1785753031 and isinstance(ev.ts, int)
    assert "10.0.0.1" in ev.content                  # payload framed as evidence


def test_metrics_selector_and_latest_sample():
    # /metrics takes PromQL: a selector is synthesised, a result vector -> one Evidence per
    # series with device/ts derived from labels + the LATEST in-window sample.
    assert _selector(Filters(start=1, end=2, device="pe1", limit=5)) == '{device="pe1"}'
    sel = _selector(Filters(start=1, end=2, device="pe1", pattern="cpu", limit=5))
    assert 'device="pe1"' in sel and '__name__=~".*cpu.*"' in sel
    routes = {"/metrics": {"result": [
        {"metric": {"__name__": "node_cpu_pct", "device": "pe1"},
         "values": [[W_START + 10, "40"], [W_START + 40, "91"]]},   # latest = 91
    ]}}
    res = _adapter(routes).metrics(_filters())
    ev = res.evidence[0]
    assert ev.device == "pe1" and ev.ts == W_START + 40
    assert "value=91" in ev.content and "metric=node_cpu_pct" in ev.content


def test_validate_runs_before_any_fetch():
    # critical: serve_rows must validate BEFORE the fetch thunk fires, or an over-broad / frozen
    # call does a wire read (past T_snapshot!) before the guard bites. Count fetches to prove it.
    calls = []

    def counting(path, params):
        calls.append(path)
        return {"result": [], "rows": []}

    a = HttpAdapter("http://x", fetch=counting)
    for bad in (Filters(start=W_START, end=W_END, limit=10),                     # over-broad
                Filters(start=W_START, end=W_END, device="pe1", limit=10,        # end > T_snapshot
                        t_snapshot=W_START)):
        try:
            a.metrics(bad)
        except FilterError:
            pass
        else:
            raise AssertionError(f"must reject before fetch: {bad}")
    assert calls == [], f"validate must run before any fetch, got {calls}"


def test_http_get_maps_faults_to_adaptererror():
    # the ONLY code that maps httpx faults -> AdapterError; drive it through a real MockTransport.
    def _t(handler):
        return HttpAdapter("http://x", transport=httpx.MockTransport(handler))

    def _refuse(_req):
        raise httpx.ConnectError("connection refused")

    cases = [
        _t(lambda req: httpx.Response(502, text="bad gateway")),          # 5xx
        _t(_refuse),                                                       # connect refusal
        _t(lambda req: httpx.Response(200, text="<html>proxy error")),    # non-JSON body (ValueError)
    ]
    for a in cases:
        try:
            a._http_get("/metrics", {})
        except AdapterError:
            pass
        else:
            raise AssertionError("httpx fault must map to AdapterError")


def test_flows_pattern_ignores_ts_digits():
    # pattern must search PAYLOAD only, not the normalised epoch ts, or a numeric pattern
    # spuriously matches the ts / byte counts.
    routes = {"/flows": {"rows": [
        {"ts": "2026-08-03 10:30:31", "device": "pe1", "ip_src": "10.0.0.1", "bytes": 56},
    ]}}
    a = _adapter(routes)
    assert a.flows(_filters(pattern="1785753031")).evidence == (), "ts epoch must not match"
    assert a.flows(_filters(pattern="10.0.0.1")).evidence != (), "real payload field matches"


def test_events_pattern_searches_all_payload_fields():
    # same tool arg, same contract as flows: severity/app are real event columns (sources.py).
    routes = {"/events": {"rows": [
        {"ts": "2026-08-03T05:20:07Z", "device": "pe1", "severity": "critical", "line": "x"},
    ]}}
    assert _adapter(routes).events(_filters(pattern="critical")).evidence != ()


def test_events_pattern_and_offset_work_adapterside():
    # /events has NO pattern/offset -> both are adapter-side (fetch-then-filter + serve_rows page).
    rows = [{"ts": "2026-08-03T05:20:07Z", "device": "pe1", "line": f"msg {i} bgp"} for i in range(5)]
    rows += [{"ts": "2026-08-03T05:20:07Z", "device": "pe1", "line": "ospf hello"}]
    a = _adapter({"/events": {"rows": rows}})
    # pattern filters to the 5 'bgp' lines; offset pages within THOSE (not the raw list).
    res = a.events(_filters(pattern="bgp", limit=2))
    assert len(res.evidence) == 2 and res.next_page == "2"
    assert all("bgp" in ev.content for ev in res.evidence)
    res2 = a.events(_filters(pattern="bgp", limit=2, offset=2))
    assert "msg 2 bgp" in res2.evidence[0].content, "offset pages the filtered set"
    # a non-matching pattern yields nothing (silent no-op would have leaked the ospf line).
    assert a.events(_filters(pattern="zzz")).evidence == ()


def test_walk_topology_bfs_enriched_and_unknown_focus():
    topo = {"nodes": [], "links": [{"source": "pe1", "target": "p1"},
                                   {"source": "p1", "target": "p2"}]}
    metrics = {"result": [
        {"metric": {"__name__": "node_cpu_pct", "device": "pe1"}, "values": [[W_START, "5"]]},
        {"metric": {"__name__": "node_cpu_pct", "device": "p2"}, "values": [[W_START, "8"]]},
    ]}
    a = _adapter({"/topology": topo, "/metrics": metrics})
    win = WindowContext(W_START, W_END)
    states = a.walk_topology("pe1", 2, win)
    assert [(s.node, s.hop) for s in states] == [("pe1", 0), ("p1", 1), ("p2", 2)]
    by = {s.node: s.status for s in states}
    assert by["pe1"] == "node_cpu_pct=5" and by["p2"] == "node_cpu_pct=8"
    assert by["p1"] == "no metrics", "node with no live data still in the subgraph"
    assert a.walk_topology("ghost", 2, win) == (), "unknown focus -> no fabricated node"


def test_transport_fault_raises_adaptererror_not_bare_exception():
    boom = AdapterError("dataapi /metrics unavailable: connect refused")
    for call in (
        lambda: _adapter({"/metrics": boom}).metrics(_filters()),
        lambda: _adapter({"/events": boom}).events(_filters()),
        lambda: _adapter({"/topology": boom}).walk_topology("pe1", 1, WindowContext(W_START, W_END)),
    ):
        try:
            call()
        except AdapterError:
            pass
        else:
            raise AssertionError("transport fault must raise AdapterError")


def test_dispatch_converts_fault_to_observation_not_raise_not_false_unknown():
    # registry.dispatch (A1 change): a dataapi fault comes back AS a tool observation with no
    # cites -- NOT an unhandled raise out of investigate(), and NOT a false 'unknown device'.
    boom = AdapterError("dataapi /metrics unavailable: 502")
    win = WindowContext(W_START, W_END)
    obs, cites = dispatch("query_metrics", {"device": "pe1"}, _adapter({"/metrics": boom}), win)
    assert obs.startswith("error:") and cites == () and "502" in obs

    # walk: the fault must beat the empty-walk 'unknown device' path (else a false fact).
    obs, cites = dispatch("walk_topology_graph", {"device": "pe1"},
                          _adapter({"/topology": AdapterError("boom")}), win)
    assert obs.startswith("error:") and "unknown device" not in obs and cites == ()


def test_http_adapter_satisfies_protocol():
    assert isinstance(_adapter({}), ToolAdapter)


def _run():
    test_iso_and_stamp_ts_normalise_to_epoch_int()
    test_events_ts_reaches_evidence_as_int_no_typeerror_at_gate()
    test_flows_stamp_reaches_evidence_as_int()
    test_metrics_selector_and_latest_sample()
    test_validate_runs_before_any_fetch()
    test_http_get_maps_faults_to_adaptererror()
    test_flows_pattern_ignores_ts_digits()
    test_events_pattern_searches_all_payload_fields()
    test_events_pattern_and_offset_work_adapterside()
    test_walk_topology_bfs_enriched_and_unknown_focus()
    test_transport_fault_raises_adaptererror_not_bare_exception()
    test_dispatch_converts_fault_to_observation_not_raise_not_false_unknown()
    test_http_adapter_satisfies_protocol()
    print("copilot.adapter.http self-check OK")


if __name__ == "__main__":
    _run()
