# Data API service

## Purpose

Local-only FastAPI service (`dataapi/`) that fronts the lab's telemetry stack with one flat HTTP contract for the Grafana plugin and the AI/ML/RAG pipeline. It does three things: (1) passthrough/aggregate live queries to VictoriaMetrics (metrics), Loki (events), nfacctd (`docker logs`, flows), the fault-label JSONL files, and the generated topology YAML; (2) join all of that plus fault labels into one canonical Parquet table per time window (`export.py`, consumed by `/datasets`); (3) proxy fault injection into the lab (`faults_api.py`, wraps `faults/orchestrator.py`) and PA live-alert scoring (`pa_alerts` service on :8002). Binds `127.0.0.1:8000` only — sits between the live docker-lab telemetry stack and everything downstream (Grafana plugin, ML dataset consumers, the copilot).

## Entry points

**HTTP service** (`app.py`):
```bash
cd dataapi && uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1
# or:
./start.sh
```
Single worker only — `/faults/*` keeps its live-injection registry in process memory (`faults_api.py:62-63`); a multi-worker deploy would split inject from `/active`/`/revert` (`start.sh:6-8`).

**Dataset build CLI** (`export.py`):
```bash
python3 export.py --start 1700000000 --end 1700003600 --step 30
python3 export.py --minutes 60          # shortcut: last N minutes
```
(`export.py:587-608`)

**Schema gate** (`check_dataset.py`) — assert-based, run after `export.py`:
```bash
python3 check_dataset.py                # checks newest dataset in datasets/
python3 check_dataset.py <path>          # check one file
```
(`check_dataset.py:8-9`)

**Reschema an old Parquet** onto the current canonical schema without re-querying telemetry:
```bash
python3 reschema.py <in.parquet> [-o out.parquet] [--step 30]
```
(`reschema.py:20`)

**Live topology graph edges** for the predictive-analysis model:
```bash
python3 topology_edges.py            # writes graph_data/topology_edges.parquet
python3 topology_edges.py --check    # build + self-check
```
(`topology_edges.py:28-29`)

**Module self-checks** (no test framework, assert + `__main__`):
```bash
python3 sources.py            # topology/label cache self-check (sources.py:276-289)
python3 test_flows_window.py  # /flows window scoping, spied source (test_flows_window.py:8)
python3 test_coverage_seam.py # export.build_dataset full-column coverage (test_coverage_seam.py:6)
pytest test_faults_api.py     # /faults/* routes, run_scenario mocked (test_faults_api.py:1-4)
```

## Modules

- **`app.py`** — FastAPI app; all HTTP routes. Thin handlers, logic delegated to `sources.py`/`export.py`/`faults_api.py`. Key: `metrics()` app.py:76, `metrics_batch()` app.py:93, `events()` app.py:114, `flows()` app.py:129, `labels()` app.py:144, `topology()` app.py:149, `datasets()` app.py:154, `pa_alerts()` app.py:53.
- **`sources.py`** — data-access layer shared by `app.py` and `export.py`. VM/Loki HTTP calls, `docker logs` flow parsing, label-file loading, topology-YAML-to-graph derivation. Key: `vm_query()` sources.py:51, `vm_query_range()` sources.py:58, `loki_query_range()` sources.py:72, `events_rows()` sources.py:89, `flow_rows()` sources.py:116, `label_rows()` sources.py:188, `topology_graph()` sources.py:239.
- **`export.py`** — the join. Builds the 59-column canonical ML dataset from VM range queries + flow aggregation/modelling + fault-label overlap join. Key: `export_df()` export.py:533, `build_dataset()` export.py:573, `_apply_labels()` export.py:327, `attach_labels()` export.py:379, `finalize_schema()` export.py:470, `_modelled_flow()` export.py:245, `precursor_mask()` export.py:459.
- **`faults_api.py`** — `/faults/*` router; wraps `faults/orchestrator.py` (sibling dir) in a daemon-thread runner + `Lock`-guarded in-memory registry so injections are non-blocking and revertible. Key: `inject()` faults_api.py:94, `active()` faults_api.py:130, `revert()` faults_api.py:147, `scenarios()` faults_api.py:83.
- **`topology_edges.py`** — one-shot generator: turns `topology/topology-meta.json` into `graph_data/topology_edges.parquet`, the live-lab edge set the PA graph model reads (reuses `synthetic/topology_paths._static_edges`). Not exposed over HTTP. Key: `build_live_edges()` topology_edges.py:71, `_inventory()` topology_edges.py:58.
- **`reschema.py`** — offline: re-derive `ts`/`severity`/labels/`vrf` encoding on an already-exported Parquet without re-querying the stack. Key: `reschema()` reschema.py:39.
- **`check_dataset.py`** — assert-based CI gate: column-list match, `dataset.schema.json` validation over a 500-row sample, positive-label-presence check. Key: `main()` check_dataset.py:27.
- **`test_coverage_seam.py`, `test_faults_api.py`, `test_flows_window.py`** — see Entry points for run commands.

