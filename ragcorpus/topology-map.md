# Topology map — SD-WAN over MPLS NOC lab

> RAG seed. Derived from `generator/generate.py` / `topology-spec.yaml` /
> rendered `topology/clab.yml`. Live graph JSON at `GET /topology`. Join key
> for all telemetry is `device` (the node name below).

## Scale

148 containers = 70 FRR (24 P + 12 PE + 34 CE) + 78 hosts.

## Layers

- **Provider MPLS core** — P routers `p1`..`p24` (global sequential index,
  not per-POP names). LDP LSRs, OSPF-only. 6 POPs x 4 P routers each.
- **Provider edge** — PE routers `pe1`..`pe12` (LERs: OSPF + LDP + MP-BGP
  VPNv4, per-customer VRFs). Single AS 65000, iBGP via **route reflection**
  (not full mesh): `pe1`/`pe2` are RRs, `pe3`-`pe12` are clients. Sessions =
  10 clients x 2 RRs + 1 RR-RR = **21** (full mesh would be 66).
- **Customer edge (CE)** — homes into a PE, eBGP per VRF, named
  `ce_{site_type}{n}`:
  - branch: `ce_branch1`..`ce_branch24` (24, CORP+VOICE, ASN 65101-65124)
  - hub: `ce_hub1`..`ce_hub6` (6, CORP+VOICE+GUEST, ASN 65201-65206,
    WireGuard hub/concentrator)
  - dc: `ce_dc1`..`ce_dc4` (4, CORP+VOICE+GUEST, ASN 65301-65304, WireGuard
    spoke)
  - 34 CE total.
- **Hosts** — 78 containers, one per (site, VRF) pair, named
  `h_{site_type}{n}_{vrf_lower}` (e.g. `h_branch1_corp`). Count = branch
  24x2 (no GUEST) + hub 6x3 + dc 4x3 = 48+18+12 = 78.

## CE -> PE attachment

Round-robin over a single linear index across all CEs in build order
(branch, then hub, then dc) — `pe_idx = (linear_index % 12) + 1`. Not
geography- or POP-aware.

## POP / OSPF areas

6 POPs (`pop1`..`pop6`), 4 P routers each. Per POP, the first 2 P routers
are the ABRs. Areas:
- Intra-POP mesh (non-ABR-ABR links) — area = POP number, i.e. areas 1-6.
- The ABR-ABR link inside a POP is forced into area 0.
- Inter-POP ring + 3 chords (`[1,4] [2,5] [3,6]`) between ABR pairs — all
  area 0, cost 100 (intra-POP cost is 10).

## iBGP route reflection

RRs: `pe1`, `pe2`. Clients: `pe3`-`pe12`. 21 sessions total (see above).
Provider AS: 65000.

## SD-WAN overlay (WireGuard)

`172.16.0.0/24`, UDP port 51820. 28 spokes (24 branch + 4 dc CEs), 6 hubs
(`ce_hub1`-`ce_hub6`). Every spoke peers with **all 6 hubs**: 28 x 6 = 168
spoke-hub tunnels. Hubs pair up adjacently for 3 hub-hub tunnels
(`ce_hub1`-`ce_hub2`, `ce_hub3`-`ce_hub4`, `ce_hub5`-`ce_hub6`). 171 tunnels
total. Per-tunnel telemetry (latency/jitter/loss/rekeys) is **simulated**
by the controller, not measured (see runbook for detail).

## VRFs (L3VPN segmentation)

| VRF | RT (shared) | RD | table id | sites |
|-----|-------------|-----|----------|-------|
| CORP  | 65000:10 | `<pe_loopback>:10` (per PE) | 10 | branch, hub, dc |
| VOICE | 65000:20 | `<pe_loopback>:20` (per PE) | 20 | branch, hub, dc |
| GUEST | 65000:30 | `<pe_loopback>:30` (per PE) | 30 | hub, dc only (branch excluded) |

RD is per-PE (e.g. `10.255.2.3:10` on pe3), not a single shared
`65000:<vrf>` value; RT is the shared community and is what actually
controls import/export.

## QoS

Each CE's uplink interface (first data interface is `eth1`; a CE with
multiple VRFs gets later even-numbered `ethN` uplinks for the rest) runs
`tc` HTB with `htb default` set to that VRF's own class — no DSCP `u32`
filters (nothing marks DSCP in this lab). Branch CEs never get a GUEST
class (GUEST isn't attached at branch sites).
