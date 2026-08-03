---
name: investigate_bgp_adjacency
description: BGP session down or flapping (bgp_flap, node_failure) — pairs runbook-bgp-adjacency-down.
---
Faults: `bgp_flap` (transient clears, self-recovers) and `node_failure` (a node dies, all its
sessions drop at once).

1. `search_logs device=<suspect> pattern=ADJCHANGE` — count the BGP clears. A burst that
   self-recovers = `bgp_flap`; a single drop that stays = `node_failure`.
2. `query_metrics device=<suspect> pattern=bgp_peer_established` — a per-PE COUNT of Established
   peers (not a 1/0 flag), emitted for PE nodes only. It drops toward 0 (by however many peers
   that PE holds) on `node_failure`, then recovers. If the suspect is a CE, this series is
   absent — lean on the `ADJCHANGE` logs from step 1 instead.
3. `walk_topology_graph device=<suspect>` — for a dead node, which CEs behind it lost
   reachability.
4. `search_runbooks query="bgp adjacency down"` for the cited method; bound the window from
   the label.

Both self-recover in the lab (flap ends / watchfrr respawns). Distinguish transient (flap)
from structural (node) by fan-out width + persistence.
