# Runbook — BGP adjacency down / flapping

> RAG seed. Ties to fault scenarios `bgp_flap` and `node_failure`.

## Symptom

BGP session to a neighbor drops or flaps; prefixes withdrawn then relearned;
routing table churn. In a VRF this means a customer site loses (then regains)
reachability across the L3VPN.

## Telemetry signature (what the model / on-call sees)

- **Loki events** (`/events`): bursts of `bgp` app log lines with
  `BGP ADJCHANGE` / neighbor Up/Down; for `node_failure` also a `bgpd` process
  gap until watchfrr respawns it.
- **Dataset rows**: `is_fault=true`, `fault_type=bgp_flap` (or `node_failure`)
  on the affected `device` window; `impact_method=modelled` (no single clean
  metric — confirm via the event stream).
- Transient prefix withdrawal can show as a brief reachability/flow dip on
  dependent sites.

## Triage

1. Identify device + neighbor from the Loki ADJCHANGE lines (`/events?device=`).
2. On the device: `vtysh -c "show bgp summary"` and
   `vtysh -c "show bgp vrf <VRF> ipv4 unicast"`.
3. Check whether it is a flap (self-recovering, repeated ADJCHANGE) vs a hard
   `bgpd` failure (process gap → `node_failure`).
4. Correlate `t_start`/`t_impact` from `/labels` to bound the impact window.

## Likely causes (lab scenarios)

- **`bgp_flap`** — repeated `clear bgp` churn; self-recovers in seconds.
- **`node_failure`** — `bgpd` killed; watchfrr restarts it (60–600 s window).
- **`policy_drift`** — not a down event but a local-pref change causing
  route-selection drift (see the policy-drift runbook).

## Resolution

Flaps self-clear. For a stuck session: `vtysh -c "clear bgp <neighbor>"`. For a
dead daemon, confirm watchfrr restarted it (`pidof bgpd`); if not, restart FRR.
Lab fault injectors revert automatically at `t_end`.
