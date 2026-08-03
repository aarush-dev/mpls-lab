"""copilot.forensic.case -- Forensic case creation + the replay adapter (R5b, ADR-0014/0010).

R5a's trigger fires a `handle(record, window)` on every alerting Prediction Record; THIS module
is the production `handle`. ADR-0014's pipeline, in order:

    freeze the window -> copy the concerned observability window into cases/<id>/window/
    -> write prediction.json -> generate the initial report (case.md) -> (spawn chats)

`snapshot_window` does the copy: it drains the LIVE adapter over the frozen window (device-scoped
to the fault entity -- the mandatory-filter contract, ADR-0015) and dumps epoch-int-ts rows to
disk, so the case is self-contained. `ReplayAdapter` reads that snapshot back through F2's SAME
`serve_rows` pipeline -- a second `ToolAdapter` (the ticket's in-scope, previously-unowned
dependency) with NO live backend, so the case replays from disk and reproduces the live run's
evidence ids ({source}:{offset} off the same rows). The initial report is a real agent run
(`copilot.agent.investigate`) against the replay adapter, so the report is built from exactly the
frozen evidence, never live data (the ADR-0002 freeze, airtight by construction).

case.md format resolves ADR-0010 §Open ("structured vs prose for answers and case.md"): a small
STRUCTURED header (verdict fields the record already carries) + the agent's CITED PROSE body +
a trace footer. Prose for the body because the loop emits cited prose (SYSTEM_PROMPT) and the
quality gate enforces per-claim citations (gate.CITE_RE); forcing whole-answer JSON would fight
both. ADR-0010 amended in this commit.

#40 reuse: ts reaches disk as epoch int because the snapshot drains the live HttpAdapter, which
already normalised ISO->int (A1) -- so replay never re-emits a raw ISO string at the gate's numeric
ts compare (contract.py:133, `filters.start <= r["ts"] <= filters.end`). Inherited, not re-done.

ponytail: single-chat case here. n_concurrent>1 (n chats + a master, ADR-0014) is R6/#24, which
this ticket unblocks -- staged, not skipped.

Self-check:  python3 -m copilot.forensic.test_case
"""
import dataclasses
import json
import os
import re

from copilot.adapter import Filters, MAX_LIMIT, NodeState, Result, row_text, serve_rows
from copilot.adapter.contract import EVIDENCE_CLOSE, EVIDENCE_OPEN
from copilot.agent import investigate
from copilot.emulator import drift_state, fault_type, is_abstain
from copilot.retrieval.contract import Doc   # contract only: no lancedb import cost, no cycle
from copilot.window import WindowContext

_SOURCES = ("metrics", "events", "flows")
_TOPO_HOPS = (1, 2)   # blast-radius depths captured for the fault focus (ADR-0007 small proximity)


# ---------------------------------------------------------------- ids + io
def case_id(record: dict) -> str:
    """One id per episode (ADR-0014 one-case-per-episode): the alert_id, which is deterministic
    from the scenario_id and idempotent in the ledger -- so an episode maps to ONE case dir.
    Filesystem-sanitised (a hostile scenario_id can't escape cases_root)."""
    alert_id = (record.get("explanation_ref") or {}).get("alert_id") or "unknown"
    return re.sub(r"[^A-Za-z0-9._-]", "_", alert_id)


def _dump(path: str, obj) -> None:
    with open(path, "w") as fh:
        json.dump(obj, fh)


