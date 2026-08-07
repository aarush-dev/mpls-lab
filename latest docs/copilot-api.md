# Copilot — API service

## Purpose
FastAPI HTTP service that fronts the F3 agent loop (`copilot.agent.investigate`) for the air-gapped NOC copilot. It is the one network-reachable seam between the Grafana UI (or any HTTP client) and the copilot's LLM-driven investigation loop: `POST /chat` takes a question + optional time window/session/case/skills, drives one turn of the agent loop, and streams the resulting ADR-0009 trace events (`user_msg | think | tool_call | tool_result | gate | assistant_msg | artifact`) back as Server-Sent Events. It also exposes read paths for forensic case records (`/cases`, `/cases/{cid}`) and for out-of-band artifact bytes (`/sessions/{sid}/artifacts/{name}`). Sits at the top of the pipeline: UI -> `copilot/api/app.py` -> `copilot/agent` (loop/gate) -> `copilot/adapter` (dataapi HTTP) / `copilot/llm` / `copilot/retrieval` / `copilot/memory` / `copilot/forensic` / `copilot/workspace`. All other subsystems are dependency-injected here, not owned by this file. (`copilot/api/app.py:1-17`)

## Entry points
- **`POST /chat`** — run one investigation turn, stream SSE trace events. (`copilot/api/app.py:245-305`)
  ```bash
  curl -N -X POST http://127.0.0.1:8100/chat \
    -H 'content-type: application/json' \
    -d '{"question": "why is r1 slow?", "start": 100, "end": 200}'
  ```
- **`GET /cases`** — list open forensic cases (id, ts, device, fault_type, severity). (`copilot/api/app.py:223-228`)
  ```bash
  curl http://127.0.0.1:8100/cases
  ```
- **`GET /cases/{cid}`** — one case's `case.md` + `prediction.json` + chat list. 404 on unknown/traversal id. (`copilot/api/app.py:231-242`)
  ```bash
  curl http://127.0.0.1:8100/cases/al-pe6
  ```
- **`GET /sessions/{sid}/artifacts/{name}`** — serve an over-cap artifact's raw bytes as a forced download (never inline-rendered). (`copilot/api/app.py:201-220`)
  ```bash
  curl -O http://127.0.0.1:8100/sessions/art/artifacts/0000-cpu.png
  ```
- **Run the server** (no `__main__` block in `app.py`; run via uvicorn per the module docstring):
  ```bash
  uvicorn copilot.api.app:app --host 127.0.0.1 --port 8100
  ```
  (`copilot/api/app.py:11`; also importable as `uvicorn copilot.api:app`, `copilot/api/__init__.py:6,8`)
- **Tests** (self-check, no `__main__` args):
  ```bash
  python3 -m copilot.api.test_api
  # or
  pytest copilot/api/test_api.py
  ```
  (`copilot/api/test_api.py:7,802-803`)

## Modules
- **`copilot/api/app.py`** — the entire service: FastAPI app, CORS, `ChatRequest` schema, all dependency providers (`get_config`, `get_llm`, `get_adapter`, `get_kg`, `get_skills`, `get_retriever`, `get_cases_root`, `get_sessions`, `get_ledger`), relative-time parsing, and the 4 routes.
  - `app = FastAPI(...)` — app object, mounted with CORS middleware. (`copilot/api/app.py:43,49-54`)
  - `class ChatRequest(BaseModel)` — request schema for `/chat`. (`copilot/api/app.py:57-64`)
  - `get_config() -> Config` — loads `Config` via `copilot.config.load()`, with a `COPILOT_GATE_DISABLE` env escape hatch. (`copilot/api/app.py:67-73`)
  - `get_llm(cfg) -> LLMClient` — builds the real LLM client via `copilot.llm.make_client(cfg)`. (`copilot/api/app.py:76-81`)
  - `get_adapter(cfg) -> ToolAdapter` — builds `HttpAdapter` over `cfg.dataapi_url` (env override). (`copilot/api/app.py:84-89`)
  - `get_kg(cfg) -> dict[str,str] | None` — loads the curated knowledge-graph JSON if enabled + sourced. (`copilot/api/app.py:92-104`)
  - `get_skills(cfg) -> dict[str, Skill] | None` — loads + memoizes diagnostic skills from a dir. (`copilot/api/app.py:107-121`)
  - `get_retriever(cfg) -> Retriever | None` — builds + memoizes a `LanceRetriever` over a KB URI. (`copilot/api/app.py:124-140`)
  - `get_cases_root() -> str` — forensic case-dir root. (`copilot/api/app.py:143-147`)
  - `get_sessions() -> SessionStore` — file-backed session store. (`copilot/api/app.py:150-154`)
  - `get_ledger() -> Ledger` — SQLite-backed append-only Event Ledger. (`copilot/api/app.py:157-161`)
  - `_parse_relative_start(question, now) -> int | None` — regex-parses "last/past N hour(s)/..." phrases into an epoch start. (`copilot/api/app.py:173-178`)
  - `_window(req, cfg) -> WindowContext` — resolves the query time window: explicit `start`/`end` win, else parsed relative text, else live rolling window. (`copilot/api/app.py:181-190`)
  - `_sse(outcome)` — generator turning an `Outcome`'s events into `data: <json>\n\n` SSE lines via `event_wire`. (`copilot/api/app.py:193-198`)
  - `get_artifact(sid, name, sessions)` — route handler, `GET /sessions/{sid}/artifacts/{name}`. (`copilot/api/app.py:201-220`)
  - `get_cases(cases_root)` — route handler, `GET /cases`. (`copilot/api/app.py:223-228`)
  - `get_case(cid, cases_root)` — route handler, `GET /cases/{cid}`. (`copilot/api/app.py:231-242`)
  - `chat(req, cfg, llm, adapter, retriever, kg, skills, sessions, cases_root, ledger)` — route handler, `POST /chat`. (`copilot/api/app.py:245-305`)
