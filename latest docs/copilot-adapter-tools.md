# Copilot — Adapter & Tools

## Purpose
The adapter layer (`copilot/adapter/`) is the ONE seam between the copilot agent and `dataapi`
(the metrics/events/flows/topology HTTP API). It enforces a mandatory-filter contract (window +
device/pattern + low row cap, ADR-0015), frames every returned row as untrusted evidence
(ADR-0016), and hides dataapi's raw endpoint shapes from the rest of the copilot (ADR-0006) so a
shape mismatch is fixed in one place. Two concrete adapters satisfy the same `ToolAdapter`
protocol: `StubAdapter` (canned rows, for tests) and `HttpAdapter` (live dataapi). The tools layer
(`copilot/tools/registry.py`) sits directly on top: it is the tool table + arg-schema + `dispatch`
the agent loop (`copilot/agent/loop.py`, another lane) calls for every model tool-call
(`query_metrics`, `search_logs`, `flows`, `search_runbooks`, `search_incidents`,
`walk_topology_graph`). Sits in the pipeline between the LLM agent loop and dataapi/the retrieval
KB.

## Entry points
No CLI or FastAPI routes of its own — this subsystem is a library consumed by `copilot/agent/`
and `copilot/api/app.py` (`get_adapter()`, `copilot/api/app.py:89`, builds `HttpAdapter` from
`COPILOT_DATAAPI_URL` env or `cfg.dataapi_url`). Each module carries an assert-based self-check
(`if __name__ == "__main__":` block, no test framework, per repo convention):

```
python3 -m copilot.adapter.test_adapter    # F2 contract: Filters/serve_rows/BFS/framing
python3 -m copilot.adapter.test_http       # A1 HttpAdapter: ts normalisation, selector, ranged caps
python3 -m copilot.tools.test_tools        # I1/I2b/I3 registry: dispatch, cites, retrieval, walk
```
(`copilot/adapter/test_adapter.py:160`, `copilot/adapter/test_http.py:265`,
`copilot/tools/test_tools.py:318`)

Programmatic use (what a caller does):
```python
from copilot.adapter import HttpAdapter, Filters
from copilot.tools import dispatch
from copilot.window import WindowContext

adapter = HttpAdapter("http://127.0.0.1:8000")
window = WindowContext(start=1754000000, end=1754003600)
text, cites = dispatch("query_metrics", {"device": "core-r1", "pattern": "latency"},
                        adapter, window)
```

## Modules

### `copilot/adapter/contract.py`
The seam: `Filters`/`Result`/`Evidence` shapes, mandatory-filter validation, the shared
`serve_rows` read pipeline, BFS topology helpers, and the `ToolAdapter` Protocol.
- `Filters.validate()` — window/scope/limit/offset/freeze checks (`copilot/adapter/contract.py:58`)
- `sanitize(text)` / `frame(text)` — injection guard + evidence delimiters (`contract.py:97`, `:108`)
- `row_text(row)` — compact `k=v` row rendering (`contract.py:113`)
- `payload_matches(row, pattern)` — case-insensitive substring match over payload, excludes `ts`
  (`contract.py:119`)
- `serve_rows(source, filters, rows, max_limit)` — validate → window-filter → page → provenance →
  frame; shared by both adapters (`contract.py:129`)
- `bfs_hops(links, focus, n)` / `hops_within_links(...)` — undirected BFS on `/topology` links
  (`contract.py:164`, `:185`)
- `known_nodes(nodes, links)` — every device id the topology knows (`contract.py:191`)
- `NodeState` — one topology-walk node: `node`, `hop`, `status` (`contract.py:199`)
- `ToolAdapter` (Protocol) — `metrics`/`events`/`flows`/`hops_within`/`known_devices`/
  `walk_topology` (`contract.py:208`)
- `FilterError` (model-fixable guidance) vs `AdapterError` (transport fault) — distinct exception
  types so the tools layer routes them differently (`contract.py:31`, `:36`)

### `copilot/adapter/stub.py`
`StubAdapter` — canned-row adapter for deterministic tests; rides the same `serve_rows` pipeline
as the real adapter (`copilot/adapter/stub.py:16`). Filters rows by `device`/`pattern`
adapter-side (`stub.py:78`); `_status(device)` derives a topology-walk node's status from the
latest canned metrics row for that device (`stub.py:66`).

### `copilot/adapter/http.py`
`HttpAdapter` — the live adapter over a running dataapi; owns every dataapi shape quirk (ISO
timestamps, PromQL selector synthesis, `/events` and `/flows` missing `pattern`/`offset` support,
`docker logs`-based flow windowing).
- `HttpAdapter.__init__(base_url, timeout=25.0, fetch=None, transport=None, max_limit=100)`
  (`copilot/adapter/http.py:125`)
