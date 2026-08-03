# Incident report — SD-WAN tunnel degrade

> RAG seed. Fill from the data API (`/metrics`, `/events`, `/flows`, `/labels`,
> `/datasets`). Keep concise; one incident per file.

- **Incident ID:** INC-20260802-02
- **Detected (UTC):** 2026-08-02T09:41:00Z
- **Device(s):** ce_branch7
- **Site type / VRF:** branch — VOICE (EF traffic degrades first)
- **Entity:** tunnel ce_branch7-ce_hub3
- **Severity:** medium
- **Fault type:** tunnel_degrade

## Timeline (UTC)

| t | event |
|---|-------|
| t_start  2026-08-02T09:40:15Z | jitter+loss ramp + wg0 rekey bounce start on eth1 |
| t_impact 2026-08-02T09:41:00Z | `sdwan_tunnel_loss_pct` crosses threshold |
| t_end    2026-08-02T09:42:45Z | netem cleared, wg0 stable |

- **Lead time (s):** 45.0

## Telemetry evidence

- **Metrics:** `sdwan_tunnel_jitter_ms{device="ce_branch7",tunnel="ce_branch7-ce_hub3"}`
  4 → 22 ms; `sdwan_tunnel_loss_pct` 0.2% → 3.1%; `sdwan_tunnel_rekeys_total`
  clustered (2 rekeys in the 90s window vs ~1/hour baseline).
- **Events:** WireGuard handshake retry lines around the rekey bounces.
- **Flows:** VOICE VRF flow volume dips as calls degrade/drop.
- **Label:** `type=tunnel_degrade`, `scenario_id=tunnel_degrade-ce_branch7-3fe881bb`

## Root cause

netem jitter+loss ramp on the CE uplink (HTB leaf, VOICE class) combined with
a forced wg0 rekey bounce — rekey clustering is the extra signature that
distinguishes `tunnel_degrade` from plain `congestion`.

## Resolution & follow-up

Lab injector reverts automatically at t_end (netem cleared, HTB root restored,
wg0 handshake stable). In production: reroute the site over its second hub
(dual-hub overlay) while the primary path's rekey churn is investigated.
