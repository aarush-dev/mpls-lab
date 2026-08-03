---
name: investigate_rr_bgp_cascade
description: RR death or BGP cascade, wide blast radius (rr_failure, bgp_cascade) — pairs runbook-rr-bgp-cascade.
---
Faults: `rr_failure` (a Route Reflector's `bgpd` dies) and `bgp_cascade` (a hub CE takes
repeated clears). Both EXCLUSIVE (run alone), higher blast radius than a plain `bgp_flap`.
Both `modelled`.

1. Scope the fan-out: `search_logs pattern=ADJCHANGE`. One RR's `bgpd` gap dropping peers
   cluster-wide = `rr_failure`; repeated clears on a single hub CE = `bgp_cascade`.
2. `rr_failure`: `query_metrics pattern=bgp_peer_established` — each client PE peers with BOTH
   RRs (`pe1`+`pe2`), so one RR's death drops every client's count by ONE, not to zero (the
   surviving RR holds the mesh). A full stall needs both RRs down.
3. `bgp_cascade`: no attributable metric (`sdwan_path_changes_total` is fabric-wide RNG noise
   — do NOT cite it). The signal is the ADJCHANGE burst count on the hub CE (1 / 3 / 5 for
   low / med / high).
4. `walk_topology_graph device=<hub-CE>` for the spokes behind a cascading hub.