- `_http_get(path, params)` — default transport; maps `httpx.HTTPError`/`InvalidURL`/`ValueError`
  to `AdapterError` (`http.py:134`)
- `metrics`/`events`/`flows` — each wraps `serve_rows` with an adapter-specific fetch thunk
  (`http.py:152-159`)
- `_metrics_rows(filters)` — PromQL fetch, ranged-mode series ranking/capping/decimation, latest
  vs multi-sample extraction (`http.py:161`)
- `_events_rows(filters)` / `_flows_rows(filters)` — fetch full window, normalise `ts`, filter by
  `pattern` adapter-side (`http.py:188`, `:198`)
- `hops_within`/`known_devices`/`walk_topology`/`_walk_status` — topology + batched `/metrics`
  join (`http.py:210-252`)
- `_iso_to_epoch(s)` — ISO/space-sep timestamp string → epoch int, or `None` if unparseable
  (`http.py:70`)
- `_selector(filters)` — PromQL instant selector from `Filters` (`http.py:85`)
- `_series_rank(name)` — 3-tier sort key for ranged-mode series ordering (`http.py:66`)
- `_decimate(samples, cap)` — evenly-spaced down-sample, keeps first/last (`http.py:109`)

### `copilot/adapter/__init__.py`
Re-exports the public surface of `contract`/`http`/`stub` as `copilot.adapter.*`
(`copilot/adapter/__init__.py:9`).

### `copilot/tools/registry.py`
The tool table + arg schema + `dispatch` the agent loop calls for every model tool-call.
- `Cite` — content-blind projection `{id, source, device, ts}` fed to the quality gate
  (`copilot/tools/registry.py:23`)
- `TOOLS` — name → (adapter method, description) for the 3 windowed read tools
  (`registry.py:41`)
- `RETRIEVAL_TOOLS` — name → (KB source filter, description) for the 2 KB-search tools
  (`registry.py:58`)
- `TOOL_SPECS` — the function-calling schema advertised to the LLM backend (`registry.py:72`)
- `dispatch(name, arguments, adapter, window, retriever=None, kg=None)` — narrow → validate →
  read → render; routes `walk_topology_graph` and retrieval tools ahead of the shared read-tool
  table (`registry.py:102`)
- `_render(result, narrow)` — evidence lines + `next_page` hint, or an explicit "no rows for
  ..." echo (`registry.py:163`)
- `_retrieve(name, args, retriever, adapter)` — KB search, optional topology-hop prefilter for
  `search_incidents` (`registry.py:175`)
- `_walk(args, adapter, window, kg)` — topology walk render, mechanically-checkable
  `total=N (hop0=... hop1=...)` header, optional additive KG hint (`registry.py:213`)
- `_hops(args)` — coerce optional `hops` arg, default `DEFAULT_HOPS` (`registry.py:253`)
- `_render_hits(hits)` — retrieval-hit rendering with full provenance (`registry.py:262`)

### `copilot/tools/__init__.py`
Re-exports `TOOLS`, `RETRIEVAL_TOOLS`, `TOOL_SPECS`, `dispatch`, `Cite`
(`copilot/tools/__init__.py:11`).

## Parameters

