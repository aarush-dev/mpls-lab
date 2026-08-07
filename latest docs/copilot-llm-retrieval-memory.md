# Copilot — LLM, Retrieval, Memory

## Purpose

Three seams the agent loop (`copilot/agent`, not owned here) depends on: (1) `copilot/llm` — the
one OpenAI-compatible boundary to the language model, profile-swapped between an interim
NIM-hosted endpoint and a final air-gapped local endpoint; (2) `copilot/retrieval` — the RAG spine
over an embedded LanceDB KB (runbooks + past incidents), same profile-swap pattern for the
embedder; (3) `copilot/memory` — durable state: per-session conversation log (resume across
restarts) and an append-only Event Ledger (system timeline, queryable by device/time). The agent
loop calls `llm.make_client`/`chat()` each turn, calls `retrieval` search tools (owned by the
tools lane) to pull KB evidence into the prompt, and writes every turn/event through
`memory.SessionStore`/`memory.Ledger`.

## Entry points

No CLI/route lives in these three packages directly (FastAPI routes are in `copilot/api`, not
owned). Each package ships a `python3 -m` self-check / seeder:

```
# LLM seam self-check (scripted, no network)
python3 -m copilot.llm.test_llm

# real HTTP client self-check (in-process fake server); opt-in live smoke needs a running endpoint
python3 -m copilot.llm.test_http
COPILOT_LLM_SMOKE=1 python3 -m copilot.llm.test_http

# retrieval self-check (HashEmbedder, no deps/net)
python3 -m copilot.retrieval.test_retrieval

# seeder self-check
python3 -m copilot.retrieval.test_seed

# seed ragcorpus/*.md into the real KB (needs COPILOT_KB_URI set)
COPILOT_KB_URI=./kb.lance python3 -m copilot.retrieval.seed

# memory self-checks
python3 -m copilot.memory.test_ledger
python3 -m copilot.memory.test_session
```
(`copilot/retrieval/seed.py:9,40-44`, `copilot/llm/http.py:16-17`, `copilot/memory/ledger.py:16`,
`copilot/memory/session.py:15`)

## Modules

