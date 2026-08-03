# NOC Copilot — Grafana App Plugin

Grafana App Plugin (id `mplslab-noccopilot-app`, plugin folder name `noccopilot`), React + TypeScript, built with `@grafana/create-plugin` (webpack + swc + jest). Pinned to Grafana `>=11.1.0`, `react-router-dom` 5.3.4. Version 1.7.1. Built `dist/` is committed to the repo.

Defaults to live `api` mode against the FastAPI data API (`dataapi/`, sibling dir to this one). Mock mode still exists and is selectable (see "Mock mode" below). Copilot chat is now **live** in `api` mode against the copilot's `POST /chat` (separate service, `copilotBaseUrl` default `http://127.0.0.1:8100`), falling back to the mock in `mock` mode or when the copilot is unreachable.

## Quickstart

1. Start lab telemetry (the sim), then the data API:
   ```
   cd ../dataapi && ./start.sh
   ```
   Runs uvicorn on `127.0.0.1:8000`, single worker (the `/faults/*` in-memory registry requires it).
2. Build the plugin:
   ```
   cd plugin && node ./node_modules/webpack/bin/webpack.js -c ./.config/webpack/webpack.config.ts --env production
   ```
3. Start Grafana:
   ```
   docker compose up -d
   ```

Compose file: `docker-compose.yml` (canonical, this directory). Brings up `grafana/grafana:11.1.0` on `http://localhost:3000` and `prom/alertmanager:v0.27.0` on `http://localhost:9093`, anonymous admin, unsigned-plugin loading, analytics/update-checks off (air-gapped). Mounts: `./plugin/dist` → `/var/lib/grafana/plugins/mplslab-noccopilot-app`, `./grafana/provisioning` → `/etc/grafana/provisioning`, `./grafana/dashboards` → `/var/lib/grafana/dashboards`. (`plugin/docker-compose.yaml` is the create-plugin scaffold and is not used.) Grafana must run on port 3000 specifically — `dataapi`'s CORS allow-list is `http://localhost:3000` + `http://127.0.0.1:3000` only.

Open the app at `http://localhost:3000/a/mplslab-noccopilot-app`.

## Pages

9 pages, all under `plugin/src/pages/`:

