---
name: investigate_tunnel_degradation
description: SD-WAN tunnel latency/loss (congestion, tunnel_degrade, asymmetric_loss, brownout, hub_spoke_congest) — pairs runbook-tunnel-latency-high.
---
Faults: `congestion`, `tunnel_degrade`, `asymmetric_loss`, `brownout`, `hub_spoke_congest`.
Overlay symptoms on the SD-WAN tunnel. The tunnel gauges are per-spoke and UNDIFFERENTIATED —
no per-direction field; direction comes only from `flows`.

1. `query_metrics device=<spoke> pattern=sdwan_tunnel_` — one substring hits the whole family
   (`_latency_ms`, `_loss_pct`, `_jitter_ms`). Read the shape: loss up, latency ~normal -> step 2;
   latency+loss+jitter climbing together -> step 3; all three flat -> step 4. Every fault here is
   netem on the spoke's OWN `eth1` + a calibrated overlay, so the signal is on the target spoke,
   not its hub or siblings.
2. Loss up, latency normal: confirm with `flows device=<spoke>` — retransmits isolated to one
   direction (upload or download) confirms `asymmetric_loss`. No isolated direction in flows,
   don't call the fault off the gauge alone.
3. Symmetric climb splits first on the rekey burst: `search_logs device=<spoke> pattern=rekey`
   (case-insensitive). Clustered handshake retries -> `tunnel_degrade`. No clustering, the climb
   is `congestion` / `hub_spoke_congest` / `brownout` — all spoke-local symmetric netem, separable
   only by magnitude and the rate cap: `hub_spoke_congest` peaks highest; `brownout` adds a
   throughput ceiling, so `flows device=<spoke>` shows offered rate pinned at the cap while a plain
   `congestion` does not. When the gauge shape alone can't split them, abstain and cite the label
   (`when_to_abstain`).
4. Flat gauge: no overlay/netem is currently active on this spoke (reverted, or the event is not
   one of these tunnel faults). Don't force a call off a flat gauge — check the label / recent
   `search_incidents` before naming a fault.
5. `search_incidents query="tunnel degradation" device=<spoke>` for nearby past cases;
   `search_runbooks query="tunnel degradation"` for method.
