# Incident report — P-node failure (catastrophic core)

> RAG seed. Fill from the data API (`/metrics`, `/events`, `/flows`, `/labels`,
> `/datasets`). Keep concise; one incident per file.

- **Incident ID:** INC-20260802-04
- **Detected (UTC):** 2026-08-02T18:20:05Z
- **Device(s):** p15
- **Site type / VRF:** provider core (pop4) — no single VRF, whole POP mesh affected
- **Entity:** all core interfaces of p15 (`MultiLinkFault`)
- **Severity:** null (severity_inert — multi-link-set fault; injector ignores severity)
- **Fault type:** p_node_failure

## Timeline (UTC)

| t | event |
|---|-------|
| t_start  2026-08-02T18:20:04Z | all core links of p15 brought down atomically |
| t_impact 2026-08-02T18:20:06Z | `ospf_neighbor_state` = 0 for every p15 peer (modelled +2s) |
| t_end    2026-08-02T18:23:00Z | links restored, OSPF reconverges |

- **Lead time (s):** 2.0

## Telemetry evidence

- **Metrics:** `ospf_neighbor_state{device="p15"}` drops to 0 for all peers
  simultaneously; `mpls_lsp_count` shifts on p15's neighbours (p13, p14, p16)
  as LSPs re-signal around the failed node.
- **Events:** burst of OSPF neighbor-down lines across all p15 adjacencies at
  once (the multi-link signature — distinguishes this from a single-link
  `mpls_underlay_failure`), followed by SPF recalculation logs.
- **Flows:** cross-POP flows transiting p15 reroute via the intra-POP mesh and
  PE dual-homing; brief flow disruption on pop4 traffic during reconvergence.
- **Label:** `type=p_node_failure`, `scenario_id=p_node_failure-p15-c4e29f17`

## Root cause

`MultiLinkFault` took down every core interface of p15 at once, simulating a
full node failure (vs. a single interface flap). All of p15's OSPF
adjacencies drop in the same instant — the fan-out is the tell.

## Resolution & follow-up

Links restored at t_end; OSPF reconverges via the intra-POP mesh and inter-POP
chords. Confirm `mpls_lsp_count` on p13/p14/p16 returns to baseline. No PE
customer downtime expected — dual-homing to two PEs per POP absorbs a single
P-node loss.
