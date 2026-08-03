"""Assert-based self-check for concurrent-fault fan-out + master synthesis (R6b, ADR-0014/0008).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework); mirrors test_chat/test_case.
Seams under test:
  synthesize_concurrent(...)       -- n_concurrent>1 -> n per-fault chats + a master synthesis chat
  master_synthesis(...)            -- inherits sub-chat cites, attributes each claim, passes the gate
  run_gate(prior_cites=...)        -- prior-chat cites first-class, integrity kept, single-fault untouched
Run:  python3 -m copilot.forensic.test_synthesis
"""
import os
import tempfile

from copilot.agent.gate import run_gate
from copilot.config import Config
from copilot.emulator import emulate_record
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
