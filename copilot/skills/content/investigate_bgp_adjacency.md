---
name: investigate_bgp_adjacency
description: BGP session down or flapping (bgp_flap, node_failure) — pairs runbook-bgp-adjacency-down.
---
Faults this skill covers: `bgp_flap` (transient clears, recovers fast) and `node_failure` (a
node's bgpd dies, recovers slow — watchfrr respawns it ~90s later). A dead node stays **silent**
about its own death; only its peers log the drop.

1. Search logs for `ADJCHANGE`. If the suspect looks dead (unresponsive, not merely flapping),
   search fabric-wide, not scoped to the suspect — the dead stay silent, so its peers' "neighbor
   <suspect> Down" logs are the only trace. If the suspect is live and just suspected of flapping,
   scope the search to that device.
2. Classify by what step 1 found — pick exactly one:
   - **No `ADJCHANGE` hits anywhere** → the symptom lives outside BGP adjacency. Hand off to the
     tunnel or OSPF skill.
   - **A burst that clears on its own** → `bgp_flap`, already recovered. Done.
   - **A drop that persists, or a fabric-wide "neighbor down" burst** → `node_failure` suspected.
     Continue to step 3.
3. Check fan-out with `bgp_peer_established` (a per-PE COUNT of Established peers, PE-only —
   absent on a CE; each client PE peers both route reflectors):
   - Suspect is a CE → skip this metric, the step-1 logs already carry the evidence. Continue to
     step 4.
   - Suspect is a PE → query the suspect and the other client PEs / both route reflectors.
     - **Narrow** (only the suspect's own peers — its 2 RRs + its CEs — drop) → confirmed
       `node_failure`, single node. Continue to step 4.
     - **Wide** (every client PE drops by 1) → a route reflector died, not the suspect —
       `rr_failure`. Hand off to the rr-cascade skill.
4. Map blast radius: `walk_topology_graph device=<suspect> hops=<n>` — which CEs behind the dead
   node lost reachability.
5. Cite the runbook: `search_runbooks query="bgp adjacency down"`, bound the window from its label.

Both faults recover on their own: `bgp_flap` the moment the transient clears, `node_failure` ~90s
later when watchfrr respawns bgpd — wider and slower, not permanent.
