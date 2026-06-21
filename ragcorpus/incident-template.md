# Incident report template

> RAG seed. Fill from the data API (`/metrics`, `/events`, `/flows`, `/labels`,
> `/datasets`). Keep concise; one incident per file.

- **Incident ID:** INC-YYYYMMDD-NN
- **Detected (UTC):**
- **Device(s):**            <!-- join key: node name(s) -->
- **Site type / VRF:**
- **Entity:**               <!-- interface (ethX) or tunnel (spoke-hub) -->
- **Severity:** low | medium | high
- **Fault type:** congestion | bgp_flap | tunnel_degrade | policy_drift | node_failure | asymmetric_loss | brownout

## Timeline (UTC)

| t | event |
|---|-------|
| t_start  | precursor first observed |
| t_impact | user-visible impact began |
| t_end    | resolved / reverted |

- **Lead time (s):**        <!-- t_impact − t_start; predictive window -->

## Telemetry evidence

- **Metrics:** <PromQL + observed delta, e.g. latency 25 → 84 ms>
- **Events:** <key Loki lines, e.g. BGP ADJCHANGE burst>
- **Flows:** <relevant flow shift, if any>
- **Label:** <scenario_id from /labels, if a known scenario>

## Root cause


## Resolution & follow-up

