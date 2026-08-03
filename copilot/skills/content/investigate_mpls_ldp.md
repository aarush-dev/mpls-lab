---
name: investigate_mpls_ldp
description: MPLS underlay / LDP flap (mpls_underlay_failure, ldp_session_flap) — pairs runbook-mpls-ldp.
---
Faults: `mpls_underlay_failure` (P-router core link down) and `ldp_session_flap`. Underlay, one
layer below the L3VPN — no customer-facing BGP event.

1. `search_logs device=<P-router> pattern=ldp` — LDP Down/Up = a flap; then
   `search_logs device=<P-router> pattern=ospf` — a single OSPF neighbor-down bracket = a hard
   link down. `pattern` is one substring per call (not an OR), so split the two signals.
2. `query_metrics device=<P> pattern=ospf_neighbor_state` — may flip 1 → 0, but the pusher
   polls every 30 s against a 15–45 s window + ~1 s reconverge, so the drop often falls
   between samples (`modelled`). Don't hinge the finding on a crossing you can't see.
3. `query_metrics pattern=mpls_lsp_count` on the neighbouring P routers — LSPs re-signal.
4. `search_runbooks query="mpls ldp underlay"`; the event stream + label are the reliable
   evidence.
