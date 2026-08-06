"""copilot.e2e.pipeline -- end-to-end validation harness for the live pipeline.

Fires everything in sequence after fault injection and dumps every seam so a one-off run is
inspectable:  network sim -> fault injection -> PA emulator -> copilot initial diagnosis.

Everything is REAL: real containerlab faults (run_scenario), the real /labels timeline off
dataapi, the real emulator seam (predict_once), the real Forensic trigger (poll_once) opening
real cases against a real live-adapter drain + real LLM (get_llm/get_adapter). Nothing stubbed.

What it proves (issue #48 / ADR-0014, cause-keyed alert_id):
  Two overlapping faults A,B (A ends first).  While both are active the primary cause is stable,
  so the churning n_concurrent (1->2->1) re-emits the SAME alert_id -> ledger no-op -> opens 0
  extra cases (the ADR-0002 freeze holds).  When A's window ends the primary refines to B's cause
  -> fresh alert_id -> the trigger forks a NEW case.  Expected: exactly 2 cases, churn opened 0.

Each seam is written to <out>/checkpoint_{1,2,3}_*.json AND printed, so you can stop and read the
data the moment a run looks wrong.

PRECONDITION: the sim + dataapi + copilot config (LLM reachable) must be up --
    ./sim-up.sh && ./copilot-up.sh          # bring the lab + telemetry + dataapi live
Run:
    python3 -m copilot.e2e.pipeline                          # defaults: congestion + bgp_flap on ce_branch1
    python3 -m copilot.e2e.pipeline --scenario-b tunnel_degrade --out /tmp/run1
Exits nonzero if the issue-48 invariant fails (2 cases / churn 0), like the verify_* family.
"""
import argparse
import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone

from copilot.api.app import (get_adapter, get_kg, get_llm, get_retriever, get_skills)
from copilot.config import load
from copilot.emulator.emulate import fault_type, fetch_labels
from copilot.emulator.predictor import predict_once
from copilot.forensic.case import make_handler
from copilot.forensic.trigger import Cursor, poll_once
from copilot.memory import Ledger


def _dump(path: str, obj) -> None:
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2, default=str)
    print(f"  wrote {path}")


def _fire_faults(scen_a, tgt_a, dur_a, scen_b, tgt_b, dur_b, severity):
    """Fire A and B as concurrent REAL faults (threads); B outlives A so the primary cause refines
    A->B when A's window ends. Returns nothing -- both scenarios have appended their label rows to
    faults/labels.jsonl by the time both threads join. Errors surface via `errs`."""
    from faults.orchestrator import run_scenario
    errs = []

    def go(name, target, duration):
        try:
            run_scenario(name, target, severity=severity, duration=duration)
        except Exception as e:  # noqa: BLE001 -- collect, report after join
            errs.append(f"{name}/{target}: {e!r}")

    ta = threading.Thread(target=go, args=(scen_a, tgt_a, dur_a))
    tb = threading.Thread(target=go, args=(scen_b, tgt_b, dur_b))
    ta.start(); tb.start()          # ponytail: start together -- windows overlap, dur_b>dur_a => A ends first
    ta.join(); tb.join()
    if errs:
        sys.exit("fault injection failed: " + "; ".join(errs))


