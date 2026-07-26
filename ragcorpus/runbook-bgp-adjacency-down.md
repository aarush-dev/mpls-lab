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
- **Metrics** (`/metrics`, PE nodes only — not emitted for CE):
  `bgp_peer_established{device}` (distinct Established peer count on the
  default instance) drops; an RR (`pe1`/`pe2`) failure collapses it
  cluster-wide. `bgp_vrf_prefix_count{device,vrf}` (summed per-AFI/SAFI RIB
  entries) drops for the affected VRF.
- **Dataset rows**: `is_fault=true`, `fault_type=bgp_flap` (or `node_failure`)
  on the affected `device` window; `impact_method=modelled` (no single clean
  metric — confirm via the event stream).
- Transient prefix withdrawal can show as a brief reachability/flow dip on
  dependent sites.

## Triage

1. Identify device + neighbor from the Loki ADJCHANGE lines (`/events?device=`).
2. On the device: `vtysh -c "show bgp summary"` (default instance) and
   `vtysh -c "show bgp vrf <VRF> summary json"` (per-VRF; CE/PE BGP runs
   inside `vrf_CORP`/`vrf_VOICE`/`vrf_GUEST`, not the default instance).
3. Check whether it is a flap (self-recovering, repeated ADJCHANGE) vs a hard
   `bgpd` failure (process gap → `node_failure`).
4. Correlate `t_start`/`t_impact` from `/labels` to bound the impact window.

## Likely causes (lab scenarios)

- **`bgp_flap`** — repeated `clear bgp` churn; self-recovers in seconds.
- **`node_failure`** — `bgpd` killed; watchfrr polls for respawn up to 60 s,
  then forces a restart (total window observed ~60-90 s).
- **`policy_drift`** — not a down event but a local-pref change causing
  route-selection drift (see the policy-drift runbook).

## Resolution

Flaps self-clear. For a stuck session: `vtysh -c "clear bgp vrf <VRF>
<neighbor>"` (a bare `clear bgp <neighbor>` with no VRF clause is a no-op —
CE and per-VRF PE sessions do not run on the default BGP instance). For a
dead daemon, confirm watchfrr restarted it (`pidof bgpd`); if not, restart
FRR. Lab fault injectors revert automatically at `t_end`.
