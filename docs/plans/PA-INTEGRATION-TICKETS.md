# Real PA Integration — Ticket Breakdown

Replace the PA-emulator (`copilot/emulator/emulate.py`) with the real predictive-analysis
service (`bah_predictive_analysis/training/src/serve`). Two lanes:

- **Lane A (baseline)** — serve `reduced_v3_baseline_test0.758` (temporal, single-window,
  correct gate). Runs today, no graph infra. **Ship first.**
- **Lane G (graph v2)** — serve `reduced_graph_v2_test0.859` (best: PR-AUC 0.859, TPR@1% 0.585).
  Needs live topology edges + a snapshot endpoint. **Upgrade after A.**

Every ticket declares **BEFORE** (must close first) + **MODIFIES** (files it touches) + **CONSUMES**
(stub/artifact it reads). Model per CLAUDE.md: code→opus/med, docs/mechanical→sonnet/med.

Ground truth verified: 28 channels = `dataapi/export.py:56` COLUMNS = model `FEATURE_COLUMNS`
(`src/data/schema.py`); L=168, 30 s; response shape from `src/serve/predictor.py:_build_output`;
graph batch recipe = `src/data/graph.py:GraphCollate`.

---

## EPIC: PA-0 — Replace emulator with real PA

Seam already exists: `emulate.prediction()` routes `cfg.emulate_pa` → `real_pa(now)`
(`emulate.py:298`), today `_no_real_pa` raises (`emulate.py:330`). Flip flag + inject client.
Consumers unchanged (`fault_type`, `is_abstain`, `drift_state`, `persist`, forensic case).

**Done when:** `emulate_pa=false` runs end-to-end against a live PA service, a real alerting
window opens a forensic case, suites green.

---

# LANE A — Baseline (ship first)

## PA-A1 — Deploy baseline PA inference service on the VM
**Type:** infra · **Effort:** M · **Model:** opus/med
**BEFORE:** none · **MODIFIES:** new `noc-pa.service`, `copilot-up.sh` (start PA) · **CONSUMES:** `runs_backup/reduced_v3_baseline_test0.758` (present on VM)

**Steps**
1. Linux venv (bundled `.venv` is macOS/py3.14 — unusable):
   ```bash
   python3.11 -m venv /root/pa-venv && . /root/pa-venv/bin/activate
   pip install torch==2.5.1 numpy pandas pyarrow fastapi "uvicorn[standard]" httpx
   ```
   Air-gap: pre-stage wheels, `pip install --no-index --find-links=<wheeldir>`.
2. Boot:
   ```bash
   cd /root/LAB/bah_predictive_analysis/training
   RUN_DIR=runs_backup/reduced_v3_baseline_test0.758 PYTHONPATH=. \
     uvicorn src.serve.app:app --host 127.0.0.1 --port 8001
   ```
3. `noc-pa.service` systemd unit (env `RUN_DIR`, `PYTHONPATH`, venv python), `WantedBy` before copilot.

**Acceptance**
- `curl :8001/v1/health` → `serving_mode:"temporal"`, `window_shape:[168,28]`, 28 channels, `conformal_available:true`.
- `POST /v1/predict` with a synthetic 168×28 window → 200, `decision.alert` boolean present.
- Latency p50 < 10 ms/window (spec: ~2 ms).

## PA-A2 — dataapi `export_df()` extractor
**Type:** feature · **Effort:** S · **Model:** opus/med
**BEFORE:** none · **MODIFIES:** `dataapi/export.py` · **CONSUMES:** existing `_collect`/`_flow_bucketed`/metric-maps

Refactor the CLI body into an importable function returning the assembled long table
(one row per `(device, entity, entity_type, ts)` × 28 channel cols). No new logic — lift what
`main()` already builds.
```python
def export_df(start:int, end:int, step:int=30) -> pd.DataFrame: ...
```
**Acceptance:** `export_df(now-5040, now, 30)` returns a DataFrame with COLUMNS schema; CLI still works (calls `export_df` then writes parquet); `dataapi` unit suite green.

## PA-A3 — copilot config: PA client fields
**Type:** feature · **Effort:** S · **Model:** opus/med
**BEFORE:** none · **MODIFIES:** `copilot/config.py`, `copilot/config.yaml`

