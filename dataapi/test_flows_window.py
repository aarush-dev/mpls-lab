"""Self-check: the /flows endpoint honours the window (ADR-0002/0015 prereq for I1).

Prior art: check_dataset.py (assert + __main__, no framework). Seam under test: the
HTTP /flows handler, with sources.flow_rows spied so no live nfacctd/docker is needed
(the source's since/until scoping is already covered by export.py's use of it).
Run (from dataapi/):  python3 test_flows_window.py
"""
from fastapi.testclient import TestClient

import app as app_mod
import sources


def _spy_client():
    """Replace flow_rows with a spy that records its kwargs and returns them as a row."""
    seen = {}

    def fake_flow_rows(limit=500, device=None, since=None, until=None):
        seen.update(limit=limit, device=device, since=since, until=until)
        return [{"device": device, "since": since, "until": until}]

    sources.flow_rows = fake_flow_rows          # app.py calls sources.flow_rows(...)
    return TestClient(app_mod.app), seen


def test_start_end_reach_the_flow_source():
    client, seen = _spy_client()
    r = client.get("/flows", params={"device": "r1", "start": 100, "end": 200, "limit": 5})
    assert r.status_code == 200
    # the window the caller asked for must scope the fetch itself, not be dropped
    assert seen["since"] == 100 and seen["until"] == 200
    assert seen["device"] == "r1" and seen["limit"] == 5


def test_window_optional_defaults_to_none():
    # no start/end -> no window scoping (unchanged legacy behaviour: newest N)
    client, seen = _spy_client()
    r = client.get("/flows", params={"device": "r1"})
    assert r.status_code == 200
    assert seen["since"] is None and seen["until"] is None


def test_forensic_end_caps_the_fetch():
    # a frozen forensic window passes end=T_snapshot as `until`, so the source cannot
    # be asked for flows newer than T (the freeze guard itself is R3; I1 delivers the
    # plumbing that makes it possible).
    client, seen = _spy_client()
    t_snapshot = 1_700_000_000
    client.get("/flows", params={"device": "r1", "start": t_snapshot - 600, "end": t_snapshot})
    assert seen["until"] == t_snapshot


def _run():
    test_start_end_reach_the_flow_source()
    test_window_optional_defaults_to_none()
    test_forensic_end_caps_the_fetch()
    print("dataapi /flows window self-check OK")


if __name__ == "__main__":
    _run()
