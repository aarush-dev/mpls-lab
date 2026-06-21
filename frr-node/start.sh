#!/usr/bin/env bash
# frr-node startup script
#
# Smoke-test after build:
#   docker run --rm --cap-add NET_ADMIN frr-node vtysh -c "show version"
#   docker exec <ctr> snmpwalk -v2c -c public 127.0.0.1 1.3.6.1.2.1.1
#
# Ordering matters:
#   1. MPLS sysctls (best-effort, fails silently if kernel disallows)
#   2. snmpd     — must be up BEFORE FRR so the AgentX master socket exists
#   3. pmacctd   — IPFIX exporter; backgrounded; tolerates unreachable collector
#   4. FRR       — connects to snmpd AgentX master on startup (when frr-snmp is available)

set -euo pipefail

# ── 1. MPLS kernel tunables (best-effort) ────────────────────────────────────
# ponytail: sysctl may be blocked in non-privileged containers; don't die.
sysctl -w net.mpls.platform_labels=1048575 2>/dev/null || true
# Per-interface MPLS input is set by the topology generator (exec: hooks) after
# interfaces exist; not done here because interface names are unknown at image build.

# ── 2. Start snmpd (AgentX master must be ready before FRR) ──────────────────
# snmpd listens on UDP 161 and opens the AgentX socket at /var/agentx/master.
# FRR's AgentX subagent will connect to that socket on startup.
# ponytail: use default /etc/snmp/snmpd.conf (installed by net-snmp pkg);
# containerlab bind-mounts a node-specific one over it at runtime.
snmpd -f -Lo &
SNMPD_PID=$!

# Wait briefly for the AgentX socket to appear before starting FRR.
# ponytail: 2 s sleep is cheap and avoids a retry loop.
sleep 2

# ── 3. Start pmacctd (IPFIX exporter → nfacctd collector in Phase 2) ────────
# Collector defaults to 127.0.0.1:2055 (UDP fire-and-forget; pmacctd stays alive).
# Phase 2 generator bind-mounts a real nfacctd IP over /etc/pmacctd.conf at runtime.
# Guard: || true so a failure (e.g. pcap permission denied) doesn't kill the container.
pmacctd -f /etc/pmacctd.conf >>/var/log/pmacctd.log 2>&1 &

# ── 4. Start FRR (foreground via /usr/lib/frr/docker-start) ──────────────────
# docker-start reads /etc/frr/daemons and starts all enabled daemons, then
# exec's watchfrr in the foreground — that keeps the container alive.
exec /usr/lib/frr/docker-start
