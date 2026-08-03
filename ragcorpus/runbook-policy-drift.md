# Runbook — policy / controller drift (soft, no down event)

> RAG seed. Ties to fault scenarios `policy_drift` and `controller_drift`.

## Symptom

Nothing goes *down*. Path selection or failover behaviour quietly drifts off
policy: a CE's CORP VRF starts preferring the wrong exit, or the SD-WAN
controller stops failing a site over when it should. No link-down, no BGP
session drop, no netem impairment — the damage is a *decision* changing, so
these are the hardest faults to spot without the label. Both are `modelled`
(no clean single-metric crossing); the event/label is the reliable signal.

## Telemetry signature

- **Metrics**:
  - `policy_drift` — no dedicated metric. `bgp_vrf_prefix_count{device,vrf}`
    may not even move (routes still present, just less-preferred). Confirm in
    `show bgp vrf vrf_CORP` (local-pref lowered), not in a gauge.
  - `controller_drift` — `sdwan_controller_drift_active{site}` rises to 1
    for the drifted site (labelled `site`, not `device`); failover that should
    fire does not.
- **Events** (`/events`): `policy_drift` emits a soft-clear BGP ADJ line on
  the CE (route-map reapplied), no Down/Up bracket. `controller_drift` is
  posted straight to the controller (`/fault/drift`) — the tell is the
  drift-active metric plus absent failover, not a router log.
- **Dataset rows**: `is_fault=true`, `impact_method=modelled`, `probe=null`,
  `lead_time≈3` (`policy_drift`) / `≈2` (`controller_drift`). `device` is the
  CE. `policy_drift` targets any CE (CORP VRF is on all 34); `controller_drift`
  targets a hub CE (`ce_hub{1..6}`).

## Triage

1. Confirm there is **no** down event — a soft drift with a hard link/BGP
   drop in the same window is a different fault; classify that first.
2. `policy_drift`: on the CE, `vtysh -c "show bgp vrf vrf_CORP"` — check
   local-pref on the CORP prefixes (drift lowers it: `int(100 - 60*severity)`,
   i.e. 76 / 58 / 40 for low / medium / high). Compare against the site's
   route-policy baseline.
3. `controller_drift`: query `sdwan_controller_drift_active{site="<hub>"}`;
   check the SD-WAN controller for a live drift override on the site
   (`latency_threshold_mult` = 5 / 10 / 99 for low / medium / high — high
   effectively pins failover off).
4. Bound the window from `/labels`; for both the label is the dependable
   evidence.

## Likely causes (lab scenarios)

- **`policy_drift`** — `PolicyDrift` reapplies a CORP route-map with a lowered
  local-pref (`vrf_CORP`), drifting path selection; reverts at `t_end`.
- **`controller_drift`** — a `latency_threshold_mult` override POSTed to the
  SD-WAN controller (`/fault/drift`, TTL = duration + 30 s) suppresses failover
  for the site; cleared via `/fault/drift/clear` at `t_end`.

## Resolution

Both self-revert in the lab (route-map restored / controller override
cleared). In production: for `policy_drift`, audit the VRF route-policy
local-pref against intent and reapply the correct map; for `controller_drift`,
clear the stale threshold override so failover can fire — and treat suppressed
failover as a masking risk (a real underlay degradation can hide behind it).
