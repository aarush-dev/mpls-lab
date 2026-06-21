#!/usr/bin/env bash
# frr-node startup script
#
# Smoke-test after build:
#   docker run --rm --cap-add NET_ADMIN frr-node vtysh -c "show version"
#   docker exec <ctr> snmpwalk -v2c -c public 127.0.0.1 1.3.6.1.2.1.1
#
# Ordering matters:
#   1. MPLS sysctls (best-effort, fails silently if kernel disallows)
#   2. rsyslogd  — syslog forwarder; must be up BEFORE FRR so /dev/log is consumed
#   3. snmpd     — must be up BEFORE FRR so the AgentX master socket exists
#   4. pmacctd   — IPFIX exporter; backgrounded; tolerates unreachable collector
#   5. FRR       — connects to snmpd AgentX master on startup (when frr-snmp is available)

set -euo pipefail

# ── Telemetry targets (ENV-overridable) ──────────────────────────────────────
# ponytail: single place to change IPs; override with -e at container launch.
#           NFACCTD_ADDR  = IPFIX collector (nfacctd)   default: 172.20.20.53:2055
#           PROMTAIL_ADDR = syslog forwarder (promtail)  default: 172.20.20.55:1514
NFACCTD_ADDR="${NFACCTD_ADDR:-172.20.20.53:2055}"
PROMTAIL_ADDR="${PROMTAIL_ADDR:-172.20.20.55:1514}"

# Split PROMTAIL_ADDR into host+port for rsyslog omfwd action directives.
export PROMTAIL_HOST="${PROMTAIL_ADDR%:*}"
export PROMTAIL_PORT="${PROMTAIL_ADDR##*:}"
export NFACCTD_ADDR

# Stamp env vars into configs so daemons read the final values.
# ponytail: envsubst writes to /run (tmpfs) — no image layer dirtied.
envsubst '${PROMTAIL_HOST} ${PROMTAIL_PORT}' \
    </etc/rsyslog.conf >/run/rsyslog.conf
envsubst '${NFACCTD_ADDR}' \
    </etc/pmacctd.conf >/run/pmacctd.conf

# ── 1. MPLS kernel tunables (best-effort) ────────────────────────────────────
# ponytail: sysctl may be blocked in non-privileged containers; don't die.
sysctl -w net.mpls.platform_labels=1048575 2>/dev/null || true
# Per-interface MPLS input is set by the topology generator (exec: hooks) after
# interfaces exist; not done here because interface names are unknown at image build.

# ── 2. Start rsyslogd (syslog → promtail RFC5424 forwarder) ──────────────────
# Reads /dev/log (imuxsock); forwards all messages to promtail via UDP RFC5424.
# Guard: || true so a misconfigured rsyslog.conf doesn't kill the container.
rsyslogd -n -f /run/rsyslog.conf &>/var/log/rsyslogd.log &

# ── 3. Start snmpd (AgentX master must be ready before FRR) ──────────────────
# snmpd listens on UDP 161 and opens the AgentX socket at /var/agentx/master.
# FRR's AgentX subagent will connect to that socket on startup.
# ponytail: use default /etc/snmp/snmpd.conf (installed by net-snmp pkg);
# containerlab bind-mounts a node-specific one over it at runtime.
snmpd -f -Lo &
SNMPD_PID=$!

# Wait briefly for the AgentX socket to appear before starting FRR.
# ponytail: 2 s sleep is cheap and avoids a retry loop.
sleep 2

# ── 4. Start pmacctd (IPFIX exporter → nfacctd collector) ───────────────────
# Guard: || true so a failure (e.g. pcap permission denied) doesn't kill the container.
pmacctd -f /run/pmacctd.conf >>/var/log/pmacctd.log 2>&1 &

# ── 5. Start FRR (foreground via /usr/lib/frr/docker-start) ──────────────────
# docker-start reads /etc/frr/daemons and starts all enabled daemons, then
# exec's watchfrr in the foreground — that keeps the container alive.
exec /usr/lib/frr/docker-start
