# Incident report — Gray failure (silent backbone loss)

> RAG seed. Fill from the data API (`/metrics`, `/events`, `/flows`, `/labels`,
> `/datasets`). Keep concise; one incident per file.

- **Incident ID:** INC-20260801-06
- **Detected (UTC):** 2026-08-01T22:46:35Z
- **Device(s):** p5
- **Site type / VRF:** provider core (ABR, pop2) — no VRF, backbone link
- **Entity:** P-P backbone link p5-p6 (ABR-ABR, area 0)
- **Severity:** medium
- **Fault type:** gray_failure

## Timeline (UTC)

| t | event |
|---|-------|
| t_start  2026-08-01T22:46:30Z | netem 0.5-2% loss injected on the p5-p6 link, NO link-down |
| t_impact 2026-08-01T22:46:35Z | modelled +5s lag (`impact_method=modelled`, no probe) |
| t_end    2026-08-01T23:02:30Z | netem cleared |

- **Lead time (s):** 5.0

## Telemetry evidence

- **Metrics:** `ospf_neighbor_state{device="p5"}` stays at 1 the entire window
  (adjacency never drops — the defining signature). No SD-WAN tunnel metric
  reflects it either: tunnel telemetry is modelled from the CE's own `eth1`
  netem, never a P-P backbone link, so a core gray failure has NO clean
  single-metric observable — hence `impact_method=modelled` (probe=null).
- **Events:** NONE — no link-down or OSPF neighbor-down line in Loki at any
  point. Absence of an event is itself the evidence.
- **Flows:** cross-POP flows through p5-p6 show elevated retransmits, no hard
  drop.
- **Label:** `type=gray_failure`, `scenario_id=gray_failure-p5-8b13d603`

## Root cause

Sub-BFD packet loss (0.5-2%) injected on a backbone link with no interface
down event. BFD timers never trip, so the link reports healthy while quietly
dropping packets — the hardest class of fault to catch from link state alone.

## Resolution & follow-up

netem cleared at t_end. Neither link-state (`ospf_neighbor_state`) nor tunnel
metrics moved, so there is no threshold to alert on — the label is the only
reliable signal in the lab. Flag this as the reason gray_failure needs
per-link backbone loss counters, not link-state or tunnel-metric alerts, in
production.
