"""Assert-based tests for forensic case creation + the replay adapter (R5b, ADR-0014/0010).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework); mirrors test_trigger.
Seams under test:
  snapshot_window(live_adapter, record, window, dir)  -- freeze the concern to disk
  ReplayAdapter(window_dir)                            -- ToolAdapter over the frozen snapshot
  create_case(record, window, ...) -> case_dir         -- the production handle R5a fires
Run:  python3 -m copilot.forensic.test_case
"""
import json
import os
import re
import tempfile

from copilot.adapter import Filters, FilterError, HttpAdapter, MAX_LIMIT, StubAdapter, ToolAdapter
from copilot.config import Config
from copilot.emulator import emulate_record
from copilot.llm.stub import ScriptedLLM, final, tool_call
from copilot.forensic.case import (
    ReplayAdapter, case_id, create_case, snapshot_window,
)
from copilot.window import WindowContext

# a frozen window over [1000, 1600] (10-min default X starts before; we pin end directly).
WIN = WindowContext(1000, 1600, frozen=True)

# ground-truth label -> a real §3.3 record; device matches gate ENTITY_RE (pe\d+).
LABEL = {"scenario_id": "congestion-pe6", "type": "congestion", "device": "pe6",
         "severity": "high", "target": {"device": "pe6"},
         "t_start": "2026-06-21T14:00:00Z", "t_impact": "2026-06-21T14:01:00Z",
         "t_end": "2026-06-21T14:02:00Z", "lead_time": 60.0, "probe": "latency_ms",
         "baseline_value": 10.0, "impact_value": 40.0, "signature": "sig-pe6"}
REC = emulate_record(LABEL, error_profile="oracle", now="2026-06-21T14:01:00Z")

# two in-window metric rows for pe6 (epoch-int ts) -> >=2 evidence so the gate's floor is met.
STUB_METRICS = [{"device": "pe6", "ts": 1200, "latency_ms": 40},
                {"device": "pe6", "ts": 1300, "queue_drops": 5}]


def _stub_live():
    return StubAdapter(metrics_rows=STUB_METRICS)


def test_snapshot_then_replay_reproduce_evidence_ids():
    with tempfile.TemporaryDirectory() as d:
        wdir = os.path.join(d, "window")
        snapshot_window(_stub_live(), REC, WIN, wdir)
        assert os.path.exists(os.path.join(wdir, "metrics.json")), "snapshot writes per-source rows"
        f = Filters(start=WIN.start, end=WIN.end, device="pe6", t_snapshot=WIN.t_snapshot)
        a, b = ReplayAdapter(wdir), ReplayAdapter(wdir)           # two independent replays, one disk
        ids_a = [e.id for e in a.metrics(f).evidence]
        ids_b = [e.id for e in b.metrics(f).evidence]
        assert ids_a == ids_b == ["metrics:0", "metrics:1"], "same disk -> same evidence ids"
        assert all(isinstance(e.ts, int) for e in a.metrics(f).evidence), "ts epoch-int on replay"


def test_replay_adapter_satisfies_tool_adapter_protocol():
    with tempfile.TemporaryDirectory() as d:
        wdir = os.path.join(d, "window")
        snapshot_window(_stub_live(), REC, WIN, wdir)
        replay = ReplayAdapter(wdir)
        assert isinstance(replay, ToolAdapter), "structurally satisfies the F2 ToolAdapter protocol"
        f = Filters(start=WIN.start, end=WIN.end, device="pe6", t_snapshot=WIN.t_snapshot)
        # every read serves through F2's serve_rows without a live backend
        assert replay.metrics(f).evidence and replay.events(f).evidence == ()


def test_replay_adapter_passes_f2_contract():
    # the ticket widens scope to own the replay adapter and says it "passes F2's contract tests".
    # F2's suite is bound to StubAdapter; re-assert the same contract invariants over ReplayAdapter.
    rows = [{"device": "pe6", "ts": 1000 + i, "latency_ms": i} for i in range(25)]
    with tempfile.TemporaryDirectory() as d:
        wdir = os.path.join(d, "window")
        snapshot_window(StubAdapter(metrics_rows=rows), REC, WindowContext(900, 2000, frozen=True),
                        wdir)
        replay = ReplayAdapter(wdir)
        base = dict(start=1000, end=2000, device="pe6", t_snapshot=2000)
        try:                                                 # over-broad (no device/pattern) rejected
            replay.metrics(Filters(start=1000, end=2000, t_snapshot=2000))
            raise AssertionError("over-broad call must be rejected")
        except FilterError:
            pass
        try:                                                 # limit over cap rejected
            replay.metrics(Filters(**base, limit=MAX_LIMIT + 1))
            raise AssertionError("limit over cap must be rejected")
        except FilterError:
            pass
        res = replay.metrics(Filters(**base, limit=10))      # cap + provenance + paging
        assert len(res.evidence) == 10 and res.next_page == "10"
        ev = res.evidence[0]
        assert ev.source == "metrics" and ev.device == "pe6" and ev.id == "metrics:0"
        assert "<<evidence>>" in ev.content, "content is framed (untrusted, ADR-0016)"
        assert replay.metrics(Filters(**base, limit=10, offset=20)).next_page is None
        # freeze guard (ADR-0002): reading past T_snapshot is rejected on replay too
        try:
            replay.metrics(Filters(start=1000, end=2500, device="pe6", t_snapshot=2000, limit=10))
            raise AssertionError("freeze guard must reject end > t_snapshot")
        except FilterError:
            pass