Add: `pa_url` (`http://127.0.0.1:8001`), `pa_target_fpr` (0.01), `pa_top_k` (3), `pa_mode` (`temporal`|`snapshot`, default `temporal`). Keep `emulate_pa` (default `true` until PA-A8).
**Acceptance:** `python3 copilot/config.py` self-check passes; new fields typed + defaulted; `load()` validates `pa_target_fpr ∈ {0.005,0.01,0.02,0.05}`.

## PA-A4 — entity/site code vocab
**Type:** feature · **Effort:** S · **Model:** opus/med
**BEFORE:** PA-A1 · **MODIFIES:** new `copilot/window/vocab.json` + loader in `build.py` · **CONSUMES:** training `vocabs` (NOT shipped)

Vocab (`entity_type`→code, `site_type`→code) is assignment-order dependent (`prepare.py:77`),
not persisted in the run. Options: (a) regenerate by running `prepare` category-encode over a
bundled `synthetic_*.parquet` to reproduce order; (b) fallback all-zero (minor embedding degrade).
Ship (a) if reproducible, else (b) documented.
**Acceptance:** `entity_type` codes for `interface`/`tunnel`/`device` resolve; documented whether exact-match or zero-fallback.

## PA-A5 — live window builder `copilot/window/build.py`
**Type:** feature · **Effort:** M · **Model:** opus/med
**BEFORE:** PA-A2, PA-A3, PA-A4 · **MODIFIES:** new `copilot/window/build.py` · **CONSUMES:** `export_df` (PA-A2), `/v1/health.channels` (PA-A1), vocab (PA-A4)

`build_windows(now_iso, channels, L=168, step=30) -> {entity: {window,entity_type,vrf,device,etc,stc}}`:
- `export_df(now-(L-1)*step, now, step)`, groupby `(device,entity,entity_type)`,
- reindex to exactly L step-aligned rows, `df[channels].to_numpy(f32)` → (168,28),
- **NaN = structurally-absent** (tunnel_* on device row, bgp_* on interface row); emit JSON `null`, never 0 (model masks on `np.isfinite`, `predictor.py:predict`),
- drop entities without full L rows.
**Acceptance (self-check, `python -m copilot.window.build`):** against a live/canned `export_df`, every emitted window is (168,28), channel order == `/v1/health.channels`, N/A channels are `null`, ≥1 entity returned.

## PA-A6 — real PA client + translation shim `copilot/emulator/real_pa.py`
**Type:** feature · **Effort:** L · **Model:** opus/med
**BEFORE:** PA-A1, PA-A3, PA-A5 · **MODIFIES:** new `copilot/emulator/real_pa.py`, wire in `emulate.prediction()` caller · **CONSUMES:** window builder (PA-A5), PA `/v1/predict`

`RealPA(cfg).predict(now)`:
- cache `/v1/health.channels`; `build_windows(now, channels)`,
- per entity `POST /v1/predict` `{window,entity_id,entity_type,vrf,window_end_ts,entity_type_code,site_type_code,target_fpr,top_k}`,
- collect alerting (`decision.alert`); none → `None`; else pick max `calibrated_probability` → `_to_record`.

`_to_record(alerting, now)` — fill §3.3 gaps the real response omits (verified missing in `predictor.py`):

| §3.3 field | rule |
|---|---|
| `device` | winner `entity_id` — **required** (ledger index / forensic window) |
| `explanation_ref.alert_id` | `emulate._alert_id(entity, cause)` — **required** (`persist()` KeyErrors otherwise) |
| `n_concurrent` / `concurrent_faults` | `len(alerting)` / `[{device,cause}]` (element 0 = winner) — restores ADR-0014 |
| `decision.abstain` | `False` |
| `health.drift_state` | `"R0"` (real drift = separate unbuilt endpoint) |
| `anomaly.vq_label` | `f"vq_{vq_code}"` |
| `localization` / `forecast.channels` | omit (not emitted) |
| `risk.*`,`decision.*`,`anomaly.vq_code`,`forecast.quantile_spread_mean` | pass through |

Wire: build loop/API with `real_pa=RealPA(cfg).predict`.
**Acceptance (`test_real_pa.py`):** a canned PA response → `_to_record` yields a record that round-trips through `emulate.persist()` (has `alert_id`+`device`); `is_abstain`/`fault_type`/`drift_state` read it without error; no alert → `None`.