| name | default | env-var/CLI-flag | units | what it controls | source (file:line) |
|---|---|---|---|---|---|
| `MAX_LIMIT` | 100 | none (constant) | rows | hard ceiling on `Filters.limit`; ADR-0015 context-size guard | `copilot/adapter/contract.py:21` |
| `Filters.limit` | 10 | tool arg `limit` | rows | rows returned per call, capped by `max_limit` | `contract.py:52` |
| `Filters.offset` | 0 | tool arg `offset` | rows | paging offset into in-window rows | `contract.py:53` |
| `Filters.start`/`end` | `None`/`None` (required) | derived from `WindowContext`, not model-set | epoch seconds | investigation window; both required or `FilterError` | `contract.py:48-49`, enforced `contract.py:59` |
| `Filters.t_snapshot` | `None` | from `WindowContext.t_snapshot` | epoch seconds | forensic freeze pin (ADR-0002); `end` may not exceed it | `contract.py:54`, `:66` |
| `Filters.ranged` | `False` | tool arg `ranged` (metrics only, truthy-string coerced) | bool | multi-sample trend series vs single latest sample | `contract.py:55`; coercion `copilot/tools/registry.py:131` |
| `EVIDENCE_OPEN`/`EVIDENCE_CLOSE` | `"<<evidence>>"` / `"<<end-evidence>>"` | none (constant) | n/a | untrusted-data frame delimiters (ADR-0016) | `contract.py:27-28` |
| `HttpAdapter._TIMEOUT` | 25.0 | ctor `timeout` kwarg | seconds | httpx client timeout; exceeds dataapi's 20s `/flows` subprocess budget | `copilot/adapter/http.py:39` |
| `HttpAdapter._STEP` | 30 | none (constant) | seconds | PromQL range-query step, mirrors dataapi default | `http.py:41` |
| `HttpAdapter._FETCH_CAP` | 1000 | none (constant) | rows | per-call fetch bound before adapter-side filter/page (events/flows) | `http.py:47` |
| `HttpAdapter._RANGED_MAX_SERIES` | 15 | none (constant) | series | max metric series kept in ranged mode (post tunnel/network/hardware sort) | `http.py:51` |
| `HttpAdapter._RANGED_MAX_SAMPLES` | 20 | none (constant) | samples/series | max decimated samples per series in ranged mode | `http.py:53` |
| `HttpAdapter._TUNNEL_PREFIX` | `"sdwan_"` | none (constant) | n/a | rank-0 metric-name prefix in ranged-mode series sort | `http.py:62` |
| `HttpAdapter._NETWORK_PREFIXES` | `("interface_", "iface_")` | none (constant) | n/a | rank-1 metric-name prefixes in ranged-mode series sort | `http.py:63` |
| `HttpAdapter` `max_limit` ctor arg | `MAX_LIMIT` (100) | ctor kwarg | rows | overrides the cap `serve_rows` enforces for this adapter instance | `http.py:126` |
| `DEFAULT_HOPS` | 2 | tool arg `hops` (via `_hops`) | hops | default topology-walk / incident-hop-filter radius | `copilot/tools/registry.py:66` |
| `COPILOT_DATAAPI_URL` | `cfg.dataapi_url` = `"http://127.0.0.1:8000"` | env `COPILOT_DATAAPI_URL` | URL | base URL `HttpAdapter` targets; env wins over config (owned by `copilot/api/app.py`, not this lane) | `copilot/config.py:85`; consumer `copilot/api/app.py:89` |
| retrieval `k` | 5 | tool arg `k` | hits | KB search top-k, clamped `1..MAX_LIMIT` | `copilot/tools/registry.py:202`, `:205` |

## Data flow
**Windowed reads (`query_metrics`/`search_logs`/`flows`)**
1. Agent loop calls `dispatch(name, arguments, adapter, window)` — `window: WindowContext` comes
   from the loop (not the model): `start`/`end`/`frozen`/`t_snapshot` (`copilot/tools/registry.py:119-138`).
2. `dispatch` coerces model args (`device`, `pattern`, `limit`, `offset`, `ranged`) into a
   `Filters`, with `start`/`end`/`t_snapshot` forced from `window` — the model cannot widen or
   escape the window (`registry.py:125-138`).
3. `Filters.validate()` runs first (`contract.py:143`) — rejects before any network call.
4. `StubAdapter`: filters canned in-memory rows by device/pattern (`stub.py:78-82`), then
   `serve_rows` window-filters/pages/frames them.
   `HttpAdapter`: fetches from dataapi (`GET /metrics|/events|/flows` on `COPILOT_DATAAPI_URL`),
   normalises `ts` (`_iso_to_epoch`, `http.py:70`), filters by `pattern` adapter-side for
   events/flows (no server-side pattern support, `http.py:194`, `:205`), then `serve_rows`.
5. `serve_rows` output → `dispatch` builds `Cite` tuples (content-blind: id/source/device/ts) for
   the quality gate, and renders human-readable text via `_render` (`registry.py:159-172`).
6. On empty result with a `device` filter set, `dispatch` calls `adapter.known_devices()` (hits
   `/topology` on HTTP) to distinguish "unknown device" from "known device, no data"
   (`registry.py:152-158`).

**Retrieval reads (`search_runbooks`/`search_incidents`)**
`args.query` → (if `search_incidents` + `device` focus) `adapter.hops_within(focus, hops)` →
`/topology` fetch → BFS node set → `retriever.search(query, k, source, nodes)` (I2a Retriever,
outside this lane) → `Hit` list → rendered + `Cite`s (`registry.py:175-210`).

