# Incident report template

> RAG seed. Fill from the data API (`/metrics`, `/events`, `/flows`, `/labels`,
> `/datasets`). Keep concise; one incident per file.

- **Incident ID:** INC-YYYYMMDD-NN
- **Detected (UTC):**
- **Device(s):**            <!-- join key: node name(s) -->
- **Site type / VRF:**
- **Entity:**               <!-- interface (ethX) or tunnel (spoke-hub) -->
- **Severity:** low | medium | high
- **Fault type:** one of 21 scenario types (e.g. congestion, bgp_flap,
  tunnel_degrade, policy_drift, node_failure, asymmetric_loss, brownout,
  hub_spoke_congest, bgp_cascade, rr_failure, gray_failure, ...) — see
  `type` field on `/labels` for the full set.

## Timeline (UTC)

| t | event |
|---|-------|
| t_start  | precursor first observed |
| t_impact | user-visible impact began |
| t_end    | resolved / reverted |

- **Lead time (s):**        <!-- t_impact − t_start; predictive window;
  raw /labels field is `lead_time` (no _s suffix), dataset column is
  `lead_time_s` -->

## Telemetry evidence

- **Metrics:** <PromQL + observed delta, e.g. latency 25 → 84 ms>
- **Events:** <key Loki lines, e.g. BGP ADJCHANGE burst>
- **Flows:** <relevant flow shift, if any>
- **Label:** <`type` + `scenario_id` from /labels, if a known scenario;
  `scenario_id` is a per-run instance id like
  `congestion-ce_branch1-87844aed`, not the scenario type itself>

## Root cause


## Resolution & follow-up

