---
name: investigate_policy_drift
description: Soft policy/controller drift, no down event (policy_drift, controller_drift) — pairs runbook-policy-drift.
---
Faults: `policy_drift` (CORP VRF prefers the wrong exit, on a CE) and `controller_drift`
(SD-WAN failover suppressed, on a hub site). Nothing goes down — a DECISION drifts, silent
without the label. Both `modelled`.

1. Confirm the drift is soft: `search_logs pattern=down` over the window. Zero Down/Up
   brackets confirms soft drift. Any match is a hard drop — classify that fault instead.
2. Branch on which decision drifted: `query_metrics pattern=sdwan_controller_drift_active`,
   NO device filter — the series is labelled `site`, not `device`; a device filter returns
   empty. A row at 1 names the drifted hub site -> `controller_drift`. No row -> `policy_drift`
   on a CE.
3. Confirm the branch, and only that branch:
   - `controller_drift`: the step-2 row IS the confirmation — drift-active = 1 for that site,
     the failover that should fire does not.
   - `policy_drift`: no gauge moves. Confirm via the runbook's `show bgp vrf` method — lowered
     local-pref on CORP prefixes (`int(100 − 60·severity)` = 76 / 58 / 40 for low / med / high).
4. `search_runbooks query="policy controller drift"` for remediation.
