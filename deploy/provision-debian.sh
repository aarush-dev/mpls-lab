#!/usr/bin/env bash
# provision-debian.sh — turn a BARE Debian 12 machine into a lab host.
# Installs OS packages + kernel modules + sysctls. Needs root + internet.
# Idempotent: re-running is safe. Run this BEFORE extracting the bundle / restore.sh.
#
#   sudo ./provision-debian.sh
#
# ponytail: leans on the official docker/containerlab install scripts instead of
# hand-rolling apt repo pinning — they already do the gpg-key + source-list dance.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root (sudo)"; exit 1; }

echo "== apt base packages =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  ca-certificates curl gnupg git xz-utils kmod iproute2 sudo \
  wireguard-tools python3 python3-pip jq

echo "== docker (get.docker.com) =="
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh          # installs docker-ce + compose plugin
fi
systemctl enable --now docker || service docker start || true

echo "== containerlab 0.76.1 (get.containerlab.dev) =="
# Pin to the source-host version for parity — the installer takes -v.
command -v containerlab >/dev/null || curl -sL https://get.containerlab.dev | bash -s -- -v 0.76.1

echo "== claude code CLI (optional — for the migrated context) =="
# ~/.local/bin/claude; tolerate failure (host may be air-gapped for this bit).
command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash || \
  echo "  (claude CLI install skipped — install manually if you want the migrated chats)"
# install goes to ~/.local/bin which isn't on root's non-login/systemd PATH — expose it.
[[ -x "$HOME/.local/bin/claude" ]] && ln -sf "$HOME/.local/bin/claude" /usr/local/bin/claude || true

echo "== kernel modules (/etc/modules-load.d/noc-lab.conf) =="
# Mirror of the source host. NOTE: which of these are built-in (=y) vs loadable (=m)
# is kernel-specific. On a different Debian kernel some may FATAL as built-in (see
# docs/PHASE0ENVIRONMENT.md). modprobe below tolerates that; the file only lists loadables.
cat > /etc/modules-load.d/noc-lab.conf <<'EOF'
# Docker daemon prereqs (dockerd won't start without bridge)
bridge
br_netfilter
ip6_tables
ip6table_filter
# Lab dataplane
mpls_router
mpls_gso
vrf
sch_netem
cls_u32
EOF
for m in bridge br_netfilter ip6_tables ip6table_filter mpls_router mpls_gso vrf sch_netem cls_u32 dummy wireguard; do
  modprobe "$m" 2>/dev/null && echo "  [ok] $m" || echo "  [skip/built-in] $m"
done

echo "== sysctls =="
cat > /etc/sysctl.d/99-mpls.conf <<'EOF'
net.mpls.platform_labels = 1048575
net.mpls.default_ttl = 255
EOF
cat > /etc/sysctl.d/99-noc-inotify.conf <<'EOF'
# large containerlab topology → many watches
fs.inotify.max_user_instances = 1024
fs.inotify.max_user_watches = 1048576
EOF
sysctl --system >/dev/null

echo
echo "== Phase-0 verify (docs/PHASE0ENVIRONMENT.md) =="
fail=0
chk() { printf '  %-24s ' "$1"; shift; if "$@" >/dev/null 2>&1; then echo PASS; else echo FAIL; fail=1; fi; }
chk "mpls_router"      test -d /proc/sys/net/mpls
chk "mpls label-impose" bash -c 'ip route add 10.99.99.99/32 encap mpls 100 via 127.0.0.1 dev lo && ip route del 10.99.99.99/32'
chk "vrf"              bash -c 'ip link del vr0 2>/dev/null; ip link add vr0 type vrf table 100 && ip link del vr0'
# netem: capture the qdisc-add rc explicitly — a trailing cleanup `;` would mask a missing sch_netem as PASS.
chk "netem"           bash -c 'ip link del d0 2>/dev/null; ip link add d0 type dummy && tc qdisc add dev d0 root netem delay 10ms; rc=$?; tc qdisc del dev d0 root 2>/dev/null; ip link del d0 2>/dev/null; exit $rc'
chk "veth"            bash -c 'ip link del v0 2>/dev/null; ip link add v0 type veth peer name v1 && ip link del v0'
chk "wireguard"       modprobe wireguard

echo
if [[ $fail == 0 ]]; then
  echo "== host ready. Next: extract the bundle to /root, then /root/LAB/deploy/restore.sh =="
else
  echo "== SOME CHECKS FAILED — MPLS/VRF dataplane needs a kernel with CONFIG_LWTUNNEL +"
  echo "   CONFIG_MPLS_IPTUNNEL + CONFIG_NET_VRF. Control-plane telemetry still works; VRF"
  echo "   forwarding metrics won't. See docs/PHASE0ENVIRONMENT.md. Fix the kernel or use a full VM."
fi