- **`copilot/api/__init__.py`** — re-exports `app` so `uvicorn copilot.api:app` works. (`copilot/api/__init__.py:8-10`)
- **`copilot/api/test_api.py`** — behaviour tests over `TestClient(app)`, all dependencies stubbed via `app.dependency_overrides`. Run with `python3 -m copilot.api.test_api` or `pytest copilot/api/test_api.py` (see Entry points).

## Parameters

| name | default | env-var/CLI-flag | units | what it controls | source (file:line) |
|---|---|---|---|---|---|
| `ChatRequest.question` | required | JSON body field `question` | text | the user's question fed to the loop | `copilot/api/app.py:58` |
| `ChatRequest.start` | `None` | JSON body field `start` | epoch seconds | explicit window start; wins over relative-text parsing | `copilot/api/app.py:59` |
| `ChatRequest.end` | `None` | JSON body field `end` | epoch seconds | explicit window end | `copilot/api/app.py:60` |
| `ChatRequest.skills` | `None` | JSON body field `skills` | list[str] | skill names to manually invoke (bodies preloaded into prompt) | `copilot/api/app.py:61` |
| `ChatRequest.session_id` | `None` | JSON body field `session_id` | str | resumes/persists a session; `None` = stateless one-off chat | `copilot/api/app.py:62` |
| `ChatRequest.case_id` | `None` | JSON body field `case_id` | str | routes to a forensic follow-up bound to a frozen case window | `copilot/api/app.py:63` |
| `ChatRequest.workspace` | `False` | JSON body field `workspace` | bool | opts in to bash/present tools (needs `session_id` too) | `copilot/api/app.py:64` |
| CORS allowed origins | `["http://localhost:3000", "http://127.0.0.1:3000"]` | hardcoded (no env) | — | which browser origins may call the API (Grafana UI) | `copilot/api/app.py:51` |
| CORS allowed methods | `["GET", "POST"]` | hardcoded | — | which HTTP methods CORS permits | `copilot/api/app.py:52` |
| CORS allowed headers | `["*"]` | hardcoded | — | which request headers CORS permits | `copilot/api/app.py:53` |
| gate disable escape hatch | off (gate on) | `COPILOT_GATE_DISABLE=1` | bool flag | skips the I4a/I4b answer gate for live requests | `copilot/api/app.py:71-72` |
| dataapi base URL | `cfg.dataapi_url` | `COPILOT_DATAAPI_URL` | URL | base URL `HttpAdapter` reads metrics/logs/flows/topology from | `copilot/api/app.py:89` |
| curated KG source | none (`None`) | `COPILOT_KG_URI` | file path (JSON) | path to the `{node: hint}` curated knowledge-graph map | `copilot/api/app.py:100-104` |
| KG enable flag | `cfg.kg_enabled` | (via `Config`, not a direct env here) | bool | gates whether the KG loads at all | `copilot/api/app.py:98-99` |
| skills directory | none (`None`) | `COPILOT_SKILLS_DIR` | dir path | dir of `skills/*.md` loaded into `{name: Skill}` | `copilot/api/app.py:116-121` |
| KB (retrieval) URI | none (`None`) | `COPILOT_KB_URI` | dir path (LanceDB) | path to the seeded LanceDB used by `search_runbooks`/`search_incidents` | `copilot/api/app.py:135-139` |
| cases root | `"cases"` | `COPILOT_CASES_DIR` | dir path | root dir of `cases/<id>/` forensic case dirs | `copilot/api/app.py:147` |
| sessions root | `"sessions"` | `COPILOT_SESSIONS_DIR` | dir path | root dir for the file-backed `SessionStore` | `copilot/api/app.py:154` |
| ledger path | `"ledger.db"` | `COPILOT_LEDGER_PATH` | file path (SQLite) | path to the append-only Event Ledger DB | `copilot/api/app.py:161` |
| relative-time regex | see pattern | none (code constant) | — | matches `"last/past N hour\|hr\|minute\|min\|day\|week\|month(s)"` in free text | `copilot/api/app.py:167-168` |
| unit-to-seconds map | hour/hr=3600, minute/min=60, day=86400, week=7×86400, month=30×86400 | none (code constant) | seconds per unit | converts a matched relative-time unit to seconds; month is a flat 30d (no calendar lib) | `copilot/api/app.py:169-170` |
| `_SKILLS_CACHE` | `{}` at import | none (process-lifetime in-memory dict) | — | memoizes `load_skills(dir)` per dir so markdown isn't re-read every request | `copilot/api/app.py:107,119-120` |
| `_KB_CACHE` | `{}` at import | none (process-lifetime in-memory dict) | — | memoizes `LanceRetriever` per URI so LanceDB isn't reconnected every request | `copilot/api/app.py:124,138-139` |
| artifact response headers | `X-Content-Type-Options: nosniff`, `Content-Disposition: attachment` | hardcoded | — | forces `GET /sessions/{sid}/artifacts/{name}` bytes to download, never render inline (anti-stored-XSS) | `copilot/api/app.py:218-220` |
| artifact response media type | `application/octet-stream` | hardcoded | — | never trusts the agent-produced file's real MIME (e.g. svg) | `copilot/api/app.py:218` |