## PA-A7 — predictor loop: skip labels when real PA
**Type:** fix · **Effort:** S · **Model:** opus/med
**BEFORE:** PA-A6 · **MODIFIES:** `copilot/emulator/predictor.py:48`

Guard `fetch_labels` behind `cfg.emulate_pa` (real PA self-gates on `decision.alert`, needs no ground truth). Keep transient-fault keep-alive.
**Acceptance:** `test_predictor` green for both flag values; `emulate_pa=false` tick does zero `/labels` GETs.

## PA-A8 — end-to-end verify + flip `emulate_pa=false`
**Type:** verify · **Effort:** M · **Model:** opus/med
**BEFORE:** PA-A1..A7 · **MODIFIES:** `copilot/config.yaml` (`emulate_pa:false`), docs

**Steps:** bring lab up (`sim-up.sh` — data plane currently down, `/metrics` empty); confirm `export_df` returns live rows; boot PA (PA-A1); one real window round-trips; run predictor loop against real PA; inject a fault → confirm a forensic case opens off a real alerting record.
**Acceptance:** with `emulate_pa=false`: injected fault → PA `decision.alert=true` → ledger row → forensic case at frozen T_snapshot; full copilot suite green; no `_no_real_pa` RuntimeError.

---

# LANE G — Graph v2 (best model; upgrade after Lane A)

Weights present: `runs_backup/reduced_graph_v2_test0.859` (`final.pt`, `config.json use_graph=true`,
`phase3/meta_conformal.json` TPR@1%=0.585). Graph forward needs `gb={edge_index,edge_type,node_of_window,n_nodes,node_device_ids}` (`model.py:117-159`).

## PA-G1 — Generate live `topology_edges.parquet` **(BLOCKER)**
**Type:** feature · **Effort:** L · **Model:** opus/med
**BEFORE:** none · **MODIFIES:** new `dataapi/topology_edges.py` + output parquet · **CONSUMES:** live `/topology` (or generator/`topology-spec.yaml`)

Model trained on 12 synthetic topos (`topo_p2…p8`); **live lab (24 P/12 PE/6 POP) is a different `topology_id` NOT in the trained parquet** → `snapshot_edges` returns empty → graph no-ops. Must emit the LIVE topology's edges, schema-matched to the training file (columns verified from `/vol/full_seed42/topology_edges.parquet`):
`[topology_id, src_entity, dst_entity, relation, valid_from, valid_to, igp_cost, srlg_group, area_id]`,
device-level endpoints, `relation ∈ {ospf_adj, ldp_session, ibgp_session, wg_tunnel, shares_srlg, belongs_to, incident_to}`, **`valid_to = NULL`** (open) so live `now` is always valid.
**Acceptance:** `GraphSnapshotCache(that_root).snapshot_edges(live_topology_id, now_us, dev_to_local)` returns non-empty `edge_index` for the live device set; relations map into the vocab; `_to_device` collapse matches live device names.

## PA-G2 — Predictor loads GraphSnapshotCache at serve time
**Type:** feature · **Effort:** S · **Model:** opus/med
**BEFORE:** PA-G1 · **MODIFIES:** `bah_predictive_analysis/training/src/serve/predictor.py` (+`app.py`)

When `serves_graph`, build `self.graph_cache = GraphSnapshotCache(GRAPH_DATA_ROOT)` at load (today the cache lives only in the offline `DataBundle`). Expose for the snapshot endpoint.
**Acceptance:** graph run boots with `GRAPH_DATA_ROOT` pointing at PA-G1 output; `/v1/health` `graph_fused:true`; cache holds the live topology.

## PA-G3 — New serve endpoint `POST /v1/predict/snapshot`
**Type:** feature · **Effort:** L · **Model:** opus/med
**BEFORE:** PA-G1, PA-G2 · **MODIFIES:** `src/serve/app.py` (+`predictor.py` helper) · **CONSUMES:** `GraphCollate` recipe (`data/graph.py`)