**llm/**
- `client.py` — the stable `LLMClient` Protocol + wire shapes. `ToolCall(name, arguments, id)`
  (`client.py:16-20`), `Reply(content, tool_calls)` (`client.py:23-31`), `LLMClient.chat(messages,
  tools=None) -> Reply` (`client.py:39-40`).
- `http.py` — real client. `make_client(cfg)` dispatches on `cfg.llm_profile` (`http.py:42-51`);
  `OpenAIClient.chat()` POSTs `/chat/completions` (`http.py:66-80`); `_as_function(spec)` wraps a
  flat tool spec into chat-completions wire form, idempotent (`http.py:83-92`); `_to_reply(msg)`
  parses the response message into `Reply`, degrading malformed `arguments` JSON to `{}` instead of
  raising (`http.py:95-105,115-122`); `_clean_name(raw)` strips a leaked gpt-oss/harmony channel
  token off a tool-call name (`http.py:108-112`, issue #45).
- `stub.py` — `ScriptedLLM` replays a scripted `Reply` list per `chat()` call, records every call
  (`stub.py:12-26`); helpers `tool_call(name, arguments, id)` and `final(text)` build script entries
  (`stub.py:29-36`).
- `__init__.py` — re-exports `LLMClient, Reply, ToolCall, ScriptedLLM, tool_call, final,
  OpenAIClient, make_client` (`llm/__init__.py:8-13`).

**retrieval/**
- `contract.py` — the retrieval seam. `Doc(id, text, source, node=None, ts=None)`
  (`contract.py:14-22`); `Hit(doc, score)` where score is cosine similarity in `[-1,1]`
  (`contract.py:26-29`); `Embedder.encode(texts, kind="passage")` Protocol (`contract.py:33-37`);
  `Retriever.add(docs)` / `Retriever.search(query, k=5, source=None, nodes=None)` Protocol
  (`contract.py:41-46`).
- `embedder.py` — `make_embedder(cfg)` dispatches on `cfg.embed_profile` (`embedder.py:16-23`);
  `NimEmbedder` — OpenAI-compatible `/embeddings` POST, asymmetric `input_type` handling
  (`embedder.py:26-64`); `LocalEmbedder` — lazy-loaded `sentence_transformers` model
  (`embedder.py:67-81`); `HashEmbedder` — deterministic bag-of-hashed-tokens test double, no
  deps/network (`embedder.py:84-103`).
- `store.py` — `LanceRetriever(embedder, uri, table="kb")` implements `Retriever` over LanceDB.
  `_schema(dim)` pins an explicit Arrow schema so an all-`None` `node` column stays typed
  (`store.py:16-24`); `add(docs)` embeds as `kind="passage"` and appends rows, no upsert
  (`store.py:33-52`); `search(query, k=5, source=None, nodes=None)` embeds the query as
  `kind="query"`, prefilters by `source`/`nodes` before the ANN scan, converts cosine distance to
  similarity (`store.py:54-75`).
- `seed.py` — `seed_from_dir(directory, retriever) -> int` globs `runbook-*.md` /
  `incident-*.md`, skips `incident-template.md`, adds each whole file as one `Doc`
  (`seed.py:23-37`); `__main__` seeds `ragcorpus/` into `COPILOT_KB_URI` (`seed.py:40-44`).
- `__init__.py` — re-exports `Doc, Hit, Embedder, Retriever, LanceRetriever, make_embedder,
  NimEmbedder, LocalEmbedder, HashEmbedder, seed_from_dir` (`retrieval/__init__.py:9-20`).

**memory/**
- `ledger.py` — `Ledger(path)` creates/opens a SQLite file with the `_SCHEMA`
  (`ledger.py:23-40`); `append(rec_id, wire, device=None)` — `INSERT OR IGNORE` on PK `id`, so a
  re-append of the same id is a no-op (`ledger.py:42-48`); `by_device(device)` (`ledger.py:50-52`);
  `by_time(start, end)` — lexical ISO-8601 UTC compare (`ledger.py:54-58`).
- `session.py` — `SessionStore(root)` (`session.py:27-32`); `append(sid, events)` — creates
  `sessions/<id>/meta.json` on first write, appends JSON lines to `events.jsonl` under an exclusive
  `flock` per append call (`session.py:37-57`); `read(sid)` — full parsed event list, `[]` if the
  session dir doesn't exist (`session.py:59-65`); `history(sid)` — filters `read()` down to
  `user_msg`/`assistant_msg` events with non-empty `content`, mapped to `{"role","content"}`
  (`session.py:67-77`).
- `__init__.py` — re-exports `Ledger, SessionStore` (`memory/__init__.py:9-12`).

## Parameters

| name | default | env-var/CLI-flag | units | what it controls | source |
|---|---|---|---|---|---|
| `llm_profile` | `"nim"` | `Config.llm_profile` (config.yaml) | enum `nim\|unsloth-local` | which LLM backend `make_client` builds | `copilot/config.py:53`, `copilot/llm/http.py:42-51` |
| nim base URL | `http://127.0.0.1:8000/v1` | `COPILOT_LLM_BASE_URL` | URL | endpoint `OpenAIClient` posts `/chat/completions` to (nim profile) | `copilot/llm/http.py:35-36` |
| nim model | `gpt-oss-20b` | `COPILOT_LLM_MODEL_NIM` | model id | `"model"` field sent in chat body (nim profile) | `copilot/llm/http.py:35-36` |
| local base URL | `http://127.0.0.1:8001/v1` | `COPILOT_LLM_BASE_URL_LOCAL` | URL | endpoint for `unsloth-local` profile | `copilot/llm/http.py:37-38` |
| local model | `unsloth/gpt-oss-20b` | `COPILOT_LLM_MODEL_LOCAL` | model id | `"model"` field for `unsloth-local` profile | `copilot/llm/http.py:37-38` |
| `llm_api_key` | `""` | `COPILOT_LLM_API_KEY` | secret string | Bearer token; empty -> no `Authorization` header sent | `copilot/config.py:98`, `copilot/llm/http.py:76` |
| reasoning effort | `""` (omitted) | `COPILOT_LLM_REASONING_EFFORT` | enum `low\|medium\|high` | sets `reasoning_effort` in chat body (gpt-oss reasoning tier); unset -> field omitted | `copilot/llm/http.py:51,71-72` |
| HTTP timeout | `120.0` | `_TIMEOUT` constant | seconds | per-`chat()` httpx request timeout | `copilot/llm/http.py:26` |
| `embed_profile` | `"nim"` | `Config.embed_profile` (config.yaml) | enum `nim\|unsloth-local` | which embedder `make_embedder` builds | `copilot/config.py:55`, `copilot/retrieval/embedder.py:16-23` |
| embed base URL | `http://127.0.0.1:8080/v1` | `COPILOT_EMBED_BASE_URL` | URL | endpoint `NimEmbedder` posts `/embeddings` to | `copilot/retrieval/embedder.py:38` |
| embed model (nim) | `bge-large-en-v1.5` | `COPILOT_EMBED_MODEL_NIM` | model id | `"model"` field in embeddings body (nim profile) | `copilot/retrieval/embedder.py:39` |
| embed model (local) | `BAAI/bge-large-en-v1.5` | `COPILOT_EMBED_MODEL_LOCAL` | model id | `sentence_transformers` model name loaded lazily | `copilot/retrieval/embedder.py:73` |
| embed input_type | `""` (plain body) | `COPILOT_EMBED_INPUT_TYPE` | `""\|"auto"\|fixed string` | `auto` -> `input_type` follows `encode()`'s `kind` (asymmetric nv-embedqa); fixed value sent as-is; unset -> field omitted | `copilot/retrieval/embedder.py:46,53-54` |
| embed truncate | `""` (omitted) | `COPILOT_EMBED_TRUNCATE` | string | `truncate` field in embeddings body if set | `copilot/retrieval/embedder.py:47,55-56` |
| `embed_api_key` | `""` | `COPILOT_EMBED_API_KEY` | secret string | Bearer token for `NimEmbedder`; empty -> no header | `copilot/config.py:99`, `copilot/retrieval/embedder.py:51,60` |
| embed HTTP timeout | `30` | constant in `encode()` | seconds | per-request httpx timeout for `NimEmbedder` | `copilot/retrieval/embedder.py:61` |
| `HashEmbedder.dim` | `64` | ctor arg | vector width | dimensionality of the test-double hashed embedding | `copilot/retrieval/embedder.py:91` |
| search `k` | `5` | `Retriever.search(k=...)` / `LanceRetriever.search(k=...)` param | count | top-k hits returned, taken WITHIN the source/nodes prefilter scope | `copilot/retrieval/contract.py:45`, `copilot/retrieval/store.py:54` |
| `LanceRetriever.table` | `"kb"` | ctor arg | table name | LanceDB table the retriever reads/writes | `copilot/retrieval/store.py:28` |
| `COPILOT_KB_URI` | none (required) | env | path/URI | LanceDB dataset location, read by `seed.py` `__main__` | `copilot/retrieval/seed.py:42` |
| `COPILOT_LEDGER_PATH` | `"ledger.db"` | env, read by callers | file path | SQLite file `Ledger` opens (default when caller doesn't override) | e.g. `copilot/forensic/trigger.py:148`, `copilot/api/app.py:161` (not owned; cited for provenance) |
| `COPILOT_SESSIONS_DIR` | `"sessions"` | env, read by callers | dir path | root `SessionStore` is constructed with | e.g. `copilot/api/app.py:154` (not owned; cited for provenance) |

## Data flow

**LLM turn**: agent loop (`copilot/agent`, not owned) builds `messages` + a flat tool-spec list ->
calls `LLMClient.chat(messages, tools)`. `make_client(cfg)` (`copilot/llm/http.py:42-51`) already
picked `OpenAIClient` bound to one profile's base URL/model at construction time (config load, not
per-call). `chat()` wraps each tool spec via `_as_function` (`http.py:83-92`), POSTs to
`{base_url}/chat/completions`, and converts the JSON response's `choices[0].message` into a
`Reply` (`http.py:66-80,95-105`). `Reply.tool_calls` (if native function-calling fired) or
`Reply.content` (raw text) goes back to the loop, whose own parser (F3, not owned) reads
`Reply.content` when there are no native tool calls.

**RAG search**: a KB search tool (owned by the tools lane, not this doc) calls
`Retriever.search(query, k, source, nodes)`. `LanceRetriever.search` (`store.py:54-75`) embeds
`query` via the injected `Embedder.encode([query], kind="query")`, opens the LanceDB table, applies
`source`/`nodes` as a `prefilter=True` WHERE clause (so top-k is computed only within scope), and
returns `Hit(doc, score)` sorted by ANN distance. The KB corpus itself is populated once by
`seed.py`: it globs `ragcorpus/runbook-*.md` and `ragcorpus/incident-*.md` (`seed.py:19-20,25-26`),
reads each whole file as one `Doc(id=stem, text=file_contents, source=...)` (`seed.py:33-35`), and
`LanceRetriever.add(docs)` embeds them via `encode(texts, kind="passage")` and appends rows
(`store.py:33-52`) — append-only, no dedup/upsert, so re-running the seeder duplicates docs
(`store.py:41-43`).

**Memory writes**: the agent loop emits `Event`s; `event_wire(e)` (`copilot/agent/loop.py:90-96`,
not owned but the schema both stores persist) turns each into `{"type", "ts", **e.data}`.
`SessionStore.append(sid, events)` writes one such dict per line to
`sessions/<sid>/events.jsonl` under an exclusive `flock` (`session.py:37-57`).
`Ledger.append(rec_id, wire, device)` writes the same wire dict (plus `wire["ts"]`/`wire["type"]`
lifted to columns) as one SQLite row, idempotent on `id` (`ledger.py:42-48`). Resume reads back
through `SessionStore.history(sid)`, which filters to `user_msg`/`assistant_msg` events and
reshapes them into `{"role","content"}` chat messages fed back into the loop as `history`
(`session.py:67-77`).

## Calculations

- **Cosine similarity from LanceDB distance**: `score = 1.0 - r["_distance"]`
  (`copilot/retrieval/store.py:74`). LanceDB's `.metric("cosine")` search
  (`store.py:59`) returns `_distance` in `[0,2]`; the subtraction maps it to similarity in
  `[-1,1]`, matching the `Hit.score` contract (`contract.py:29`).
- **HashEmbedder vector**: for each text, tokenize on whitespace/lowercase, for each token `tok`
  hash with `md5` and bucket into `v[int(md5(tok),16) % dim] += 1.0`, then L2-normalize:
  `v[i] / sqrt(sum(v[j]^2))` (or divide by `1.0` if the sum is `0`) — `embedder.py:94-103`. Inputs:
  `texts`, `dim` (default 64, `embedder.py:91`).
- **Arrow schema vector width**: `dim = len(rows[0]["vector"])` taken from the first embedded row
  on `add()` (`store.py:52`) — not a fixed constant; whatever the injected `Embedder.encode` returns
  (nv-embedqa = 1024-wide per `contract.py:34` comment, HashEmbedder = 64-wide).
- **`input_type` resolution** (`NimEmbedder.encode`, `embedder.py:53-54`): if
  `COPILOT_EMBED_INPUT_TYPE == "auto"`, `input_type = kind` (the caller's `add`->`"passage"` /
  `search`->`"query"` argument); else if the env var is any other non-empty string, that literal
  string is sent; else the field is omitted from the request body.

## Config & schemas

**LanceDB `kb` table** (`copilot/retrieval/store.py:16-24`), one row per `Doc`:
| field | type | producer/consumer |
|---|---|---|
| `id` | string | set by `seed.py:35` (`Doc.id = filename stem`) / any `Doc.id`; PK-like (no uniqueness enforced by the store) |
| `text` | string | full file contents (seeder) or caller-supplied `Doc.text` |
| `source` | string | `"runbook"` or `"incident"` from filename prefix (`seed.py:34`), or caller value; used as an exact-match prefilter in `search()` |
| `node` | string, nullable | topology node id; `None` for every seeded doc (seeder sets no node) — pinned as `string` not inferred `Null`, else the `node IN (...)` prefilter crashes (`store.py:44-48`) |
| `ts` | int64, nullable | epoch seconds; `None` unless caller sets it |
| `vector` | `list<float32>[dim]` | output of `Embedder.encode(text, kind="passage")` |

**SQLite `ledger` table** (`copilot/memory/ledger.py:23-28`):
| column | type | notes |
|---|---|---|
| `id` | TEXT PRIMARY KEY | `rec_id` arg to `append()`; `INSERT OR IGNORE` makes writes idempotent |
| `ts` | TEXT NOT NULL | from `wire["ts"]`; ISO-8601 UTC, indexed (`ix_ledger_ts`), compared lexically in `by_time` |
| `device` | TEXT, nullable | `append()`'s `device` arg, indexed (`ix_ledger_device`) |
| `type` | TEXT NOT NULL | from `wire["type"]` |
| `wire` | TEXT NOT NULL | full event dict, `json.dumps(wire)` — the only place the complete payload lives; `type`/`device`/`ts` columns are query-only projections of it |

**`sessions/<sid>/` on disk** (`copilot/memory/session.py`):
- `meta.json` — `{"id": sid}`, written once on first `append()` if absent (`session.py:44-48`); no
  other fields written by this module (comment notes other lanes may hang case/ledger metadata
  here, but that's not implemented in this file).
- `events.jsonl` — one `event_wire` JSON object per line, `{"type", "ts", **payload}`
  (`copilot/agent/loop.py:90-96`), appended under `flock`; never rewritten.

**`Config` fields this subsystem reads** (`copilot/config.py`, dataclass defaults are the source
of truth; `config.yaml` overlays, secrets from env only): `llm_profile`, `embed_profile` validated
against `{"nim","unsloth-local"}` in `__post_init__` (`config.py:102-105`); `llm_api_key`,
`embed_api_key` marked secret (`config.py:42-47,98-99`, never accepted from `config.yaml` —
`load()` rejects unknown/forbidden YAML keys, `config.py:142-144`).

## Gotchas

- `LanceRetriever.add()` is append-only, no upsert-on-id — re-running `seed.py` against an
  already-seeded KB duplicates every doc (`copilot/retrieval/store.py:41-43`).
- The Arrow schema for `node`/`ts` is explicitly pinned (`string`/`int64`) because a first batch
  where every `node` is `None` (true for every seeded doc — the seeder never sets `node`) gets
  inferred as Arrow `Null` type by default, which then crashes the `node IN (...)` prefilter in
  `search()` (`store.py:19-24,44-48`).
- Vector width (`dim`) is derived from the first embedded row, not fixed — mixing `add()` calls
  across two embedder profiles with different dims onto the same table will fail at the Arrow
  layer (nv-embedqa 1024 vs `HashEmbedder` 64, per `contract.py:34` and `embedder.py:91`).
- `NimEmbedder`'s `input_type`/`truncate` fields only exist because NVIDIA-hosted `nv-embedqa-*`
  rejects the plain OpenAI `/embeddings` body; a plain OpenAI-compatible or local server needs the
  env vars left unset or the extra fields break the request (`embedder.py:40-47`).
- `_clean_name()` strips a gpt-oss/harmony channel-token leak (`name<|channel|>commentary`) off
  tool-call names — model-specific workaround, silently a no-op for any backend that never emits
  that markup (`copilot/llm/http.py:108-112`, issue #45).
- A malformed `arguments` JSON blob on a tool call degrades to `{}` instead of raising inside
  `chat()` — a bad model completion produces an empty-args tool call the loop's gate then has to
  catch, not an exception at the LLM boundary (`copilot/llm/http.py:96-98,115-122`).
- `reasoning_effort` is only sent when `COPILOT_LLM_REASONING_EFFORT` is set — omitting it keeps
  the plain OpenAI body, so a non-gpt-oss backend that chokes on an unknown field is unaffected
  by default (`copilot/llm/http.py:64,71-72`).
- Both `Ledger` and `SessionStore` are strictly append-only — neither module implements retention,
  eviction, or a size cap. The one history-trimming mechanism in this codebase
  (`history_compaction`/`history_max_chars`, `copilot/config.py:66-68`) is consumed by
  `copilot/agent/loop.py:332-333` (not owned by this doc) to shrink in-flight `history` passed to
  the LLM — it does not delete or compact anything already written to `events.jsonl` or `ledger.db`.
- `SessionStore.append`'s `flock` is per-open-fd/advisory and only guards this process's own
  writers cooperating the same way; it is not a guarantee against a non-cooperating writer
  (`session.py:50-55`).
- `Ledger`/`SessionStore` file locations are NOT `Config` fields — they come from raw env vars
  (`COPILOT_LEDGER_PATH`, `COPILOT_SESSIONS_DIR`, `COPILOT_KB_URI`) read by each caller
  individually, so callers that forget to set them silently default to `ledger.db`/`sessions` in
  the process's current working directory (`copilot/forensic/trigger.py:148`,
  `copilot/api/app.py:154,161`, `copilot/retrieval/seed.py:42` — the last one has no default and
  raises `KeyError` if unset).
