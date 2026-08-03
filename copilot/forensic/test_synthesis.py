"""Assert-based self-check for concurrent-fault fan-out + master synthesis (R6b, ADR-0014/0008).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework); mirrors test_chat/test_case.
Seams under test:
  synthesize_concurrent(...)       -- n_concurrent>1 -> n per-fault chats + a master synthesis chat
  master_synthesis(...)            -- inherits sub-chat cites, attributes each claim, passes the gate
  run_gate(prior_cites=...)        -- prior-chat cites first-class, integrity kept, single-fault untouched
Run:  python3 -m copilot.forensic.test_synthesis
"""
import json
import os
import tempfile

from copilot.adapter import MAX_LIMIT, serve_rows
from copilot.agent.gate import run_gate
from copilot.config import Config
from copilot.emulator import emulate_record, prediction
from copilot.forensic.case import create_case
from copilot.forensic.chat import INITIAL_CHAT, case_chats
from copilot.forensic.synthesis import MASTER_CHAT
from copilot.llm.stub import ScriptedLLM, final, tool_call
from copilot.tools import Cite
from copilot.window import WindowContext

LABEL = {"scenario_id": "congestion-pe6", "type": "congestion", "device": "pe6",
         "severity": "high", "target": {"device": "pe6"},
         "t_start": "2026-06-21T14:00:00Z", "t_impact": "2026-06-21T14:01:00Z",
         "t_end": "2026-06-21T14:02:00Z", "lead_time": 60.0, "probe": "latency_ms",
         "baseline_value": 10.0, "impact_value": 40.0, "signature": "sig-pe6"}
WIN = WindowContext(1000, 1600, frozen=True)
STUB_METRICS = [{"device": "pe6", "ts": 1200, "latency_ms": 40},
                {"device": "pe6", "ts": 1300, "queue_drops": 5}]


def _rec(n_concurrent):
    return emulate_record(LABEL, error_profile="oracle", now="2026-06-21T14:01:00Z",
                          n_concurrent=n_concurrent)


def _stub_live():
    from copilot.adapter import StubAdapter
    return StubAdapter(metrics_rows=STUB_METRICS)


def _investigate():   # one investigation chat: read metrics, cite two ids, judge passes
    return [tool_call("query_metrics", {"device": "pe6"}),
            final("pe6 congestion [metrics:0] [metrics:1]."), final('{"pass": true}')]


class _DevStub:
    """A StubAdapter that server-side device-scopes the drain, like the real HttpAdapter. Plain
    StubAdapter IGNORES filters.device (serve_rows only ts-filters), which would MASK #49: a
    per-device snapshot would look identical to a single one. This scopes rows by device first, so
    a co-fault's window freezes ONLY that device's evidence -- exactly what the real dataapi does."""

    def __init__(self, metrics_rows):
        self._rows = {"metrics": list(metrics_rows), "events": [], "flows": []}

    def _serve(self, source, filters):
        rows = [r for r in self._rows[source]
                if not filters.device or r.get("device") == filters.device]
        return serve_rows(source, filters, rows, MAX_LIMIT)

    def metrics(self, f): return self._serve("metrics", f)
    def events(self, f): return self._serve("events", f)
    def flows(self, f): return self._serve("flows", f)
    def hops_within(self, focus, n): return set()
    def walk_topology(self, focus, n, w): return ()


# two REAL faults on two devices, co-active in the same window (#49 producer -> consumer).
TWO_DEV_METRICS = [{"device": "pe6", "ts": 1200, "latency_ms": 40},
                   {"device": "pe6", "ts": 1300, "queue_drops": 5},
                   {"device": "pe1", "ts": 1250, "bgp_state": "idle"},
                   {"device": "pe1", "ts": 1350, "bgp_resets": 3}]