def _tick_grid(labels, step_s):
    """UTC 'now' stamps every step_s over [min t_start, max t_end] of the given labels -- the same
    ISO ...Z shape run_predictor emits, so _active_at's lexical compare and _epoch both parse it."""
    starts = [datetime.fromisoformat(l["t_start"]) for l in labels]
    ends = [datetime.fromisoformat(l["t_end"]) for l in labels]
    t, end = min(starts), max(ends)
    grid = []
    while t <= end:
        grid.append(t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        t += timedelta(seconds=step_s)
    return grid


def _git_sha():
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001 -- provenance is best-effort
        return None


def run(scen_a, tgt_a, dur_a, scen_b, tgt_b, dur_b, severity, out):
    os.makedirs(out, exist_ok=True)
    cfg = load()
    base_url = cfg.dataapi_url

    # run manifest: everything needed to reproduce/replay this run (params + cfg + provenance).
    # Written up front so a killed run still self-describes; the tail (labels, result) lands at end.
    manifest = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "params": {"scenario_a": scen_a, "target_a": tgt_a, "dur_a": dur_a,
                   "scenario_b": scen_b, "target_b": tgt_b, "dur_b": dur_b, "severity": severity},
        "cfg": {"dataapi_url": base_url, "emulate_pa": cfg.emulate_pa,
                "error_profile": cfg.error_profile, "predict_interval_s": cfg.predict_interval_s},
        "artifacts": {"log": "<stdout>", "labels": "checkpoint_1_labels.json",
                      "ticks_summary": "checkpoint_2_ticks.json", "ticks_full": "ticks_full.jsonl",
                      "result": "checkpoint_3_cases.json", "ledger": "ledger.db",
                      "cursor": "forensic-cursor.json", "cases": "cases/<id>/"},
    }
    _dump(os.path.join(out, "run_manifest.json"), manifest)

    # --- precondition: dataapi live (raises loudly if the lab is down) ---
    before = {l["scenario_id"] for l in fetch_labels(base_url)}
    print(f"dataapi live at {base_url} ({len(before)} existing labels)")

    # === Stage 1: fault injection (two overlapping REAL faults) ===
    print(f"\n[1] firing REAL faults: {scen_a}/{tgt_a} ({dur_a}s) + {scen_b}/{tgt_b} ({dur_b}s) ...")
    _fire_faults(scen_a, tgt_a, dur_a, scen_b, tgt_b, dur_b, severity)
    all_labels = fetch_labels(base_url)
    ours = [l for l in all_labels if l["scenario_id"] not in before]
    if len(ours) != 2:
        sys.exit(f"expected 2 new labels from our two scenarios, got {len(ours)}: "
                 f"{[l['scenario_id'] for l in ours]}")
    ours.sort(key=lambda l: l["t_start"])       # match prediction()'s active[0] = first-in-list order
    _dump(os.path.join(out, "checkpoint_1_labels.json"), ours)
    for l in ours:
        print(f"    label {l['scenario_id']}: {l['type']} on {l.get('device')} "
              f"[{l['t_start']} .. {l['t_end']}]")

    # === Stage 2: emulate + diagnose, replayed tick-by-tick through an ISOLATED ledger/trigger ===
    # ponytail: harness owns its OWN ledger/cursor/cases so its counts are clean and it never
    # collides with the production predictor/trigger daemons writing the shared ledger.db.
    ledger = Ledger(os.path.join(out, "ledger.db"))
    cursor = Cursor(os.path.join(out, "forensic-cursor.json"))
    real_handle = make_handler(live_adapter=get_adapter(cfg), llm=get_llm(cfg), cfg=cfg,
                               cases_root=os.path.join(out, "cases"),
                               retriever=get_retriever(cfg), skills=get_skills(cfg), kg=get_kg(cfg))
    fired = []                                  # (tick, now, cause, alert_id, case_dir)

    def handle(record, window):                 # wrap the real handler to log each fork
        cid = real_handle(record, window)
        fired.append((record, cid))
        return cid

    print(f"\n[2] replaying emulate->diagnose over {scen_a}+{scen_b} window "
          f"@ {cfg.predict_interval_s}s (real LLM: {get_llm(cfg).__class__.__name__}) ...")
    ticks = []
    # ticks_full.jsonl: the FULL §3.3 record every tick emitted (incl. the immaterial re-emits the
    # ledger collapses) -- the complete emulator output stream, so any tick is replayable verbatim.
    full = open(os.path.join(out, "ticks_full.jsonl"), "w")
    for i, now in enumerate(_tick_grid(ours, cfg.predict_interval_s)):
        n_before = len(fired)
        rec = predict_once(cfg, ours, ledger, now, drift_tick=i)     # PA emulator seam
        n_new = poll_once(cfg, ledger, cursor, handle)               # Forensic trigger -> case
        row = {"tick": i, "now": now,
               "record_emitted": rec is not None,
               "primary_cause": fault_type(rec),
               "alert_id": (rec or {}).get("explanation_ref", {}).get("alert_id"),
               "n_concurrent": (rec or {}).get("n_concurrent"),
               "cases_opened_this_tick": len(fired) - n_before}
        ticks.append(row)
        full.write(json.dumps({"tick": i, "now": now, "cases_opened_this_tick": row["cases_opened_this_tick"],
                               "record": rec}, default=str) + "\n")
        if n_new:
            print(f"    tick {i} {now}: FORK -> {row['primary_cause']} "
                  f"(alert_id={row['alert_id']}, n_concurrent={row['n_concurrent']})")
    full.close()
    _dump(os.path.join(out, "checkpoint_2_ticks.json"), ticks)

    # === Stage 3: issue-48 invariant ===
    cases = [{"case_dir": cid, "cause": fault_type(rec),
              "alert_id": rec["explanation_ref"]["alert_id"]} for rec, cid in fired]
    total = len(cases)
    # churn ticks: n_concurrent==2 and NOT the tick that opened a case -> must have opened 0
    churn_opened = sum(t["cases_opened_this_tick"] for t in ticks
                       if t.get("n_concurrent") == 2 and t["cases_opened_this_tick"]
                       and t is not next((x for x in ticks if x["cases_opened_this_tick"]), None))
    summary = {"total_cases": total, "expected_cases": 2,
               "churn_opened_extra": churn_opened,
               "cases": cases, "pass": total == 2 and churn_opened == 0}
    _dump(os.path.join(out, "checkpoint_3_cases.json"), summary)

    manifest.update({"ended_utc": datetime.now(timezone.utc).isoformat(),
                     "scenario_ids": [l["scenario_id"] for l in ours], "result": summary})
    _dump(os.path.join(out, "run_manifest.json"), manifest)

    print(f"\n[3] issue-48 invariant:")
    for c in cases:
        print(f"    case {c['case_dir']}  cause={c['cause']}")
    print(f"    total_cases={total} (expected 2)   churn_opened_extra={churn_opened} (expected 0)")
    if not summary["pass"]:
        sys.exit(f"FAIL: {summary}")
    print("PASS: two overlapping faults -> exactly 2 cases; n_concurrent churn opened 0.")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scenario-a", default="congestion")
    p.add_argument("--target-a", default="ce_branch1")
    p.add_argument("--dur-a", type=int, default=60)
    p.add_argument("--scenario-b", default="bgp_flap")
    p.add_argument("--target-b", default="ce_branch1")
    p.add_argument("--dur-b", type=int, default=180)       # > dur-a so B outlives A (cause refines A->B)
    p.add_argument("--severity", default="high")
    p.add_argument("--out", default="pipeline-run")
    a = p.parse_args(argv)
    run(a.scenario_a, a.target_a, a.dur_a, a.scenario_b, a.target_b, a.dur_b, a.severity, a.out)


if __name__ == "__main__":
    main()
