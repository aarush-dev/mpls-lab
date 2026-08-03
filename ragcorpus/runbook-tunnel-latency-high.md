# Runbook — SD-WAN tunnel latency / loss high

> RAG seed. Ties to fault scenarios `congestion`, `tunnel_degrade`,
> `asymmetric_loss`, `brownout`, `hub_spoke_congest`.

## Symptom

A WireGuard SD-WAN tunnel shows rising latency, jitter, and/or packet loss;
VoIP (EF/VOICE VRF) quality degrades first. Often a **slow buildup** — the
predictive signal the copilot must catch before user impact.

## Telemetry signature

- **Metrics are SIMULATED, not measured.** `sdwan_tunnel_latency_ms` /
  `_jitter_ms` / `_loss_pct` (gauges) = a measured wg0 RTT/loss term plus a
  modelled congestion term plus the netem impairment read back from the
  site's `eth1` qdisc config — the wg0 ping itself never crosses the
  impaired path, so nothing here is a direct measurement of the fault.
  `sdwan_tunnel_rekeys_total` (counter) is real cumulative WireGuard rekeys.
  Labels: `device`, `tunnel`, `site`, `site_type`, `hub`. `tunnel_degrade`
  additionally shows rekey clustering. `sdwan_path_changes_total` is
  fabric-wide, unlabelled, and RNG-driven (not attributable to a device) —
  do not use it as fault evidence.
- **Dataset rows**: `entity_type=tunnel`, `is_fault=true` over the window;
  `lead_time_s` is the precursor window (latency/jitter creep before loss);
  `time_to_impact_s` counts down to first observable impact.
- **Pattern by scenario**:
  - `congestion` — latency+jitter creep first, then loss as the ramp saturates.
  - `tunnel_degrade` — jitter+loss climb + rekey clustering.
  - `asymmetric_loss` — loss% up while latency stays normal (one-directional).
  - `brownout` — queueing latency climbs under a rate cap; loss arrives late.
  - `hub_spoke_congest` — netem ramp on a **hub** uplink (`ce_hub{1..6}` eth1),
    so every spoke routed through that hub degrades at once. Modelled, **not**
    tunnel-observable: the controller folds netem only from the spoke's eth1, so
    the hub-side cap shows in no tunnel gauge — classify by the shared-hub
    fan-out (many spokes degrade together) and the label, not a metric crossing.

## Triage

1. `GET /metrics?query=sdwan_tunnel_latency_ms{device="<CE>"}` (range) — confirm
   the climb and which tunnel(s) (`ce_branchX-ce_hubY`).
2. Check loss vs latency split to classify (asymmetric vs congestion vs brownout).
3. Inspect rekeys for `tunnel_degrade`.
4. On the CE: `tc qdisc show dev eth1` (uplink) — netem impairment shows here
   in the lab; `wg show` for handshake health.
5. Bound the window via `/labels` (`t_start`/`t_impact`/`t_end`/
   `lead_time`; use `lead_time_s`/`time_to_impact_s`/`is_fault` if querying
   the joined `/datasets` output instead — those `_s`-suffixed and boolean
   fields exist only there, not on raw `/labels` rows).

## Resolution

The contributing impairment is on the CE uplink. In the lab, fault injectors
revert at `t_end` (netem cleared, QoS HTB root restored). In production:
re-route over the second hub (dual-hub overlay), shed best-effort (GUEST/BE)
traffic, or escalate the underlay congestion.