def _two_dev_rec():
    other = {**LABEL, "scenario_id": "bgp_flap-pe1", "type": "bgp_flap", "device": "pe1",
             "target": {"device": "pe1"}, "signature": "sig-pe1"}
    return prediction(Config(emulate_pa=True, error_profile="oracle"),
                      [LABEL, other], now="2026-06-21T14:01:00Z")


def _two_dev_script():
    # fault-0 investigates pe6 (primary), fault-1 investigates pe1 over its OWN frozen window, then
    # master synthesises. If the co-fault were mis-wired to pe6 (old symmetric behaviour), the pe1
    # query would hit pe1's absent-from-pe6-window rows -> no cite -> gate fail.
    return [tool_call("query_metrics", {"device": "pe6"}),
            final("pe6 congestion [metrics:0] [metrics:1]."), final('{"pass": true}'),
            tool_call("query_metrics", {"device": "pe1"}),
            final("pe1 bgp_flap [metrics:0] [metrics:1]."), final('{"pass": true}'),
            final("combined: pe6 congestion [initial:metrics:0]; pe1 bgp_flap [fault-1:metrics:0].")]


def test_cofault_investigates_its_own_device_with_its_own_frozen_window():
    # acceptance #49: each sub-chat investigates a DISTINCT fault on its OWN device with its OWN
    # frozen evidence -- not a co-active anomaly on the primary device.
    with tempfile.TemporaryDirectory() as d:
        cd = create_case(_two_dev_rec(), WIN, live_adapter=_DevStub(TWO_DEV_METRICS),
                         llm=ScriptedLLM(_two_dev_script()), cfg=Config(), cases_root=d)
        # pe1's window is frozen separately and holds ONLY pe1 rows (the real per-device freeze).
        w1 = json.load(open(os.path.join(cd, "window-1", "metrics.json")))
        assert w1 and all(r["device"] == "pe1" for r in w1), f"fault-1 froze its own device: {w1}"
        # the fault-1 sub-chat found pe1 evidence in its own window and passed the gate.
        f1 = case_chats(cd).read("fault-1")
        assert next(e for e in f1 if e["type"] == "gate")["ok"], "fault-1 gate must pass on pe1 evidence"
        # master synthesises across both, citing the pe1 sub-chat.
        master = case_chats(cd).read(MASTER_CHAT)
        assert next(e for e in master if e["type"] == "gate")["ok"], "master passes the gate"
        ans = next(e for e in master if e["type"] == "assistant_msg")["content"]
        assert "fault-1:" in ans, f"master attributes a claim to the pe1 sub-chat: {ans}"


def _concurrent_script():
    # create_case runs the primary (fault-0), synthesize runs one co-active sub-chat, then master.
    return [*_investigate(),                       # fault-0 (primary, run by create_case)
            *_investigate(),                       # fault-1 (co-active)
            # master: no tools -- synthesise, citing the ATTRIBUTED ids of the two sub-chats.
            final("pe6 root cause: congestion [initial:metrics:0] with queue drops "
                  "[fault-1:metrics:1].")]


# ---------------------------------------------------------------- fan-out
def test_concurrent_spawns_n_chats_plus_master():
    # acceptance: n_concurrent>1 spawns one chat per fault + a master.
    with tempfile.TemporaryDirectory() as d:
        cd = create_case(_rec(2), WIN, live_adapter=_stub_live(),
                         llm=ScriptedLLM(_concurrent_script()), cfg=Config(), cases_root=d)
        chats = case_chats(cd)
        assert chats.read(INITIAL_CHAT), "fault-0 = the initial primary run"
        assert chats.read("fault-1"), "fault-1 = the co-active investigation"
        assert chats.read(MASTER_CHAT), "the master synthesis chat exists"


