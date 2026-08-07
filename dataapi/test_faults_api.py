"""test_faults_api.py -- /faults/* routes with run_scenario mocked (no docker).

The mocked run_scenario blocks on the cancel Event so a fault stays "active"
until reverted -- exactly how the real one holds for `duration`. No lab needed.
"""
import threading
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import app
import faults_api
import orchestrator  # on sys.path via faults_api's insert


@pytest.fixture
def client(monkeypatch):
    # Fake run_scenario: block on cancel so the target reads as busy until
    # revert (or the test process ends). Never touches docker/VM.
    def fake_run_scenario(name, target, severity="medium", duration=90,
                          dry_run=False, cancel=None, status=None, buildup=None):
        if status is not None:  # mimic the real buildup->impact transition
            status.update(phase="impact", lead=45.0,
                          t_impact="2099-01-01T00:00:45+00:00")
        if cancel is not None:
            cancel.wait(timeout=30)
        return {"scenario_id": f"{name}-{target}-fake"}

    monkeypatch.setattr(faults_api.orchestrator, "run_scenario", fake_run_scenario)
    faults_api._ACTIVE.clear()
    return TestClient(app.app)


def test_scenarios_lists_all(client):
    body = client.get("/faults/scenarios").json()
    names = {s["name"] for s in body["scenarios"]}
    assert "congestion" in names and len(names) == 21
    assert all(s["default_duration"] == 90 for s in body["scenarios"])


def test_inject_registers_active(client):
    r = client.post("/faults/inject", json={"scenario": "congestion", "target": "ce_branch1"})
    assert r.status_code == 200
    sid = r.json()["scenario_id"]
    assert r.json()["status"] == "injecting"
    active = client.get("/faults/active").json()
    assert [a["scenario_id"] for a in active] == [sid]
    assert active[0]["target"] == "ce_branch1"


def _poll(client, pred, timeout=5.0):
    """Poll /active until pred(row0) or timeout; return the last row seen."""
    row = None
    for _ in range(int(timeout / 0.01)):
        rows = client.get("/faults/active").json()
        row = rows[0] if rows else None
        if row is not None and pred(row):
            return row
        time.sleep(0.01)
    raise AssertionError(f"condition never met; last row={row}")


def test_active_reports_lifecycle_phase(client, monkeypatch):
    # #85: /faults/active projects the real phase + lead + future t_impact,
    # observed through HTTP across the buildup->impact transition.
    gate = threading.Event()  # held in buildup until the test releases it

    def staged(name, target, severity="medium", duration=90,
               dry_run=False, cancel=None, status=None, buildup=None):
        status.update(phase="buildup", lead=45.0, t_impact="2099-01-01T00:00:45Z")
        gate.wait(timeout=5)
        status.update(phase="impact")
        if cancel is not None:
            cancel.wait(timeout=30)
        return {"scenario_id": f"{name}-{target}-fake"}

    monkeypatch.setattr(faults_api.orchestrator, "run_scenario", staged)

    sid = client.post("/faults/inject",
                      json={"scenario": "congestion", "target": "ce_branch1"}).json()["scenario_id"]

    # buildup: phase + lead + a future t_impact all visible over HTTP.
    row = _poll(client, lambda r: r["lead"] is not None)
    assert row["phase"] == "buildup"
    assert row["lead"] == 45.0
    assert datetime.fromisoformat(row["t_impact"]) > datetime.fromisoformat(row["started_at"])

    # impact: worker advances the phase; endpoint reflects it lock-free.
    gate.set()
    assert _poll(client, lambda r: r["phase"] == "impact")["phase"] == "impact"

    client.post(f"/faults/revert/{sid}")
    assert client.get("/faults/active").json() == []    # reverting drops the row


