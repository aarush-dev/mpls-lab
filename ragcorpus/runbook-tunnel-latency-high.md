# Runbook — SD-WAN tunnel latency / loss high

> RAG seed. Ties to fault scenarios `congestion`, `tunnel_degrade`,
> `asymmetric_loss`, `brownout`.

## Symptom

A WireGuard SD-WAN tunnel shows rising latency, jitter, and/or packet loss;
VoIP (EF/VOICE VRF) quality degrades first. Often a **slow buildup** — the
predictive signal the copilot must catch before user impact.

## Telemetry signature

- **Metrics** (`/metrics`): `sdwan_tunnel_latency_ms`, `sdwan_tunnel_jitter_ms`,
  `sdwan_tunnel_loss_pct` climbing for `{device=<CE>}`; for `tunnel_degrade`
  also `sdwan_tunnel_rekeys_total` clustering (handshake retries).
- **Dataset rows**: `entity_type=tunnel`, `is_fault=true` over the window;
  `lead_time_s` is the precursor window (latency/jitter creep before loss);
  `time_to_impact_s` counts down to first observable impact.
- **Pattern by scenario**:
  - `congestion` — latency+jitter creep first, then loss as the ramp saturates.
  - `tunnel_degrade` — jitter+loss climb + rekey clustering.
  - `asymmetric_loss` — loss% up while latency stays normal (one-directional).
  - `brownout` — queueing latency climbs under a rate cap; loss arrives late.

## Triage

1. `GET /metrics?query=sdwan_tunnel_latency_ms{device="<CE>"}` (range) — confirm
   the climb and which tunnel(s) (`ce_branchX-ce_hubY`).
2. Check loss vs latency split to classify (asymmetric vs congestion vs brownout).
3. Inspect rekeys for `tunnel_degrade`.
4. On the CE: `tc qdisc show dev eth1` (uplink) — netem impairment shows here
   in the lab; `wg show` for handshake health.
5. Bound the window via `/labels` (`t_start`/`t_impact`/`lead_time`).

## Resolution

The contributing impairment is on the CE uplink. In the lab, fault injectors
revert at `t_end` (netem cleared, QoS HTB root restored). In production:
re-route over the second hub (dual-hub overlay), shed best-effort (GUEST/BE)
traffic, or escalate the underlay congestion.
