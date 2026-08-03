---
name: investigate_ospf_core
description: OSPF backbone events — node/POP/partition/SRLG/flap/congestion/asymmetry/gray — pairs runbook-ospf-core.
---
Eight core faults, all `modelled`: `p_node_failure`, `pop_isolation`, `core_partition`, `srlg_cut`,
`ospf_area_flap`, `core_congestion`, `path_asymmetry`, `gray_failure`.

1. `walk_topology_graph(device, hops)` on the alerting device first — it returns the
   status-enriched neighbourhood, the POP/area membership no raw OSPF log line carries.
2. `search_logs pattern=OSPF` for the adjacency fan-out, read against that membership: one peer
   down = single link; every peer of one node = `p_node_failure`; every inter-area peer of one
   POP = `pop_isolation`; a correlated SET of 2+ links sharing one conduit, possibly spanning
   different devices = `srlg_cut`; a crossing-edge cut-set bisecting area-0 into two islands
   (can be 3+ links across 3+ nodes) = `core_partition`.
3. Repeated SPF runs (`query_metrics pattern=ospf_spf_last_executed_ms`) mean `ospf_area_flap`
   only when bracketed by repeated adjacency Down/Up in the same logs — every fault in step 2
   also re-triggers SPF, so a jump alone proves nothing.
4. `core_congestion`, `path_asymmetry`, `gray_failure` carry no telemetry crossing at all — no
   core-link and no per-direction latency series exists to query. Steps 1-2 are the only
   discriminator; when the fan-out is silent, abstain (`when_to_abstain`) instead.

Customer VPNv4 usually survives (redundant mesh + PE dual-homing); transit path/latency moves.
