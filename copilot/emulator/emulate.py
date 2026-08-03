"""copilot.emulator.emulate -- PA-emulator core (R4a, ADR-0003).

The ONLY seam between copilot and the prediction stack is the Prediction Record (PA.md §3.3,
the `/v1/predict` response). While the real PA is unbuilt, this emulator turns a ground-truth
fault label (`/labels`, dataapi -- the same jsonl `faults/labels/labels.jsonl` row) into a
full-fidelity §3.3 record, derived deterministically from that truth. `emulate_pa` (config)
routes the seam: on -> emulator, off -> the real PA endpoint. Flip the flag, no caller change.

Fidelity (ADR-0003): every §3.3 block is present -- risk / forecast / localization / anomaly /
decision -- plus two fields §3.3's example omits, resolved here (see docs/plans/PA.md §3.3):
  - `health.drift_state` -- ADR-0003 wins the §3.5 conflict: the copilot seam is ONE record,
    so the faked model-health scalar rides INSIDE it (no separate /health endpoint), giving the
    trust gate (spec #3 story 14, unbuilt -- flagged on #38) something to distrust.
  - `n_concurrent` -- referenced by ADR-0014/ADR-0009/#25, absent from §3.3: a count of faults
    concurrently active in the window (>=1). n>1 -> n investigation chats + a master (ADR-0014).

Imperfection is realism via `error_profile` (ADR-0003): `oracle` = perfect readout (deterministic
eval, ADR-0017); `light`/`heavy` perturb TTI / abstain / drift, keyed by a hash of the scenario_id
so a run reproduces (no wall-clock, no RNG). The record's numbers are a readout of ground truth --
they cannot disagree with each other, mirroring a real PA's one-softmax discipline (§2.1).

Consumers (this ticket wires the hooks; runtime delivery is downstream):
  - `is_abstain(record)` softens the quality gate (ADR-0008 §Nuances; copilot.agent.gate).
  - `fault_type(record)` steers diagnostic-skill selection (ADR-0012; copilot.agent.loop).
The periodic firing that writes records to the ledger every ~predict_interval_s is R4b (ADR-0014);
the forensic case that threads a frozen record into an investigation chat is R5.

Self-check:  python3 -m copilot.emulator.test_emulate
"""
import hashlib

# coarse fault family (PA.md §2.1 hierarchy) per ground-truth label `type`. The 21 scenarios
# (faults/orchestrator.py) fold into the five §2.1 families; an unmapped type -> device-fault
# (the safe default: "a device is misbehaving, look at it").
_FAMILY = {
    "congestion": "congestion", "core_congestion": "congestion",
    "hub_spoke_congest": "congestion", "brownout": "congestion",
    "bgp_flap": "routing-instability", "bgp_cascade": "routing-instability",
    "ospf_area_flap": "routing-instability", "ldp_session_flap": "routing-instability",
    "policy_drift": "routing-instability", "controller_drift": "routing-instability",
    "rr_failure": "routing-instability",
    "tunnel_degrade": "tunnel-degradation", "mpls_underlay_failure": "tunnel-degradation",
    "gray_failure": "tunnel-degradation",
    "node_failure": "device-fault", "p_node_failure": "device-fault",
    "pop_isolation": "device-fault", "core_partition": "device-fault",
    "srlg_cut": "link-fault", "asymmetric_loss": "link-fault", "path_asymmetry": "link-fault",
}

# ground-truth severity -> the PA's confidence in the call. A real PA is surer of a high-severity
# signature than a low one; the whole record's probabilities read off this one scalar (§2.1).
_CONFIDENCE = {"low": 0.55, "medium": 0.70, "high": 0.85, "critical": 0.95}
_HORIZONS_S = (120, 300, 600, 1800)   # §3.3 cumulative-incidence / survival horizons
_MODEL_VERSION = "emulator-v1"        # PA.md §3.3 model_version slot; the emulator's identity
_THRESHOLD = 0.62                     # §3.3 context-aware decision threshold tau*(u_t)


def family(fault_type: str) -> str:
    """The §2.1 coarse family for a ground-truth label `type` (unmapped -> device-fault)."""
    return _FAMILY.get(fault_type, "device-fault")


def _hash01(seed: str, salt: str = "") -> float:
    """A stable [0,1) from a string -- deterministic across runs (hashlib, not salted hash()).
    Drives error_profile jitter so `light`/`heavy` reproduce without a clock or RNG."""
    h = hashlib.sha1(f"{salt}:{seed}".encode()).hexdigest()
    return (int(h[:8], 16) % 1000) / 1000.0


