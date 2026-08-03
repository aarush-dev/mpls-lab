---
name: investigate_ospf_core
description: OSPF backbone events — node/POP/partition/SRLG/congestion/asymmetry/gray — pairs runbook-ospf-core.
---
Eight core faults: `p_node_failure`, `pop_isolation`, `core_partition`, `srlg_cut`,
`core_congestion`, `ospf_area_flap`, `path_asymmetry`, `gray_failure`. All `modelled`.

1. Read the OSPF event fan-out: `search_logs pattern=OSPF`. One peer = single link; all
   peers of a node = `p_node_failure`; all inter-area peers of a POP = `pop_isolation`; a split
   area-0 = `core_partition`; a correlated pair = `srlg_cut`.
2. Latency climb with NO neighbor-down → `core_congestion` (P-P netem ramp) or `gray_failure`
   (sub-BFD loss — logs NOTHING; absence is the signature). Split forward/return latency →
   `path_asymmetry`.
3. Repeated SPF runs (`query_metrics pattern=ospf_spf_last_executed_ms`) → `ospf_area_flap`.
4. `gray_failure` has no metric AND no event — the label is the only signal; abstain on a
   metric claim (see `when_to_abstain`).

Customer VPNv4 usually survives (redundant mesh + PE dual-homing); transit path/latency moves.