Note: `cfg.exec_timeout_s`, `cfg.exec_max_timeout_s`, `cfg.exec_output_cap`, `cfg.gate_max_retries` are consumed here (`copilot/api/app.py:284-285`) but *defined* in `copilot.config` (out of scope for this doc — see that subsystem's doc).

## Data flow
- **`question`/`start`/`end`/`skills`/`session_id`/`case_id`/`workspace`** — from the HTTP JSON body (`ChatRequest`, `copilot/api/app.py:57-64`) → `chat()` handler (`copilot/api/app.py:245-305`).
- **Config** — `copilot.config.load()` reads config source (owned by `copilot/config`, not this doc) → optionally patched by `COPILOT_GATE_DISABLE` env → `Config` object injected into every other dependency provider that needs it (`get_llm`, `get_adapter`, `get_kg`, `get_skills`, `get_retriever`). (`copilot/api/app.py:67-73`)
- **LLM** — `make_client(cfg)` (owned by `copilot.llm`) selects an OpenAI-compatible client per `cfg.llm_profile`; endpoint/model from `COPILOT_LLM_BASE_URL`/model env vars (consumed inside `copilot.llm`, not read directly here), key from `cfg.llm_api_key` → injected as `llm` into `chat()`. (`copilot/api/app.py:76-81`)
- **Tool adapter (dataapi)** — `HttpAdapter(base_url)` where `base_url = os.environ.get("COPILOT_DATAAPI_URL", cfg.dataapi_url)` → live HTTP reads over the dataapi service for `query_metrics`/`search_logs`/`flows`/`walk_topology_graph`/etc during the loop → injected as `adapter`. (`copilot/api/app.py:84-89`)
- **Knowledge graph** — if `cfg.kg_enabled` and `COPILOT_KG_URI` set, `json.load(open(uri))` → `{node: hint}` dict → injected as `kg`, additive/never load-bearing to the walk tool. (`copilot/api/app.py:92-104`)
- **Skills** — if `COPILOT_SKILLS_DIR` set, `load_skills(dir)` (owned by `copilot.skills`) → cached in `_SKILLS_CACHE` → `{name: Skill}` → injected as `skills`; catalog descriptions go into the base system prompt, and names in `req.skills` get their bodies preloaded (verified: `test_manual_skill_invoke_over_http`, `copilot/api/test_api.py:278-294`).
- **Retriever (KB)** — if `COPILOT_KB_URI` set, `LanceRetriever(make_embedder(cfg), uri)` → cached in `_KB_CACHE` → injected as `retriever`, backs `search_runbooks`/`search_incidents` tools.
- **Window** — `_window(req, cfg)`: if `req.start`/`req.end` both unset, `_parse_relative_start(req.question, now)` scans the question text for "last/past N <unit>" and if matched sets `start = now - n*unit_seconds`, `end = now`; otherwise both stay `None`. Result feeds `WindowContext.query(start, end, cfg, now)` (owned by `copilot.window`) — with only one bound set, `query()` falls back to `live()`. (`copilot/api/app.py:181-190`)
- **Case routing** (`req.case_id` set) — `resolve_case_dir(cases_root, req.case_id)` (owned by `copilot.forensic.chat`, sanitises + realpath-confines) → unknown/traversal id raises `ValueError` → HTTP 404; else `follow_up(case_dir, session_id, question, llm, cfg, requested_end, retriever, kg, skills, invoke)` runs a forensic follow-up over the case's FROZEN window and a `ReplayAdapter` (the injected live `adapter` is unused for a case chat) → `FilterError` (asking past the case's `T_snapshot`) → HTTP 400 with the adapter's guidance message. (`copilot/api/app.py:259-271`)
- **Session routing** (no `case_id`) — `sessions.history(sid)` if `sid` set reconstructs prior turns from `events.jsonl` (owned by `copilot.memory.SessionStore`); `for_session(sessions.root, sid)` builds a per-session `Workspace` iff `sid` AND `req.workspace` are both truthy; `Executor(ws, timeout_s=cfg.exec_timeout_s, max_timeout_s=cfg.exec_max_timeout_s, output_cap=cfg.exec_output_cap)` wraps it for the bash tool. (`copilot/api/app.py:275-285`)
- **`request_context` string** — built as `f"This call: session={'resumed' if history else ('new' if sid else 'none')}; workspace={'on' if ws else 'off'}; skills={...}."` and passed into the loop so a refusal is scoped to *this call*, never claims the feature doesn't exist system-wide (#120). (`copilot/api/app.py:288-290`)
- **Loop invocation** — `investigate(question, window, llm, adapter, cfg, retriever, kg, skills, executor, workspace=ws, invoke=req.skills, history, request_context)` (owned by `copilot.agent`) → returns an `Outcome` carrying `.events` (ordered ADR-0009 events) and `.of_type("gate")` accessor. (`copilot/api/app.py:291-294`)
- **Persistence (session)** — if `sid`: `sessions.append(sid, outcome.events)` writes this turn's events to `events.jsonl`; each `gate`-type event is separately routed to `ledger.append(f"{sid}:{ts}:{retry}", event_wire(e))` — the id folds in `session:ts:retry` so a same-timestamp blocked-then-retried pair (2 gates, 1 turn) lands as 2 distinct rows. (`copilot/api/app.py:295-304`)
- **Output** — `StreamingResponse(_sse(outcome), media_type="text/event-stream")`: `_sse` iterates `outcome.events`, yields `f"data: {json.dumps(event_wire(e))}\n\n"` per event — this is the ONE schema shared between the live SSE stream and the `events.jsonl` store (`event_wire`, owned by `copilot.agent`). (`copilot/api/app.py:193-198,271,305`)
- **`GET /cases`** — `list_cases(cases_root)` (owned by `copilot.forensic.chat`) scans `cases_root` → JSON list of `{id, ts, device, fault_type, severity}` summaries, skipping mid-write case dirs (no `case.md` yet). (`copilot/api/app.py:223-228`)
- **`GET /cases/{cid}`** — `resolve_case_dir` then `read_case(case_dir)` (owned by `copilot.forensic.chat`) → `{id, case_md, prediction, chats}`; `ValueError`/`FileNotFoundError` both map to HTTP 404. (`copilot/api/app.py:231-242`)
- **`GET /sessions/{sid}/artifacts/{name}`** — `artifact_path(sessions.root, sid, name)` (owned by `copilot.workspace`, sanitises + realpath-confines both `sid` and `name` under `sessions/<sid>/artifacts/`) → `PathPolicyError` → HTTP 404; else `FileResponse(path, media_type="application/octet-stream", headers={nosniff, attachment})` streams the raw bytes as a forced download. (`copilot/api/app.py:201-220`)