- **Overview** (`OverviewPage.tsx`, `/`) — fleet health summary: reporting/expected devices, degraded tunnels, active incidents, highest-risk device.
- **Topology** (`TopologyPage.tsx`, `/topology`) — cytoscape map of the network graph, node health coloring. Layout is the deterministic **grouped preset** (`TopologyGraph.tsx` `applyLayout`) — `src/utils/topologyLayout.ts` `computePositions()` gives each node a fixed slot, pops laid on a 3×2 cluster grid, role tiers stacked top-down (p core → pe → ce → host leaves), children (`ce` under its `pe`, `host` under its `ce`) sorted next to their parent (`slotKey`) to cut intra-pop edge crossings. (A `cose` force-directed "Auto" mode was tried and removed — Cytoscape's `cose` emits NaN positions on compound/pop-parented graphs, so it scrambled the map.) Node shape/size differ by role (`src/data/topologyStyles.ts`, keyed on the lowercase fixture roles). In `api` mode node `state` (red/amber/green) is derived live from active `/labels`; live `/topology` has no `pop`/`parent` fields, so `HttpDataClient.getTopology` derives both from link adjacency — `p`/`pe` anchor on `popOf(id)`, `ce` inherits `pop` + `parent` from its uplink `pe`, `host` inherits both from its `ce`. Hovering a node **shows a mini node card** (`NodeHoverCard` in `TopologyPage.tsx`) — identity, live state, and a few headline metrics.
- **Node Detail** (`NodeDetailPage.tsx`, `/node/:id`) — single-device view with a device picker dropdown and a **live filter box** (`FilterInput`, under the status line) that hides every stat/section whose label doesn't match the typed substring, updating per keystroke — case-insensitive on metric-panel titles and section headings (`inter` → Interfaces table + all `Interface …` graphs, `tx` → the TX graphs, `log` → Logs only, `flow`/`packet` → Flows, empty → whole page, no match → a hint). Top to bottom: status line → filter box → `InterfaceTable` (per-interface oper status, error/discard rates, RX/TX) → telemetry panels grouped by `metricGroups.ts` `groupSeries()` (one panel per entity-multiplied metric, e.g. "Interface RX errors"/"Interface TX errors" instead of one lumped "Interface errors" panel, plus one panel per non-entity group — Host CPU/memory, BGP control-plane, OSPF/RIB, MPLS, Chassis; all-zero series hidden on entity panels) → neighbor links → `FlowTable` (20-row scroll window over a 200-row buffer, sticky header, single-line rows + horizontal scroll, newest on top) → `LogTerminal` (terminal-styled, severity-colored, 20-row scroll window, newest at bottom; live syslog for the node from Loki, live-mode lookback widened to 60m so a router's boot/convergence burst stays visible; on the 78 `h_*` traffic-host nodes, which run no syslog agent, the empty state reads "no syslog agent — traffic host" instead of "no logs in window"). FRR routers only log on state-change (boot/convergence/faults) then go quiet — inject a fault to see live logs.
- **Telemetry** (`TelemetryPage.tsx`, `/telemetry`) — time-series metric panels.
- **Incidents** (`IncidentsPage.tsx`, `/incidents`) — incident and prediction list, evidence, root-cause hypotheses, recommended actions.
- **Copilot** (`CopilotPage.tsx`, `/copilot`) — chat-style assistant over the **live copilot** (`api` mode): posts to the copilot's `POST /chat` (`config.ts` `copilotBaseUrl`, default `http://127.0.0.1:8100`) via `data/CopilotClient.ts`, which reads the SSE trace and renders the cited answer + a collapsible "How I investigated" trace + a quality-gate badge. Scoped to the current TimeControl window; free-form session id persisted in localStorage. In `mock` mode (or if the copilot is unreachable) it falls back to the bundled mock. "Explain with Copilot" buttons on incident/node detail deep-link here pre-scoped (fresh session, auto-asked). Needs CORS on the copilot (see its README).
- **Forensics** (`ForensicPage.tsx`, `/forensics`) — auto-generated postmortems. **Sample data for now** (banner-flagged) behind a `getCases`/`getCase` seam; wires to the copilot's case-listing route when it exists.
- **Fault Injection** (`FaultInjectionPage.tsx`, `/inject`) — fires a **real** fault in the lab sim via `dataapi`'s `POST /faults/inject {scenario, target, severity?, duration?}` (21 scenarios, valid target roles enforced server-side), auto-reverting after `duration`; a "Revert now" button calls `POST /faults/revert/{scenario_id}` early. Layered on top is the client-side visual escalation kept from the mock-only build, for instant feedback ahead of the real telemetry catching up (`InjectedFault.phase` in `src/state/reducer.ts`, timers in `App.tsx`): `pending` (~5s, still healthy) → `predicted` (amber + a `NodeDownPredicted` alert with a deterministic 30/60/90s lead) → `down` (red + `NodeDown`). If the real inject call fails, the optimistic visual is rolled back. Topology / Overview / Node Detail re-fetch on `injectedFaults` change so the color + charts update live. Clear per-node or all (reverts the real fault too).
- **Status** (`StatusPage.tsx`, `/status`) — "Data & Integration Status": shows data source/capabilities state.

A `LabStatusBadge` in the header (`src/components/LabStatusBadge.tsx`) polls `getCapabilities()` every 10s and shows **Lab ON** / **Lab OFF** based on VictoriaMetrics reachability. A `TimeControl` next to it (`src/components/TimeControl.tsx`) switches between **Live** (auto-refresh every 5s, window follows now) and **History** (frozen span picked via `@grafana/ui`'s `TimeRangePicker` — relative quick-ranges, 5m–30d, plus an absolute from→to date/time picker, back to VictoriaMetrics' 30d retention) — see `src/state/reducer.ts` (`AppState { mode, range, liveWindowSec, refreshTick, filters, injectedFaults }`).

Every alert (node-down, T-5min prediction, or injected fault) also pops a top-right toast that auto-fades after 5s, shown on **every** page — `src/components/AlertToaster.tsx` (`AlertToasterProvider` wraps the whole app; `App.tsx` calls `notify()` for each newly-firing alert).

Route list is the source of truth in `plugin/src/plugin.json` (`includes` array).

## Data modes

