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
#   3. snmpd     — UDP 161 responder polled by Telegraf
#   4. pmacctd   — IPFIX exporter; backgrounded; tolerates unreachable collector
#   5. FRR       — foreground; keeps the container alive

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
# Backgrounded, so `set -e` won't kill the container if it dies — but it logs to
# the container's stdout/stderr, so the failure is visible in `docker logs`.
rsyslogd -n -f /run/rsyslog.conf &

# ── 3. Start snmpd ───────────────────────────────────────────────────────────
# snmpd listens on UDP 161. It also opens the AgentX master socket, but nothing
# connects to it: frr-snmp is not shipped in this image (see Dockerfile) and
# frr.conf.j2 emits no `agentx` line — so there is nothing to wait for here.
# ponytail: use default /etc/snmp/snmpd.conf (installed by net-snmp pkg);
# containerlab bind-mounts a node-specific one over it at runtime.
snmpd -f -Lo &

# ── 4. Start pmacctd (IPFIX exporter → nfacctd collector) ───────────────────
# Backgrounded (same reasoning as rsyslogd); logs to stdout so a failure such as
# pcap permission denied shows up in `docker logs` instead of a buried file.
pmacctd -f /run/pmacctd.conf &

# ── 5. Start FRR (foreground via /usr/lib/frr/docker-start) ──────────────────
# docker-start reads /etc/frr/daemons and starts all enabled daemons, then
# exec's watchfrr in the foreground — that keeps the container alive.
exec /usr/lib/frr/docker-start
