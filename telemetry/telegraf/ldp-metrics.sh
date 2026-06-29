#!/bin/bash
# ldp-metrics.sh — emit Prometheus text for LDP session state + BGP VRF prefix counts
# Called by telegraf inputs.exec; requires /var/run/docker.sock mounted r/o.
# ponytail: frr-snmp AgentX ABI mismatch → MPLS MIBs unreachable via SNMP poll.
#           exec+vtysh is the only reliable path for LDP telemetry.

set -eo pipefail

PE_NODES="pe1 pe2 pe3 pe4 pe5 pe6 pe7 pe8 pe9 pe10"
CLAB_PREFIX="clab-sdwan_mpls_noc"
VRFS="CORP VOICE GUEST"

echo "# HELP mpls_ldp_session_state LDP session state: 5=OPERATIONAL, 1=degraded/down"
echo "# TYPE mpls_ldp_session_state gauge"

for node in $PE_NODES; do
  output=$(docker exec "${CLAB_PREFIX}-${node}" vtysh -c "show mpls ldp neighbor" 2>/dev/null) || continue
  while IFS= read -r line; do
    # Match: IPv4 10.255.1.1:0  OPERATIONAL ...
    if [[ "$line" =~ ^IPv4[[:space:]]+([0-9.]+):0[[:space:]]+([A-Z_]+) ]]; then
      peer="${BASH_REMATCH[1]}"
      state_str="${BASH_REMATCH[2]}"
      state=1
      [[ "$state_str" == "OPERATIONAL" ]] && state=5
      echo "mpls_ldp_session_state{device=\"${node}\",peer=\"${peer}\"} ${state}"
    fi
  done <<< "$output"
done

echo "# HELP bgp_vrf_prefix_count Number of BGP prefixes in VRF"
echo "# TYPE bgp_vrf_prefix_count gauge"

for node in $PE_NODES; do
  for vrf in $VRFS; do
    json=$(docker exec "${CLAB_PREFIX}-${node}" vtysh -c "show bgp vrf ${vrf} summary json" 2>/dev/null) || continue
    count=$(echo "$json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('totalPrefixes',0))" 2>/dev/null) || count=0
    echo "bgp_vrf_prefix_count{device=\"${node}\",vrf=\"${vrf}\"} ${count}"
  done
done