`appConfig.mode` in `plugin/src/config.ts` defaults to `'api'`. `DataClientContext.tsx` picks the implementation: `'mock'` → `MockDataClient`, `'api'` → `HttpDataClient` (`plugin/src/data/HttpDataClient.ts`), both implementing the same `DataClient` interface (`API_CONTRACT.md`).

**`api` mode (default):** `HttpDataClient` talks to `dataapi` at `apiBaseUrl` (`http://127.0.0.1:8000`) — `/topology` (148 nodes), `/metrics` (PromQL range queries per `data/metricCatalog.ts`, 11 metric groups), `/events` (Loki), `/flows` (nfacctd), `/labels` (ground-truth fault timeline, used to derive incidents/predictions/topology state/overview). The 4 copilot methods (`getConversation`, `createConversation`, `sendMessage`, `submitFeedback`) forward to an internal `MockDataClient` — `dataapi` has no ML/LLM route, that's a separate, unbuilt component. `setCursor`/`getActiveAlerts` are mock-only hooks `HttpDataClient` doesn't implement; callers feature-detect (`if ('setCursor' in client)`).

**`mock` mode:** `MockDataClient` (`plugin/src/data/MockDataClient.ts`) reads bundled JSON fixtures under `plugin/src/fixtures/` — a recorded sample capture, not live telemetry. Predictions and Copilot responses are fabricated to look live; the UI shows no demo markers (`showDemoBadge: false`). Telemetry covers all 148 selectable topology nodes, not just the ones with real fixture series — `src/data/telemetrySynth.ts` (`synthSeries(deviceId, role, bucketCount)`) generates deterministic, role-aware synthetic series (seeded by deviceId, no `Date.now`/`Math.random`) for any device/metric missing real data. `src/utils/metricGroups.ts` (`groupSeries()`) groups panels by metric suffix so unrelated units don't share one axis. See `INTEGRATION_GUIDE.md` for how to flip back to `mock`.

## Alerting

Synthetic alerts land in Grafana's native Alerting tab, backed by a real Alertmanager container (not Grafana-managed alert rules). Two kinds, recomputed from the client-side `injectedFaults` state whenever it changes (`alerting/activeAlerts.ts` `activeAlertsFromInjected`, called from `App.tsx`):

- `NodeDown` (critical) — an injected fault in `down` phase.
- `NodeDownPredicted` (warning) — an injected fault in `predicted` phase.

Real backend faults (from `/labels`, not manually injected) surface separately as incidents/predictions, not as Alertmanager alerts.

Labels: `alertname`, `node`, `severity`, `source=noc-copilot`, `pop`. Annotations: `summary` (+ `description` with confidence for predictions). `generatorURL` deep-links to the node-detail page.

Write path: the plugin POSTs directly to Alertmanager's native API (`http://<host>:9093/api/v2/alerts`), not through Grafana's datasource proxy — Grafana's AM proxy only supports reading, and returns HTTP 400 on a posted AM-native array (it expects `definitions.PostableAlerts`). Read path: Grafana reads the alerts back via the provisioned `noc-alertmanager` datasource for its native Alerting UI. `startsAt` is omitted so Alertmanager stamps receive-time; alerts auto-resolve via `resolve_timeout` once the plugin stops re-posting them (firing set only re-sent on change, see `App.tsx`). Air-gapped: `alertmanager/alertmanager.yml` has a single blackhole receiver, nothing is ever sent anywhere.

View: Grafana → Alerting → Alert groups, source = "Alertmanager". Code: `src/alerting/alertPublisher.ts` (`buildAmAlerts`, `publishAlerts`).

See `API_CONTRACT.md` for the full `DataClient` interface, `INTEGRATION_GUIDE.md` for how `api` mode is wired and how to flip back to mock.

## Build / test / typecheck

From `plugin/`. On WSL under `/mnt/c`, npm's `.bin` symlinks break, so invoke binaries directly with `node`:

```
node ./node_modules/webpack/bin/webpack.js -c ./.config/webpack/webpack.config.ts --env production
node ./node_modules/typescript/bin/tsc --noEmit
node ./node_modules/jest/bin/jest.js
```

npm scripts (`package.json`) exist for the same commands (`build`, `typecheck`, `test:ci`) but rely on the broken `.bin` symlinks in this environment.

Verified: prod webpack build green, `tsc --noEmit` clean, 109 jest tests pass.