**Topology walk (`walk_topology_graph`)**
`args.device`/`hops` → `adapter.walk_topology(focus, hops, window)` → HTTP: one `/topology` fetch
+ `bfs_hops` + one batched `/metrics` range query over every walk node (`http.py:218-252`); Stub:
`bfs_hops` + per-node latest canned metric row (`stub.py:51-64`) → `NodeState` tuple → rendered
with a mechanically-checkable hop-count header + optional additive KG hint
(`registry.py:213-250`).

**Outputs**: every path ends as `(observation_text: str, cites: tuple[Cite, ...])` handed back to
the agent loop, which feeds `observation_text` to the LLM and `cites` to the I4a quality gate.

## Calculations

**Ranged-mode series rank** — `_series_rank(name)` (`http.py:66-67`):
```
rank = 0 if name.startswith("sdwan_")
     else 1 if name.startswith(("interface_", "iface_"))
     else 2
```
Used to `sorted(result, key=...)` before truncating to `_RANGED_MAX_SERIES` (15) so tunnel-fault
signal (rank 0) survives the cap ahead of other interface counters (rank 1) and hardware telemetry
(rank 2) (`http.py:168-170`).

**Decimation** — `_decimate(samples, cap)` (`http.py:109-117`), for `n = len(samples) > cap`:
```
kept[i] = samples[round(i * (n - 1) / (cap - 1))]   for i in 0..cap-1
```
Evenly-spaced index selection; guarantees first (`i=0`) and last (`i=cap-1`) samples are kept.
Applied per-series with `cap = _RANGED_MAX_SAMPLES` (20) when `filters.ranged` is set
(`http.py:175`).

**In-window row filter** — `serve_rows` (`contract.py:146-147`):
```
kept = [r for r in rows if r["ts"] is not None and start <= r["ts"] <= end]
```
A row with no parseable `ts` (e.g. `_iso_to_epoch` returned `None`) is dropped, never served —
"not provably in-window" reads as absent, not as a crash.

**Paging window** — `serve_rows` (`contract.py:148-160`):
```
page = kept[offset : offset + limit]
next_page = str(offset + limit) if (offset + limit) < len(kept) else None
```

**Retrieval k clamp** — `registry.py:202-205`:
```
k = max(1, min(int(args.get("k", 5)), MAX_LIMIT))
```

**Timestamp normalisation** — `_iso_to_epoch(s)` (`http.py:70-82`):
```
if not str -> int(s) if numeric else None
dt = fromisoformat(s.replace("Z","+00:00"))   # ValueError -> None
if dt.tzinfo is None: dt = dt.replace(tzinfo=UTC)   # flow stamps are naive UTC
return int(dt.timestamp())
```

**Topology hop distance** — `bfs_hops(links, focus, n)` (`contract.py:164-182`): standard BFS on
the undirected adjacency built from `{source, target}` link pairs; `hop[focus] = 0`, each
subsequent frontier increments by 1, stops at depth `n` or when the frontier is empty.

**Topology-walk hop-count header** — `_walk` (`registry.py:234-239`):
```
hop_counts[h] = count of states with that hop
header = "total={N} (hop0={c0} hop1={c1} ...)"
```
Lets the model mechanically check its own enumeration against a stated total instead of
self-tallying.

## Config & schemas
No JSON/YAML files are read or written by this subsystem directly. Two runtime schemas matter:

**`TOOL_SPECS`** (`copilot/tools/registry.py:72-99`) — the function-calling schema handed to the
LLM backend. Per read tool (`query_metrics`/`search_logs`/`flows`): `device: string`,
`pattern: string`, `limit: integer`, `offset: integer`, plus `ranged: boolean` (only on
`query_metrics`). `search_runbooks`: required `query: string`, optional `k: integer`.
`search_incidents`: required `query`, optional `k`, `device`, `hops`. `walk_topology_graph`:
required `device`, optional `hops`. Note: `start`/`end` are deliberately NOT in the schema — the
loop owns the window, the model can't set it (`registry.py:69-71`).

**dataapi row shape** (consumed, not owned — `copilot/adapter/http.py` is the one place that
parses it): `/metrics` returns `{"result": [{"metric": {"__name__":..., "device":..., ...labels},
"value": [ts, val]} | {"values": [[ts,val],...]}]}` (Prometheus-style, `http.py:97-106`,
`:161-186`). `/events` returns `{"rows": [{"device", "ts": ISO-string, ...payload}]}`
(`http.py:188-196`). `/flows` returns `{"rows": [{"device", "ts": space-sep-string,
...payload}]}` (`http.py:198-207`). `/topology` returns `{"nodes": [{"id":...}],
"links": [{"source":..., "target":...}]}` (`http.py:210-233`).