def test_reentry_does_not_duplicate_chats():
    # regression (spec review): create_case re-fires on a supported path; the fan-out must not
    # append-duplicate fault-*/master (append is append-only, no dedup). Guarded on the master chat.
    with tempfile.TemporaryDirectory() as d:
        create_case(_rec(2), WIN, live_adapter=_stub_live(),
                    llm=ScriptedLLM(_concurrent_script()), cfg=Config(), cases_root=d)
        cd = create_case(_rec(2), WIN, live_adapter=_stub_live(),   # re-fire, fresh scripted llm
                         llm=ScriptedLLM(_concurrent_script()), cfg=Config(), cases_root=d)
        master = case_chats(cd).read(MASTER_CHAT)
        assert sum(1 for e in master if e["type"] == "assistant_msg") == 1, "master not duplicated on re-entry"


def test_single_fault_has_no_master():
    # regression: n_concurrent==1 stops at the initial chat -- the fan-out never fires.
    with tempfile.TemporaryDirectory() as d:
        cd = create_case(_rec(1), WIN, live_adapter=_stub_live(),
                         llm=ScriptedLLM(_investigate()), cfg=Config(), cases_root=d)
        chats = case_chats(cd)
        assert chats.read(INITIAL_CHAT) and not chats.read(MASTER_CHAT), "no master for one fault"


# ---------------------------------------------------------------- master synthesis
def test_master_passes_gate_and_attributes_each_claim():
    # acceptance: the master synthesis passes the gate AND attributes each claim to its sub-chat.
    with tempfile.TemporaryDirectory() as d:
        cd = create_case(_rec(2), WIN, live_adapter=_stub_live(),
                         llm=ScriptedLLM(_concurrent_script()), cfg=Config(), cases_root=d)
        events = case_chats(cd).read(MASTER_CHAT)
        gate = next(e for e in events if e["type"] == "gate")
        assert gate["ok"], f"master must pass the gate, got missing={gate['missing']}"
        answer = next(e for e in events if e["type"] == "assistant_msg")["content"]
        # every cited id is attributed to a real sub-chat (fault-i:origid), not a bare evidence id.
        import re
        cited = re.findall(r"\[([^\[\]]+)\]", answer)
        assert cited, "the synthesis cites its evidence"
        for c in cited:
            assert c.split(":")[0] in (INITIAL_CHAT, "fault-1"), f"claim {c!r} not traced to a sub-chat"


# ---------------------------------------------------------------- gate: prior cites first-class
def test_prior_cites_are_first_class_but_integrity_holds():
    q = "root cause on pe6"
    prior = [Cite(id="initial:metrics:0", source="metrics", device="pe6", ts=1200),
             Cite(id="fault-1:metrics:1", source="metrics", device="pe6", ts=1300)]
    answer = "pe6 congestion [initial:metrics:0] and drops [fault-1:metrics:1]."
    # a synthesis with ZERO own cites passes on inherited prior cites (the 0-item floor is cleared).
    ok = run_gate(answer, (), window=WIN, question=q, min_evidence=2, prior_cites=prior)
    assert ok.ok, f"prior cites must satisfy the gate, got {ok.missing}"
    # integrity is NOT weakened: an out-of-window prior cite is still rejected.
    bad = [Cite(id="initial:metrics:0", source="metrics", device="pe6", ts=99)] + prior[1:]
    off = run_gate(answer, (), window=WIN, question=q, min_evidence=2, prior_cites=bad)
    assert not off.ok and any("out-of-window" in m for m in off.missing), "prior cites ride in-window check"


def test_single_fault_gate_is_byte_identical():
    # the ticket's core guarantee: default prior_cites=() leaves the ordinary path unchanged.
    q = "root cause on pe6"
    cites = [Cite(id="metrics:0", source="metrics", device="pe6", ts=1200),
             Cite(id="metrics:1", source="metrics", device="pe6", ts=1300)]
    answer = "pe6 congestion [metrics:0] [metrics:1]."
    assert run_gate(answer, cites, window=WIN, question=q, min_evidence=2) == \
           run_gate(answer, cites, window=WIN, question=q, min_evidence=2, prior_cites=())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all synthesis self-checks passed")
