---
name: investigate_tunnel_degradation
description: SD-WAN tunnel latency/loss (congestion, tunnel_degrade, asymmetric_loss, brownout, hub_spoke_congest) — pairs runbook-tunnel-latency-high.
---
Faults: `congestion`, `tunnel_degrade`, `asymmetric_loss`, `brownout`, `hub_spoke_congest`.
Overlay symptoms on the SD-WAN tunnel. The tunnel gauges are per-spoke and UNDIFFERENTIATED —
no per-direction field; direction comes only from `flows`.

1. `query_metrics device=<spoke> pattern=sdwan_tunnel_` — one substring hits the whole family
   (`_latency_ms`, `_loss_pct`, `_jitter_ms`). Read the shape: loss up, latency ~normal -> step 2;
   latency+loss+jitter climbing together -> step 3; all three flat -> step 4.
2. Loss up, latency normal: confirm with `flows device=<spoke>` — retransmits isolated to one
   direction (upload or download) confirms `asymmetric_loss`. No isolated direction in flows,
   don't call the fault off the gauge alone.
3. Symmetric climb splits on the rekey burst, the only telemetry-side difference between the two:
   `search_logs device=<spoke> pattern=rekey` (case-insensitive). Clustered handshake retries ->
   `tunnel_degrade`. No clustering -> `congestion`.
4. Flat gauge: check the sibling spokes on the same hub — `query_metrics pattern=sdwan_tunnel_
   device=<sibling>`. Siblings degrade too -> `hub_spoke_congest` (netem sits on the hub's
   `eth1`, catching every spoke behind it). Siblings stay clean -> `brownout` (a rate cap,
   `probe=null`, zero telemetry). Neither is probe-backed — whichever it is, abstain on a
   tunnel-metric claim and cite the label instead (`when_to_abstain`).
5. `search_incidents query="tunnel degradation" device=<spoke>` for nearby past cases;
   `search_runbooks query="tunnel degradation"` for method.
