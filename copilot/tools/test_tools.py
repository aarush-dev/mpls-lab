"""Assert-based tests / self-check for the investigation tool registry (I1).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Seam under test: the registry -- TOOLS / TOOL_SPECS / dispatch(name, args, adapter,
window) -> (observation, n) -- with a canned StubAdapter (spec #3 §Testing).
Run:  python3 -m copilot.tools.test_tools
"""
from copilot.adapter import StubAdapter
from copilot.tools import TOOLS, TOOL_SPECS, dispatch

WINDOW = (100, 200)
METRICS = [{"device": "r1", "ts": 100 + i, "cpu": 90 + i} for i in range(3)]
LOGS = [{"device": "r1", "ts": 100 + i, "msg": f"link flap {i}"} for i in range(2)]
FLOWS = [{"device": "r1", "ts": 100 + i, "bytes": 1000 + i} for i in range(4)]


def _adapter():
    return StubAdapter(metrics_rows=METRICS, events_rows=LOGS, flows_rows=FLOWS)


def test_registry_covers_the_three_i1_tools():
    # search_logs + flows join query_metrics; each maps to an adapter method.
    assert set(TOOLS) == {"query_metrics", "search_logs", "flows"}
    names = {s["name"] for s in TOOL_SPECS}
    assert names == set(TOOLS), "every tool is advertised in TOOL_SPECS"
    # specs expose narrowing args only -- NOT start/end (loop owns the window, ADR-0002)
    for s in TOOL_SPECS:
        props = set(s["parameters"]["properties"])
        assert props == {"device", "pattern", "limit", "offset"}, s["name"]


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
    test_registry_covers_the_three_i1_tools()
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
