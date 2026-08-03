# Incident report — Policy drift

> RAG seed. Fill from the data API (`/metrics`, `/events`, `/flows`, `/labels`,
> `/datasets`). Keep concise; one incident per file.

- **Incident ID:** INC-20260801-05
- **Detected (UTC):** 2026-08-01T11:05:40Z
- **Device(s):** ce_branch12
- **Site type / VRF:** branch — CORP
- **Entity:** eBGP session route-map (local-preference)
- **Severity:** low
- **Fault type:** policy_drift

## Timeline (UTC)

| t | event |
|---|-------|
| t_start  2026-08-01T11:05:37Z | route-map applied on ce_branch12, lowers local-pref |
| t_impact 2026-08-01T11:05:40Z | route-selection drift on CORP; soft-clear ADJ event |
| t_end    2026-08-01T11:06:40Z | route-map removed |

- **Lead time (s):** 3.0

## Telemetry evidence

- **Metrics:** no clean single-metric signal — `bgp_vrf_prefix_count{device="ce_branch12",vrf="CORP"}`
  stays flat (prefix count unchanged, only path preference shifts).
- **Events:** soft-clear ADJCHANGE line + local-pref change log entry in Loki
  right after t_start.
- **Flows:** possible suboptimal-path traffic shift on CORP; no volume drop.
- **Label:** `type=policy_drift`, `scenario_id=policy_drift-ce_branch12-5a08e912`,
  `impact_method=modelled`.

## Root cause

A CE VRF route-map lowered local-preference on the CORP eBGP session, causing
route selection to prefer a different (non-optimal) path without any session
down event — this is why it needs the BGP-adjacency runbook's "not a down
event" caveat rather than the flap triage steps.

## Resolution & follow-up

Route-map removed at t_end, soft-clear reapplies the original preference,
path reverts. Confirm via `vtysh -c "show bgp vrf CORP"` that the preferred
path is restored. No user-visible impact observed in the lab run (low
severity), but production policy changes on CORP should route through change
review given the silent path shift.
