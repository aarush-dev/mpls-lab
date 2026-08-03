"""Multi-chat per forensic case + follow-ups bound to the frozen window (R6a, ADR-0014/0002/0009).

A case (R5b) is cases/<id>/{window/, window.json, prediction.json, case.md}. R6a hangs CHATS under
it: cases/<id>/chats/<chat_id>/events.jsonl -- n independent conversations, each resuming its OWN
history (SessionStore, R2a), all reading the SAME frozen window via a ReplayAdapter (disk only, no
live backend). A follow-up cannot read past T_snapshot: the loaded window is frozen, so every tool
call carries t_snapshot and the adapter freeze guard (Filters.validate) rejects end > T_snapshot
with guidance. Concurrent writes to one chat are serialised by the SessionStore single-writer lock
(ADR-0009). The case-creation run is persisted as the first chat (`INITIAL_CHAT`) so a follow-up
resumes it.

ponytail: chats are plain dirs under the case; chat_id namespaces them. n-fault master synthesis
(n chats + a master) is R6b/#25 -- this owns the per-case multi-chat + frozen follow-up seam.

Self-check:  python3 -m copilot.forensic.test_chat
"""
import json
import os
import re

from copilot.adapter import Filters
from copilot.agent import Outcome
from copilot.forensic.case import ReplayAdapter, investigate_record
from copilot.memory import SessionStore
from copilot.window import WindowContext

INITIAL_CHAT = "initial"   # the case-creation investigation, persisted as the first chat


def case_chats(case_dir: str) -> SessionStore:
    """The per-case chat store: cases/<id>/chats/<chat_id>/events.jsonl. Each chat_id is an
    independent conversation (own history) -> n chats coexist addressably under one case."""
    return SessionStore(os.path.join(case_dir, "chats"))


def frozen_window(case_dir: str) -> WindowContext:
    """The case's frozen investigation window, persisted at create time (window.json). Frozen, so
    every follow-up is pinned at T_snapshot and the adapter freeze guard bites on any wider read."""
    with open(os.path.join(case_dir, "window.json")) as fh:
        w = json.load(fh)
    return WindowContext(w["start"], w["end"], frozen=True)


def resolve_case_dir(cases_root: str, case_id: str) -> str:
    """Map an UNTRUSTED case id (e.g. from an API request) to a real case dir. Sanitises the id,
    then confirms the resolved path is a directory that stays STRICTLY INSIDE cases_root -- a
    realpath-containment check, because sanitising alone leaves `.`/`..` (kept by the charset)
    able to escape one level. Raises ValueError otherwise -- the routing layer turns it into a
    4xx, never a traversal read."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", case_id or "")
    if not safe or safe in (".", ".."):
        raise ValueError(f"unknown case: {case_id}")
    root = os.path.realpath(cases_root)
    case_dir = os.path.realpath(os.path.join(root, safe))
    if not (case_dir == os.path.join(root, safe) and case_dir.startswith(root + os.sep)
            and os.path.isdir(case_dir)):
        raise ValueError(f"unknown case: {case_id}")
    return case_dir


def follow_up(case_dir: str, chat_id: str, question: str, *, llm, cfg, requested_end: int | None = None,
              retriever=None, skills=None, kg=None, invoke=None) -> Outcome:
    """Run one follow-up turn on chat `chat_id` under `case_dir`, bound to the frozen window and a
    ReplayAdapter over the frozen snapshot (disk only). Threads the chat's OWN prior turns as
    history, appends this turn back (serialised by the store lock). Returns the Outcome.

    `requested_end` is a window bound the caller (a UI/API) asked to read up to. A case is FROZEN
    at T_snapshot: a follow-up asking for data PAST it is rejected AT THE ADAPTER GUARD with
    guidance (Filters.validate, ADR-0002) -- not silently clamped -- so the human learns why the
    frozen case can't reach live data. Within the freeze the loop reads the frozen window as usual."""
    window = frozen_window(case_dir)
    if requested_end is not None and window.t_snapshot is not None and requested_end > window.t_snapshot:
        Filters(start=window.start, end=requested_end, t_snapshot=window.t_snapshot).validate()  # raises
    replay = ReplayAdapter(os.path.join(case_dir, "window"))
    store = case_chats(case_dir)
    with open(os.path.join(case_dir, "prediction.json")) as fh:
        record = json.load(fh)
    out = investigate_record(record, question, window, replay, llm=llm, cfg=cfg,
                             retriever=retriever, skills=skills, kg=kg, invoke=invoke,
                             history=store.history(chat_id))
    store.append(chat_id, out.events)
    return out
