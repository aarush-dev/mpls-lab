---
name: investigate_tunnel_degradation
description: SD-WAN tunnel latency/loss (congestion, tunnel_degrade, asymmetric_loss, brownout, hub_spoke_congest) — pairs runbook-tunnel-latency-high.
---
Faults: `congestion`, `tunnel_degrade`, `asymmetric_loss`, `brownout`, `hub_spoke_congest`.
Overlay symptoms — latency/loss on the SD-WAN tunnel.

1. `query_metrics device=<spoke> pattern=sdwan_tunnel_` within the window — one substring hits
   the whole tunnel family (`_latency_ms`, `_loss_pct`, `_jitter_ms`): which tunnel, how high,
   one-way vs both.
2. Symmetric climb = `congestion` / `tunnel_degrade`; one-directional loss = `asymmetric_loss`.
3. `hub_spoke_congest` AND `brownout` are `modelled` and NOT tunnel-observable — `hub_spoke_congest`
   netem sits hub-side (hub `eth1`) and the controller folds netem only from the spoke `eth1`;
   `brownout` is a `rate` cap with no telemetry observable at all (`probe=null`). For both the
   tunnel gauge won't move — the label is the only signal (abstain on a tunnel-metric claim,
   see `when_to_abstain`).
4. `search_incidents query="tunnel latency" device=<spoke>` for nearby past cases;
   `search_runbooks query="tunnel latency loss"` for method.
