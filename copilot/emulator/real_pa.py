"""PA-A6 + PA-G4: the real PA client behind the `emulate_pa=false` seam.

`emulate.prediction()` routes `cfg.emulate_pa` -> `real_pa(now)` (emulate.py:298);
this supplies that callable, replacing the `_no_real_pa` placeholder. It turns the
real service's response into the §3.3 Prediction Record the copilot consumes
(`fault_type` / `is_abstain` / `drift_state` / `persist` / forensic case), filling
the fields the real `/v1/predict[/snapshot]` response omits (verified absent in the
model's predictor.py): `device`, `explanation_ref.alert_id`, `n_concurrent` /
`concurrent_faults`, `decision.abstain`, `health.drift_state`, `anomaly.vq_label`.

Graph model (pa_mode=snapshot, default): one `/v1/predict/snapshot` per topology --
all live entities scored in one graph-fused pass. Baseline (pa_mode=temporal):
per-entity `/v1/predict`. Either way: filter to alerting entities, pick the highest
calibrated-probability as the record's primary, enumerate the rest as concurrent.

Self-check:  python3 -m copilot.emulator.test_real_pa
"""
from __future__ import annotations

from copilot.emulator.emulate import _alert_id
from copilot.window.build import build_windows
from copilot.window.vocab import CHANNELS, entity_type_code, site_type_code


def _cause(rec: dict) -> str:
    fts = (rec.get("risk") or {}).get("fault_types") or []
    return fts[0].get("cause", "unknown") if fts else "unknown"


class RealPA:
    """Client for the real PA service. `.predict(now)` matches the `real_pa(now)` seam."""

    def __init__(self, cfg, *, post=None, get=None):
        self.url = cfg.pa_url.rstrip("/")
        self.fpr = cfg.pa_target_fpr
        self.top_k = cfg.pa_top_k
        self.mode = cfg.pa_mode
        self.topology_id = cfg.pa_topology_id
        self._channels = None
        self._post = post          # injectable transport (tests)
        self._get = get

    # -- transport (httpx by default; injected in tests) ----------------------
    def _http_get(self, path):
        import httpx
        r = httpx.get(self.url + path, timeout=10)
        r.raise_for_status()
        return r.json()

    def _http_post(self, path, body):
        import httpx
        r = httpx.post(self.url + path, json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    def channels(self) -> list[str]:
        """The model's channel order. From /v1/health; falls back to the vendored
        CHANNELS if health is unreachable (they must agree -- asserted in PA-A8)."""
        if self._channels is None:
            try:
                self._channels = (self._get or self._http_get)("/v1/health")["channels"]
            except Exception:
                self._channels = CHANNELS
        return self._channels

    def predict(self, now: str):
        """Score the live window ending `now`; return the primary alerting record
        (§3.3) or None if nothing alerts."""
        wins = build_windows(now, self.channels(), etc_of=entity_type_code,
                             stc_of=site_type_code)
        if not wins:
            return None
        dev_by_ent = {e: w["device"] for e, w in wins.items()}
        records = (self._snapshot if self.mode == "snapshot" else self._temporal)(wins, now)
        alerting = [r for r in records if (r.get("decision") or {}).get("alert")]
        if not alerting:
            return None
        alerting.sort(key=lambda r: -(r["decision"].get("calibrated_probability") or 0.0))
        return self._to_record(alerting, dev_by_ent, now)

    # -- serving modes --------------------------------------------------------
    def _snapshot(self, wins: dict, now: str) -> list[dict]:
        entities = [{"entity_id": e, "device": w["device"], "entity_type": w["entity_type"],
                     "vrf": w["vrf"], "window": w["window"],
                     "entity_type_code": w["etc"], "site_type_code": w["stc"]}
                    for e, w in wins.items()]
        body = {"topology_id": self.topology_id, "window_end_ts": now,
                "entities": entities, "target_fpr": self.fpr, "top_k": self.top_k}
        return (self._post or self._http_post)("/v1/predict/snapshot", body).get("records", [])

    def _temporal(self, wins: dict, now: str) -> list[dict]:
        out = []
        for e, w in wins.items():
            body = {"window": w["window"], "entity_id": e, "entity_type": w["entity_type"],
                    "vrf": w["vrf"], "window_end_ts": now, "entity_type_code": w["etc"],
                    "site_type_code": w["stc"], "target_fpr": self.fpr, "top_k": self.top_k}
            out.append((self._post or self._http_post)("/v1/predict", body))
        return out

    # -- translation shim: real response -> §3.3 record -----------------------
    def _to_record(self, alerting: list[dict], dev_by_ent: dict, now: str) -> dict:
        winner = dict(alerting[0])
        win_dev = dev_by_ent.get(winner.get("entity_id"), winner.get("entity_id"))
        cause = _cause(winner)
        winner["device"] = win_dev
        winner["window_end_ts"] = winner.get("window_end_ts") or now
        winner["n_concurrent"] = len(alerting)
        winner["concurrent_faults"] = [
            {"device": dev_by_ent.get(r.get("entity_id"), r.get("entity_id")), "cause": _cause(r)}
            for r in alerting]
        winner.setdefault("decision", {})["abstain"] = False   # real model does not abstain
        anom = winner.setdefault("anomaly", {})
        anom.setdefault("vq_label", f"vq_{anom.get('vq_code')}")
        # real drift lives on a separate (unbuilt) endpoint -> healthy scalar for the seam
        winner["health"] = {"drift_state": "R0", "codebook_novelty": anom.get("anomaly_score", 0.0)}
        winner["explanation_ref"] = {"alert_id": _alert_id(win_dev, cause),
                                     "incident_memory_written": False,
                                     "explanation_status": "pending"}
        return winner


def _no_real_pa(now=None):
    raise RuntimeError("real PA client not wired; build RealPA(cfg).predict and pass as real_pa")