## Calculations
- **Relative window start** `start = now - n * unit_seconds`
  - Inputs: `now = int(time.time())` (`copilot/api/app.py:184`); `n = int(m.group(1) or 1)` — the parsed number, defaulting to 1 when the phrase has no digit (e.g. "last month") (`copilot/api/app.py:177`); `unit_seconds = _UNIT_SECONDS[unit]` where `unit = m.group(2).lower()` from the regex match (`copilot/api/app.py:169-170,177-178`).
  - `_UNIT_SECONDS`: `hour`/`hr` = 3600, `minute`/`min` = 60, `day` = 86400, `week` = 7×86400 = 604800, `month` = 30×86400 = 2592000 (flat 30-day month, no calendar library). (`copilot/api/app.py:169-170`)
  - Formula site: `copilot/api/app.py:178`. Only applied when the request supplies neither `start` nor `end`; if `_parse_relative_start` finds no match, `start` stays `None` and `end` is also left `None` (falls through to `WindowContext.query`'s live-window fallback). (`copilot/api/app.py:186-190`)
- **Regex match** `_RELATIVE_RE`: `\b(?:last|past)\s+(?:(\d+)\s*)?(hour|hr|minute|min|day|week|month)s?\b`, case-insensitive — group 1 is the optional digit count, group 2 is the unit (singular, trailing `s?` absorbs plurals). (`copilot/api/app.py:167-168`)
- **Ledger row id** `f"{sid}:{w['ts']}:{e.data['retry']}"` — inputs: session id `sid`, the wire-event's occurrence timestamp `w['ts']` (stamped by the loop, not the API), and the gate event's `retry` count from `e.data['retry']`. Folding in `retry` is what keeps a blocked-then-retried pair (same `sid`, same `ts` collision risk) as two distinct ledger rows rather than one `INSERT OR IGNORE` collision. (`copilot/api/app.py:302-304`)
- No other derived numeric values in this file — everything else (gate pass/fail, calibrated probability, citation checks, severity buckets) is computed inside `copilot.agent`/`copilot.forensic`/`copilot.emulator`, out of scope for this doc.

## Config & schemas
- **`ChatRequest` (JSON, HTTP request body of `POST /chat`)** — fields and meaning per Parameters table above; produced by the caller (UI or curl), consumed by `chat()`. (`copilot/api/app.py:57-64`)
- **SSE wire event (`event_wire(e)` output, HTTP response body of `POST /chat`)** — one JSON object per `data:` line. Every event carries `type` (one of `user_msg | think | tool_call | tool_result | gate | assistant_msg | artifact` — the canonical set, owned by `copilot.agent`) and an ISO-UTC tz-aware `ts` stamped at occurrence time inside the loop, not at send time here (R2a). Produced by `copilot.agent.event_wire` (out of scope), consumed by the SSE client (curl/UI) and, on session turns, appended verbatim into `events.jsonl` — so stream and store share one schema. (`copilot/api/app.py:193-198`, comment `copilot/api/app.py:1-9`)
- **`events.jsonl` (per-session file, root = sessions root / `COPILOT_SESSIONS_DIR`)** — append-only log of wire events for a session; written by `sessions.append(sid, outcome.events)`, read back by `sessions.history(sid)` to reconstruct prior turns on resume. Schema = the same `event_wire` dict as the SSE stream (verified: `test_streamed_event_round_trips_into_an_event`, `copilot/api/test_api.py:261-275`). Ownership of the file format itself is `copilot.memory.SessionStore` (out of scope); this module only calls `.append`/`.history`. (`copilot/api/app.py:276,295-296`)
- **`ledger.db` (SQLite, path = `COPILOT_LEDGER_PATH`/`ledger.db`)** — append-only Event Ledger; this module writes only `gate`-type wire events, keyed by `f"{sid}:{ts}:{retry}"`. Schema/table structure owned by `copilot.memory.Ledger` (out of scope). (`copilot/api/app.py:161,302-304`)
- **KG JSON (path = `COPILOT_KG_URI`)** — a flat `{node_id: hint_string}` map, loaded whole via `json.load`. Produced by an offline curation process (out of scope), consumed here only to pass through unmodified as the `kg` dependency. (`copilot/api/app.py:100-104`)
- **`GET /cases` response** — JSON list of case summary dicts `{id, ts, device, fault_type, severity}` (shape defined by `copilot.forensic.chat.list_cases`, out of scope; this module only calls it and returns the result as-is). (`copilot/api/app.py:223-228`)
- **`GET /cases/{cid}` response** — JSON dict `{id, case_md, prediction, chats}` (shape defined by `copilot.forensic.chat.read_case`, out of scope; passed through unmodified). (`copilot/api/app.py:231-242`)
- **`GET /sessions/{sid}/artifacts/{name}` response** — raw file bytes, `Content-Type: application/octet-stream`, `X-Content-Type-Options: nosniff`, `Content-Disposition: attachment`. Source file resolved via `copilot.workspace.artifact_path` under `sessions/<sid>/artifacts/<name>` (out of scope for path-policy internals). (`copilot/api/app.py:201-220`)

## Gotchas
- **CORS is hardcoded, not env-driven** — allowed origins/methods/headers are literal lists in `app.py`, unlike every other config knob in this file (which is env-overridable). Adding a new UI origin means editing code, not setting an env var. (`copilot/api/app.py:49-54`)
- **`get_kg`/`get_skills`/`get_retriever` all silently return `None`/no-op when their env var is unset** — a missing `COPILOT_KG_URI`/`COPILOT_SKILLS_DIR`/`COPILOT_KB_URI` is NOT an error; the loop just runs without that capability. Debugging "why isn't the KG/skills/retrieval showing up" starts here, not in a stack trace. (`copilot/api/app.py:98-104,116-121,135-139`)
- **`_SKILLS_CACHE`/`_KB_CACHE` are process-lifetime module-level dicts, keyed by dir/URI string** — changing the underlying files on disk without restarting the process (or changing the env var to a new path) will NOT be picked up; the cache never invalidates. (`copilot/api/app.py:107,124`)
- **`workspace=True` alone does nothing without `session_id`** — bash/present tools require BOTH `sid` and `req.workspace` truthy (`ws = for_session(...) if (sid and req.workspace) else None`); a one-off chat (`session_id=None`) can never get bash/present regardless of the `workspace` flag. (`copilot/api/app.py:283`)
- **`case_id` bypasses the injected live `adapter` entirely** — a forensic follow-up always reads from a frozen `ReplayAdapter` built by `follow_up()` over `cases/<id>/window/`; whatever `adapter` dependency override or `COPILOT_DATAAPI_URL` is set is unused on that code path. Tests deliberately pass an adapter with empty rows to prove this (`adapter=StubAdapter(metrics_rows=[])  # unused; frozen`, `copilot/api/test_api.py:572`). (`copilot/api/app.py:259-271`)
- **Explicit `start`/`end` always win over relative-text parsing, and parsing only fires when BOTH are unset** — `req.start=100` with no `req.end` will NOT trigger `_parse_relative_start` even if the question says "last hour"; `_window` only parses when `start is None and end is None`. (`copilot/api/app.py:186-190`)
- **Artifact bytes are deliberately served as a non-executable download** — `media_type="application/octet-stream"` + `nosniff` + `Content-Disposition: attachment` is intentional hardening: agent-produced files (e.g. `.svg`) direct-navigated would otherwise execute embedded `<script>` in-origin (stored XSS). Any change to relax this must preserve the "renderer fetches the blob, never navigates" contract. (`copilot/api/app.py:208-220`)
- **`get_case`/case dir resolution collapses two distinct failure modes into one 404** — `ValueError` (unknown/traversal id) and `FileNotFoundError` (a case dir mid-write: `prediction.json` exists, `case.md` doesn't yet, per `create_case`'s write order) both surface as HTTP 404, deliberately, so a dashboard poll during case creation doesn't see a 500. (`copilot/api/app.py:236-242`)
- **`_window` relative parsing months are flat 30 days** — "last month" is `now - 2592000`, not a calendar month; no leap/DST/variable-month-length handling. (`copilot/api/app.py:170`)
- **The module docstring's claim that a dead LLM/adapter endpoint "surfaces per-request, not as a startup 503" is a design decision baked into `get_llm`/`get_adapter`** — neither dependency provider does any connectivity check; failures only appear once the loop actually calls out. (`copilot/api/app.py:14-16,76-89`)
