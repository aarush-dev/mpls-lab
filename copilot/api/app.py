"""copilot.api.app -- FastAPI chat service, local-only (ADR-0010). Convergence (F4).

Drives the F3 agent loop (copilot.agent.investigate) and STREAMS the canonical
ADR-0009 trace events -- `user_msg | think | tool_call | tool_result | assistant_msg`
(gate/artifact land later) -- as Server-Sent Events, each stamped with an ISO-UTC `ts`.
Stream and store share ONE schema (`event_wire`): every streamed event round-trips into
`events.jsonl` unchanged (ADR-0009/0010; the store is R2). SSE = the native browser
primitive the C1 demo consumes via EventSource.

Run:  uvicorn copilot.api.app:app --host 127.0.0.1 --port 8100

The LLM client + tool adapter are FastAPI dependencies: tests (and later R1) override
them via `app.dependency_overrides`. The defaults 503 until the real backends are wired.
"""
import json
import os
import time
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from copilot.adapter import ToolAdapter
from copilot.agent import Event, Outcome, investigate
from copilot.config import Config, load
from copilot.llm import LLMClient
from copilot.retrieval import LanceRetriever, Retriever, make_embedder
from copilot.skills import Skill, load_skills

app = FastAPI(title="NOC Copilot", version="1.0")


class ChatRequest(BaseModel):
    question: str
    start: int | None = None          # window start, epoch s (loop supplies it to tools)
    end: int | None = None            # window end, epoch s
    skills: list[str] | None = None   # I5: skill names to manually invoke (bodies preloaded)


def get_config() -> Config:
    return load()


def get_llm(cfg: Config = Depends(get_config)) -> LLMClient:
    # ponytail: no runnable client yet (R1 ships the real HTTP one); tests override this.
    raise HTTPException(503, "LLM backend not wired yet (R1)")


def get_adapter(cfg: Config = Depends(get_config)) -> ToolAdapter:
    # ponytail: real adapter needs a live dataapi; tests override this.
    raise HTTPException(503, "tool adapter not wired yet (needs live dataapi)")


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


def _window(req: ChatRequest, cfg: Config) -> tuple[int, int]:
    if req.start is not None and req.end is not None:
        return (req.start, req.end)
    # ponytail: bare live window (now - X min .. now); R3 swaps in WindowContext (ADR-0002).
    end = int(time.time())
    return (end - cfg.window_x_min * 60, end)


def event_wire(e: Event, ts: str) -> dict:
    """Canonical wire/store shape (ADR-0009): type + ts + the event's payload. ONE
    schema for the live stream and the persisted events.jsonl (R2 reuses this). The
    payload owns no `type`/`ts` key, so the round-trip is lossless -- guard it."""
    assert "type" not in e.data and "ts" not in e.data, \
        f"event payload must not shadow type/ts (round-trip would clobber): {e.data!r}"
    return {"type": e.type, "ts": ts, **e.data}


def _sse(outcome: Outcome):
    # ponytail: the loop is synchronous -> we stream its completed event tuple, each
    # stamped as it goes out (send-time ~= occurrence-time when one LLM round-trip
    # blocks). Live flush + true per-step ts need the loop to yield (Lane-Investigation
    # change; F3 deferred ts-stamping to F4 by design) -- not worth it yet.
    for e in outcome.events:
        ts = datetime.now(timezone.utc).isoformat()
        yield f"data: {json.dumps(event_wire(e, ts))}\n\n"


@app.post("/chat")
def chat(req: ChatRequest, cfg: Config = Depends(get_config),
         llm: LLMClient = Depends(get_llm),
         adapter: ToolAdapter = Depends(get_adapter),
         retriever: Retriever | None = Depends(get_retriever),
         kg: dict[str, str] | None = Depends(get_kg),
         skills: dict[str, Skill] | None = Depends(get_skills)) -> StreamingResponse:
    outcome = investigate(req.question, _window(req, cfg),
                          llm=llm, adapter=adapter, cfg=cfg, retriever=retriever, kg=kg,
                          skills=skills, invoke=req.skills)
    return StreamingResponse(_sse(outcome), media_type="text/event-stream")
