# INTEGRATION_GUIDE.md

How live (`api`) mode is wired, how to flip back to mock, and how to extend the mock topology.

## Live mode (default)

`config.ts` defaults to:
```ts
export const appConfig: AppConfig = {
  mode: 'api',
  apiBaseUrl: 'http://127.0.0.1:8000',
  requestTimeoutMs: 8000,
  showDemoBadge: false,
};
```

`DataClientContext.tsx` picks the client from `appConfig.mode`:
```ts
function createDataClient(): DataClient {
  if (appConfig.mode === 'mock') {
    return new MockDataClient();
  }
  return new HttpDataClient(appConfig.apiBaseUrl, appConfig.requestTimeoutMs);
}
```

`HttpDataClient` (`plugin/src/data/HttpDataClient.ts`) implements all 12 `DataClient` methods against `dataapi`'s live endpoints. See `API_CONTRACT.md` for the full method-by-method mapping. Notes:

- `getTelemetry` queries every descriptor in `data/metricCatalog.ts` (11 metric groups, PromQL templated with `$dev`) in parallel against `GET /metrics`; a dead metric returns an empty series rather than failing the page.
- `getTopology`, `getIncidents`, `getPredictions`, `getOverview` are all derived client-side from `GET /topology` + `GET /labels` (`dataapi` has no incidents/predictions/overview endpoints).
- Copilot methods (`getConversation`, `createConversation`, `sendMessage`, `submitFeedback`) forward to a private `MockDataClient` instance — `dataapi` has no LLM/copilot route; that's a separate, unbuilt component.
- `setCursor`/`getActiveAlerts` are mock-only; `HttpDataClient` doesn't implement them. Callers feature-detect (`if ('setCursor' in client)`).
- Fault injection (`getScenarios`, `injectFault`, `getActiveFaults`, `revertFault`) is extra surface beyond `DataClient`, used only by `FaultInjectionPage.tsx` — see `API_CONTRACT.md`.

`dataapi/app.py` binds `127.0.0.1` only and CORS-allows `http://localhost:3000` + `http://127.0.0.1:3000` — Grafana must run on port 3000 for `api` mode to work. Run `dataapi` with `./start.sh` (single worker — the `/faults/*` registry is in-process memory).

## Flipping back to mock

Edit `plugin/src/config.ts`, set `mode: 'mock'`, rebuild. `MockDataClient` needs no backend — bundled fixtures only. Fault injection falls back to visual-only (client-side escalation, no real lab fault); `FaultInjectionPage` detects this by feature-testing `injectFault` on the data client and shows "Backend offline — running in visual-only demo mode."

## Extending topology

Adding nodes/links is data-driven, no code required for a new node **role**:

- In `api` mode, node/link rows come from a live `GET /topology` in `dataapi/app.py`. In `mock` mode they come from fixtures (`plugin/src/fixtures/topology.json`, shape `{ nodes: TopologyNode[], links: TopologyLink[] }` per `plugin/src/data/types.ts`).
- Visual style per role is a registry in `plugin/src/data/topologyStyles.ts`: `roleStyles: Record<string, RoleStyle>` currently covers `P`, `PE`, `CE`, `host`. `styleForRole(role)` looks up the registry and falls back to `defaultRoleStyle` for any unlisted role. A new role in fixture/API data renders immediately (shape ellipse, default color/size) with zero code changes; add an entry to `roleStyles` only if it needs a distinct look.
- Health-state coloring (`stateColors`: red/amber/green, plus `neutralColor`) is separate from role styling and keyed off node state, not role. In `api` mode, state is derived live per-node from active `/labels` in `HttpDataClient.getTopology`.

To add fixture rows manually (mock mode): append entries to `nodes`/`links` arrays in `plugin/src/fixtures/topology.json` matching the `TopologyNode`/`TopologyLink` shapes. Prefer regenerating via the script below over hand-editing, so the fixture set stays internally consistent (e.g. `meta.json` device lists).

## How fixtures are generated

`scripts/generate_fixtures.py`:

- Reads committed sample Parquet captures (`dataapi/datasets/dataset_1785032386_1785033870_30s.parquet` and a synthetic capture under `synthetic/output/`) plus `topology-spec.yaml`, and reuses `dataapi/export.py` (`COLUMNS`, `precursor_mask`, `SEVERITY_ORDINAL`) for the same column/label logic the real data API uses.
- Emits JSON into `plugin/src/fixtures/`: `meta.json`, `topology.json`, `nodeStates.json`, `telemetry.json`, `incidents.json`, `predictions.json`, `events.json`, `flows.json`, `conversations.json`.
- Deterministic by design (per the script's own docstring contract): no unseeded `random`, no `datetime.now()`, no hash-seed-dependent iteration — sorted keys throughout. Rerunning produces byte-identical output.
- Bucketing: `BUCKET_MS = 30000` (30s buckets), `WINDOW_BUCKETS = 50` (sliding window size).
- Run: `python3 scripts/generate_fixtures.py` (needs `pandas`, `pyyaml`; resolves `dataapi/` and its own output dir relative to its own file location, not CWD). **Stale path:** the script still writes to `<repo-root>/frontend/plugin/src/fixtures` — a `frontend/` prefix that predates this directory's current name and no longer exists. Committed fixtures under `plugin/src/fixtures/` were not produced by the script as it stands today; fix the script's `FIXTURES_DIR` before relying on a rerun.

Current fixture counts (from `plugin/src/fixtures/`, verified against the JSON): 152 buckets, 50-bucket sliding window, 148 topology nodes, 361 links, 70 devices (27 with telemetry), 28 incidents, 69 predictions, 21 fault types.