Shipped serve has NO live-snapshot path (`/predict/sample` reads offline shards). Add endpoint taking
`{topology_id, window_end_ts, entities:[{entity_id,device,entity_type,vrf,window[168×28],entity_type_code,site_type_code}], target_fpr, top_k}`. Reproduce `GraphCollate`: stack `x`/mask, `dev_to_local` (first-seen), `node_of_window`, `n_nodes`, `edge_index/edge_type = graph_cache.snapshot_edges(topology_id, ts_us, dev_to_local)`, `node_device_ids`. Run `model(x,mask,etc,stc,gb=…)`, return per-entity `_build_output(out,i)` + entity meta + `graph_fused:true`.
**Acceptance:** posting a real topology's live windows → one 200 with a record per entity, `snapshot_nodes ≥ 2`; a known pre-fault entity shows elevated `p_any` (sanity vs the isolated-window p_any=0.02 → snapshot 0.98 effect); latency ~215 ms / 256 entities.

## PA-G4 — copilot snapshot-mode `real_pa`
**Type:** feature · **Effort:** M · **Model:** opus/med
**BEFORE:** PA-G3, PA-A6 · **MODIFIES:** `copilot/emulator/real_pa.py` (extend), `pa_mode` switch (PA-A3)

When `cfg.pa_mode=="snapshot"`: group live windows by `topology_id`, one `POST /v1/predict/snapshot` per topology, flatten per-entity records, then the **same** alert-filter + `_to_record` shim as PA-A6.
**Acceptance:** `test_real_pa_snapshot.py` — canned snapshot response → per-entity records → winner record round-trips `persist()`; `n_concurrent` reflects the alerting set across the topology.

## PA-G5 — Serve graph v2 + end-to-end verify
**Type:** verify · **Effort:** M · **Model:** opus/med
**BEFORE:** PA-G1..G4 · **MODIFIES:** `noc-pa.service` (RUN_DIR + GRAPH_DATA_ROOT), docs

Switch service to `RUN_DIR=runs_backup/reduced_graph_v2_test0.859`, `GRAPH_DATA_ROOT=<PA-G1 dir>`, `pa_mode=snapshot`. Full loop on live data.
**Acceptance:** injected propagating fault (bgp_cascade/srlg_cut) alerts EARLIER than baseline; gate empirical FPR ≈ target; forensic case opens; suites green.

## PA-G6 — Verify edge-validity units + snapshot coverage
**Type:** fix · **Effort:** S · **Model:** opus/med
**BEFORE:** PA-G1 · **MODIFIES:** `data/graph.py` (only if unit bug confirmed)

`GraphSnapshotCache` does `valid_from.astype("int64")` (ns if `datetime64[ns]`, µs if `[us]`) but compares to `ts_us` (microseconds). Confirm the live parquet's timestamp resolution so `vf <= ts_us < vt` holds at live `now`; fix unit or write `valid_from`/`valid_to` as int-µs in PA-G1.
**Acceptance:** unit test: an edge valid at live `now` passes the interval filter; no silent empty-edge fallback.

---

# Cross-cutting

## PA-D1 — Docs
**BEFORE:** PA-A8 (and PA-G5 for graph) · **MODIFIES:** `docs/adr/0003-pa-seam-and-emulator.md` (resolve §Open — real PA wired), `docs/copilot-build-plan.md` (this ticket set; the ticket that OWNS replacing `_no_real_pa` = PA-A6), `docs/plans/PA-INTEGRATION.md`, `PLAN.md`, `HANDOFF.md`.

## PA-D2 — (optional) Air-gap wheel staging
Pre-stage `torch==2.5.1` + deps wheels for offline `pip install --no-index`. Fold into PA-A1 if the enclave has no PyPI.

---

## Dependency graph (start order)

```
Lane A:  PA-A1 ─┐
         PA-A2 ─┤
         PA-A3 ─┼─▶ PA-A4 ─▶ PA-A5 ─▶ PA-A6 ─▶ PA-A7 ─▶ PA-A8 ─▶ PA-D1
                │            (A5 needs A2,A3,A4)
Lane G:  PA-G1 ─▶ PA-G2 ─▶ PA-G3 ─▶ PA-G4 ─▶ PA-G5
         PA-G1 ─▶ PA-G6                 ▲
         PA-A6 ───────────────────────┘  (G4 reuses the shim)
```

**Modification-disjoint lanes** (safe to parallelize): PA-A2 (`dataapi`) ∥ PA-A3 (`copilot/config`)
∥ PA-A1 (infra). PA-A5/A6 both touch `copilot/` new files — serialize A5→A6. Lane G serve tickets
(PA-G2/G3) touch the PA repo, disjoint from copilot; PA-G4 touches `real_pa.py` (after A6).
