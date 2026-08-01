# NOC Copilot — Grafana App Plugin

Grafana App Plugin (id `mplslab-noccopilot-app`, plugin folder name `noccopilot`), React + TypeScript, built with `@grafana/create-plugin` (webpack + swc + jest). Pinned to Grafana `>=11.1.0`, `react-router-dom` 5.3.4. Version 1.5.1. Built `dist/` is committed to the repo.

Runs entirely on bundled mock data today (see "Mock mode" below). No live backend wired up.

## Quickstart

```
cd frontend
docker compose up -d
```

Compose file: `frontend/docker-compose.yml` (canonical). Brings up `grafana/grafana:11.1.0` on `http://localhost:3000` and `prom/alertmanager:v0.27.0` on `http://localhost:9093`, anonymous admin, unsigned-plugin loading, analytics/update-checks off (air-gapped). Mounts: `./plugin/dist` → `/var/lib/grafana/plugins/mplslab-noccopilot-app`, `./grafana/provisioning` → `/etc/grafana/provisioning`, `./grafana/dashboards` → `/var/lib/grafana/dashboards`. (`frontend/plugin/docker-compose.yaml` is the create-plugin scaffold and is not used.)

First run after upgrading to v1.3.0: `docker compose up -d` then restart the `grafana` container once so it picks up the new Alertmanager datasource.

Open the app at `http://localhost:3000/a/mplslab-noccopilot-app`.

## Pages

8 pages, all under `frontend/plugin/src/pages/`:

- **Overview** (`OverviewPage.tsx`, `/`) — fleet health summary: reporting/expected devices, degraded tunnels, active incidents, highest-risk device.
- **Topology** (`TopologyPage.tsx`, `/topology`) — live cytoscape map of the network graph, node health coloring.
- **Node Detail** (`NodeDetailPage.tsx`, `/node/:id`) — single-device view with a device picker dropdown.
- **Telemetry** (`TelemetryPage.tsx`, `/telemetry`) — time-series metric panels.
- **Incidents** (`IncidentsPage.tsx`, `/incidents`) — incident and prediction list, evidence, root-cause hypotheses, recommended actions.
- **Copilot** (`CopilotPage.tsx`, `/copilot`) — chat-style assistant over incidents/telemetry.
- **Fault Injection** (`FaultInjectionPage.tsx`, `/inject`) — demo control: pick a node + fault type and fire it. The node turns red on every page (`state.injectedFaults` overlays the replayed node states in `MockDataClient.nodeStateAt`) and raises a `NodeDown` alert (Grafana Alerting + toast). Clear per-node or all.
- **Status** (`StatusPage.tsx`, `/status`) — "Data & Integration Status": shows data source/capabilities state.

Every alert (node-down, T-5min prediction, or injected fault) also pops a top-right toast that auto-fades after 5s, shown on **every** page — `src/components/AlertToaster.tsx` (`AlertToasterProvider` wraps the whole app; `App.tsx` calls `notify()` for each newly-firing alert).

Route list is the source of truth in `frontend/plugin/src/plugin.json` (`includes` array).

## Mock mode

`appConfig.mode` in `frontend/plugin/src/config.ts` defaults to `'mock'`. `MockDataClient` (`frontend/plugin/src/data/MockDataClient.ts`) reads bundled JSON fixtures under `frontend/plugin/src/fixtures/` and replays them against a shared demo clock driven by `App.tsx`. Data is a recorded sample capture, not live telemetry. Predictions and Copilot responses are fabricated to look live; the UI shows no demo markers (`showDemoBadge: false`).

Playback runs in real time: `AppProvider` (`src/state/AppContext.tsx`) paces one bucket per bucket's wall-clock width (`MOCK_BUCKET_META.bucketMs`, 30s), so 30s of data takes 30s. `speed` is a multiplier on that (`intervalMs = bucketMs / speed`): 2x → 15s/bucket, 0.5x → 60s/bucket.

Two clock values, both in `AppState` (`src/state/reducer.ts`): `cursor` is the wrapped data index into the 152-bucket tape (wraps every loop); `absTick` is an ever-increasing tick that never wraps. Display (chart x-axis, `PlaybackControls` clock label, `CopilotPage` timestamps) uses `absTick` so time keeps counting up across loops instead of jumping ~76 min backward. `MockDataClient.getTelemetry` rewrites each point's `tMs` from `absTick` (monotonic) but still reads values from the wrapped `cursor` window. `curTsMs()` stays `cursor`-based — event/prediction/incident gating must not run past fixture timestamps on a loop.

Telemetry covers all 148 selectable topology nodes, not just the ones with real fixture series. `src/data/telemetrySynth.ts` (`synthSeries(deviceId, role, bucketCount)`) generates deterministic, role-aware synthetic series (seeded by deviceId, no `Date.now`/`Math.random`) for any device or metric missing real data — including interface error counters and PE-router transceiver metrics, previously flat/null. `MockDataClient.telemetryFor(deviceId)` keeps real series where present and fills the rest with synth. `src/utils/metricGroups.ts` (`groupSeries()`, used by `TelemetryPage.tsx` and `NodeDetailPage.tsx`) groups panels by metric suffix so unrelated units (octets vs errors vs latency) don't share one axis.

Live mode (`'api'`) exists as a config value but has no implementation — see `INTEGRATION_GUIDE.md`.

## Alerting

Synthetic alerts land in Grafana's native Alerting tab, backed by a real Alertmanager container (not Grafana-managed alert rules). Two kinds, recomputed every demo-clock tick from `MockDataClient.getActiveAlerts()`:

- `NodeDown` (critical) — a node whose live state is red at the current bucket.
- `NodeDownPredicted` (warning) — an active fabricated prediction within 300s of its predicted impact.

Labels: `alertname`, `node`, `severity`, `source=noc-copilot`, `pop`. Annotations: `summary` (+ `description` with confidence for predictions). `generatorURL` deep-links to the node-detail page.

Write path: the plugin POSTs directly to Alertmanager's native API (`http://<host>:9093/api/v2/alerts`), not through Grafana's datasource proxy — Grafana's AM proxy only supports reading, and returns HTTP 400 on a posted AM-native array (it expects `definitions.PostableAlerts`). Read path: Grafana reads the alerts back via the provisioned `noc-alertmanager` datasource for its native Alerting UI. `startsAt` is omitted so Alertmanager stamps receive-time; alerts auto-resolve via `resolve_timeout` once the plugin stops re-posting them (firing set only re-sent on change, see `App.tsx`). Air-gapped: `frontend/alertmanager/alertmanager.yml` has a single blackhole receiver, nothing is ever sent anywhere.

View: Grafana → Alerting → Alert groups, source = "Alertmanager". Code: `src/alerting/alertPublisher.ts` (`buildAmAlerts`, `publishAlerts`).

See `API_CONTRACT.md` for the full `DataClient` interface, `INTEGRATION_GUIDE.md` for how to wire up a real backend.

## Build / test / typecheck

From `frontend/plugin/`. On WSL under `/mnt/c`, npm's `.bin` symlinks break, so invoke binaries directly with `node`:

```
node ./node_modules/webpack/bin/webpack.js -c ./.config/webpack/webpack.config.ts --env production
node ./node_modules/typescript/bin/tsc --noEmit
node ./node_modules/jest/bin/jest.js
```

npm scripts (`package.json`) exist for the same commands (`build`, `typecheck`, `test:ci`) but rely on the broken `.bin` symlinks in this environment.
