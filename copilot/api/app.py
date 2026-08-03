"""copilot.api.app -- FastAPI chat service, local-only (ADR-0010). Convergence (F4).

Drives the F3 agent loop (copilot.agent.investigate) and STREAMS the canonical
ADR-0009 trace events -- `user_msg | think | tool_call | tool_result | gate |
assistant_msg` -- as Server-Sent Events, each carrying its own emit-time ISO-UTC `ts`
(R2a moved the stamp from send-time here to occurrence-time in the loop). Stream and
store share ONE schema (`event_wire`): every streamed event round-trips into
`events.jsonl` unchanged. A `session_id` on the request resumes/persists via the R2a
`SessionStore`. SSE = the native browser primitive the C1 demo consumes via EventSource.

Run:  uvicorn copilot.api.app:app --host 127.0.0.1 --port 8100

The LLM client + tool adapter are FastAPI dependencies: tests override them via
`app.dependency_overrides`. The defaults are now real backends -- the config-selected
OpenAI client (R1) and the HTTP dataapi adapter (A1); a dead endpoint surfaces per-request,
not as a startup 503. The live end-to-end run on a real model + seeded corpora is E1/#42.
"""
import json
import os
import time

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from copilot.adapter import FilterError, HttpAdapter, ToolAdapter
from copilot.agent import Outcome, event_wire, investigate
from copilot.forensic.chat import INITIAL_CHAT, follow_up, resolve_case_dir
from copilot.config import Config, load
from copilot.llm import LLMClient, make_client
from copilot.memory import Ledger, SessionStore
from copilot.retrieval import LanceRetriever, Retriever, make_embedder
from copilot.skills import Skill, load_skills
from copilot.window import WindowContext
from copilot.workspace import Executor, for_session

app = FastAPI(title="NOC Copilot", version="1.0")


class ChatRequest(BaseModel):
    question: str
    start: int | None = None          # window start, epoch s (loop supplies it to tools)
    end: int | None = None            # window end, epoch s
    skills: list[str] | None = None   # I5: skill names to manually invoke (bodies preloaded)
    session_id: str | None = None     # R2a: resume this session; None = a one-off, unpersisted chat
    case_id: str | None = None        # R6a: follow-up on a forensic case; session_id names the chat


def get_config() -> Config:
    return load()


def get_llm(cfg: Config = Depends(get_config)) -> LLMClient:
    # R1 (#16): the real OpenAI-compatible client, selected by cfg.llm_profile (config-only
    # swap, ADR-0004). Endpoint/model come from env (COPILOT_LLM_BASE_URL / *_MODEL_*), key
    # from cfg.llm_api_key. A dead endpoint surfaces per-request as a transport error, not a
    # 503 here -- the live end-to-end run against a real model is E1/#42. Tests override this.
    return make_client(cfg)


def get_adapter(cfg: Config = Depends(get_config)) -> ToolAdapter:
    # A1 (#40): the real HTTP read over a live dataapi (replaces the F2 StubAdapter). Base URL
    # from cfg.dataapi_url, env COPILOT_DATAAPI_URL wins (off-localhost stack) -- mirrors the
    # env-override the other backends use. A transport fault surfaces per-tool as an
    # AdapterError observation (registry), not a 503 here. Tests override with StubAdapter.
    return HttpAdapter(os.environ.get("COPILOT_DATAAPI_URL", cfg.dataapi_url))


def get_kg(cfg: Config = Depends(get_config)) -> dict[str, str] | None:
    # Curated KG (ADR-0007): a {node: hint} map, additive & never load-bearing. Gated by
    # cfg.kg_enabled (default-on, off-able) -- OFF -> None -> the walk is KG-free, so its
    # correctness is identical with the flag off. ON -> load the curated map from
    # COPILOT_KG_URI (a JSON file; env, mirroring COPILOT_KB_URI since config.py is another
    # lane's file); unset -> None (no curated KG seeded yet, S-phase). Tests override this.
    if not cfg.kg_enabled:
        return None
    uri = os.environ.get("COPILOT_KG_URI")
    if not uri:
        return None
    with open(uri) as f:
        return json.load(f)


_SKILLS_CACHE: dict[str, dict[str, Skill]] = {}


def get_skills(cfg: Config = Depends(get_config)) -> dict[str, Skill] | None:
    # I5 diagnostic skills (ADR-0012): load the skills/*.md dir named by COPILOT_SKILLS_DIR
    # (env, mirroring COPILOT_KB_URI/KG since config.py is another lane's file) -> {name: Skill}.
    # Unset -> None -> the loop advertises no load_skill tool + adds no catalog (byte-identical
    # to a skills-free run) until S3 seeds the content dir. Tests override this.
    # ponytail: memoize per dir so we don't re-read the markdown every request.
    d = os.environ.get("COPILOT_SKILLS_DIR")
    if not d:
        return None
    if d not in _SKILLS_CACHE:
        _SKILLS_CACHE[d] = load_skills(d)
    return _SKILLS_CACHE[d]


_KB_CACHE: dict[str, Retriever] = {}


def get_retriever(cfg: Config = Depends(get_config)) -> Retriever | None:
    # The KB is OPTIONAL (unlike the llm/adapter the loop can't run without): if a seeded
    # LanceDB is pointed to by COPILOT_KB_URI, wire the real retriever (embedder profile per
    # cfg, ADR-0004) so search_runbooks/search_incidents work over the HTTP seam; otherwise
    # None -> a read-only investigation still runs, the search_* tools just report "backend
    # not available" until S1/S2 seed a corpus. Env, not config.yaml, mirrors the I2a
    # embedder env vars (config.py is another lane's file). Tests override this directly.
    # ponytail: memoize per uri so we don't reconnect LanceDB every request.
    uri = os.environ.get("COPILOT_KB_URI")
    if not uri:
        return None
    if uri not in _KB_CACHE:
        _KB_CACHE[uri] = LanceRetriever(make_embedder(cfg), uri)
    return _KB_CACHE[uri]


