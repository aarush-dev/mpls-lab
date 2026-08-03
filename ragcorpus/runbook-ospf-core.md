# Runbook — OSPF backbone instability (core / area-0)

> RAG seed. Ties to fault scenarios `p_node_failure`, `pop_isolation`,
> `core_partition`, `srlg_cut`, `core_congestion`, `ospf_area_flap`,
> `path_asymmetry`, and `gray_failure`.

## Symptom

A provider-core event perturbs OSPF: a whole P router dies, a POP is cut off,
the backbone splits, an SRLG conduit drops both its links at once, a backbone
link congests, an inter-POP adjacency flaps, path costs skew one direction, or
a link quietly loses packets with no down event. Customer VPNv4 usually survives
(redundant POP mesh + PE dual-homing) but transit latency and path selection
move.

## Telemetry signature

- **Metrics** (P/PE nodes): `ospf_neighbor_state{device}` = 0 for the affected
  peers — **all at once** for `p_node_failure` (every peer of the node),
  every **inter-area** peer for `pop_isolation`/`core_partition`,
  a correlated **pair** for `srlg_cut`. `ospf_spf_last_executed_ms` jumps on
  repeated SPF runs (`ospf_area_flap`). `mpls_lsp_count` shifts as LSPs reroute.
  `core_congestion` and `gray_failure` leave OSPF **up** — they degrade transit
  without a neighbor-down.
- **Events** (`/events`): bursts of OSPF neighbor-down + SPF-recalc lines
  (fan-out width tells node-loss from single-link). `gray_failure` logs
  **nothing** — absence of an event is the signature.
- **Dataset rows**: all eight are `impact_method=modelled` (`probe=null`) —
  including `gray_failure`, whose sub-BFD loss has no clean single metric *and*
  no event, so the label is its only reliable signal (the core loss is not read
  into SD-WAN tunnel telemetry). `device` is the real epicenter node (the P
  router that failed, or the POP ABR at the cut).
  `pop_isolation`/`core_partition`/`srlg_cut` are **named tests**, excluded from
  the Poisson campaign.

## Triage

1. Read the OSPF event fan-out (`/events?device=`): one peer = single link;
   all peers of a node = `p_node_failure`; all inter-area peers of a POP =
   `pop_isolation`; a split area-0 = `core_partition`.
2. On an epicenter/neighbour: `vtysh -c "show ip ospf neighbor"` +
   `vtysh -c "show ip route ospf"` — which adjacencies dropped, which IA routes
   withdrew.
3. Latency climb with **no** neighbor-down → `core_congestion` (a P-P link
   ramp) or `gray_failure` (sub-BFD loss); split forward/return latency →
   `path_asymmetry` (one-directional OSPF cost shift).
4. Repeated SPF runs / ECMP oscillation → `ospf_area_flap`.
5. Bound the window from `/labels`; for `gray_failure` the label is the only
   dependable evidence.

## Likely causes (lab scenarios)

- **`p_node_failure`** — `MultiLinkFault` downs every core iface of one P router.
- **`pop_isolation`** — cuts all inter-POP links of one POP; the region isolates.
- **`core_partition`** — cuts the ring edge cut-set; area-0 splits into two islands.
- **`srlg_cut`** — both links of one SRLG conduit drop simultaneously (correlated).
- **`core_congestion`** — netem delay+loss ramp on a P-P backbone link; no down.
- **`ospf_area_flap`** — an inter-POP area-0 adjacency flapped repeatedly.
- **`path_asymmetry`** — OSPF cost raised in one direction only; paths diverge.
- **`gray_failure`** — 0.5–2 % sub-BFD loss on a backbone link, no down event.

## Resolution

Structural cuts (`p_node_failure`, `pop_isolation`, `core_partition`,
`srlg_cut`) self-restore at `t_end`; confirm `ospf_neighbor_state` and
`mpls_lsp_count` return to baseline on the neighbours. `core_congestion` /
`gray_failure` clear when netem is removed. In production, `gray_failure` needs
per-link backbone loss counters (not link-state, not tunnel metrics) to catch at
all; `path_asymmetry` needs cost audit on both link ends.
