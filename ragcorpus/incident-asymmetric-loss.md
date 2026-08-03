# Incident report — Asymmetric loss

> RAG seed. Fill from the data API (`/metrics`, `/events`, `/flows`, `/labels`,
> `/datasets`). Keep concise; one incident per file.

- **Incident ID:** INC-20260801-07
- **Detected (UTC):** 2026-08-01T16:33:12Z
- **Device(s):** ce_dc2
- **Site type / VRF:** dc — CORP
- **Entity:** tunnel ce_dc2-ce_hub5, uplink eth1 (egress direction only)
- **Severity:** medium
- **Fault type:** asymmetric_loss

## Timeline (UTC)

| t | event |
|---|-------|
| t_start  2026-08-01T16:33:10Z | egress-only netem loss on ce_dc2 eth1 |
| t_impact 2026-08-01T16:33:12Z | `sdwan_tunnel_loss_pct` crosses threshold, latency unchanged |
| t_end    2026-08-01T16:36:10Z | netem cleared |

- **Lead time (s):** 2.0    <!-- ramp=False: loss jumps at once, crosses on the next 3s poll -->


## Telemetry evidence

- **Metrics:** `sdwan_tunnel_loss_pct{device="ce_dc2",tunnel="ce_dc2-ce_hub5"}`
  0.1% → 4.5% while `sdwan_tunnel_latency_ms` stays flat (~22 ms) — the
  asymmetric signature: loss up, latency normal.
- **Events:** none distinctive in Loki — no BGP/link event, evidence is
  metric-only, which is what makes this fault type hard to diagnose from logs
  alone.
- **Flows:** one-directional flow pattern (egress packets from ce_dc2 lost,
  ingress from ce_hub5 unaffected).
- **Label:** `type=asymmetric_loss`, `scenario_id=asymmetric_loss-ce_dc2-2ee7a450`

## Root cause

netem egress-only loss on the ce_dc2 uplink. Because latency stayed normal, a
latency-only alert would have missed this entirely — loss and latency must be
checked independently, not inferred from each other.

## Resolution & follow-up

netem cleared at t_end, loss returns to baseline. Recommend directional
(egress vs ingress) loss monitoring per tunnel rather than a single
round-trip metric, since this class of fault hides behind normal latency.
