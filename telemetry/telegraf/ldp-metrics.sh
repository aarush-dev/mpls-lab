#!/bin/bash
# ldp-metrics.sh — emit Prometheus text for MPLS-core control-plane telemetry that
# SNMP can't reach (frr-snmp AgentX ABI mismatch). docker exec + vtysh json is the
# only reliable path. Pushed to VictoriaMetrics by the noc-ldp-metrics sidecar.
#
# Series emitted:
#   mpls_ldp_session_state{device,peer}   5=OPERATIONAL, 1=down            (PE)
#   bgp_vrf_prefix_count{device,vrf}       prefixes per VRF                 (PE)
#   ospf_neighbor_state{device,peer}       1=Full adjacency, 0=not          (P+PE)
#   ospf_spf_last_duration_ms{device}      last SPF compute time            (P+PE)
#   ospf_spf_last_executed_ms{device}      msec-since-boot of last SPF run  (P+PE)
#   mpls_lsp_count{device}                 installed MPLS forwarding entries(P+PE)
#   bgp_peer_established{device}           Established iBGP/VPNv4 peers      (PE)
#
# The OSPF/LSP/BGP-peer series are the MPLS precursors the predictive NOC needs:
# area-flap → spf_* moves; node/SRLG cut → neighbor drops + lsp_count falls;
# RR failure → bgp_peer_established collapses cluster-wide.

set -eo pipefail

CLAB_PREFIX="clab-sdwan_mpls_noc"
PE_NODES=$(seq -f "pe%g" 1 12)
P_NODES=$(seq -f "p%g" 1 24)
PEP_NODES="$P_NODES $PE_NODES"
VRFS="CORP VOICE GUEST"

vtj() { docker exec "${CLAB_PREFIX}-$1" vtysh -c "$2" 2>/dev/null; }

# ── LDP session state (PE) ───────────────────────────────────────────────────
echo "# HELP mpls_ldp_session_state LDP session state: 5=OPERATIONAL, 1=degraded/down"
echo "# TYPE mpls_ldp_session_state gauge"
for node in $PE_NODES; do
  output=$(vtj "$node" "show mpls ldp neighbor") || continue
  while IFS= read -r line; do
    if [[ "$line" =~ ^ipv4[[:space:]]+([0-9.]+)[[:space:]]+([A-Z_]+) ]]; then
      peer="${BASH_REMATCH[1]}"; state_str="${BASH_REMATCH[2]}"
      state=1; [[ "$state_str" == "OPERATIONAL" ]] && state=5
      echo "mpls_ldp_session_state{device=\"${node}\",peer=\"${peer}\"} ${state}"
    fi
  done <<< "$output"
done

# ── BGP VRF prefix count (PE) ────────────────────────────────────────────────
echo "# HELP bgp_vrf_prefix_count Number of BGP prefixes in VRF"
echo "# TYPE bgp_vrf_prefix_count gauge"
for node in $PE_NODES; do
  for vrf in $VRFS; do
    json=$(vtj "$node" "show bgp vrf ${vrf} summary json") || continue
    count=$(echo "$json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('totalPrefixes',0))" 2>/dev/null) || count=0
    echo "bgp_vrf_prefix_count{device=\"${node}\",vrf=\"${vrf}\"} ${count}"
  done
done

# ── OSPF neighbor state (P+PE) ───────────────────────────────────────────────
echo "# HELP ospf_neighbor_state OSPF adjacency: 1=Full, 0=not full"
echo "# TYPE ospf_neighbor_state gauge"
for node in $PEP_NODES; do
  json=$(vtj "$node" "show ip ospf neighbor json") || continue
  echo "$json" | NODE=$node python3 -c '
import sys,os,json
node=os.environ["NODE"]
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
for rid,lst in (d.get("neighbors") or {}).items():
    if not isinstance(lst,list): lst=[lst]
    for nb in lst:
        st=1 if str(nb.get("nbrState","")).startswith("Full") else 0
        print("ospf_neighbor_state{device=\"%s\",peer=\"%s\"} %d" % (node,rid,st))
' 2>/dev/null || continue
done

# ── OSPF SPF stats (P+PE) — convergence-stress precursor ─────────────────────
echo "# HELP ospf_spf_last_duration_ms Duration of the last OSPF SPF computation"
echo "# TYPE ospf_spf_last_duration_ms gauge"
echo "# HELP ospf_spf_last_executed_ms Msec-since-boot of the last OSPF SPF run (jumps on each run)"
echo "# TYPE ospf_spf_last_executed_ms gauge"
for node in $PEP_NODES; do
  json=$(vtj "$node" "show ip ospf json") || continue
  echo "$json" | NODE=$node python3 -c '
import sys,os,json
node=os.environ["NODE"]
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
dur=d.get("spfLastDurationMsecs"); ex=d.get("spfLastExecutedMsecs")
if dur is not None: print("ospf_spf_last_duration_ms{device=\"%s\"} %s" % (node,dur))
if ex  is not None: print("ospf_spf_last_executed_ms{device=\"%s\"} %s" % (node,ex))
' 2>/dev/null || continue
done

# ── MPLS LSP count (P+PE) ────────────────────────────────────────────────────
echo "# HELP mpls_lsp_count Installed MPLS forwarding entries (label-switched paths)"
echo "# TYPE mpls_lsp_count gauge"
for node in $PEP_NODES; do
  json=$(vtj "$node" "show mpls table json") || continue
  echo "$json" | NODE=$node python3 -c '
import sys,os,json
node=os.environ["NODE"]
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
print("mpls_lsp_count{device=\"%s\"} %d" % (node, len(d) if isinstance(d,dict) else 0))
' 2>/dev/null || continue
done

# ── BGP peer established (PE) — RR/VPNv4 control-plane health ─────────────────
echo "# HELP bgp_peer_established Count of Established BGP peers on the default instance"
echo "# TYPE bgp_peer_established gauge"
for node in $PE_NODES; do
  json=$(vtj "$node" "show bgp summary json") || continue
  echo "$json" | NODE=$node python3 -c '
import sys,os,json
node=os.environ["NODE"]
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
n=0
for fam,fd in d.items():
    if isinstance(fd,dict):
        for p in (fd.get("peers") or {}).values():
            if str(p.get("state"))=="Established": n+=1
print("bgp_peer_established{device=\"%s\"} %d" % (node,n))
' 2>/dev/null || continue
done