## Parameters

| name | default | env-var/CLI-flag | units | what it controls | source (file:line) |
|---|---|---|---|---|---|
| `VM_URL` | `http://127.0.0.1:8428` | env `VM_URL` | URL | VictoriaMetrics base URL | sources.py:32 |
| `LOKI_URL` | `http://127.0.0.1:3100` | env `LOKI_URL` | URL | Loki base URL | sources.py:33 |
| `NFACCTD_CONTAINER` | `tele-nfacctd` | env `NFACCTD_CONTAINER` | container name | which container `docker logs` reads flows from | sources.py:34 |
| `PA_ALERTS_URL` | `http://127.0.0.1:8002` | env `PA_ALERTS_URL` | URL | pa_alerts service proxied by `/pa/alerts` | app.py:50 |
| `_HTTP_TIMEOUT` | 15.0 | (const) | seconds | httpx timeout for VM/Loki calls | sources.py:41 |
| pa_alerts proxy timeout | 5 | (const) | seconds | httpx timeout for `/pa/alerts` upstream call | app.py:57 |
| `FLOW_CACHE_TTL_S` | 5 | env `FLOW_CACHE_TTL_S` | seconds | TTL of the in-process `flow_rows()` cache | sources.py:113 |
| docker-logs tail multiplier | 4 | (const) | multiplier ×`limit` | how many raw log lines to pull per wanted flow record (slack for non-purge chatter) | sources.py:140 |
| docker-logs subprocess timeout | 20 | (const) | seconds | `subprocess.run` timeout for `docker logs` | sources.py:145 |
| GZip `minimum_size` | 500 | (const) | bytes | responses below this size are not gzipped | app.py:34 |
| CORS allowed origins | `http://localhost:3000`, `http://127.0.0.1:3000` | (const) | — | only origin allowed to call this API — the auth boundary for `/faults/*` | app.py:41 |
| CORS allowed methods | `GET`, `POST` | (const) | — | HTTP methods CORS allows | app.py:42 |
| `/metrics` `step` | 30 | query param `step` | seconds | PromQL range-query step | app.py:81 |
| `/metrics/batch` `step` | 30 | JSON body `step` | seconds | range-query step for each batched query | app.py:102 |
| `/metrics/batch` thread pool | 8 | (const) `max_workers` | threads | parallelism for fan-out of batched PromQL queries | app.py:110 |
| `/events` window | now−3600..now | query params `start`/`end` | epoch s | default 1h lookback when unspecified | app.py:121-122 |
| `/events` `limit` | 1000 | query param `limit` | rows | max Loki log lines returned | app.py:119 |
| `/flows` `limit` | 500 | query param `limit` | rows | max flow records returned | app.py:131 |
| `/datasets` window | now−3600..now | query params `start`/`end` | epoch s | default 1h window when `build=true`/`start` given | app.py:164-165 |
| `/datasets` `step` | 30 | query param `step` | seconds | dataset ts-bucket size | app.py:158 |
| `_FLOW_TICK_S` | 360.0 | (const) | seconds | one trafficgen tick ≈ 6 min of modelled time; scales modelled flow to the bucket `step` | export.py:46 |
| bytes-per-packet estimate | 1400 | (const, divisor) | bytes | `flow_packets` derived from modelled `flow_bytes / 1400` | export.py:275 |
| `SEVERITY_ORDINAL` | `low`=0.33, `medium`=0.66, `high`=1.0 | (const) | ordinal | maps string severity to the numeric `severity` column | export.py:105 |
| `_SEV_RANK` | `low`=1, `medium`=2, `high`=3 | (const) | ordinal | picks the "primary" episode among concurrent faults (highest rank wins) | export.py:279 |
| `SEVERITY_INERT_FAULTS` | 7 scenario types (`node_failure`, `mpls_underlay_failure`, `p_node_failure`, `pop_isolation`, `core_partition`, `srlg_cut`, `rr_failure`) | (const) | — | scenario types whose `severity` must stay null (no severity concept in the injector) | export.py:113-116 |
| `_VRF_NAMES` | `("CORP","VOICE","GUEST")` | (const) | — | the only VRF names recognized when deriving the `vrf` column | export.py:285 |
| `DEFAULT_DURATION` (faults) | 90 | (const) / body field `duration` | seconds | default hold time for `POST /faults/inject` | faults_api.py:58, 70 |
| `TOPOLOGY_ID` | `"live_lab"` | (const) | — | topology id stamped on every row of `topology_edges.parquet` | topology_edges.py:50 |
| `_VALID_FROM` | `2000-01-01T00:00Z` | (const) | timestamp | `valid_from` for every static live-lab graph edge (always valid at live "now") | topology_edges.py:55 |
| uvicorn workers | 1 | CLI flag `--workers` | — | forced to 1 (in-memory `/faults` registry) | start.sh:9 |

