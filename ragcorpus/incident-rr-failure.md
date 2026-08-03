# Incident report — RR failure

> RAG seed. Fill from the data API (`/metrics`, `/events`, `/flows`, `/labels`,
> `/datasets`). Keep concise; one incident per file.

- **Incident ID:** INC-20260802-01
- **Detected (UTC):** 2026-08-02T03:14:07Z
- **Device(s):** pe1
- **Site type / VRF:** provider core — RR, all VRFs (CORP/VOICE/GUEST) affected cluster-wide
- **Entity:** bgpd process / iBGP RR sessions to pe3-pe12 (+ pe2)
- **Severity:** null (severity_inert — process-kill fault; injector ignores severity)
- **Fault type:** rr_failure

## Timeline (UTC)

| t | event |
|---|-------|
| t_start  2026-08-02T03:14:07Z | `kill -9 bgpd` on pe1 |
| t_impact 2026-08-02T03:14:10Z | `bgp_peer_established` collapses cluster-wide (modelled +3s) |
| t_end    2026-08-02T03:15:42Z | watchfrr respawns bgpd, sessions re-establish |

- **Lead time (s):** 3.0

## Telemetry evidence

- **Metrics:** `bgp_peer_established{device="pe1"}` drops out entirely — dead bgpd
  means the sidecar scrape fails, so no sample (not a measured 0). The observable is
  each RR client (pe3-pe12) seeing its own count fall by 1 as the pe1 session drops.
- **Events:** bgpd process gap in Loki (no ADJCHANGE logged from the dead
  process itself); watchfrr respawn log line ~90s later.
- **Flows:** brief VPNv4 prefix-propagation stall across all VRFs, no clean
  flow-volume signature (routes withdrawn, not traffic dropped outright).
- **Label:** `type=rr_failure`, `scenario_id=rr_failure-pe1-9c1a2e04`

## Root cause

bgpd killed on route reflector pe1. RRs are `pe1`/`pe2`; killing one collapses
iBGP for every client that peers it (all 10 RR clients), not just pe1's local
sessions — this is why rr_failure is device-exclusive (no other test can share
pe1's routing daemon mid-outage).

## Resolution & follow-up

watchfrr detects the dead process and respawns bgpd within ~90s; sessions
re-establish automatically (confirmed via `vtysh -c "show bgp summary"` on
pe3-pe12). No manual action needed in the lab. Follow-up: production RR pairs
should never share a failure domain (rack/power) — a single rr_failure here
already takes out cluster-wide propagation.
