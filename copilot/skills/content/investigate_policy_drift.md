---
name: investigate_policy_drift
description: Soft policy/controller drift, no down event (policy_drift, controller_drift) — pairs runbook-policy-drift.
---
Faults: `policy_drift` (CORP VRF prefers the wrong exit) and `controller_drift` (SD-WAN
failover suppressed). Nothing goes down — a DECISION drifts, the hardest to spot without the
label. Both `modelled`.

1. Confirm there is NO down event in the window first — a hard drop alongside is a different
   fault; classify that.
2. `policy_drift`: no gauge moves. The tell is lowered local-pref on CORP prefixes
   (`int(100 − 60·severity)` = 76 / 58 / 40 for low / med / high). Confirm via the runbook
   method, not a metric.
3. `controller_drift`: `query_metrics pattern=sdwan_controller_drift_active` — labelled `site`
   (NOT `device`), rises to 1 for the drifted hub site; the failover that should fire does not.
4. `search_runbooks query="policy controller drift"`; the drift-active metric + label are the
   evidence, absent failover is the symptom.
