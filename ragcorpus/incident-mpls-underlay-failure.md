# Incident report — MPLS underlay failure

> RAG seed. Fill from the data API (`/metrics`, `/events`, `/flows`, `/labels`,
> `/datasets`). Keep concise; one incident per file.

- **Incident ID:** INC-20260802-03
- **Detected (UTC):** 2026-08-02T14:02:31Z
- **Device(s):** p3
- **Site type / VRF:** provider core (non-ABR P router, pop1) — no single VRF, all VPNv4 transiting p3
- **Entity:** P-PE-facing interface (eth2)
- **Severity:** null (severity_inert — link-set fault; injector ignores severity)
- **Fault type:** mpls_underlay_failure

## Timeline (UTC)

| t | event |
|---|-------|
| t_start  2026-08-02T14:02:30Z | `ip link set eth2 down` on p3 |
| t_impact 2026-08-02T14:02:32Z | LDP/OSPF reconverge to secondary path (modelled +2s) |
| t_end    2026-08-02T14:04:10Z | `ip link set eth2 up`, link restored |

- **Lead time (s):** 2.0

## Telemetry evidence

- **Metrics:** `ospf_neighbor_state{device="p3"}` for the eth2 peer drops
  1 → 0; `mpls_lsp_count` shifts on p3's remaining neighbours as LSPs
  re-signal over the surviving links.
- **Events:** Loki LDP session-down line, OSPF neighbor down/up bracketing the
  outage.
- **Flows:** transiting VPNv4 traffic reroutes to the secondary path; brief
  flow gap during BFD-driven reconvergence (~1s).
- **Label:** `type=mpls_underlay_failure`, `scenario_id=mpls_underlay_failure-p3-71bd0aa2`

## Root cause

P-PE-facing core interface on non-ABR p3 dropped (`ip link down`); LDP/OSPF
reconverge to a secondary path with BFD assisting, so the observable window
is short (~1s) despite it being a hard link failure.

## Resolution & follow-up

Interface restored automatically at t_end; confirm LDP session re-established
(`vtysh -c "show mpls ldp neighbor"`) and LSP counts back to baseline on
neighbouring P routers. No lasting VPNv4 loss — BFD-assisted reroute worked.
