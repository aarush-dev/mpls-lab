FROM quay.io/frrouting/frr:10.5.1

# Extra tooling for frr-node.
# FRR + vtysh already present; do NOT reinstall.
#
# frr-snmp (AgentX .so module) is NOT shipped by the base image and the Alpine
# repo only carries frr-snmp-10.2.2 (ABI mismatch with 10.5.1).  We therefore
# run net-snmp's snmpd as the AgentX MASTER and leave the FRR AgentX sub-agent
# disabled for now.  Telegraf (Phase 2 telemetry stack) polls IF-MIB / standard
# OIDs directly from snmpd; that gives the same telemetry signals the ML models
# need.  Re-evaluate when Alpine ships frr-snmp-10.5.x.
#
# ponytail: no grafana/telegraf/fluentd here — those live in telemetry/docker-compose.yml.
RUN apk add --no-cache \
    net-snmp \
    net-snmp-tools \
    pmacct \
    iproute2 \
    wireguard-tools \
    wireguard-go \
    iptables \
    tcpdump \
    bash

COPY start.sh /start.sh
COPY pmacctd.conf /etc/pmacctd.conf
RUN chmod +x /start.sh

# Overridable: containerlab can still exec into the container or override CMD.
CMD ["/start.sh"]
