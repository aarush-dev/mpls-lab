"""faults_api.py -- real fault-injection HTTP routes for the Grafana browser plugin.

Thin router over faults/orchestrator.py. Injection runs in a daemon thread so the
POST returns immediately; an in-memory registry (Lock-guarded) tracks what is
live so the same device can't be double-injected and a running fault can be cut
short early.

Security boundary: these routes run `docker exec` into the lab. They are scoped
to localhost + the CORS allow-list in app.py -- that IS the auth (per brief).

Routes (mounted in app.py):
  GET  /faults/scenarios          -- the 21 scenario types + valid roles
  POST /faults/inject             -- spawn a scenario (non-blocking) -> scenario_id
  GET  /faults/active             -- currently-running injections
  POST /faults/revert/{id}        -- early-revert a running injection
"""
import os
import sys
import threading
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# orchestrator lives in faults/ (sibling of dataapi/ under the repo root).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "faults"))
import orchestrator  # noqa: E402

router = APIRouter(prefix="/faults")


# --- valid target roles per scenario (derived from the orchestrator's pools) ---
# Ground truth = CAMPAIGN_POOLS + the two whole-region scenarios excluded from
# the random campaign (they still take a POP target). No hardcoded device lists.
_POOLS = dict(orchestrator.CAMPAIGN_POOLS)
_POOLS["pop_isolation"] = orchestrator._POPS
_POOLS["core_partition"] = orchestrator._POPS


def _role_of(target):
    """Coarse role of a device/target id, matching the pool naming."""
    t = str(target)
    for r in ("ce_branch", "ce_hub", "ce_dc"):
        if t.startswith(r):
            return r
    if t.startswith("pe"):
        return "pe"
    if t.startswith("pop"):
        return "pop"
    if t.startswith("srlg"):
        return "srlg"
    if t.startswith("p"):
        return "p"
    return "unknown"


VALID_ROLES = {n: sorted({_role_of(x) for x in pool}) for n, pool in _POOLS.items()}
DEFAULT_DURATION = 90  # run_scenario's default hold (orchestrator.run_scenario)


# --- in-memory registry of live injections (Lock-guarded) ----------------------
_LOCK = threading.Lock()
_ACTIVE = {}  # scenario_id -> {scenario, target, type, severity, started_at, duration, cancel, status}


class InjectBody(BaseModel):
    scenario: str
    target: str
    severity: str = "medium"
    duration: int = DEFAULT_DURATION


def _runner(scenario_id, name, target, severity, duration, cancel, status):
    """Thread body: run the scenario, then always drop it from the registry."""
    try:
        orchestrator.run_scenario(name, target, severity=severity,
                                  duration=duration, cancel=cancel, status=status)
    finally:
        with _LOCK:
            _ACTIVE.pop(scenario_id, None)


@router.get("/scenarios")
def scenarios():
    return {"scenarios": [
        {"name": n,
         "description": fn.__doc__.strip().splitlines()[0] if fn.__doc__ else "",
         "valid_roles": VALID_ROLES.get(n, []),
         "default_duration": DEFAULT_DURATION}
        for n, fn in orchestrator.SCENARIOS.items()
    ]}


@router.post("/inject")
def inject(body: InjectBody):
    if body.scenario not in orchestrator.SCENARIOS:
        raise HTTPException(404, f"unknown scenario '{body.scenario}'")
    if _role_of(body.target) not in VALID_ROLES.get(body.scenario, []):
        raise HTTPException(422, f"target '{body.target}' role not valid for "
                                 f"'{body.scenario}' (valid roles: {VALID_ROLES[body.scenario]})")
    # scenario_id in the orchestrator's own format. ponytail: this is the API's
    # tracking handle; run_scenario mints its OWN id for the written label row, so
    # the two don't match -- the label is keyed on device+time regardless.
    scenario_id = f"{body.scenario}-{body.target}-{uuid.uuid4().hex[:8]}"
    cancel = threading.Event()
    with _LOCK:
        if any(e["target"] == body.target for e in _ACTIVE.values()):
            raise HTTPException(409, f"'{body.target}' is already being injected")
        # status: shared mutable dict the worker fills with the live phase
        # (buildup/impact/reverting) via GIL-atomic key writes -- its keys are
        # read lock-free in /active. run_scenario owns lead + t_impact.
        status = {"phase": "buildup"}
        _ACTIVE[scenario_id] = {
            "scenario": body.scenario, "target": body.target,
            # type/cause = the ground-truth type the t_end /labels row will carry
            # (SCENARIO_TYPES), served now so #91 can build a pre-impact §3.3 record
            # + a stable base alert_id. severity is the inject param (dropped until
            # now). Both are additive; existing /active consumers are unaffected.
            "type": orchestrator.SCENARIO_TYPES[body.scenario],
            "severity": body.severity,
            "started_at": orchestrator.iso(orchestrator.now_utc()),
            "duration": body.duration, "cancel": cancel, "status": status,
        }
    threading.Thread(target=_runner, daemon=True, args=(
        scenario_id, body.scenario, body.target, body.severity,
        body.duration, cancel, status)).start()
    return {"scenario_id": scenario_id, "status": "injecting"}


@router.get("/active")
def active():
    with _LOCK:
        entries = list(_ACTIVE.items())
    # status keys are read lock-free (worker writes them GIL-atomically); only the
    # _ACTIVE snapshot above takes the lock.
    # ponytail: lead/t_impact are null in the sub-ms window between inject and the
    #   worker's first status write (phase seeded "buildup" at inject so it never is).
    return [{"scenario_id": sid, "scenario": e["scenario"], "target": e["target"],
             "type": e["type"], "severity": e["severity"],
             "started_at": e["started_at"], "duration": e["duration"],
             "phase": e["status"].get("phase"),
             "lead": e["status"].get("lead"),
             "t_impact": e["status"].get("t_impact")}
            for sid, e in entries]


@router.post("/revert/{scenario_id}")
def revert(scenario_id: str):
    with _LOCK:
        entry = _ACTIVE.pop(scenario_id, None)
    if entry is None:
        raise HTTPException(404, f"no active injection '{scenario_id}'")
    entry["cancel"].set()  # wakes run_scenario's hold; its finally reverts the lab
    return {"scenario_id": scenario_id, "status": "reverting"}
