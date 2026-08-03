"""test_faults_api.py -- /faults/* routes with run_scenario mocked (no docker).

The mocked run_scenario blocks on the cancel Event so a fault stays "active"
until reverted -- exactly how the real one holds for `duration`. No lab needed.
"""
import pytest
from fastapi.testclient import TestClient

import app
import faults_api


@pytest.fixture
def client(monkeypatch):
    # Fake run_scenario: block on cancel so the target reads as busy until
    # revert (or the test process ends). Never touches docker/VM.
    def fake_run_scenario(name, target, severity="medium", duration=90,
                          ramp_steps=6, dry_run=False, cancel=None):
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