def get_cases_root() -> str:
    # R6a: root of the forensic case dirs (cases/<id>/), where a case_id chat resolves. Env
    # (COPILOT_CASES_DIR) else ./cases -- same env-override pattern; the R5b case writer uses
    # the matching cases_root. Tests override with a tmpdir.
    return os.environ.get("COPILOT_CASES_DIR", "cases")


def get_sessions() -> SessionStore:
    # R2a (ADR-0009): the file-backed session store. Root from env (COPILOT_SESSIONS_DIR),
    # else ./sessions -- mirrors the env-override pattern the other backends use. Tests
    # override with a tmpdir. A None session_id on a request skips it entirely (one-off chat).
    return SessionStore(os.environ.get("COPILOT_SESSIONS_DIR", "sessions"))


def get_ledger() -> Ledger:
    # R2b (ADR-0009): the append-only Event Ledger (SQLite), the system's timeline. Path from
    # env (COPILOT_LEDGER_PATH), else ./ledger.db -- same env-override pattern. Tests override
    # with a tmp file. Gate outcomes route here through the F4 event pipeline (see chat()).
    return Ledger(os.environ.get("COPILOT_LEDGER_PATH", "ledger.db"))


def _window(req: ChatRequest, cfg: Config) -> WindowContext:
    # R3 (ADR-0002): Query when the request names a period, else rolling Live (now-X..now).
    # Forensic (frozen) windows are built by the case layer (R5b), not from a chat request.
    return WindowContext.query(req.start, req.end, cfg, int(time.time()))


def _sse(outcome: Outcome):
    # ponytail: the loop is synchronous -> stream its completed event tuple. Each event now
    # carries its own occurrence-time ts (R2a: stamped by the loop at emit, not here at send),
    # so `event_wire` reads e.ts -- stream and events.jsonl serialize the SAME shape.
    for e in outcome.events:
        yield f"data: {json.dumps(event_wire(e))}\n\n"


@app.post("/chat")
def chat(req: ChatRequest, cfg: Config = Depends(get_config),
         llm: LLMClient = Depends(get_llm),
         adapter: ToolAdapter = Depends(get_adapter),
         retriever: Retriever | None = Depends(get_retriever),
         kg: dict[str, str] | None = Depends(get_kg),
         skills: dict[str, Skill] | None = Depends(get_skills),
         sessions: SessionStore = Depends(get_sessions),
         cases_root: str = Depends(get_cases_root),
         ledger: Ledger = Depends(get_ledger)) -> StreamingResponse:
    # R6a: a case_id routes to a forensic follow-up -- pinned to the case's FROZEN window + a
    # ReplayAdapter over cases/<id>/window/ (disk only; the injected live adapter is unused, a
    # frozen case never reads live, ADR-0002). session_id names the chat under the case (default
    # the initial-report chat); n chats coexist addressably, each resuming only its own history.
    if req.case_id:
        try:
            case_dir = resolve_case_dir(cases_root, req.case_id)   # untrusted id -> real dir or reject
        except ValueError:
            raise HTTPException(status_code=404, detail="unknown case")   # don't echo raw input
        try:
            outcome = follow_up(case_dir, req.session_id or INITIAL_CHAT, req.question,
                               llm=llm, cfg=cfg, requested_end=req.end,
                               retriever=retriever, kg=kg, skills=skills, invoke=req.skills)
        except FilterError as e:
            # a follow-up asking past the case's frozen T_snapshot -> the adapter guard's guidance.
            raise HTTPException(status_code=400, detail=str(e))
        return StreamingResponse(_sse(outcome), media_type="text/event-stream")
    # R2a: a session_id resumes the conversation -- prior turns are reconstructed from the
    # session's events.jsonl and threaded into the loop, and this turn's events are appended
    # back (write-through). No session_id -> a stateless one-off chat, nothing persisted.
    sid = req.session_id
    history = sessions.history(sid) if sid else None
    # B3a/B3b (ADR-0011/0013): the coding-agent tools need a per-session workspace, which lives
    # under the session dir (same root as SessionStore). No session_id -> no persistent workspace
    # -> no bash tool (a one-off chat can't run code) and no present tool. The Executor confines
    # bash (no-net, timeout, cwd); the Workspace owns the scratchpad + append-only artifacts (B3b).
    ws = for_session(sessions.root, sid) if sid else None
    executor = (Executor(ws, timeout_s=cfg.exec_timeout_s, max_timeout_s=cfg.exec_max_timeout_s,
                         output_cap=cfg.exec_output_cap) if ws else None)
    outcome = investigate(req.question, _window(req, cfg),
                          llm=llm, adapter=adapter, cfg=cfg, retriever=retriever, kg=kg,
                          skills=skills, executor=executor, workspace=ws, invoke=req.skills,
                          history=history)
    if sid:
        sessions.append(sid, outcome.events)
        # R2b (ADR-0009): route this turn's gate outcomes (pass and fail) into the Event Ledger
        # -- the system's timeline. A turn emits >=1 gate (one per blocked retry + the terminal
        # one), so the record id folds in `retry` alongside sid+ts: unique per emitted gate,
        # stable across a re-persist (idempotent). Keyed to a session -> a one-off chat records
        # nothing (mirrors R2a). Prediction Records land here from their producer (#20/#23).
        for e in outcome.of_type("gate"):
            w = event_wire(e)
            ledger.append(f"{sid}:{w['ts']}:{e.data['retry']}", w)
    return StreamingResponse(_sse(outcome), media_type="text/event-stream")
