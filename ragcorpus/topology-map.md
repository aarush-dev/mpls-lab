# Topology map — SD-WAN over MPLS NOC lab

> RAG seed. Derived from `topology-spec.yaml` / `topology/clab.yml`. The live
> graph JSON is always available at `GET /topology`. Join key for all telemetry
> is `device` (the node name below).

## Layers

- **Provider MPLS core** — P routers `p1 p2 p3` (LDP LSRs, OSPF-only, full mesh).
- **Provider edge** — PE routers `pe1 pe2 pe3` (LERs: OSPF + LDP + MP-BGP VPNv4,
  per-customer VRFs; full-mesh iBGP in AS 65000).
- **Customer edge (CE)** — home into PEs, eBGP per VRF:
  - branch: `ce_branch1 ce_branch2 ce_branch3 ce_branch4` (small, CORP+VOICE)
  - hub: `ce_hub1 ce_hub2` (regional aggregation, CORP+VOICE+GUEST, WireGuard hubs)
  - dc: `ce_dc1 ce_dc2` (datacenter sinks, CORP+VOICE+GUEST, WireGuard spokes)
- **Hosts** — 20 host containers, one per (site, VRF), behind the CEs.

## CE → PE attachment (round-robin)

| CE | homes into PE |
|----|---------------|
| ce_branch1 | pe1 |
| ce_branch2 | pe2 |
| ce_branch3 | pe3 |
| ce_branch4 | pe1 |
| ce_hub1 | pe2 |
| ce_hub2 | pe3 |
| ce_dc1 | pe1 |
| ce_dc2 | pe2 |

## VRFs (L3VPN segmentation)

| VRF | RD/RT | DSCP | QoS priority | sites |
|-----|-------|------|--------------|-------|
| CORP  | 65000:10 | AF31 | 2 | branch, hub, dc |
| VOICE | 65000:20 | EF   | 1 (highest) | branch, hub, dc |
| GUEST | 65000:30 | BE   | 3 (lowest) | hub, dc only |

## SD-WAN overlay

WireGuard hub-spoke (`172.16.0.0/24`). Both hub CEs are concentrators
(`ce_hub1`=.1, `ce_hub2`=.2); every branch/dc CE is a spoke peering to **both**
hubs. Per-tunnel telemetry (latency/jitter/loss/rekeys) is emitted by the
SD-WAN controller and scraped into VictoriaMetrics. Tunnel id format:
`<spoke>-<hub>`, e.g. `ce_branch1-ce_hub1`.

## QoS

CE egress (toward PE) runs `tc` HTB with three classes by DSCP: VOICE(EF, 30%),
CORP(AF31, 50%), GUEST(BE, 20%).
