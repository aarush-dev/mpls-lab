# Phase 0 — Environment Findings & Local-Agent Checklist

This plan was researched/approved in a **remote authoring sandbox**. That sandbox has a stripped
kernel and partial resources, so the **live lab must be built on the local agent / workstation**.
This file records what was checked remotely and exactly what the local agent must verify before
deploying.

## What the remote sandbox had
| Item | Result |
|---|---|
| Docker | 29.3.1, daemon startable |
| Containerlab | 0.76.1 installed OK |
| iproute2 (`ip`) | 6.1.0 installed OK |
| CPU / RAM / disk | 4 cores / 15 GB / ~30 GB free (NOT the target 18c/120G/300G) |
| `modprobe` / kmod | absent |
| `/lib/modules/$(uname -r)` | absent (no loadable modules) |
| `net.mpls.platform_labels` sysctl | **absent** |
| `vrf` device type | **"Unknown device type"** (not available) |
| kernel | 6.18.5, container/WSL-style minimal |

**Conclusion:** the remote sandbox kernel cannot do MPLS or VRF dataplane, so the lab was not
deployed remotely. All design/research is captured in `PLAN.md`; the build happens locally.

## Local agent: verify BEFORE deploying (Phase 0 re-run)
Run these on the workstation/WSL kernel and pick `mpls` vs `vrflite` underlay accordingly:

```bash
# 1. kernel MPLS
sudo modprobe mpls_router mpls_gso mpls_iptunnel && \
  sudo sysctl -w net.mpls.platform_labels=1048575 && echo "MPLS OK"      # else use vrflite

# 2. VRF
sudo ip link add vr0 type vrf table 100 && sudo ip link del vr0 && echo "VRF OK"

# 3. netem (fault injection)
sudo ip link add d0 type dummy && sudo tc qdisc add dev d0 root netem delay 10ms && \
  sudo tc qdisc del dev d0 root && sudo ip link del d0 && echo "netem OK"

# 4. veth (containerlab core requirement)
sudo ip link add v0 type veth peer name v1 && sudo ip link del v0 && echo "veth OK"

# 5. wireguard (overlay)
sudo modprobe wireguard && echo "wg OK"   # else use userspace wireguard-go / strongSwan
```

### WSL2 notes
- Default WSL2 kernels often lack `CONFIG_MPLS_ROUTING`/`CONFIG_NET_VRF`. If steps 1–2 fail,
  build a custom WSL2 kernel with those options, or run the lab in a full Linux VM, **or** use the
  `vrflite` underlay (L3VPN emulated with VRFs — control-plane/telemetry/fault signals unchanged).
- Load needed modules at boot via `/etc/modules-load.d/` then `wsl --shutdown`.
- `cls_u32` must be loaded for the QoS DSCP `tc` filters (loadable, not built-in on this WSL2
  kernel; not auto-loaded → filters fail after reboot). It is now in
  `/etc/modules-load.d/noc-lab.conf` (along with `mpls_router`/`mpls_iptunnel`/`mpls_gso`).
- Raise inotify limits for large labs: `fs.inotify.max_user_instances`, `max_user_watches`.

## Status of Phase 0 (remote)
- [x] Docker daemon up
- [x] iproute2 installed
- [x] Containerlab 0.76.1 installed
- [x] Kernel MPLS/VRF/netem — **verified on local WSL2 kernel 6.18.33.1: all PASS → underlay = mpls**
- [ ] Full topology deploy — **local agent**
