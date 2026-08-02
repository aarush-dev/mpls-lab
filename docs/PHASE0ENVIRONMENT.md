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
Run these on the workstation/WSL kernel. There is no fallback mode: MPLS/LDP is
emitted unconditionally for every P/PE (`topology-spec.yaml` — the `fallback.vrflite_mode`
block that used to offer a VRF-only underlay has been deleted). If step 1 or 2 fails, fix
the kernel; there is nothing to fall back to.

```bash
# 1. kernel MPLS
sudo modprobe mpls_router mpls_gso mpls_iptunnel; echo "modprobe exit: $?"
sudo sysctl -w net.mpls.platform_labels=1048575 && echo "MPLS OK"
# NOTE: `modprobe mpls_router 2>&1 | grep -q "^$"` (the old check here) ALWAYS reports
# FAIL, even on a successful modprobe. `grep "^$"` matches a LINE that is empty, not
# the absence of any output — and a successful `modprobe` prints zero lines (no stdout,
# no stderr), so there is no empty line for grep to match against. `grep -q` on zero
# lines of input exits 1 regardless of pattern (verified: `printf "" | grep -q "^$"`
# exits 1; only `printf "\n" | grep -q "^$"` exits 0). The check was testing for a
# blank line that a successful modprobe never emits. Check `$?` directly instead.

# 2. VRF
sudo ip link add vr0 type vrf table 100 && sudo ip link del vr0 && echo "VRF OK"

# 3. netem (fault injection) — needs the `dummy` module loaded first
sudo modprobe dummy
sudo ip link add d0 type dummy && sudo tc qdisc add dev d0 root netem delay 10ms && \
  sudo tc qdisc del dev d0 root && sudo ip link del d0 && echo "netem OK"

# 4. veth (containerlab core requirement)
sudo ip link add v0 type veth peer name v1 && sudo ip link del v0 && echo "veth OK"

# 5. wireguard (overlay)
sudo modprobe wireguard && echo "wg OK"
```

Verified on this host (19 cores / 108 GB RAM / 1007 GB disk, kernel 6.18.33.1-microsoft-standard-WSL2)
on 2026-07-26: all 5 checks PASS, including the corrected modprobe check and `netem` after
loading `dummy`.

### Step 1 is a FALSE PASS on this kernel — add step 1b

`CONFIG_MPLS_ROUTING=y` and `modprobe mpls_router` succeed, and `net.mpls.platform_labels`
sets fine — but label **imposition** needs `CONFIG_LWTUNNEL`, which this kernel lacks. Test it
directly:

```bash
# 1b. MPLS label imposition (the check step 1 does NOT cover)
sudo ip route add 10.99.99.99/32 encap mpls 100 via 127.0.0.1 dev lo && \
  sudo ip route del 10.99.99.99/32 && echo "MPLS encap OK"
```

On kernel 6.18.33.1-microsoft-standard-WSL2 (2026-07-26) this returns:

```
Error: CONFIG_LWTUNNEL is not enabled in this kernel.
```

Observed consequences on a full deploy: FRR reports `Status: Label Changed Failed`, pe1 learns
114 OSPF routes but installs only 9 in the FIB, iBGP VPNv4 sessions sit in `Connect`, and
`bgp_peer_established` stays 0. OSPF/LDP control-plane telemetry is still real and usable;
VRF-scoped forwarding metrics are not. Fix = a kernel with `CONFIG_LWTUNNEL` +
`CONFIG_MPLS_IPTUNNEL`, or a full Linux VM. The `vrflite` fallback named in
`topology-spec.yaml:52-53,239` is **not implemented** in the generator.

### WSL2 notes
- Default WSL2 kernels often lack `CONFIG_MPLS_ROUTING`/`CONFIG_NET_VRF`. If steps 1–2 fail,
  build a custom WSL2 kernel with those options, or run the lab in a full Linux VM. There is
  no fallback underlay to switch to instead — MPLS/LDP is unconditional in the generator.
- Load needed modules at boot via `/etc/modules-load.d/` then `wsl --shutdown`.
- `cls_u32` must be loaded for the QoS DSCP `tc` filters (loadable, not built-in on this WSL2
  kernel; not auto-loaded → filters fail after reboot). It is in
  `/etc/modules-load.d/noc-lab.conf` (along with `mpls_router`/`mpls_gso`).

### dockerd will not start after a WSL restart until `bridge` is loaded

`CONFIG_BRIDGE=m` on this kernel and nothing auto-loads it, so a cold WSL boot gives:

```
Failed to create bridge docker0 via netlink   error="operation not supported"
failed to start daemon: Error initializing network controller: \
  error creating default "bridge" network: operation not supported
```

`service docker start` then fails, and after a few retries systemd gives up with
"Start request repeated too quickly" — which hides the real cause. Fix:

```bash
sudo modprobe bridge br_netfilter ip6_tables ip6table_filter
sudo systemctl reset-failed docker.service   # clear the retry lockout
sudo service docker start
```

`/etc/modules-load.d/noc-lab.conf` now carries these so it survives a reboot.

**Do NOT list `mpls_iptunnel` there.** It is built in on this kernel
(`CONFIG_MPLS_ROUTING=y`), so `modprobe` FATALs on it at every boot. Verify what is a
module vs built-in before adding entries:

```bash
zcat /proc/config.gz | grep -E "^CONFIG_(BRIDGE|NET_VRF|MPLS_ROUTING|VETH)="
# =m -> needs modules-load.d;  =y -> built in, leave it out
```
- Raise inotify limits for large labs: `fs.inotify.max_user_instances`, `max_user_watches`.

## Status of Phase 0 (remote)
- [x] Docker daemon up
- [x] iproute2 installed
- [x] Containerlab 0.76.1 installed
- [x] Kernel MPLS/VRF/netem — **verified on local WSL2 kernel 6.18.33.1: all PASS → underlay = mpls**
- [ ] Full topology deploy — **local agent**