def test_active_exposes_type_and_severity(client):
    # #96: pre-impact fidelity source -- /faults/active must carry the fault
    # type/cause + severity on a LIVE entry (before any /labels row exists), so
    # #91 can build a §3.3 salvage record + a stable base alert_id during buildup.
    client.post("/faults/inject",
                json={"scenario": "congestion", "target": "ce_branch1", "severity": "high"})
    row = client.get("/faults/active").json()[0]
    assert row["severity"] == "high"                              # == inject param
    assert row["type"] == orchestrator.SCENARIO_TYPES["congestion"]
    # type served now == the type the t_end /labels row will carry (same base id).
    assert row["type"] == "congestion"


def test_scenario_types_match_specs():
    # #96 drift guard: SCENARIO_TYPES is the pre-impact source; each entry must
    # equal the type its scen_ fn puts in spec (which _label_row writes at t_end).
    for name in orchestrator.SCENARIOS:
        target = sorted(faults_api._POOLS[name])[0]
        spec = orchestrator.SCENARIOS[name](target, "medium", 90)
        assert spec["type"] == orchestrator.SCENARIO_TYPES[name], name


def test_double_inject_same_target_409(client):
    client.post("/faults/inject", json={"scenario": "congestion", "target": "ce_branch1"})
    r = client.post("/faults/inject", json={"scenario": "tunnel_degrade", "target": "ce_branch1"})
    assert r.status_code == 409


def test_unknown_scenario_404(client):
    r = client.post("/faults/inject", json={"scenario": "nope", "target": "ce_branch1"})
    assert r.status_code == 404


def test_invalid_role_422(client):
    # congestion targets CEs; a P router is the wrong role.
    r = client.post("/faults/inject", json={"scenario": "congestion", "target": "p3"})
    assert r.status_code == 422


def test_revert_unknown_id_404(client):
    r = client.post("/faults/revert/does-not-exist")
    assert r.status_code == 404


def test_revert_active_removes_it(client):
    sid = client.post("/faults/inject",
                      json={"scenario": "congestion", "target": "ce_branch1"}).json()["scenario_id"]
    r = client.post(f"/faults/revert/{sid}")
    assert r.status_code == 200
    assert client.get("/faults/active").json() == []


# --- R59-T3: buildup->impact->hold->revert state machine (orchestrator, no docker) ---

def test_overlay_flag_only_on_tunnel_ramp_spokes():
    # the 6 formerly-inert faults all carry the overlay flag now (R59-T5/#64).
    for n in ("congestion", "tunnel_degrade", "asymmetric_loss", "brownout",
              "hub_spoke_congest", "controller_drift"):
        assert orchestrator.SCENARIOS[n]("ce_branch1", "medium", 60).get("overlay") is True
    # ...iface_down / control-plane / backbone faults do not.
    for n in ("bgp_flap", "policy_drift", "node_failure"):
        assert not orchestrator.SCENARIOS[n]("ce_branch1", "medium", 60).get("overlay")


@pytest.fixture
def fake_lab(monkeypatch):
    """Replace docker/HTTP with recorders so run_scenario runs off-lab and fast."""
    injectors = []

    class FakeNetem:
        def __init__(self, *a, **k):
            self.calls = []
            injectors.append(self)

        def apply(self): self.calls.append("apply")
        def revert(self): self.calls.append("revert")
        def ramp(self, *a, **k): self.calls.append("ramp")

    posts = []

    class FakeOverlay:
        def __init__(self, site, ft, lead, dur, sev):
            self.site, self.ft, self.lead_s = site, ft, lead

        def apply(self): posts.append(("apply", self.site, self.ft))
        def revert(self): posts.append(("revert", self.site))

    monkeypatch.setattr(orchestrator.inj, "NetemImpair", FakeNetem)
    monkeypatch.setattr(orchestrator, "_OverlayInjector", FakeOverlay)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda s: None)  # skip 30-60s buildup+hold
    monkeypatch.setattr(orchestrator, "write_label", lambda row: None)
    return injectors, posts


