# Runbook — MPLS underlay / LDP session instability

> RAG seed. Ties to fault scenarios `mpls_underlay_failure` and
> `ldp_session_flap`.

## Symptom

A provider-core (P/PE) LDP label-distribution session tears down or flaps, or a
P-router core interface goes down. Transit LSPs re-signal over a secondary path;
VPNv4 customer traffic keeps flowing (dual-homing + BFD-assisted reroute) but
sees a brief gap during reconvergence. No customer-facing BGP event — this is an
underlay problem, one layer below the L3VPN.

## Telemetry signature

- **Metrics** (P/PE nodes): `ospf_neighbor_state{device}` for the affected peer
  flips `1 → 0` in FRR on a hard `mpls_underlay_failure` (link down), but the
  LDP/BGP metrics pusher polls every 30 s while the fault window is 15–45 s and
  reconvergence is ~1 s — so the drop often falls between samples and is not a
  dependable crossing (hence `impact_method=modelled`). `mpls_lsp_count` shifts
  on the neighbouring P routers as LSPs re-signal. `ldp_session_flap` is
  transient and self-recovering — it leaves no clean single-metric mark either.
- **Events** (`/events`): Loki `ldp_event=Down/Up` lines for `ldp_session_flap`;
  an OSPF neighbor down/up bracket for `mpls_underlay_failure`.
- **Dataset rows**: `is_fault=true` on the affected `device`; both are
  `impact_method=modelled` (no clean single-metric observable — confirm via the
  event stream). `mpls_underlay_failure` targets a **non-ABR** P router (ABRs
  have no P-PE link and are rejected by the injector).

## Triage

1. From the Loki lines (`/events?device=`) identify the P router + interface (or
   LDP neighbor) and whether it is a **flap** (repeated Down/Up, self-recovers)
   or a **hard link down** (single Down, held until revert).
2. On the device: `vtysh -c "show mpls ldp neighbor"` and
   `vtysh -c "show mpls ldp binding"` — confirm the session state + label bindings.
3. `vtysh -c "show ip ospf neighbor"` — did the underlay adjacency drop
   (link failure) or stay up (pure LDP flap)?
4. Bound the window from `/labels` (`t_start`/`t_impact`/`t_end`).

## Likely causes (lab scenarios)

- **`mpls_underlay_failure`** — `ip link set <iface> down` on a P-router
  P-PE-facing interface; LDP/OSPF reconverge to a secondary path (~1 s with
  BFD), then the injector restores the link at `t_end`.
- **`ldp_session_flap`** — `vtysh clear mpls ldp neighbor` N times (severity
  scales the count); the session is torn and re-established each cycle and
  self-recovers.

## Resolution

Both self-clear in the lab (injector reverts at `t_end`). For a stuck LDP
session in production: verify the underlay OSPF adjacency first (LDP rides on
IGP reachability), then `clear mpls ldp neighbor <ip>`. For a dead core link,
confirm the interface came back and LSP counts returned to baseline on the
neighbouring P routers.