def _load(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as fh:
        return json.load(fh)


def _unframe(content: str) -> str:
    """Strip the untrusted-evidence delimiters (adapter.frame wraps as OPEN\\n{body}\\nCLOSE) so the
    snapshot stores the row's payload once; ReplayAdapter's serve_rows re-frames it on read (id and
    ts -- what replay must reproduce -- are unaffected; only the rendered text round-trips)."""
    body = content
    if body.startswith(EVIDENCE_OPEN + "\n"):
        body = body[len(EVIDENCE_OPEN) + 1:]
    if body.endswith("\n" + EVIDENCE_CLOSE):
        body = body[:-(len(EVIDENCE_CLOSE) + 1)]
    return body


# ---------------------------------------------------------------- snapshot (freeze)
def _drain(read, device: str | None, window: WindowContext) -> list[dict]:
    """Page a windowed read to exhaustion, device-scoped, -> replay rows {device, ts(int), line}.
    ts is epoch int (the live adapter normalised it, #40); `line` is the unframed payload."""
    rows: list[dict] = []
    offset = 0
    while True:
        f = Filters(start=window.start, end=window.end, device=device,
                    t_snapshot=window.t_snapshot, limit=MAX_LIMIT, offset=offset)
        res: Result = read(f)
        for ev in res.evidence:
            rows.append({"device": ev.device, "ts": ev.ts, "line": _unframe(ev.content)})
        if not res.next_page:
            return rows
        offset = int(res.next_page)


def _snapshot_topology(adapter, device: str | None, window: WindowContext) -> dict:
    """Capture the fault focus's blast radius (hops + enriched walk) at the small ADR-0007 depths,
    via the adapter's public topology methods (no endpoint-shape knowledge here, ADR-0006). A
    transport fault -> empty (topology is additive to a case; ponytail ceiling: a walk from a focus
    OTHER than the fault device, or beyond _TOPO_HOPS, is out of the frozen concern and replays as
    ())."""
    if not device:
        return {"hops": {}, "walk": {}}
    hops, walk = {}, {}
    for n in _TOPO_HOPS:
        try:
            hops[f"{device}:{n}"] = sorted(adapter.hops_within(device, n))
            walk[f"{device}:{n}"] = [dataclasses.asdict(ns)
                                     for ns in adapter.walk_topology(device, n, window)]
        except Exception:   # ponytail: topology is additive to a case -- on any read fault (a dead
            break           # backend, AdapterError) drop it and stop; deeper hops share the same
            #                 transport so they'd fail too. Ceiling: retry/partial-capture if a
            #                 flaky topology endpoint ever loses a usable blast radius here.
    return {"hops": hops, "walk": walk}


def snapshot_window(live_adapter, record: dict, window: WindowContext, window_dir: str) -> None:
    """Freeze the concerned observability window to `window_dir` from the LIVE adapter (ADR-0014).
    Device-scoped to the fault entity; each source drained to exhaustion; topology blast-radius
    captured. After this the case reads only disk -- no live backend (ADR-0002 freeze)."""
    os.makedirs(window_dir, exist_ok=True)
    device = record.get("device")
    for source in _SOURCES:
        _dump(os.path.join(window_dir, f"{source}.json"),
              _drain(getattr(live_adapter, source), device, window))
    _dump(os.path.join(window_dir, "topology.json"),
          _snapshot_topology(live_adapter, device, window))


# ---------------------------------------------------------------- replay adapter
class ReplayAdapter:
    """A `ToolAdapter` over a frozen cases/<id>/window/ snapshot -- no live backend (R5b).

    Windowed reads ride F2's SAME `serve_rows` (validate -> ts-filter -> page -> provenance ->
    frame), so it passes F2's contract tests and reproduces the live run's evidence ids exactly
    (ids are {source}:{offset} off the same on-disk rows). Topology replays the captured hops/walk
    for the fault focus. Unlike StubAdapter, reads are re-scoped by device/pattern BEFORE serve_rows
    (mirroring HttpAdapter's server-side scoping, http.py:154/_matches) -- so a replay of a live run
    that queried a NEIGHBOUR device honestly returns () (those rows were never captured) rather than
    the fault device's frozen rows: replay stays faithful to a real HTTP live run, not just the stub."""

    def __init__(self, window_dir: str, max_limit: int = MAX_LIMIT):
        self._rows = {s: _load(os.path.join(window_dir, f"{s}.json"), []) for s in _SOURCES}
        self._topo = _load(os.path.join(window_dir, "topology.json"), {}) or {}
        self._max_limit = max_limit

    def metrics(self, filters: Filters) -> Result:
        return self._serve("metrics", filters)

    def events(self, filters: Filters) -> Result:
        return self._serve("events", filters)

    def flows(self, filters: Filters) -> Result:
        return self._serve("flows", filters)

    def _serve(self, source: str, filters: Filters) -> Result:
        rows = self._rows[source]
        if filters.device:                                   # exact device scope (HttpAdapter query)
            rows = [r for r in rows if r.get("device") == filters.device]
        if filters.pattern:                                  # substring over payload (http._matches)
            p = filters.pattern.lower()
            rows = [r for r in rows
                    if p in row_text({k: v for k, v in r.items() if k != "ts"}).lower()]
        return serve_rows(source, filters, rows, self._max_limit)

    def hops_within(self, focus: str, n: int) -> set[str]:
        return set(self._topo.get("hops", {}).get(f"{focus}:{n}", ()))

    def walk_topology(self, focus: str, n: int, window: WindowContext) -> tuple[NodeState, ...]:
        canned = self._topo.get("walk", {}).get(f"{focus}:{n}")
        if not canned:
            return ()
        return tuple(NodeState(**d) for d in canned)


# ---------------------------------------------------------------- case.md + create
def investigate_record(record: dict, question: str, window: WindowContext, adapter, *, llm, cfg,
                       retriever=None, skills=None, kg=None, invoke=None, history=None):
    """Run `investigate` with the Prediction Record's steer fields (fault_type/abstain/drift_state,
    R4a/T1) extracted once -- shared by the initial case run AND R6a follow-ups so a new steer kwarg
    edits ONE site, not both."""
    return investigate(question, window, llm=llm, adapter=adapter, cfg=cfg,
                       retriever=retriever, skills=skills, kg=kg, invoke=invoke, history=history,
                       fault_type=fault_type(record), abstain=is_abstain(record),
                       drift_state=drift_state(record))


def _case_question(record: dict) -> str:
    """The initial-report prompt (ADR-0014 'generate the initial report'). Auto-derived from the
    record so the agent investigates the predicted fault; names the device so the gate anchors on
    it (gate.ENTITY_RE) and cites its evidence."""
    dev = record.get("device") or "the affected device"
    cause = fault_type(record) or "a network fault"
    return (f"A {cause} is predicted on {dev}. Using the frozen telemetry window, identify the "
            f"likely root cause and blast radius, and cite the supporting evidence.")


def render_case_md(record: dict, window: WindowContext, outcome, cid: str) -> str:
    """The initial report: a structured verdict header + the agent's cited prose + a trace footer
    (ADR-0010 §Open resolved -- header structured, body prose)."""
    dec = record.get("decision") or {}
    family = (((record.get("risk") or {}).get("fault_types") or [{}])[0]).get("family", "?")
    lines = [
        f"# Forensic case {cid}",
        "",
        f"- **device:** {record.get('device')}",
        f"- **predicted cause:** {fault_type(record)} ({family})",
        f"- **alert:** {dec.get('alert')} (calibrated p={dec.get('calibrated_probability')}, "
        f"threshold={dec.get('threshold')})",
        f"- **abstain:** {is_abstain(record)}   **model-health:** {drift_state(record)}",
        f"- **window:** [{window.start}, {window.end}] frozen  "
        f"(window_end_ts={record.get('window_end_ts')})",
        f"- **model_version:** {record.get('model_version')}",
        "",
        "## Report",
        "",
        outcome.answer or "_no report produced_",
        "",
        "## Trace",
        "",
    ]
    for e in outcome.events:
        if e.type == "tool_call":
            lines.append(f"- tool `{e.data.get('name')}`({e.data.get('arguments')})")
        elif e.type == "gate":
            lines.append(f"- gate ok={e.data.get('ok')} missing={e.data.get('missing') or []}")
    return "\n".join(lines) + "\n"


def _verdict_to_kb(record: dict, md: str, cid: str, window: WindowContext, retriever, cfg) -> None:
    """B4 (ADR-0009): embed the finalised case verdict into the KB as an `incident` Doc, so it
    becomes a future search_incidents hit. Gated by cfg.ledger_to_kb -- the flag is the ONLY echo
    guard (reinforcing an earlier wrong call); a gate-failed verdict embeds like any other, per spec
    #33 "Echo risk = why it's a toggle". Deterministic id (`case-<cid>`) so a future upsert-on-id
    store dedups a re-fire; today the caller's first-creation guard is the dedup. node=device feeds
    the hop filter; ts=window.end is the incident's epoch-int time (record.window_end_ts is ISO,
    store ts is int64)."""
    if not cfg.ledger_to_kb or retriever is None:
        return
    if int(record.get("n_concurrent", 1)) > 1:
        return   # ponytail: for a concurrent case the finalised verdict is the master synthesis
        #          (R6b), not this initial md -- embedding that master is staged to R6b/#25.
    retriever.add([Doc(id=f"case-{cid}", text=md, source="incident",
                       node=record.get("device"), ts=window.end)])


def create_case(record: dict, window: WindowContext, *, live_adapter, llm, cfg,
                cases_root: str = "cases", retriever=None, skills=None, kg=None) -> str:
    """The production forensic `handle` (ADR-0014): freeze the window to disk, write the record,
    run the initial investigation against the frozen snapshot, write case.md. Returns the case dir.
    Idempotent per episode via `case_id` -- a re-fire lands the same dir (the trigger dedups first,
    so this is a backstop, not the primary guard)."""
    cid = case_id(record)
    case_dir = os.path.join(cases_root, cid)
    window_dir = os.path.join(case_dir, "window")
    snapshot_window(live_adapter, record, window, window_dir)
    _dump(os.path.join(case_dir, "prediction.json"), record)
    _dump(os.path.join(case_dir, "window.json"),   # R6a: follow-ups reload the frozen window bound
          {"start": window.start, "end": window.end})

    replay = ReplayAdapter(window_dir)             # the report is built from disk only (frozen)
    outcome = investigate_record(record, _case_question(record), window, replay,
                                 llm=llm, cfg=cfg, retriever=retriever, skills=skills, kg=kg)
    md = render_case_md(record, window, outcome, cid)
    with open(os.path.join(case_dir, "case.md"), "w") as fh:
        fh.write(md)
    # R6a: persist the creation run as the first chat so a follow-up resumes it (ADR-0014 "spawn
    # chats"). Function-local import: chat.py imports ReplayAdapter from here, so top-level would cycle.
    # Seed only if empty -- an idempotent re-fire (backstop) must not double the chat's history.
    from copilot.forensic.chat import INITIAL_CHAT, case_chats
    chats = case_chats(case_dir)
    if not chats.read(INITIAL_CHAT):
        chats.append(INITIAL_CHAT, outcome.events)
        # B4: embed the verdict once, on first creation. ponytail: dedup rides this chat-seeding
        # guard; move to store upsert-on-id when LanceRetriever.add supports it (store.py:41).
        _verdict_to_kb(record, md, cid, window, retriever, cfg)
    # R6b: concurrent faults -> n per-fault chats + a master synthesis (ADR-0014). Single-fault
    # cases stop at the initial chat above. Function-local import: synthesis imports from here.
    if int(record.get("n_concurrent", 1)) > 1:
        from copilot.forensic.synthesis import synthesize_concurrent
        synthesize_concurrent(case_dir, record, window, replay, primary=outcome, llm=llm, cfg=cfg,
                              retriever=retriever, skills=skills, kg=kg)
    return case_dir


def make_handler(*, live_adapter, llm, cfg, cases_root: str = "cases",
                 retriever=None, skills=None, kg=None):
    """Bind the case-creation dependencies into the `handle(record, window)` seam R5a's
    `run_forensic`/`poll_once` fires. The trigger owns the loop + dedup; this owns one case."""
    def handle(record: dict, window: WindowContext) -> str:
        return create_case(record, window, live_adapter=live_adapter, llm=llm, cfg=cfg,
                           cases_root=cases_root, retriever=retriever, skills=skills, kg=kg)
    return handle
