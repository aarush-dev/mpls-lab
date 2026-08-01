# INTEGRATION_GUIDE.md

How to go from mock-only to a live backend, and how to extend the mock topology. Nothing in this doc is built yet unless stated otherwise.

## Going live

1. **Implement `HttpDataClient implements DataClient`** in `frontend/plugin/src/data/` (e.g. `HttpDataClient.ts`), matching the interface in `frontend/plugin/src/data/DataClient.ts`. See `API_CONTRACT.md` for the method-by-method mapping to `dataapi/app.py`'s 6 GET endpoints (`/metrics`, `/events`, `/flows`, `/labels`, `/topology`, `/datasets`) — several `DataClient` methods (`getCapabilities`, `getOverview`, `getIncidents`, `getPredictions`, all conversation/copilot methods) have no server-side counterpart yet and need either new backend routes or client-side derivation from the existing ones.
2. **Flip config.** `frontend/plugin/src/config.ts`:
   ```ts
   export const appConfig: AppConfig = {
     mode: 'api',
     apiBaseUrl: 'http://127.0.0.1:8000',
     requestTimeoutMs: 8000,
     showDemoBadge: false,
   };
   ```
   `apiBaseUrl` already defaults to `dataapi`'s bind address (`127.0.0.1:8000`); update if the backend moves.
3. **Wire it in `frontend/plugin/src/data/DataClientContext.tsx`.** Currently:
   ```ts
   function createDataClient(): DataClient {
     if (appConfig.mode === 'mock') {
       return new MockDataClient();
     }
     // HttpDataClient lands in a later milestone; until then `api` mode falls back to mock so the
     // app never crashes on an unimplemented client.
     return new MockDataClient();
   }
   ```
   Change the fallback branch to `return new HttpDataClient(appConfig.apiBaseUrl, appConfig.requestTimeoutMs)` (or equivalent) once `HttpDataClient` exists.
4. `dataapi/app.py` binds `127.0.0.1` only (local-only, air-gapped lab tool) — confirm network reachability from wherever Grafana runs before pointing `apiBaseUrl` at it.

## Extending topology

Adding nodes/links is data-driven, no code required for a new node **role**:

- Node/link rows come from fixtures (`frontend/plugin/src/fixtures/topology.json`, shape `{ nodes: TopologyNode[], links: TopologyLink[] }` per `frontend/plugin/src/data/types.ts`), or from a live `GET /topology` in `dataapi/app.py`.
- Visual style per role is a registry in `frontend/plugin/src/data/topologyStyles.ts`: `roleStyles: Record<string, RoleStyle>` currently covers `P`, `PE`, `CE`, `host`. `styleForRole(role)` looks up the registry and falls back to `defaultRoleStyle` for any unlisted role. A new role in fixture/API data renders immediately (shape ellipse, default color/size) with zero code changes; add an entry to `roleStyles` only if it needs a distinct look.
- Health-state coloring (`stateColors`: red/amber/green, plus `neutralColor`) is separate from role styling and keyed off node state, not role.

To add fixture rows manually: append entries to `nodes`/`links` arrays in `frontend/plugin/src/fixtures/topology.json` matching the `TopologyNode`/`TopologyLink` shapes. Prefer regenerating via the script below over hand-editing, so the fixture set stays internally consistent (e.g. `meta.json` device lists).

## How fixtures are generated

`frontend/scripts/generate_fixtures.py`:

- Reads committed sample Parquet captures (`dataapi/datasets/dataset_1785032386_1785033870_30s.parquet` and a synthetic capture under `synthetic/output/`) plus `topology-spec.yaml`, and reuses `dataapi/export.py` (`COLUMNS`, `precursor_mask`, `SEVERITY_ORDINAL`) for the same column/label logic the real data API uses.
- Emits JSON into `frontend/plugin/src/fixtures/`: `meta.json`, `topology.json`, `nodeStates.json`, `telemetry.json`, `incidents.json`, `predictions.json`, `events.json`, `flows.json`, `conversations.json`.
- Deterministic by design (per the script's own docstring contract): no unseeded `random`, no `datetime.now()`, no hash-seed-dependent iteration — sorted keys throughout. Rerunning produces byte-identical output.
- Bucketing: `BUCKET_MS = 30000` (30s buckets), `WINDOW_BUCKETS = 50` (sliding window size used by the mock clock).
- Run: `python3 frontend/scripts/generate_fixtures.py` from repo root (needs `pandas`, `pyyaml`, and `dataapi/export.py` importable — the script adds `dataapi/` to `sys.path` itself).

Current fixture counts (from `frontend/plugin/src/fixtures/`, verified against the JSON): 152 buckets, 50-bucket sliding window, 148 topology nodes, 361 links, 70 devices (27 with telemetry), 28 incidents, 69 predictions, 21 fault types.
