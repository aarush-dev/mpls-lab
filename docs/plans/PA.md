# Pipeline Scope and Exact Outputs

This document answers three questions about the plan in this repository: **what is actually in scope**
(predictive pipeline vs. dataset/simulation generator), **exactly what the predictive analysis
pipeline outputs** per prediction, and **exactly what the serving API returns** after taking inference
from the models.

---

## 1. Scope map — what each file covers

The plan is **not** only the predictive analysis pipeline. Roughly a third of it is dataset/simulation
generator work, and that work is a prerequisite, not scope creep.

| File | Covers |
|---|---|
| `Research_Training_Strategy_Prompt.md` | The original brief. Explicitly states *"exploit the existing dataset generation system... not redesign it"* — pipeline-only intent, stated up front. |
| `research/01`–`09` + `REFERENCES.md` | **Pure predictive pipeline.** Training strategy, representation learning, expert objectives (forecast / hazard / classification), graph learning, VAE, meta-learner, drift/continual learning, validation/deployment, novelty. |
| `research/10` | Compute/data sizing — straddles both (model FLOPs, and how much data the generator must produce). |
| `research/11`, `research/12` | Execution plans — straddle both, since early days of any schedule are blocked on the generator being correct. |
| `research/13` | **Pure dataset/simulation.** A P0/P1/P2 diff against the actual `generate.py` / `orchestrator.py` / `export.py`. |
| `research/14` | **Pure dataset/simulation.** An audit of three shipped Parquet files against `13`'s requirements — found three blockers. |
| `PROMPT_fix_dataset_blockers.md` | **Pure dataset/simulation**, and explicitly a *separate repo's* task — self-contained instructions to paste into a Claude Code session in the simulation/generator repository. |
| `SIMULATION_AND_DATASET_NOTES (1).md` | Source documentation of the existing generator — a reference, not a plan. |
| `TODO.md` | Consolidates both halves into one schedule. |

### Why the dataset side had to be in scope

`research/14`'s audit found that the shipped data makes the hazard head's central claim — a real
time-to-impact distribution — literally unlearnable:

- `lead_time_s` had only **9 distinct values** (CV = 0.03, against a documented CV ≈ 0.71)
- `vrf` was **100% null**
- Three error-counter columns were a **perfect synthetic-vs-real detector**

No amount of model-side cleverness fixes mislabeled data, so the plan grew to include a fix targeted at
the generator repo, kept separate from anything touching the model.

**If the plan should be strictly pipeline-only going forward**, the cut line is clean: treat `13`, `14`,
and `PROMPT_fix_dataset_blockers.md` as prerequisite inputs delivered by a separate workstream, and the
pipeline plan proper is `01`–`09` plus the model-training portions of `10`–`12`.

---

## 2. Exact output of the predictive analysis pipeline

Per sliding window, the model emits **one joint object**, not several independent scores.

### 2.1 The raw model output

A single softmax over `K·J + 1` logits (`research/03` §3.3.2): a joint PMF `p_{k,j}(x)` over
(fault type `k`, time bin `j`), plus a "no impact in horizon" mass `p∅`.

Everything below is a **deterministic readout of this one distribution** — they cannot disagree with
each other by construction:

| Derived quantity | Formula | This *is* |
|---|---|---|
| Cumulative incidence `F_k(h\|x)` | `Σ_{j≤h} p_{k,j}` | **the classification output** — P(fault type k occurs within horizon h) |
| Survival `S(h\|x)` | `1 − Σ_k F_k(h\|x)` | P(nothing happens by h) |
| Discrete hazard `λ_k(j\|x)` | `p_{k,j} / S(j-1\|x)` | instantaneous risk at bin j given survival to j-1 |
| Time-to-impact | `argmin_h {F_k(h) ≥ 0.5·F_k(J)}` + IQR from `F_k` | **the estimate-time-to-impact output** — a median + interval, not a point guess |

Fault types are **multi-label and 2-level hierarchical** (coarse family — congestion / routing-instability
/ tunnel-degradation / device-fault / link-fault — then fine type within family), so concurrent faults are
natively representable, not forced into one class.

### 2.2 Forecast trajectory

`research/03` §3.1 — quantile forecast of each telemetry channel, 7 quantiles (`0.05…0.95`), monotone by
construction, over horizon `H = 4×` the target lead time. Its quantile spread (`q90−q10`) and residual are
**also fed forward** into the hazard head, the drift monitor, and the meta-learner — an input as much as
an output.

### 2.3 Localization

`research/04` §4.8 — which device/interface/tunnel/path drove the prediction, via:

- attention attribution
- a GNNExplainer minimal subgraph
- a counterfactual probe ("remove this link, does the risk drop") that converts the prediction into a
  recommended action

### 2.4 Anomaly signal

`research/05` §5.6 — a continuous novelty p-value from the VAE, plus a discrete **VQ incident-vocabulary
token** (and a human-readable label for it) — the symbolic bridge to the offline LLM.

### 2.5 The fused, calibrated decision

`research/06` §6.3 — five heads off one context vector, deliberately built from expert
outputs/uncertainties/drift stats, **not raw telemetry**:

