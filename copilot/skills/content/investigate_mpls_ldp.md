---
name: investigate_mpls_ldp
description: MPLS underlay / LDP flap (mpls_underlay_failure, ldp_session_flap) — pairs runbook-mpls-ldp.
---
Faults: `mpls_underlay_failure` (P-router core link down) and `ldp_session_flap`. Both sit in the
underlay, one layer below the L3VPN — classify between these two, never a BGP event.

1. `search_logs device=<P-router> pattern=ldp` for the flap, then a separate
   `search_logs device=<P-router> pattern=ospf` for a neighbor-down bracket. `pattern` matches one
   literal substring per call — `ldp|ospf` in a single call matches nothing, so issue the two
   searches separately. An "Interface down" / "Interface up" bracket in the OSPF search names
   `mpls_underlay_failure`; LDP Down/Up with no such bracket names `ldp_session_flap`. Done when
   you can state which of the two this is.
2. Corroborate with `query_metrics device=<P> pattern=ospf_neighbor_state` only when step 1's OSPF
   search came back without a bracket — the bracket alone already proves the hard-down. The pusher
   polls every 30s against a 15–45s fault window plus ~1s reconverge, so a real 1→0 crossing often
   lands between samples; a flat gauge here is inconclusive, not counter-evidence.
3. `walk_topology_graph(device=<P>, hops=1)` to name the neighbouring P routers, then
   `query_metrics pattern=mpls_lsp_count` on each one returned — their LSPs re-signal too. Done
   when every neighbour from the walk has been queried.
4. `search_runbooks query="mpls ldp underlay"`. The log event stream and the label from step 1 are
   the reliable evidence; the metrics in steps 2–3 corroborate.
