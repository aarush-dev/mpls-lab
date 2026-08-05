# Real PA ↔ Simulation ↔ Copilot — Integration Plan

Wiring the real predictive-analytics model (`github.com/sidd20228/pa_bah`,
`training/src/serve`) into the live lab (sim + `dataapi`) and the copilot, replacing
the ground-truth PA-emulator (`copilot/emulator/emulate.py`).

Ground truth for every claim below is code, cited `file:line`.

---

## 1. Verdict — compatible, small mechanical shim

Decisive fact: **the PA model trained on this sim's exact schema.**

- `pa_bah/training/src/data/schema.py` `FEATURE_COLUMNS` = the same **28 channels**
  `dataapi/export.py:56 COLUMNS` emits — same `59col-frozen-v1` schema, **30 s** cadence,
  `L=168` window (`pa_bah .../config.py:19-20`), same entity namespace
  (`device` / `entity` / `entity_type`).
- All 28 channel names grep-hit in the lab sim (`telemetry/`, `controller/`, `dataapi/`).

So the deepest risk — train/serve domain drift — **mostly dissolves**: the live lab *is*
the training distribution. Two real gaps remain, both small:

1. **Response schema** — real `/v1/predict` ≠ copilot's §3.3 seam contract.
2. **Input assembly** — nobody builds the live 168×28 window (doc §8: "Telemetry fetch — not in the API").

The copilot seam was **built for this swap**: `copilot/emulator/emulate.py:284 prediction()`
routes on `cfg.emulate_pa`; `emulate_pa=false` calls `real_pa(now)`, today a placeholder
(`emulate.py:330 _no_real_pa`) that raises. Inject the real client → **no copilot caller changes.**

---

## 2. Data path

```
sim (VictoriaMetrics :8428)
   └─ dataapi/export.py  (28-col VM→schema map, 30 s step)
        └─ [NEW live window builder]  (168×28, NaN=absent)
             └─ PA /v1/predict  (baseline temporal run)
                  └─ [NEW translate shim]  → §3.3 Prediction Record
                       └─ copilot: ledger + forensic trigger (decision.alert)
```

Three systems, not two:
- **Simulation** (this repo: `topology/ telemetry/ faults/ dataapi/ grafana ui/`) — live MPLS lab.
- **Copilot** (`copilot/`) — LLM agent, already has the PA seam.
- **Real PA** (`pa_bah/training/src/serve`) — separate repo + weights; deploy as its own service.

---

## 3. Real response vs copilot seam (§3.3) — the schema gap

Authoritative real shape from **code** (`pa_bah .../serve/predictor.py _build_output`), not
just the doc. It emits **no**: `device`, `explanation_ref.alert_id`, `decision.abstain`,
`health`, `n_concurrent`, `concurrent_faults`, `localization`, `forecast.channels`, `anomaly.vq_label`.

**Hard breaks** if copilot consumes it raw:

| Field | copilot use | real gives | effect |
|---|---|---|---|
| `explanation_ref.alert_id` | `emulate.persist()` index + idempotency | absent | **KeyError** in `persist()` |
| `device` (top-level) | ledger index, forensic device window | only `entity_id` | `None` → forensic can't scope |

**Soft degrades** (no crash, thinner output — all read via `.get`):