def test_state_machine_happy_path(fake_lab):
    injectors, posts = fake_lab
    row = orchestrator.run_scenario("congestion", "ce_branch1", duration=1)
    assert injectors[0].calls == ["apply", "revert"]                 # fired at impact, reverted
    assert posts == [("apply", "ce_branch1", "congestion"),          # overlay posted then cleared
                     ("revert", "ce_branch1")]
    assert row["impact_method"] == "overlay_lead"
    assert 30.0 <= row["lead_time"] <= 60.0                          # lead floored to [30,60]


def test_early_revert_during_buildup_skips_fire(fake_lab):
    injectors, posts = fake_lab
    cancel = threading.Event()
    cancel.set()  # already reverting before buildup completes
    row = orchestrator.run_scenario("congestion", "ce_branch1", duration=1, cancel=cancel)
    assert injectors[0].calls == []                                  # physical action never fired
    assert posts == [("apply", "ce_branch1", "congestion"),          # overlay still cleared
                     ("revert", "ce_branch1")]
    assert row["error"] == "early_revert_before_impact"              # not a real injection


def test_status_dict_tracks_phases(fake_lab):
    # #85: run_scenario reports buildup->impact->reverting into the status dict.
    st = {}
    row = orchestrator.run_scenario("congestion", "ce_branch1", duration=1, status=st)
    assert st["phase"] == "reverting"                    # ends reverting
    assert 30.0 <= st["lead"] <= 60.0                    # buildup carried the lead
    assert st["t_impact"] == row["t_impact"]            # ...and the computed impact time


def test_status_dict_early_revert_never_impacts(fake_lab):
    st = {}
    cancel = threading.Event()
    cancel.set()
    orchestrator.run_scenario("congestion", "ce_branch1", duration=1, cancel=cancel, status=st)
    assert st["phase"] == "reverting"                    # buildup -> reverting, impact skipped


def test_non_overlay_gets_lead_but_no_overlay(fake_lab):
    injectors, posts = fake_lab
    # core_congestion is a backbone netem fault: lead + physical injection, no overlay.
    row = orchestrator.run_scenario("core_congestion", orchestrator._ABRS[0], duration=1)
    assert injectors[0].calls == ["apply", "revert"]
    assert posts == []                                             # no overlay for a non-overlay fault
    assert row["impact_method"] == "modelled"
    assert 30.0 <= row["lead_time"] <= 60.0                        # lead is universal, buildup still runs
    assert row["t_impact_ramp"] is None                           # ...but no calibrated ramp emitted


def test_hub_spoke_congest_overlays_the_spoke(fake_lab):
    # hub_spoke_congest injects on (and overlays) the spoke it targets — the only
    # place the controller can observe it (netem/overlay fold per spoke site).
    injectors, posts = fake_lab
    row = orchestrator.run_scenario("hub_spoke_congest", "ce_branch2", duration=1)
    assert injectors[0].calls == ["apply", "revert"]
    assert posts == [("apply", "ce_branch2", "hub_spoke_congest"),
                     ("revert", "ce_branch2")]
    assert row["impact_method"] == "overlay_lead"
    assert row["device"] == "ce_branch2"          # label joins telemetry on device


def test_overlay_post_failure_still_injects(fake_lab, monkeypatch):
    injectors, posts = fake_lab

    class BoomOverlay:
        def __init__(self, site, ft, lead, dur, sev): self.lead_s = lead
        def apply(self): raise RuntimeError("controller 400")
        def revert(self): posts.append(("revert",))  # must NOT be reached

    monkeypatch.setattr(orchestrator, "_OverlayInjector", BoomOverlay)
    row = orchestrator.run_scenario("congestion", "ce_branch1", duration=1)
    assert injectors[0].calls == ["apply", "revert"]               # real fault still injected
    assert posts == []                                             # dropped overlay, nothing to clear
    assert "overlay_post_failed" in (row["error"] or "")
    # buildup is universal, so timing stands; but no ramp was emitted -> modelled
    assert 30.0 <= row["lead_time"] <= 60.0
    assert row["impact_method"] == "modelled" and row["t_impact_ramp"] is None