1. **Fused probability** — log-odds fusion across experts
2. **Context-conditioned temperature** — calibration adapts to regime, not a single fixed number
3. **Context-aware decision threshold** — `τ*(u_t)`, cost-sensitive
4. **Decomposed uncertainty** — aleatoric vs. epistemic vs. gate-ensemble variance, reported separately
5. **Abstention flag** — routed to the LLM as "anomalous, no confident call, here's the evidence" rather
   than forced into a guess

### 2.6 Conformal wrapper

`research/06` §6.5 — the whole output is passed through conformal risk control so the *deployed* alert
rate has a **guaranteed FPR bound**, not just a nominally-calibrated one.

### 2.7 What actually reaches the NOC per alert

`research/README.md` §4, the exact packaged payload:

```
prediction + lead time + hazard curve + attribution + VQ incident code
  ─▶ Incident Memory (vector + symbolic)  ─▶ offline LLM copilot
  ─▶ Drift monitors ─▶ graded response ladder ─▶ shadow challenger
```

- The **first branch** (Incident Memory → LLM copilot) is consumed asynchronously for natural-language
  explanation.
- The **second branch** (drift → response ladder → shadow challenger) is a **model-health output**, not
  a per-alert one — it answers "is the model still trustworthy," separately from any individual
  prediction.

---

## 3. Exact FastAPI output — the serving contract

No FastAPI contract exists elsewhere in the plan (`research/*.md`, `TODO.md` were checked for
"fastapi/endpoint/schema/response" — none found). What follows formalizes §2's outputs as concrete JSON,
not a new design.

### 3.1 Two endpoints, not one

The docs are explicit that LLM invocation is off the critical path (`research/09`, latency section) —
inlining it would blow the ~65ms p50 / ~95ms p99 inference budget by an order of magnitude. The contract
splits accordingly:

- **`POST /v1/predict`** — synchronous, returns everything the models compute in one inference pass,
  within the latency budget.
- **`GET /v1/explain/{alert_id}`** — asynchronous, returns the LLM's natural-language explanation once
  Incident Memory retrieval + LLM composition finishes (not bounded by the same SLA).

### 3.2 `POST /v1/predict` — request

```json
{
  "entity_id": "ce_branch5:tun-hub1",
  "entity_type": "tunnel",
  "window_end_ts": "2026-07-31T09:14:30Z",
  "vrf": "CORP"
}
```

### 3.3 `POST /v1/predict` — response

```jsonc
{
  "entity_id": "ce_branch5:tun-hub1",
  "entity_type": "tunnel",
  "vrf": "CORP",
  "window_end_ts": "2026-07-31T09:14:30Z",
  "model_version": "v0.3.1-phi3",
  "inference_latency_ms": 71,

  // ---- 1. Hazard head (research/03 §3.3.2) — one softmax, everything below is a readout of it ----
  "risk": {
    "fault_types": [
      {
        "cause": "congestion",
        "family": "congestion",
        "cumulative_incidence": [                 // F_k(h|x), non-decreasing by construction
          {"horizon_s": 120, "p": 0.04},
          {"horizon_s": 300, "p": 0.31},
          {"horizon_s": 600, "p": 0.58},
          {"horizon_s": 1800, "p": 0.71}
        ],
        "time_to_impact": {
          "median_s": 340,
          "iqr_s": [210, 610]                     // from F_k, not a separate model
        },
        "hazard_curve": [
          {"bin_s": 120, "lambda": 0.04},
          {"bin_s": 300, "lambda": 0.09}
        ]
      },
      {
        "cause": "tunnel_degrade",
        "family": "tunnel-degradation",
        "cumulative_incidence": [ /* ... */ ],
        "time_to_impact": {"median_s": null, "iqr_s": null}   // null = not in top causes this window
      }
    ],
    "survival_curve": [
      {"horizon_s": 120, "p": 0.96},
      {"horizon_s": 1800, "p": 0.22}
    ],
    "p_no_impact_in_horizon": 0.22
  },

  // ---- 2. Forecast (research/03 §3.1) — quantile trajectory, monotone by construction ----
  "forecast": {
    "horizon_steps": 480,
    "channels": [
      {
        "name": "tunnel_latency_ms",
        "quantiles": {
          "0.05": [12.1, 12.4, "...480 values"],
          "0.5":  [14.0, 15.2, "..."],
          "0.95": [22.7, 29.4, "..."]
        }
      },
      {"name": "if_out_discards", "quantiles": { "0.5": ["..."] } }
    ],
    "quantile_spread_q90_q10": 8.9              // early-warning signal, also feeds the meta-learner
  },

  // ---- 3. Localization (research/04 §4.8) ----
  "localization": {
    "attention_attribution": [
      {"relation": "path->link", "hop": 3, "weight": 0.41},
      {"relation": "device->interface", "hop": 1, "weight": 0.22}
    ],
    "minimal_subgraph": {
      "nodes": ["ce_branch5", "pe_hub1", "p_core2"],
      "edges": [{"src": "ce_branch5", "dst": "pe_hub1", "type": "tunnel"}]
    },
    "counterfactual": {
      "edge_removed": {"src": "ce_branch5", "dst": "pe_hub1"},
      "risk_delta": -0.18          // predicted drop in cumulative incidence if this edge were healthy
    }
  },

  // ---- 4. Anomaly / novelty (research/05 §5.6) ----
  "anomaly": {
    "novelty_pvalue": 0.006,
    "vq_code": 142,
    "vq_label": "PE-facing link approaching 85% utilisation, rising",
    "codebook_novelty": 0.03      // rising value = drift signal, feeds 07
  },

  // ---- 5. Fused, calibrated decision (research/06 §6.3-6.5) ----
  "decision": {
    "fused_probability": 0.83,             // log-odds fusion across experts
    "calibrated_probability": 0.79,        // after context-conditioned temperature
    "temperature": 1.34,
    "threshold": 0.62,                     // context-aware tau*(u_t)
    "alert": true,
    "conformal_set_valid_at_fpr": 0.01,    // guaranteed FPR bound, not nominal calibration
    "uncertainty": {
      "aleatoric": 0.09,
      "epistemic": 0.21,
      "gate_ensemble_variance": 0.03
    },
    "abstain": false,
    "expert_weights": {
      "hazard_head": 0.51,
      "forecast": 0.22,
      "graph": 0.15,
      "vae": 0.12
    }
  },

  // ---- 6. Pointer, not payload — explanation is async (see /v1/explain) ----
  "explanation_ref": {
    "alert_id": "alt_9f3a2b",
    "incident_memory_written": true,
    "explanation_status": "pending"
  }
}
```