def test_replay_reproduces_live_run_evidence_ids():
    # acceptance bullet 4: replay reproduces the SAME evidence ids as the live run that created it.
    # Run the identical investigation against the LIVE adapter and against the frozen ReplayAdapter;
    # both ride serve_rows over the same rows -> identical Cite ids.
    from copilot.agent import investigate
    live = _stub_live()
    script = [tool_call("query_metrics", {"device": "pe6"}),
              final("pe6 congestion [metrics:0] [metrics:1]."), final('{"pass": true}')]
    live_out = investigate("investigate pe6", WIN, llm=ScriptedLLM(list(script)), adapter=live,
                           cfg=Config())
    with tempfile.TemporaryDirectory() as d:
        wdir = os.path.join(d, "window")
        snapshot_window(live, REC, WIN, wdir)
        replay_out = investigate("investigate pe6", WIN, llm=ScriptedLLM(list(script)),
                                 adapter=ReplayAdapter(wdir), cfg=Config())
    def _ids(out):   # the bracketed evidence ids the run gathered, in order
        return re.findall(r"\[([a-z]+:\d+)\]",
                          " ".join(e.data["content"] for e in out.events if e.type == "tool_result"))
    live_ids, replay_ids = _ids(live_out), _ids(replay_out)
    assert live_ids == replay_ids == ["metrics:0", "metrics:1"], \
        f"replay must reproduce the live run's evidence ids: {live_ids} vs {replay_ids}"


def test_iso_ts_normalised_to_epoch_int_on_disk():
    # #40's crash risk: a replay that stored raw ISO ts would blow up gate.py's numeric compare.
    # Snapshot from the REAL HttpAdapter (ISO in) -> disk must hold epoch-int ts.
    def fetch(path, params):
        if path == "/events":
            return {"rows": [{"device": "pe6", "ts": "2026-06-21T14:00:20Z", "line": "bgp down"}]}
        return {"rows": [], "result": []}
    with tempfile.TemporaryDirectory() as d:
        wdir = os.path.join(d, "window")
        win = WindowContext(1782050400, 1782050600, frozen=True)   # brackets the ISO ts above
        snapshot_window(HttpAdapter("http://x", fetch=fetch), REC, win, wdir)
        rows = json.load(open(os.path.join(wdir, "events.json")))
        assert rows and all(isinstance(r["ts"], int) for r in rows), "ISO ts -> epoch int on disk"


def test_create_case_writes_dir_prediction_and_report():
    # scripted run: model calls query_metrics, then answers with two citations; judge passes.
    script = [tool_call("query_metrics", {"device": "pe6"}),
              final("pe6 shows congestion: high latency [metrics:0] and queue drops [metrics:1]."),
              final('{"pass": true}')]
    with tempfile.TemporaryDirectory() as d:
        case_dir = create_case(REC, WIN, live_adapter=_stub_live(), llm=ScriptedLLM(script),
                               cfg=Config(), cases_root=d)
        assert case_dir.endswith(case_id(REC))
        for rel in ("window/metrics.json", "prediction.json", "case.md"):
            assert os.path.exists(os.path.join(case_dir, rel)), f"case is missing {rel}"
        assert json.load(open(os.path.join(case_dir, "prediction.json"))) == REC, "record frozen verbatim"
        md = open(os.path.join(case_dir, "case.md")).read()
        assert "pe6" in md and "[metrics:0]" in md, "case.md is the cited human report"


def test_replay_reproduces_live_case_run_evidence_ids():
    # the case run reads only the frozen snapshot; a second run over the same disk cites the
    # SAME ids -> replayable from disk with no live backend (acceptance).
    script = [tool_call("query_metrics", {"device": "pe6"}),
              final("pe6 congestion [metrics:0] [metrics:1]."), final('{"pass": true}')]
    from copilot.agent import investigate
    with tempfile.TemporaryDirectory() as d:
        case_dir = create_case(REC, WIN, live_adapter=_stub_live(), llm=ScriptedLLM(script),
                               cfg=Config(), cases_root=d)
        # re-run the investigation against ONLY the frozen disk snapshot (no live adapter).
        replay = ReplayAdapter(os.path.join(case_dir, "window"))
        out = investigate("investigate pe6", WIN, llm=ScriptedLLM(list(script)),
                          adapter=replay, cfg=Config())
        served = [e.data["content"] for e in out.events if e.type == "tool_result"]
        assert served and "metrics:0" in served[0] and "metrics:1" in served[0], \
            "disk-only replay serves the same evidence ids the live case run cited"


def _run():
    test_snapshot_then_replay_reproduce_evidence_ids()
    test_replay_adapter_satisfies_tool_adapter_protocol()
    test_replay_adapter_passes_f2_contract()
    test_replay_reproduces_live_run_evidence_ids()
    test_iso_ts_normalised_to_epoch_int_on_disk()
    test_create_case_writes_dir_prediction_and_report()
    test_replay_reproduces_live_case_run_evidence_ids()
    print("copilot.forensic.test_case OK")


if __name__ == "__main__":
    _run()
