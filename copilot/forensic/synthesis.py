"""Concurrent-fault fan-out + master synthesis (R6b, ADR-0014/0009/0008).

ADR-0014: `n_concurrent > 1` -> **n investigation chats (one per fault) + a master chat that
synthesises them**. R6a hung addressable chats under a case; this fans them out and adds the master.

Count-driven over the frozen case (the emulator collapses concurrency into ONE Prediction Record
carrying `n_concurrent` -- emulate.py:274 keeps active[0], discards the rest -- and the snapshot is
device-scoped, so the forensic side has a COUNT, not n per-fault records). fault-0 is the record's
known fault (create_case's initial run, reused). fault-1..n-1 investigate the OTHER co-active
anomalies over the same frozen window. A distinct emulator emitting n per-device records is a
separate gap, logged as missing ticket #49 -- not this ticket (Consumes stub: None).

Master synthesis merges the sub-chats' FINDINGS, not telemetry, so it gathers no `Cite`s of its
own. It INHERITS the sub-chats' cites, attributed per sub-chat (`fault-i:metrics:0`) so every claim
traces to the sub-chat evidence it came from, and passes them to the gate as `prior_cites` (first-
class, integrity still enforced -- gate.run_gate). The single-fault gate is untouched (create_case
with n_concurrent==1 never calls this).

Self-check:  python3 -m copilot.forensic.test_synthesis
"""
import dataclasses

from copilot.agent import Outcome
from copilot.agent.gate import run_gate
from copilot.agent.loop import Event
from copilot.forensic.case import investigate_record
from copilot.forensic.chat import INITIAL_CHAT, case_chats

MASTER_CHAT = "master"


def _fault_chat_id(i: int) -> str:
    """fault-0 IS the initial run (create_case already persisted it as `initial`); 1..n-1 are the
    co-active investigations spawned here."""
    return INITIAL_CHAT if i == 0 else f"fault-{i}"


def _cofault_question(record: dict, i: int) -> str:
    """The prompt for co-active fault #i (i>=1): investigate a DISTINCT anomaly in the same frozen
    window, independent of the primary fault, and cite its evidence -- names the device so the gate
    anchors on it (gate.ENTITY_RE)."""
    dev = record.get("device") or "the affected device"
    return (f"A second fault is concurrently active on {dev} (co-active #{i}). Independent of the "
            f"primary fault, identify a distinct anomaly in the frozen telemetry window for {dev} "
            f"and cite the supporting evidence.")


def _attributed_cites(chat_id: str, outcome: Outcome):
    """The sub-chat's cites, id-prefixed with its chat_id -> attribution baked into the citation
    (`fault-1:metrics:0`). Source/device/ts are the sub-chat's own, so the gate's in-window/on-topic
    integrity binds unchanged (a synthesis cannot launder out-of-window evidence)."""
    return [dataclasses.replace(c, id=f"{chat_id}:{c.id}") for c in outcome.cites]


def _master_question(record: dict, n: int) -> str:
    dev = record.get("device") or "the affected device"
    return (f"{n} concurrent faults on {dev} were investigated in separate chats. Synthesise a "
            f"single combined root-cause picture, attributing every claim to the sub-chat evidence "
            f"it rests on.")


def _brief(subs) -> str:
    """The synthesis prompt body: each sub-chat's answer + the attributed cite ids available to
    cite. A real LLM reads this; the scripted test LLM ignores it and returns a canned synthesis."""
    lines = []
    for cid, out in subs:
        ids = " ".join(f"[{cid}:{c.id}]" for c in out.cites)
        lines.append(f"## {cid}\n{out.answer or '(no finding)'}\nEvidence: {ids or '(none)'}")
    return "\n\n".join(lines)


def master_synthesis(record: dict, window, subs, *, llm, cfg) -> Outcome:
    """One master chat: synthesise the n sub-chats into a combined answer that cites their evidence,
    gated with the inherited cites as first-class `prior_cites` (gate.run_gate). subs is
    [(chat_id, Outcome)]. Stage-1 deterministic gate only -- the ticket's gate concern is pre_gate +
    citation_check (gate.py); self_judge (stage 2, loop) is not re-run here.
    ponytail: single-shot -- no agentic retry; on gate fail the missing[] IS the answer (mirrors the
    loop). Ceiling: a retry round if a real model routinely under-cites the synthesis."""
    question = _master_question(record, len(subs))
    prior = [ac for cid, out in subs for ac in _attributed_cites(cid, out)]
    prompt = (f"{question}\n\nSub-chat findings:\n\n{_brief(subs)}\n\n"
              "Cite each claim by the bracketed attributed id EXACTLY as shown above.")
    events = [Event("user_msg", {"content": question})]
    reply = llm.chat([{"role": "user", "content": prompt}])
    answer = reply.content or ""
    gate = run_gate(answer, (), window=window, question=question,
                    min_evidence=cfg.gate_min_evidence, prior_cites=prior)
    events.append(Event("gate", {"ok": gate.ok, "missing": list(gate.missing), "retry": 0}))
    if not gate.ok:
        answer = "cannot synthesise yet: " + "; ".join(gate.missing)
    events.append(Event("assistant_msg", {"content": answer}))
    return Outcome(answer=answer, events=tuple(events), cites=tuple(prior))


def synthesize_concurrent(case_dir: str, record: dict, window, replay, *, primary: Outcome,
                          llm, cfg, retriever=None, skills=None, kg=None, invoke=None) -> Outcome:
    """The R6b fan-out (ADR-0014): with `n_concurrent > 1`, run one investigation chat per fault
    (fault-0 = the reused primary run) + a master synthesis chat, all persisted under the case.
    Returns the master Outcome. `n_concurrent == 1` never reaches here (create_case guards)."""
    n = max(1, int(record.get("n_concurrent", 1)))
    chats = case_chats(case_dir)
    subs = [(INITIAL_CHAT, primary)]
    for i in range(1, n):
        out = investigate_record(record, _cofault_question(record, i), window, replay, llm=llm,
                                 cfg=cfg, retriever=retriever, skills=skills, kg=kg, invoke=invoke)
        cid = _fault_chat_id(i)
        chats.append(cid, out.events)
        subs.append((cid, out))
    master = master_synthesis(record, window, subs, llm=llm, cfg=cfg)
    chats.append(MASTER_CHAT, master.events)
    return master