### 3.3.1 Two seam fields the example above omits (resolved R4a, ADR-0003)

The §3.3 example predates two fields the copilot seam needs. R4a's PA-emulator
(`copilot/emulator/emulate.py`) emits both; recorded here as the seam's ground truth:

- **`health` (top-level)** — `{"drift_state": "R0..R5", "codebook_novelty": <float>}`. The
  faked model-health scalar (ADR-0003 §Nuances: the `research/07` R0–R5 ladder + a rising
  novelty). **Conflict with §3.5, resolved in ADR-0003's favour:** §3.5 says drift belongs on a
  separate `GET /v1/health/drift`, *not* in the per-prediction response. That split is a real-PA
  *latency* optimization (§3.1) — the copilot's seam is ONE record (ADR-0003), with no separate
  endpoint to air-gap and poll, so the emulator folds the scalar into the record. §3.5's
  "deliberately not in the sync response" therefore does **not** bind the copilot seam; it stays
  true only for the real PA's latency-budgeted `/v1/predict`.
- **`n_concurrent` (top-level, `int >= 1`)** — the count of faults concurrently active in the
  window this record covers. Absent from §3.3 but load-bearing for ADR-0014 / ADR-0009 / #25:
  `n_concurrent > 1` → n investigation chats (one per fault) + a master chat that synthesizes.
  The emulator sets it from how many `/labels` faults overlap the window (`prediction` seam).
- **`concurrent_faults` (top-level, `list[{device, cause}]`, len == `n_concurrent`)** — enumerates
  EACH concurrently-active fault, not just the count (#49). Element 0 = the primary (record's
  `device`/top cause). R6b freezes each fault's OWN device window and investigates it per sub-chat,
  so a count alone was not enough — the other faults' devices must be named. `cause` is the reported
  cause (post-error-profile confusion). A lone record self-enumerates (one entry).

### 3.4 `GET /v1/explain/{alert_id}` — response (arrives later)

```jsonc
{
  "alert_id": "alt_9f3a2b",
  "status": "ready",
  "nearest_incidents": [
    {"incident_id": "inc_2026_0231", "code_sequence": [142, 142, 87, 301], "similarity": 0.91}
  ],
  "narrative": "Tunnel ce_branch5→pe_hub1 is following the same congestion signature as inc_2026_0231 (91% match): PE-facing link approaching 85% utilisation and rising. Removing the ce_branch5–pe_hub1 hop from the path would reduce projected risk by 18 points. Estimated SLA breach in 3.5–10 min if unaddressed.",
  "evidence_used": ["minimal_subgraph", "vq_code=142", "counterfactual"]
}
```

### 3.5 What is deliberately *not* in the sync response

- **Raw telemetry** — the meta-learner's context vector (`u_t`) explicitly excludes it (`06` §6.3); the
  response reflects that same discipline.
- **Natural-language text** — only a `pending` pointer; the LLM composition is async per the latency
  budget in `09`.
- **Drift/model-health status** — for the **real PA** that's a separate signal (`R0`–`R5` ladder,
  `07` §7.4), on something like `GET /v1/health/drift`, independent of any single prediction, so the
  latency-budgeted `/v1/predict` stays lean. **This does not bind the copilot seam** (ADR-0003): the
  emulator/copilot record is ONE object, so it folds a `health.drift_state` scalar in (see §3.3.1) —
  no separate endpoint to air-gap. Amended R4a; the loser of the §3.3.1 conflict.
