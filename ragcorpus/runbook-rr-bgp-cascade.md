# Runbook — RR failure & BGP cascade (catastrophic control-plane)

> RAG seed. Ties to fault scenarios `rr_failure` and `bgp_cascade`. Both are
> **exclusive** (run alone, never overlapped) — higher blast radius than the
> transient `bgp_flap` in the BGP-adjacency runbook.

## Symptom

A control-plane event with wide reach: a Route Reflector's `bgpd` dies and
every client PE loses one of its two RR sessions — VPNv4 redundancy collapses
cluster-wide (the surviving RR keeps reflecting) until watchfrr respawns it; or a hub CE
takes repeated BGP session clears in a row, thrashing the RIB and forcing many
path-switches on every spoke sitting behind that hub. Neither leaves a clean
single-metric crossing — both are `modelled`; the Loki ADJCHANGE stream plus
the label are the evidence.

## Telemetry signature

- **Metrics** (PE nodes only):
  - `rr_failure` — `bgp_peer_established{device}` dips **cluster-wide**: each
    client PE peers with both RRs (`pe1`+`pe2`), so one RR's death drops every
    client's Established count by one (not to zero — the surviving RR holds the
    mesh). A full VPNv4 propagation stall needs both RRs down.
  - `bgp_cascade` — no attributable metric. `sdwan_path_changes_total` is
    fabric-wide, unlabelled and RNG-driven — **do not** use it as evidence.
    The signal is the ADJCHANGE burst count on the hub CE.
- **Events** (`/events`): `rr_failure` shows a `bgpd` process gap on the RR
  then a watchfrr restart line. `bgp_cascade` shows a burst of `BGP ADJCHANGE`
  clears on the hub CE (`count` = 1 / 3 / 5 for low / medium / high, ~8 s apart).
- **Dataset rows**: `is_fault=true`, `impact_method=modelled`, `probe=null`.
  `rr_failure` `device` ∈ {`pe1`, `pe2`} (the RRs), `lead_time≈3`,
  `severity_inert` (process kill ignores severity). `bgp_cascade` `device` ∈
  `ce_hub{1..6}`, `lead_time≈2`.

## Triage

1. Scope the blast radius from the ADJCHANGE fan-out (`/events?device=`): one
   RR's `bgpd` gap collapsing peers **cluster-wide** = `rr_failure`; repeated
   clears on a single **hub CE** = `bgp_cascade`.
2. `rr_failure`: on the RR, `pidof bgpd` (did watchfrr respawn it?) and
   `vtysh -c "show bgp summary"`; on a client PE, confirm
   `bgp_peer_established` recovered after the RR came back.
3. `bgp_cascade`: on the hub CE, `vtysh -c "show bgp vrf <VRF> summary"` —
   confirm sessions re-established and the RIB settled (no residual churn).
4. Bound the window from `/labels`.

## Likely causes (lab scenarios)

- **`rr_failure`** — `ProcessKill` on the RR's `bgpd`; VPNv4 propagation
  stalls cluster-wide until watchfrr restarts it (~seconds), then re-converges.
- **`bgp_cascade`** — `BgpFlap` clears the hub CE's session N times (severity
  scales `count`); each clear forces a path re-select and RIB re-walk.

## Resolution

Both self-recover in the lab (watchfrr respawns `bgpd` / the flap sequence
ends). In production: for a dead RR, confirm watchfrr restarted `bgpd`
(`pidof bgpd`) and that clients re-established — a wedged RR is a full FRR
restart; a second RR should have absorbed the mesh (verify redundancy). For a
cascading hub CE, stop the source of the clears, then confirm the RIB settled
and dependent spokes re-homed.
