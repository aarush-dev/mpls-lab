# NOC Copilot — Grafana App Plugin

Grafana App Plugin (id `mplslab-noccopilot-app`, plugin folder name `noccopilot`), React + TypeScript, built with `@grafana/create-plugin` (webpack + swc + jest). Pinned to Grafana `>=11.1.0`, `react-router-dom` 5.3.4. Version 1.7.1. Built `dist/` is committed to the repo.

Defaults to live `api` mode against the FastAPI data API (`dataapi/`, sibling dir to this one). Mock mode still exists and is selectable (see "Mock mode" below); Copilot chat always uses the mock, even in `api` mode — the ML/Copilot backend is a separate, unbuilt component.

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

Provisioned datasources (`grafana/provisioning/datasources/datasources.yaml`): **VictoriaMetrics** (uid `victoriametrics`, Prometheus type, `httpMethod: POST`, default) and **Loki** (uid `loki`), both reached via `host.docker.internal` pinned to the bridge gateway by the grafana service's `extra_hosts: ["host.docker.internal:host-gateway"]` (VM/Loki publish to host ports 8428/3100), plus the read-only Alertmanager. Without these datasources the provisioned dashboards show "No data". Compose project name is `noc-plugin` (the running container is `noc-plugin-grafana-1`); recreate with `docker compose -p noc-plugin up -d --force-recreate --no-deps grafana`, and after rebuilding `dist/` restart grafana so it serves the new plugin bundle (it caches the old one in memory at startup).

Open the app at `http://localhost:3000/a/mplslab-noccopilot-app`.

Provisioned dashboards: **NOC Overview** (11 metric panels) and **Router Logs** at `http://localhost:3000/d/router-logs/router-logs`. Router Logs queries Loki with `{job="syslog", device=~"${device:regex}"}`, offers a device dropdown, and polls every 5 seconds over the selected Grafana time range. For a true live tail use **Explore → Loki → Live**. FRR file logging stays disabled; router shells do not contain a local FRR log file.

## Pages

8 pages, all under `plugin/src/pages/`:

- **Overview** (`OverviewPage.tsx`, `/`) — fleet health summary: reporting/expected devices, degraded tunnels, active incidents, highest-risk device.
- **Topology** (`TopologyPage.tsx`, `/topology`) — cytoscape map of the network graph, node health coloring. Layout is the deterministic **grouped preset** (`TopologyGraph.tsx` `applyLayout`) — `src/utils/topologyLayout.ts` `computePositions()` gives each node a fixed slot, pops laid on a 3×2 cluster grid, role tiers stacked top-down (p core → pe → ce → host leaves), children (`ce` under its `pe`, `host` under its `ce`) sorted next to their parent (`slotKey`) to cut intra-pop edge crossings. (A `cose` force-directed "Auto" mode was tried and removed — Cytoscape's `cose` emits NaN positions on compound/pop-parented graphs, so it scrambled the map.) Node shape/size differ by role (`src/data/topologyStyles.ts`, keyed on the lowercase fixture roles). In `api` mode node `state` (red/amber/green) is derived live from active `/labels`; live `/topology` has no `pop`/`parent` fields, so `HttpDataClient.getTopology` derives both from link adjacency — `p`/`pe` anchor on `popOf(id)`, `ce` inherits `pop` + `parent` from its uplink `pe`, `host` inherits both from its `ce`. Hovering a node **shows a mini node card** (`NodeHoverCard` in `TopologyPage.tsx`) — identity, live state, and a few headline metrics.
- **Node Detail** (`NodeDetailPage.tsx`, `/node/:id`) — single-device view with a device picker dropdown and a **live filter box** (`FilterInput`, under the status line) that hides every stat/section whose label doesn't match the typed substring, updating per keystroke — case-insensitive on metric-panel titles and section headings (`inter` → Interfaces table + all `Interface …` graphs, `tx` → the TX graphs, `log` → Logs only, `flow`/`packet` → Flows, empty → whole page, no match → a hint). Top to bottom: status line → filter box → `InterfaceTable` (per-interface oper status, error/discard rates, RX/TX) → telemetry panels grouped by `metricGroups.ts` `groupSeries()` (one panel per entity-multiplied metric, e.g. "Interface RX errors"/"Interface TX errors" instead of one lumped "Interface errors" panel, plus one panel per non-entity group — Host CPU/memory, BGP control-plane, OSPF/RIB, MPLS, Chassis; all-zero series hidden on entity panels) → neighbor links → `FlowTable` (20-row scroll window over a 200-row buffer, sticky header, single-line rows + horizontal scroll, newest on top) → `LogTerminal` (terminal-styled, severity-colored, 20-row scroll window, newest at bottom; live syslog for the node from Loki, live-mode lookback widened to 60m so a router's boot/convergence burst stays visible; on the 78 `h_*` traffic-host nodes, which run no syslog agent, the empty state reads "no syslog agent — traffic host" instead of "no logs in window"). FRR routers only log on state-change (boot/convergence/faults) then go quiet — inject a fault to see live logs.
- **Telemetry** (`TelemetryPage.tsx`, `/telemetry`) — time-series metric panels.
- **Incidents** (`IncidentsPage.tsx`, `/incidents`) — incident and prediction list, evidence, root-cause hypotheses, recommended actions.
- **Copilot** (`CopilotPage.tsx`, `/copilot`) — chat-style assistant over incidents/telemetry. Answers for real over the streaming copilot `/chat` (#66): the data-layer seam (`DataClient.chat`, T1/#67) plus the `useCopilotChat` hook + minimal render (T2/#68) — user/assistant bubbles, "investigating…", error + Retry, History-mode time scoping; and a Claude-style collapsed trace with citation chips (`CopilotTrace.tsx`, T3/#69) — each event is an expandable card, the answer carries `[source:offset]` chips that hover-preview and click-jump to their evidence row. No mock path exists.
- **Fault Injection** (`FaultInjectionPage.tsx`, `/inject`) — fires a **real** fault in the lab sim via `dataapi`'s `POST /faults/inject {scenario, target, severity?, duration?}` (21 scenarios, valid target roles enforced server-side), auto-reverting after `duration`; a "Revert now" button calls `POST /faults/revert/{scenario_id}` early. Layered on top is the client-side visual escalation kept from the mock-only build, for instant feedback ahead of the real telemetry catching up (`InjectedFault.phase` in `src/state/reducer.ts`, timers in `App.tsx`): `pending` (~5s, still healthy) → `predicted` (amber + a `NodeDownPredicted` alert with a deterministic 30/60/90s lead) → `down` (red + `NodeDown`). If the real inject call fails, the optimistic visual is rolled back. Topology / Overview / Node Detail re-fetch on `injectedFaults` change so the color + charts update live. Clear per-node or all (reverts the real fault too). The injected-fault overlay **and** its backend `scenario_id` map persist to `localStorage` (`noc.injectedFaults` / `noc.injectIds`, hydrated in `AppContext.tsx` / `FaultInjectionPage.tsx`), so the visual survives a page refresh or navigation — the backend fault keeps running its `duration` regardless, so a wiped overlay used to read as a spurious revert. A `predicted` (amber) fault rehydrated after refresh lost its down-timer, so `App.tsx` re-arms just that leg; `pending` re-escalates via the existing effect, `down` is terminal.
- **Status** (`StatusPage.tsx`, `/status`) — "Data & Integration Status": shows data source/capabilities state.

A `LabStatusBadge` in the header (`src/components/LabStatusBadge.tsx`) polls `getCapabilities()` every 10s and shows **Lab ON** / **Lab OFF** based on VictoriaMetrics reachability. A `TimeControl` next to it (`src/components/TimeControl.tsx`) switches between **Live** (auto-refresh every 5s, window follows now) and **History** (frozen span picked via `@grafana/ui`'s `TimeRangePicker` — relative quick-ranges, 5m–30d, plus an absolute from→to date/time picker, back to VictoriaMetrics' 30d retention) — see `src/state/reducer.ts` (`AppState { mode, range, liveWindowSec, refreshTick, filters, injectedFaults }`).

Every alert (node-down, T-5min prediction, or injected fault) also pops a top-right toast that auto-fades after 5s, shown on **every** page — `src/components/AlertToaster.tsx` (`AlertToasterProvider` wraps the whole app; `App.tsx` calls `notify()` for each newly-firing alert).

Route list is the source of truth in `plugin/src/plugin.json` (`includes` array).

## Data source

The data client always talks to the real backends — no mock mode (removed in #66; a wrong answer can never be silently served). `DataClientContext.tsx` builds one `HttpDataClient` (`plugin/src/data/HttpDataClient.ts`) implementing the `DataClient` interface (`API_CONTRACT.md`).

**dataapi reads** ride `apiBaseUrl` (`http://127.0.0.1:8000`, `requestTimeoutMs` 8s) — `/topology` (148 nodes), `/metrics` (PromQL range queries per `data/metricCatalog.ts`, 11 metric groups), `/events` (Loki), `/flows` (nfacctd), `/labels` (ground-truth fault timeline, used to derive incidents/predictions/topology state/overview).

**Copilot chat** rides a separate service on `copilotBaseUrl` (`http://127.0.0.1:8100`, `copilotTimeoutMs` 180s — an investigation can take ~3 min). `DataClient.chat(request, onEvent)` POSTs `/chat` and streams its SSE `event_wire` trace via a `fetch` + `ReadableStream` reader (not `EventSource`, which is GET-only): `parseSseFrames` buffers partial frames, `mapEventsToTurn` folds the 7-type `ChatEvent` trace into a `CopilotTurn` (`data/copilotChat.ts`). `setCursor`/`getActiveAlerts` are mock-only hooks `HttpDataClient` doesn't implement; callers feature-detect (`if ('setCursor' in client)`).

## Alerting

Synthetic alerts land in Grafana's native Alerting tab, backed by a real Alertmanager container (not Grafana-managed alert rules). Two kinds, recomputed from the client-side `injectedFaults` state whenever it changes (`alerting/activeAlerts.ts` `activeAlertsFromInjected`, called from `App.tsx`):

- `NodeDown` (critical) — an injected fault in `down` phase.
- `NodeDownPredicted` (warning) — an injected fault in `predicted` phase.

Real backend faults (from `/labels`, not manually injected) surface separately as incidents/predictions, not as Alertmanager alerts.

Labels: `alertname`, `node`, `severity`, `source=noc-copilot`, `pop`. Annotations: `summary` (+ `description` with confidence for predictions). `generatorURL` deep-links to the node-detail page.

Write path: the plugin POSTs directly to Alertmanager's native API (`http://<host>:9093/api/v2/alerts`), not through Grafana's datasource proxy — Grafana's AM proxy only supports reading, and returns HTTP 400 on a posted AM-native array (it expects `definitions.PostableAlerts`). Read path: Grafana reads the alerts back via the provisioned `noc-alertmanager` datasource for its native Alerting UI. `startsAt` is omitted so Alertmanager stamps receive-time; alerts auto-resolve via `resolve_timeout` once the plugin stops re-posting them (firing set only re-sent on change, see `App.tsx`). Air-gapped: `alertmanager/alertmanager.yml` has a single blackhole receiver, nothing is ever sent anywhere.

View: Grafana → Alerting → Alert groups, source = "Alertmanager". Code: `src/alerting/alertPublisher.ts` (`buildAmAlerts`, `publishAlerts`).

See `API_CONTRACT.md` for the full `DataClient` interface, `INTEGRATION_GUIDE.md` for how the real backends are wired.

## Build / test / typecheck

From `plugin/`. On WSL under `/mnt/c`, npm's `.bin` symlinks break, so invoke binaries directly with `node`:

```
node ./node_modules/webpack/bin/webpack.js -c ./.config/webpack/webpack.config.ts --env production
node ./node_modules/typescript/bin/tsc --noEmit
node ./node_modules/jest/bin/jest.js
```

npm scripts (`package.json`) exist for the same commands (`build`, `typecheck`, `test:ci`) but rely on the broken `.bin` symlinks in this environment.

Verified: prod webpack build green, `tsc --noEmit` clean, 113 jest tests pass.