| Field | copilot use | real gives | effect |
|---|---|---|---|
| `decision.abstain` | `is_abstain()` softens gate | absent | always False; gate not softened |
| `health.drift_state` | trust gate (unbuilt #38) | absent | None; no drift signal |
| `n_concurrent` / `concurrent_faults` | ADR-0014 multi-chat + master | absent | single-fault only |
| `localization` | forensic LLM evidence | doc §8 not built | narrative loses subgraph + counterfactual |
| `forecast.channels[]` | forecast context | only `quantile_spread_mean` | thinner |
| `anomaly.vq_label` | human regime label | only `vq_code` | no label |

---

## 4. Changes — by side

### 4.A  PA service (pa_bah) — deploy only, ~no code

1. **Get weights** — not in repo (`runs_backup/…` on the Modal volume, `training/modal_app.py`).
   Pull `reduced_v3_baseline_test0.758`. Air-gap: pre-stage it + wheels
   (`fastapi uvicorn httpx torch numpy pandas pyarrow`).
2. **Serve the BASELINE (temporal) run**, own port:
   ```bash
   RUN_DIR=runs_backup/reduced_v3_baseline_test0.758 PYTHONPATH=. \
   uvicorn src.serve.app:app --host 0.0.0.0 --port 8001
   ```
   Baseline because `/v1/predict` scores a single window (`serve/app.py:97`). The **graph run
   400s** on `/v1/predict` (needs the topology snapshot) and its conformal gate is
   over-conservative (doc §8) — do not use it for the alert gate yet.
3. Add a `noc-pa.service` unit alongside `noc-copilot.service`.

### 4.B  Sim side (dataapi) — one small extractor

`dataapi/export.py` is CLI-only today and writes parquet. Its collectors already do the
live window source work: `_collect` (`:201`), `_flow_bucketed` (`:217`), metric maps
(`:127` interface, `:154` device). **Add one df-returning function** (refactor, no new logic):

```python
def export_df(start, end, step=30):
    """The assembled long table the CLI already builds internally:
    one row per (device, entity, entity_type, ts) × the 28 channel columns."""
    ...  # extract the body export's main() already runs; return the merged DataFrame
```

### 4.C  Copilot side — 1 config edit + 2 new files + 1 loop tweak

**C1 — `copilot/config.py` + `config.yaml`:**
```yaml
pa_url: "http://localhost:8001"
pa_target_fpr: 0.01
pa_top_k: 3
```

**C2 — new `copilot/window/build.py`** (live window builder, reuses 4.B):
```python
def build_windows(now_iso, channels, L=168, step=30):
    end = _epoch(now_iso); start = end - (L - 1) * step
    df = export.export_df(start, end, step)               # dataapi
    out = {}
    for (dev, ent, etype), g in df.groupby(["device", "entity", "entity_type"]):
        g = g.set_index("ts").reindex(_grid(start, end, step))   # exactly L rows
        mat = g[channels].to_numpy(np.float32)            # (168, 28), NaN where channel absent
        if mat.shape != (L, 28):
            continue
        out[ent] = {"window": _nan_to_null(mat), "entity_type": etype,
                    "vrf": g["vrf"].iloc[-1], "device": dev,
                    "etc": _ETC.get(etype, 0), "stc": _STC.get(g["site_type"].iloc[-1], 0)}
    return out
```
- `channels` = `/v1/health.channels` (authoritative order).
- NaN = structurally-absent (`tunnel_*` on a device row, `bgp_*` on an interface row). The
  model builds its mask from `np.isfinite` (`serve/predictor.py predict`). Feed JSON `null`, not 0.
- `_ETC`/`_STC`: entity_type/site_type → the int codes the model's metadata embeddings expect
  (`pa_bah .../data/prepare.py:97 vocabs`). Export the training `vocabs` to match exactly; else
  default 0 (minor embedding degrade, no crash).

**C3 — new `copilot/emulator/real_pa.py`** (the `real_pa(now)` callable):
```python
class RealPA:
    def __init__(self, cfg):
        self.url = cfg.pa_url.rstrip("/"); self.fpr = cfg.pa_target_fpr; self.top_k = cfg.pa_top_k
        self._ch = None

    def _channels(self):
        if self._ch is None:
            self._ch = httpx.get(f"{self.url}/v1/health", timeout=10).json()["channels"]
        return self._ch

    def predict(self, now):                              # matches the real_pa(now) seam
        wins = build_windows(now, self._channels())
        recs = []
        with httpx.Client(timeout=30) as c:
            for ent, w in wins.items():
                r = c.post(f"{self.url}/v1/predict", json={
                    "window": w["window"], "entity_id": ent, "entity_type": w["entity_type"],
                    "vrf": w["vrf"], "window_end_ts": now, "entity_type_code": w["etc"],
                    "site_type_code": w["stc"], "target_fpr": self.fpr, "top_k": self.top_k}).json()
                recs.append((w, r))
        alerting = [(w, r) for w, r in recs if (r.get("decision") or {}).get("alert")]
        if not alerting:
            return None                                  # None = no alert this tick (seam contract)
        alerting.sort(key=lambda wr: -(wr[1]["decision"].get("calibrated_probability") or 0))
        return _to_record(alerting, now)
```
Wire: build the loop with `real_pa=RealPA(cfg).predict`; `emulate.py:298` already calls it.

**`_to_record(alerting, now)` — the §3.3 translation shim (whole schema fix):**

| §3.3 field | source | fill rule |
|---|---|---|
| `device` (top) | winner `entity_id` | **required** — ledger index / forensic window |
| `explanation_ref.alert_id` | — | `emulate._alert_id(entity, cause)` — **required**, idempotency + case identity |
| `risk.*`, `decision.*`, `anomaly.vq_code`, `forecast.quantile_spread_mean` | verbatim | pass through — shapes match §3.3 |
| `n_concurrent` | `len(alerting)` | **restored from the real model** — entities alerting this tick |
| `concurrent_faults` | each `{entity, top cause}` in `alerting` | element 0 = winner → ADR-0014 multi-chat/master |
| `decision.abstain` | — | `False` (real model has no abstain; emulator invented it) |
| `health.drift_state` | — | `"R0"` (real drift = separate unbuilt `GET /v1/health/drift`, doc §3.5) |
| `anomaly.vq_label` | — | `f"vq_{vq_code}"` or cause string |
| `localization` | doc §8 not built | omit / `{}` — forensic LLM loses subgraph + counterfactual |
| `forecast.channels[]` | only spread emitted | omit |

**C4 — `copilot/emulator/predictor.py:48`:** skip `fetch_labels` when `not cfg.emulate_pa`
(real PA self-gates on `decision.alert`; needs no ground-truth labels).

### 4.D  Docs (per CLAUDE.md, same commit)
- `docs/adr/0003-pa-seam-and-emulator.md` — resolve §Open: real PA wired, `emulate_pa=false` live.
- `docs/copilot-build-plan.md` — add the ticket that **owns replacing `_no_real_pa`** (codependency
  rule: the stub had no replacing ticket). Cross-lane edge: `dataapi.export_df`.
- `PLAN.md` phase status.

---

## 5. Known imperfections (state, don't hide)

1. **No localization** in real output (doc §8) → forensic narrative drops the "remove link X →
   −N pts" evidence the emulator faked. No crash; thinner report.
2. **Drift/trust hardcoded R0** until PA builds `GET /v1/health/drift`. Fine — trust gate unbuilt (#38).
3. **`entity_type_code`/`site_type_code`** must match training `vocabs` or embeddings drift → export
   the run's vocab. Low stakes (defaults to 0).
4. **Latency** — N entities × ~2 ms/window per 30 s tick. Hundreds fit; if the lab grows, batch or
   move to the graph-snapshot path (doc §6.2).
5. **Graph model deferred** — better risk numbers, broken gate + snapshot assembly. Baseline first.

---

## 6. Verification (before flipping `emulate_pa=false`)

```bash
# 1. channel order matches
curl :8001/v1/health | jq .channels          # 28 names == export.py column subset, in order

# 2. window builder yields 168×28 per entity
python -c "from copilot.window.build import build_windows; \
  w=build_windows(NOW, CH); e=next(iter(w)); print(len(w), len(w[e]['window']))"   # expect 168

# 3. one real predict round-trips through emulate.persist() with alert_id + device set
```

Net: **~3 new/edited copilot files + 1 dataapi extractor + deploy the baseline PA service.**
The seam and the shared schema do the heavy lifting.