**`Evidence`/`Result`** (`contract.py:81-94`) — the normalized shape both adapters emit:
`Evidence{id, source, device, ts, content}` where `content` is framed+sanitized text; `Result`
is `(evidence: tuple[Evidence,...], next_page: str|None)`.

**`Cite`** (`registry.py:23-38`) — the gate-facing projection: `{id, source, device, ts}`,
deliberately content-blind (drops `Evidence.content`) so the quality gate can't be swayed by
untrusted evidence text — it reasons on provenance only.

**`NodeState`** (`contract.py:199-206`) — `{node, hop, status}`, one per topology-walk result row.

## Gotchas
- `MAX_LIMIT` and `DEFAULT_HOPS` are hardcoded constants in `contract.py:21` / `registry.py:66`,
  not in `copilot/config.py` — deliberate (ADR-0015 wants a low ceiling by construction, and
  config.py is a different lane's file). Don't expect an env var to tune them.
- `serve_rows` validates BEFORE fetching (`contract.py:143-145`, rows passed as a zero-arg
  callable for `HttpAdapter`) — an over-broad or freeze-violating call never fires a network
  read. Passing an already-fetched list instead of a thunk would leak a wire read past
  `T_snapshot` before the guard bites (`contract.py:134-137`).
- `/events` and `/flows` have no server-side `pattern`/`offset` support — `HttpAdapter` fetches
  the full window (capped at `_FETCH_CAP=1000`) and filters/pages adapter-side
  (`http.py:188-207`). A window with more than 1000 in-scope rows silently truncates the tail,
  AND `next_page` will read "exhausted" against the truncated set, not the true set
  (`http.py:44-46`).
- `/flows` windowing is `docker logs --since/--until` = log PRINT time, not event time — a
  genuinely relevant flow row can land just outside `[start, end]` and be dropped as
  out-of-window at the gate (`http.py:16-18`, `:198-201`). Known source ceiling, not fixed here.
- Ranged-mode metrics is a two-tier cap: `_RANGED_MAX_SERIES` (15) truncates which series survive
  BEFORE `_RANGED_MAX_SAMPLES` (20) decimates each series' points. A broad selector (bare device,
  no pattern) can drop an entire metric family if it sorts after rank 2 and the cap already filled
  (`http.py:161-170`).
- `_decimate` is even-spacing, not peak-preserving — a spike strictly between two kept indices is
  lost. Documented ceiling, not a bug (`http.py:109-113`).
- `payload_matches` (`contract.py:119-126`) treats `pattern` as a LITERAL substring, never regex —
  `'error|fault|down'` matches only that literal 3-word string, not an alternation. Same function
  is shared by stub and HTTP adapters so behavior doesn't diverge (`contract.py:123-124`).
  `pattern` also excludes the `ts` field from matching so a numeric pattern like `'443'` can't
  spuriously hit an epoch timestamp or byte count.
- `FilterError` (model-fixable, e.g. "over-broad") vs `AdapterError` (transport fault) are
  different exception types on purpose — `dispatch` catches both but the tools/gate treat a
  dataapi outage as an observation the model reacts to, never an unhandled raise that kills the
  SSE stream (`contract.py:31-41`, `registry.py:141-146`).
- "No rows" alone doesn't say why (unknown device vs known-device-no-data vs bad pattern) — a
  misspelled device used to burn 2-6 wasted tool calls before the model happened to call
  `walk_topology_graph`. Fixed by an extra `known_devices()` check only when the result is empty
  AND a `device` filter was set (`registry.py:147-158`); an unreachable topology (`AdapterError`
  or empty known set) never asserts "unknown" since absence can't be proven then.
- `ranged` coercion (`registry.py:131`) treats only `"true"`/`"1"` (case-insensitive, after
  `str()`) as on — a JSON `false`/`0` from a weak model correctly reads as off, not truthy-string.
- When `ranged=true` and the model didn't pass an explicit `limit`, `dispatch` raises the
  effective default from `Filters`' own default (10) to `MAX_LIMIT` (100) — else a trend read
  would silently truncate to 10 samples (`registry.py:135-136`).
- `Filters` is frozen/immutable (`@dataclass(frozen=True)`, `contract.py:44`) — the window fields
  are injected once by `dispatch` from the caller's `WindowContext`, never mutated by tool args
  the model controls (`registry.py:137-138`).
- `sanitize()` is a light injection guard (delimiter-escape + control-char strip only,
  `contract.py:97-105`) — the real backstop is the I4a quality gate's citation check, not this
  function alone.