## Data flow

- **Metrics**: VictoriaMetrics `:8428` Prometheus API → `sources.vm_query`/`vm_query_range` (`GET /api/v1/query[_range]`, httpx) → raw Prometheus `result` array passthrough, no transform → served by `/metrics`, `/metrics/batch`, and consumed internally by `export._collect` for the dataset join. sources.py:51-66, app.py:76-111.
- **Events**: Loki `:3100` LogQL API → `sources.loki_query_range` (`GET /loki/api/v1/query_range`, ns-scaled `start`/`end`) → `events_rows` flattens each stream's `(ts_ns, line)` pairs into one row per log line tagged with `device`/`app`/`severity` from the stream labels, sorted by `ts` → served by `/events`. sources.py:72-107, app.py:114-126.
- **Flows**: nfacctd container stdout (`docker logs tele-nfacctd`) → `sources._flow_rows_uncached` parses one JSON object per line, keeps only `event_type=="purge"`, filters by `device`/window, TTL-cached 5s in `flow_rows` → served raw by `/flows`; bucketed per (device, `step`-aligned ts) by `export._flow_bucketed` for the dataset join, gap-filled with `export._modelled_flow` (see Calculations) when nfacctd has no coverage for a bucket. sources.py:116-175, export.py:217-276.
- **Labels**: `faults/labels/*.jsonl` (one JSON object per line, ground-truth fault episodes) → `sources.label_rows` (mtime-keyed cache, invalidated when any label file's mtime changes) → served raw by `/labels`; LEFT-joined onto the dataset by `export._apply_labels` on device + bucket-interval overlap. sources.py:188-206, export.py:327-376.
- **Topology**: `topology/clab.yml` (+ `topology-spec.yaml` if present) → `sources.topology_graph` (mtime-keyed cache) parses clab node/link definitions, derives `role`/`site_type`/`vrfs` per node → served by `/topology`; also feeds `export._site_vrfs` (tunnel VRF derivation) and `faults_api` (valid target roles, via `orchestrator.CAMPAIGN_POOLS`, not this module directly). sources.py:212-273, app.py:149-151.
- **Dataset (join)**: `export.export_df` pulls interface/tunnel/device metric ranges from VM (`_IF_METRICS`/`_TUN_METRICS`/`_DEV_METRICS`), flow data (real + modelled), fault labels, VRF fill, then `finalize_schema` fixes column order/dtypes → written atomically as Parquet by `build_dataset` (tmp file + `os.replace`) → served as a file download by `/datasets`, or built fresh on `build=true`/`start` given. export.py:533-584, app.py:154-175.
- **Fault injection**: `POST /faults/inject` → validates scenario/target role against `orchestrator.CAMPAIGN_POOLS` (+ `pop_isolation`/`core_partition` whole-region pools) → spawns a daemon thread calling `orchestrator.run_scenario` (in `faults/`, sibling dir) → thread writes phase/lead/t_impact into a shared `status` dict read lock-free by `/faults/active`; `/faults/revert/{id}` sets a `threading.Event` the runner is blocked on. faults_api.py:29-154.
- **PA alerts proxy**: `GET /pa/alerts` → httpx `GET {PA_ALERTS_URL}/alerts` (pa_alerts service on :8002) → passthrough JSON, or a degraded `{alerts: [], predictions: [], error: "..."}` shape on any failure so the Grafana panel never hard-fails. app.py:53-62.
- **Topology edges (offline, PA model input)**: `topology/topology-meta.json` → `topology_edges._inventory` (P routers → `site_type=core`, PE routers → `site_type=pe`) → `synthetic/topology_paths._static_edges` (reused generator, not owned by this doc) → static edge set (`valid_from`=epoch 2000, `valid_to`=NULL) written to `graph_data/topology_edges.parquet`. Not served over HTTP; consumed directly off disk by the predictive-analysis model. topology_edges.py:44-86.

## Calculations

- **`severity` (ordinal)** = `SEVERITY_ORDINAL[label.severity]` where `SEVERITY_ORDINAL = {"low": 0.33, "medium": 0.66, "high": 1.0}`; null if the label's severity string isn't one of those three (e.g. inert-severity scenarios). Inputs: the fault label's `severity` field. export.py:105, export.py:368.
- **`time_to_impact_s` (per episode)** = `round(t_impact − bucket_epoch, 1)` where `t_impact` is the label's parsed `t_impact` timestamp and `bucket_epoch` is the row's `ts` as epoch seconds. Positive before impact, negative after. Inputs: `lab["t_impact"]`, `df["ts"]`. export.py:372.
- **Label-overlap mask (which rows get a fault episode)**: a row at bucket `ts` (covering `[ts, ts+step)`) matches label `[t_start, t_end]` for device `d` iff `df.device == d AND ts + step > t_start AND ts <= t_end`. Interface-scoped labels (label has `target.interface`) additionally require `df.entity == target.interface OR df.entity_type != "interface"`. Inputs: `df.ts`, `df.device`, `df.entity`, `df.entity_type`, `step`, label `t_start`/`t_end`/`target`. export.py:342-360.
- **Primary episode selection**: among all episodes matching a row, sort by `-rank` where `rank = _SEV_RANK.get(severity_label, 0)` (`low`=1, `medium`=2, `high`=3, unknown=0); index 0 after sort is "primary" and its fields populate the scalar columns (`fault_type`, `severity`, `scenario_id`, …) while the full sorted list populates `fault_types`/`severities`/`scenario_ids`/`impact_methods`/`sla_binding_vrf` (index-aligned, element 0 = primary). Inputs: per-row episode list from the overlap join. export.py:279, export.py:364, export.py:408-422.
- **`n_concurrent`** = `len(episodes)` for the row (0 if none). Inputs: per-row episode list. export.py:405, 423.
- **Modelled `flow_bytes` (fallback flow, per device+bucket)** = `sum over VRF v of site's VRFs: VRF_FLOW[v]["flows_max"] * diurnal.util(hour_of_cycle, v) * diurnal.week_scale(...) * VRF_FLOW[v]["bytes_per_flow"]`, then `× ticks` where `ticks = step / _FLOW_TICK_S` (`_FLOW_TICK_S = 360.0`). `hour_of_cycle`/`week_scale` come from `diurnal.hour_of_cycle(epoch, PERIOD_SECONDS)`/`diurnal.week_scale(epoch, PERIOD_SECONDS)` (in `trafficgen/`, not owned by this doc — reused, not reimplemented, so the fallback tracks the live traffic-generator curve). Applies only to device rows whose site has VRFs (P routers stay null). Inputs: `VRF_FLOW` dict (per-VRF `flows_max`/`bytes_per_flow`), `_site_vrfs()` (device→VRF list from topology), row `ts`/`device`, `step`. export.py:245-276.
- **Modelled `flow_packets`** = `round(flow_bytes / 1400.0, 1)` — fixed bytes-per-packet estimate. export.py:275.
- **Bucket alignment (real flows)**: `bucket = int(epoch // step) * step`, then formatted ISO. Inputs: parsed nfacctd `stamp_updated`, `step`. export.py:227-234.
- **`vrf` for an interface entity** (`vrf_of_entity`): if entity name starts with `vrf_` and the suffix is one of `_VRF_NAMES` (`CORP`/`VOICE`/`GUEST`), return the suffix; elif the entity name itself IS one of `_VRF_NAMES`, return it; else `None` (physical `eth*`, `lo`, `wg0`, all P-router interfaces). Inputs: `entity` string, `_VRF_NAMES`. export.py:288-294.
- **`vrf` for a tunnel entity** (`tunnel_vrf_set`): sorted list of `_VRF_NAMES` present in the site's VRF set (from topology). Not a single VRF — a tunnel is shared across every VRF the site runs, and under failover a non-preferred hub too. Inputs: `_site_vrfs()[device]`. export.py:297-313.
- **`is_hard_negative` / `is_root` / cascade fields (`cascade_parent_id`, `cascade_depth`, `cascade_motif_id`, `affected_entity_count`, `injection_seed`)**: NOT computed by the live `export.py` path — these columns exist in `COLUMNS` for schema parity with the synthetic generator (`synthetic/generate.py`, not owned by this doc) and default to `False`/`null` here (`attach_labels` uses `.get()` with no live source ever populating them). export.py:391-395, 424-432.

## Config & schemas

- **`schema/dataset.schema.json`** — JSON Schema (draft 2020-12) describing the JSON projection of one dataset row (Parquet on disk is typed; JSON has no `timestamp`/no `NaN`). `required`: `ts`, `device`, `entity`, `entity_type`, `is_fault` (schema/dataset.schema.json:6). Enforced by `check_dataset.py` over a 500-row sample of the newest (or given) Parquet, with `_json()` first converting Timestamp→ISO string, numpy scalars→Python, list/array NaN entries→`null`. check_dataset.py:50-69.
  - **STALE**: this schema file only lists 49 of the 59 live `export.COLUMNS` — missing `sla_binding_vrf`, `topology_id`, `stream`, `is_hard_negative`, `is_root`, `cascade_parent_id`, `cascade_depth`, `cascade_motif_id`, `affected_entity_count`, `injection_seed`. Since it has no `additionalProperties: false`, `check_dataset.py`'s `iter_errors` never flags the gap — those 10 columns pass through unvalidated. Verified by counting `export.COLUMNS` (59) vs. schema properties (49).
- **`schema/README.md`** — hand-written prose doc of the same dataset row contract for the AI/ML/RAG team. Also stale against current code: states 49 columns (`schema/README.md:20`) and describes `vrf` as a scalar string (`schema/README.md:29,44`), but `export._as_vrf_list` (export.py:316-324) makes `vrf` a `list<string> | None` on every row since DEFECT 2a. Treat `export.py`/`export.COLUMNS`/`dataset.schema.json` as ground truth, not this file.
- **Dataset Parquet files** (`datasets/dataset_<start>_<end>_<step>s.parquet`) — written atomically (`<path>.<pid>.tmp` → `os.replace`) by `export.build_dataset`, one row per `(device, entity, entity_type, ts-bucket)`, 59 columns in `export.COLUMNS` fixed order, dtypes fixed by `export.finalize_schema` (e.g. `ts`→`datetime64[us, UTC]`, `is_fault`→`bool`, `n_concurrent`→`int8`, `cascade_depth`→`Int8`, `injection_seed`→`Int64`, list columns→`object`). export.py:573-584, export.py:470-500.
- **`graph_data/topology_edges.parquet`** — written by `topology_edges.build_live_edges`, schema/columns fixed by the reused `synthetic/topology_paths._edges_frame` (not owned here); adds `valid_from`/`valid_to` (open interval) and strips any `_`-prefixed internal fields before writing. topology_edges.py:71-86.
- **Fault-label JSONL** (`faults/labels/*.jsonl`, read not written here) — one JSON object per line; fields consumed by this codebase: `device`, `target` (dict with `device`/`interface`/`neighbor`/`vrf`, or a bare device-name string), `scenario_id`, `type`, `severity` (`low`/`medium`/`high`), `t_start`, `t_end`, `t_impact`, `lead_time`, `impact_method`, `sla_binding_vrf`. sources.py:181-206, export.py:344-375.
- **`topology/clab.yml`** (read not written) — containerlab topology file; consumed fields: `topology.nodes` (list of node names) and `topology.links` (list of `{endpoints: ["node:iface", "node:iface"]}`). sources.py:250-271.
- **`topology-spec.yaml`** (read not written, optional) — consumed fields: `vrfs.<name>.sites` (list of site types, e.g. `branch`/`hub`/`dc`) used to derive which VRFs a CE site participates in. sources.py:231-236, 252.
- **`topology/topology-meta.json`** (read not written, by `topology_edges.py` only) — consumed fields: `pops` (dict of POP→member P-router list), `pe_pop` (dict of PE router→POP). topology_edges.py:58-68.

## Gotchas

- **`/faults/*` has no auth of its own** — CORS origin allow-list (`localhost:3000`/`127.0.0.1:3000`) IS the security boundary for routes that run `docker exec` into the lab. Anything that can reach `:8000` directly (curl, another local process) bypasses it entirely — CORS is a browser-enforced control. app.py:36-44, faults_api.py:8-9.
- **Single uvicorn worker is load-bearing, not a perf choice** — `/faults/active`/`/faults/inject`/`/faults/revert` share an in-process `_ACTIVE` dict; running `--workers >1` silently splits state (inject lands on worker A, `/active` reads worker B's empty dict). start.sh:6-9, faults_api.py:62-63.
- **`/flows` raises HTTP 502 on source failure, never returns 200 with `[]`** — a dead docker/nfacctd source must not look like "no flows happened". If a consumer treats empty-list and 502 the same way, it will silently hide the outage. sources.py:146-148, app.py:140-141.
- **`flow_rows()` has a 5s TTL cache but the cache key includes `since`/`until`** — every distinct forensic window gets its own cache entry (never reused across windows), so the TTL only helps polling with identical params (e.g. the plugin's live-tail default window). sources.py:128-135.
- **`docker logs --tail limit*4` is empirical, not exact** — if purge-record density in the log drops (more chatter, fewer flows) below 1-in-4 lines, `flow_rows` silently returns fewer than `limit` rows even when more exist in-window; there's no signal distinguishing "fewer than limit exist" from "tail cutoff missed some." sources.py:140, 149-175.
- **`_apply_labels` overlap condition uses `ts + step > t_start` (strict) but `ts <= t_end` (inclusive)** — a bucket exactly ending at `t_start` is excluded, one exactly starting at `t_end` is included. Asymmetric by design (half-open `[ts, ts+step)` bucket vs. closed label interval) but easy to get backwards when reasoning about edge buckets. export.py:328-334, 351.
- **Modelled flow only fires for device rows whose site has VRFs** (`_site_vrfs()` non-empty for that device) — P routers (no VRF) get `flow_bytes=null` even when gap-filling, by design, not a bug; a consumer expecting non-null flow everywhere will see structural nulls on core routers. export.py:257-266, export.py:118-124.
- **`export.py`'s modelled-flow fallback silently no-ops if `trafficgen/` isn't mounted** (`synthetic-only host`) — logs a `WARN` to stderr and disables modelled flow entirely (`VRF_FLOW = None`), so *both* real and modelled flow can be all-null for a window with no import error surfaced to the HTTP caller. export.py:37-45.
- **`schema/dataset.schema.json` and `schema/README.md` are stale by 10 columns** (G1/G6/G7/G8/DEFECT-2b additions) and `README.md` still describes `vrf` as a scalar string post-DEFECT-2a — see Config & schemas. Anyone hand-writing a JSON-Schema-based consumer against the checked-in schema file will silently accept malformed `vrf`/topology/cascade data.
- **`/datasets` "return newest" picks by parsed window END from the filename** (`int(basename.split("_")[2])`), not lexicographic sort — a dataset for an *earlier* window built *later* can still lose to one with a later END built earlier; don't assume directory listing order. app.py:172-173, check_dataset.py:33-34.
- **`faults_api.py`'s `scenario_id` (the API's tracking handle, `{scenario}-{target}-{uuid8}`) does NOT match the `scenario_id` `orchestrator.run_scenario` mints for the written `/labels` row** — the two are correlated only by device + time, never by string equality; don't join on this id against the label file. faults_api.py:101-104.
- **`_topology_graph_uncached` derives `role` purely from name prefix** (`h_`, `ce_branch`, `ce_hub`, `ce_dc`, `pe`, `p`) with a fallback ordering where `pe*` is checked before the bare `p*` prefix — renaming a device without matching one of these prefixes silently drops it to `role="unknown"` with no `site_type`/`vrfs`. sources.py:212-225.
- **`GZipMiddleware(minimum_size=500)` only compresses responses ≥500 bytes** — the small instant-query `/metrics` replies intentionally skip gzip; don't expect `Content-Encoding: gzip` on every response when testing. app.py:32-34.