def _abstain(label: dict, profile: str, conf: float) -> bool:
    """When the PA punts ("anomalous, no confident call" -- §2.5 abstention flag). oracle never
    abstains (ground truth is known); light abstains on a genuinely ambiguous low-severity call;
    heavy abstains whenever confidence is thin -- stressing the quality gate (ADR-0003)."""
    if profile == "oracle":
        return False
    if profile == "heavy":
        return conf < 0.75
    return str(label.get("severity")) == "low"     # light


def _jitter(label: dict, profile: str) -> float:
    """A signed fractional TTI perturbation (0 for oracle; +-, keyed by scenario_id otherwise)."""
    if profile == "oracle":
        return 0.0
    span = 0.5 if profile == "heavy" else 0.2
    return (_hash01(str(label.get("scenario_id", "")), "tti") - 0.5) * 2 * span


_LADDER = ("R0", "R1", "R2", "R3", "R4", "R5")   # ADR-0003 model-health degradation ladder


def _drift(profile: str, tick: int = 0) -> tuple[str, float]:
    """Faked model-health (ADR-0003 §Nuances): the R0-R5 ladder + a rising codebook_novelty.
    oracle = healthy (R0) forever; light/heavy START a rung up (light R1, heavy R3) and CLIMB one
    rung every 3 predictor ticks, capped at R5 (R4b acceptance: the scalar evolves over a run), so
    the (unbuilt) trust gate has a signal that worsens. novelty tracks the rung."""
    if profile == "oracle":
        return ("R0", 0.01)
    idx = min(len(_LADDER) - 1, (1 if profile == "light" else 3) + max(0, tick) // 3)
    return (_LADDER[idx], round(0.05 + 0.03 * idx, 3))


def _confuse(label: dict, profile: str, ftype: str) -> str:
    """The reported cause -- oracle reports the true type; light/heavy occasionally report a
    confusable SIBLING (same §2.1 family, different specific type: right family, wrong call). This
    is the realism that mis-steers skill selection (ADR-0012). Keyed by scenario_id -> deterministic
    per scenario; heavy confuses more often than light."""
    if profile == "oracle":
        return ftype
    sibs = sorted(t for t, f in _FAMILY.items() if f == family(ftype) and t != ftype)
    if not sibs:
        return ftype
    sid = str(label.get("scenario_id", ftype))
    if _hash01(sid, "confuse") < (0.4 if profile == "heavy" else 0.15):
        return sibs[int(_hash01(sid, "pick") * len(sibs)) % len(sibs)]
    return ftype


def _incidence_curve(conf: float) -> list[dict]:
    """A non-decreasing cumulative-incidence F_k(h) rising toward `conf` (§3.3, monotone by
    construction: p scales with the horizon fraction, so later horizons never dip)."""
    return [{"horizon_s": h, "p": round(conf * min(1.0, h / _HORIZONS_S[-1]), 3)}
            for h in _HORIZONS_S]


def _fault_ref(label: dict, profile: str) -> dict:
    """A {device, cause} handle for one concurrently-active fault -- device + the REPORTED cause
    (post-`_confuse`, honest to the error profile). The per-fault detail R6b's fan-out investigates
    over that fault's OWN device window (#49); a count alone could not name the other devices."""
    dev = label.get("device") or (label.get("target") or {}).get("device")
    return {"device": dev, "cause": _confuse(label, profile, str(label.get("type", "unknown")))}


def emulate_record(label: dict, *, error_profile: str = "light",
                   n_concurrent: int = 1, now: str | None = None,
                   drift_tick: int = 0, concurrent_faults: list[dict] | None = None) -> dict:
    """One ground-truth fault `label` -> a full §3.3 Prediction Record (ADR-0003).

    `label` is a `/labels` row (dataapi): {type, target, severity, t_start, t_impact, t_end,
    lead_time, probe, baseline_value, impact_value, signature, device, scenario_id}. `now` is the
    record's window_end_ts (ISO-8601 UTC); defaults to the label's t_impact (occurrence time).
    `n_concurrent` is the count of faults concurrently active in the window (the seam supplies it).
    Every number is a deterministic readout of the label -- oracle is exact, light/heavy perturb.
    """
    dev = label.get("device") or (label.get("target") or {}).get("device")
    true_type = str(label.get("type", "unknown"))
    ftype = _confuse(label, error_profile, true_type)   # reported cause (confusable sibling, ADR-0012)
    conf = _CONFIDENCE.get(str(label.get("severity")), 0.60)
    lead = float(label.get("lead_time") or 300.0)
    tti = max(1.0, lead * (1 + _jitter(label, error_profile)))
    abstain = _abstain(label, error_profile, conf)
    drift_state, novelty = _drift(error_profile, drift_tick)
    sid = str(label.get("scenario_id", dev or ftype))
    incidence = _incidence_curve(conf)
    baseline = float(label.get("baseline_value") or 0.0)
    impact = float(label.get("impact_value") or baseline)
    spread = round(abs(impact - baseline), 3)
    vq = int(hashlib.sha1(ftype.encode()).hexdigest(), 16) % 512

    return {
        "entity_id": dev,
        "entity_type": (label.get("target") or {}).get("entity_type", "device"),
        "vrf": (label.get("target") or {}).get("vrf"),
        "device": dev,
        "window_end_ts": now or label.get("t_impact") or label.get("t_start"),
        "model_version": _MODEL_VERSION,
        "n_concurrent": max(1, int(n_concurrent)),      # invented field (b); >=1 (ADR-0014/0009)
        # #49: enumerate every concurrently-active fault (device + cause), not just the count. The
        # seam supplies the list; a lone record self-enumerates so consumers need no n==1 branch.
        "concurrent_faults": concurrent_faults or [{"device": dev, "cause": ftype}],
        # ---- 1. risk (hazard head, §3.3) -- one curve, everything below reads off it ----
        "risk": {
            "fault_types": [{
                "cause": ftype,
                "family": family(ftype),
                "cumulative_incidence": incidence,
                "time_to_impact": {"median_s": round(tti, 1),
                                   "iqr_s": [round(tti * 0.6, 1), round(tti * 1.4, 1)]},
                "hazard_curve": [{"bin_s": _HORIZONS_S[0], "lambda": incidence[0]["p"]},
                                 {"bin_s": _HORIZONS_S[1],
                                  "lambda": round(incidence[1]["p"] - incidence[0]["p"], 3)}],
            }],
            "survival_curve": [{"horizon_s": c["horizon_s"], "p": round(1 - c["p"], 3)}
                               for c in incidence],
            "p_no_impact_in_horizon": round(1 - conf, 3),
        },
        # ---- 2. forecast (§3.3) -- the label's probe channel, baseline -> impact trajectory ----
        "forecast": {
            "horizon_steps": 480,
            "channels": [{
                "name": label.get("probe") or f"{ftype}_signal",
                "quantiles": {"0.05": [round(baseline * 0.95, 3), round(impact * 0.95, 3)],
                              "0.5": [baseline, impact],
                              "0.95": [round(baseline * 1.05, 3), round(impact * 1.15, 3)]},
            }],
            "quantile_spread_q90_q10": spread,
        },
        # ---- 3. localization (§3.3) -- the ground-truth device drove it ----
        "localization": {
            "attention_attribution": [{"relation": "device->interface", "hop": 1, "weight": 0.6}],
            "minimal_subgraph": {"nodes": [dev] if dev else [], "edges": []},
            "counterfactual": {"edge_removed": {"src": dev, "dst": None},
                               "risk_delta": -round(conf * 0.3, 3)},
        },
        # ---- 4. anomaly (§3.3) -- severity -> novelty, signature = the human label ----
        "anomaly": {
            "novelty_pvalue": round(max(0.001, 1 - conf) / 10, 4),
            "vq_code": vq,
            "vq_label": label.get("signature") or ftype,
            "codebook_novelty": novelty,
        },
        # ---- 5. decision (§3.3) -- fused/calibrated off the one confidence scalar ----
        "decision": {
            "fused_probability": round(conf, 3),
            "calibrated_probability": round(conf * 0.95, 3),
            "temperature": 1.2,
            "threshold": _THRESHOLD,
            "alert": (conf * 0.95) >= _THRESHOLD and not abstain,
            "conformal_set_valid_at_fpr": 0.01,
            "uncertainty": {"aleatoric": round(1 - conf, 3), "epistemic": round(novelty * 2, 3),
                            "gate_ensemble_variance": 0.03},
            "abstain": abstain,
            "expert_weights": {"hazard_head": 0.5, "forecast": 0.2, "graph": 0.18, "vae": 0.12},
        },
        # ---- model-health, resolved conflict (a): folded into the ONE seam record ----
        "health": {"drift_state": drift_state, "codebook_novelty": novelty},
        # ---- pointer, not payload (§3.3) -- explanation is async ----
        "explanation_ref": {"alert_id": f"alt_{sid}", "incident_memory_written": False,
                            "explanation_status": "pending"},
    }


def fault_type(record: dict | None) -> str | None:
    """The top fault cause the record flags -- the context the agent uses to pick a skill
    (ADR-0012, no rigid mapping). None if no record or no fault types."""
    if not record:
        return None
    fts = (record.get("risk") or {}).get("fault_types") or []
    return fts[0].get("cause") if fts else None


def is_abstain(record: dict | None) -> bool:
    """Whether the PA abstained ("anomalous, no confident call") -- softens the gate (ADR-0008)."""
    return bool(record and (record.get("decision") or {}).get("abstain"))


def drift_state(record: dict | None) -> str | None:
    """The PA's model-health rung (ADR-0003 R0-R5 ladder) the trust gate distrusts (spec #3 story
    14; T1). Read from `health.drift_state` -- the location #20 resolved (ADR-0003 folds it into the
    ONE seam record, over PA.md §3.5's separate surface). None if no record / no health block."""
    return (record.get("health") or {}).get("drift_state") if record else None


def to_wire(record: dict) -> dict:
    """Wrap a Prediction Record as an Event-Ledger row (ADR-0009: the ledger holds records too).
    `type`/`ts` are the ledger's index columns; the §3.3 record rides nested + verbatim under
    `record`, so a read round-trips losslessly and cannot collide with the record's own keys."""
    return {"type": "prediction", "ts": record["window_end_ts"], "record": record}


def persist(ledger, record: dict) -> None:
    """Append a Prediction Record to the Event Ledger, idempotent by alert_id (ADR-0009). The
    alert_id is deterministic from the scenario_id, so a re-emit of the same episode is a no-op."""
    ledger.append(record["explanation_ref"]["alert_id"], to_wire(record), device=record.get("device"))


def prediction(cfg, labels: list[dict], *, now: str | None = None,
               real_pa=None, drift_tick: int = 0) -> dict | None:
    """The PA seam (ADR-0003): return a Prediction Record for the fault active `now`, WITHOUT the
    caller knowing which stack produced it. `cfg.emulate_pa` routes:
      True  -> the emulator: pick the ground-truth label active at `now` (t_start <= now <= t_end);
               n_concurrent = how many labels overlap it. None active -> None (no prediction).
      False -> the real PA endpoint (`real_pa(now)` callable, injected in tests). `now` IS the
               §3.2 `window_end_ts` the /v1/predict request needs, so it crosses the seam; the real
               branch resolves the rest of that request (entity_id/vrf) when a PA exists (R-later).
               Unbuilt today -> the default raises a per-call error, surfaced where a real caller
               would read it, not a startup crash.
    The caller invokes prediction() identically under either flag -- the return type is the record
    (or None); only the record's origin differs."""
    if not cfg.emulate_pa:
        return (real_pa or _no_real_pa)(now)
    active = [lb for lb in labels if _active_at(lb, now)] if now else list(labels)
    if not active:
        return None
    return emulate_record(active[0], error_profile=cfg.error_profile,
                          n_concurrent=len(active), now=now, drift_tick=drift_tick,
                          concurrent_faults=[_fault_ref(lb, cfg.error_profile) for lb in active])


def fetch_labels(base_url: str, *, fetch=None) -> list[dict]:
    """The ground-truth `/labels` timeline from dataapi (the emulator's trigger source, ADR-0003)
    -- the rows `prediction`/`emulate_record` consume. `fetch` is the injectable transport seam
    (tests pass a canned one); the default hits dataapi over httpx and raises on a dead endpoint,
    letting the periodic firing (R4b) decide how to handle an outage. ponytail: a bare GET -- the
    firing loop, not this reader, owns cadence/retry."""
    return (fetch or _http_labels)(base_url)


def _http_labels(base_url: str) -> list[dict]:
    import httpx
    r = httpx.get(base_url.rstrip("/") + "/labels", timeout=10)
    r.raise_for_status()
    return r.json().get("rows", [])


def _active_at(label: dict, now: str) -> bool:
    """True if `now` (ISO-8601 UTC) falls in the label's [t_start, t_end] -- a lexical compare is
    chronological for same-offset zero-padded UTC stamps (as the labels are written)."""
    start, end = label.get("t_start"), label.get("t_end")
    return bool(start and end and start <= now <= end)


def _no_real_pa(now=None):
    # ponytail: no real PA exists yet (emulate_pa=false is the post-milestone path). A clear
    # per-call error, mirroring HttpAdapter's transport-fault surface -- not a silent None.
    raise RuntimeError(f"emulate_pa=false but no real PA endpoint is built yet (ADR-0003); "
                       f"a real /v1/predict for window_end_ts={now} lands with the PA stack")
