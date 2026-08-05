# INTEGRATION_GUIDE.md

How the live data layer and copilot chat seam are wired, and how to extend topology.

## Live data (only mode — no mock)

`config.ts`:
```ts
export const appConfig: AppConfig = {
  apiBaseUrl: 'http://127.0.0.1:8000',
  requestTimeoutMs: 8000,
  copilotBaseUrl: 'http://127.0.0.1:8100',
  copilotTimeoutMs: 180000,
};
```

`DataClientContext.tsx` always builds one `HttpDataClient(appConfig.apiBaseUrl, appConfig.requestTimeoutMs, appConfig.copilotBaseUrl, appConfig.copilotTimeoutMs)`. No mode switch, no mock fallback.

`HttpDataClient` (`plugin/src/data/HttpDataClient.ts`) implements all 9 `DataClient` methods. See `API_CONTRACT.md` for the full method-by-method mapping. Notes:

- `getTelemetry` queries every descriptor in `data/metricCatalog.ts` (11 metric groups, PromQL templated with `$dev`) in parallel against `GET /metrics` (`dataapi`, `:8000`); a dead metric returns an empty series rather than failing the page.
- `getTopology`, `getIncidents`, `getPredictions`, `getOverview` are all derived client-side from `GET /topology` + `GET /labels` (`dataapi` has no incidents/predictions/overview endpoints).
- `chat` streams from the copilot service (`copilotBaseUrl`, `:8100`, separate from `dataapi`) via `POST /chat` + SSE. See `API_CONTRACT.md` for `ChatRequest`/`ChatEvent`/`CopilotTurn`.
- `setCursor`/`getActiveAlerts` don't exist — those were mock-only hooks on the deleted `MockDataClient`. Callers still feature-detect (`if ('setCursor' in client)`).
- Fault injection (`getScenarios`, `injectFault`, `getActiveFaults`, `revertFault`) is extra surface beyond `DataClient`, used only by `FaultInjectionPage.tsx` — see `API_CONTRACT.md`.

`dataapi/app.py` binds `127.0.0.1` only and CORS-allows `http://localhost:3000` + `http://127.0.0.1:3000` — Grafana must run on port 3000. Run `dataapi` with `./start.sh` (single worker — the `/faults/*` registry is in-process memory).

## Extending topology

Adding nodes/links is data-driven, no code required for a new node **role**:

- Node/link rows come from a live `GET /topology` in `dataapi/app.py` (shape `{ nodes: TopologyNode[], links: TopologyLink[] }` per `plugin/src/data/types.ts`).
- Visual style per role is a registry in `plugin/src/data/topologyStyles.ts`: `roleStyles: Record<string, RoleStyle>` currently covers `P`, `PE`, `CE`, `host`. `styleForRole(role)` looks up the registry and falls back to `defaultRoleStyle` for any unlisted role. A new role in API data renders immediately (shape ellipse, default color/size) with zero code changes; add an entry to `roleStyles` only if it needs a distinct look.
- Health-state coloring (`stateColors`: red/amber/green, plus `neutralColor`) is separate from role styling and keyed off node state, not role. State is derived live per-node from active `/labels` in `HttpDataClient.getTopology`.

## How fixtures are generated

`scripts/generate_fixtures.py`:

- Reads committed sample Parquet captures (`dataapi/datasets/dataset_1785032386_1785033870_30s.parquet` and a synthetic capture under `synthetic/output/`) plus `topology-spec.yaml`, and reuses `dataapi/export.py` (`COLUMNS`, `precursor_mask`, `SEVERITY_ORDINAL`) for the same column/label logic the real data API uses.
- Emits JSON into `plugin/src/fixtures/`: `meta.json`, `topology.json`, `nodeStates.json`, `telemetry.json`, `incidents.json`, `predictions.json`, `events.json`, `flows.json`, `conversations.json`.
- Deterministic by design (per the script's own docstring contract): no unseeded `random`, no `datetime.now()`, no hash-seed-dependent iteration — sorted keys throughout. Rerunning produces byte-identical output.
- Bucketing: `BUCKET_MS = 30000` (30s buckets), `WINDOW_BUCKETS = 50` (sliding window size).
- Run: `python3 scripts/generate_fixtures.py` (needs `pandas`, `pyyaml`; resolves `dataapi/` and its own output dir relative to its own file location, not CWD). **Stale path:** the script still writes to `<repo-root>/frontend/plugin/src/fixtures` — a `frontend/` prefix that predates this directory's current name and no longer exists. Committed fixtures under `plugin/src/fixtures/` were not produced by the script as it stands today; fix the script's `FIXTURES_DIR` before relying on a rerun.

Current fixture counts (from `plugin/src/fixtures/`, verified against the JSON): 152 buckets, 50-bucket sliding window, 148 topology nodes, 361 links, 70 devices (27 with telemetry), 28 incidents, 69 predictions, 21 fault types.
