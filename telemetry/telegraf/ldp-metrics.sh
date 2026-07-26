#!/bin/bash
# ldp-metrics.sh — emit Prometheus text for MPLS-core control-plane telemetry that
# SNMP can't reach (frr-snmp AgentX ABI mismatch). docker exec + vtysh json is the
# only reliable path. Pushed to VictoriaMetrics by the noc-ldp-metrics sidecar.
#
# Series emitted:
#   mpls_ldp_session_state{device,peer}   5=OPERATIONAL, 1=down            (PE)
#   bgp_vrf_prefix_count{device,vrf}       RIB entries per VRF (all AFI/SAFI)(PE)
#   ospf_neighbor_state{device,peer}       1=Full adjacency, 0=not          (P+PE)
#   ospf_spf_last_duration_ms{device}      last SPF compute time            (P+PE)
#   ospf_spf_last_executed_ms{device}      msec-since-boot of last SPF run  (P+PE)
#   mpls_lsp_count{device}                 installed MPLS forwarding entries(P+PE)
#   bgp_peer_established{device}           Distinct Established BGP peers   (PE)
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
echo "# HELP bgp_vrf_prefix_count BGP RIB entries in VRF (sum of per-AFI/SAFI ribCount)"
echo "# TYPE bgp_vrf_prefix_count gauge"
# `show bgp vrf X summary json` has NO top-level totalPrefixes key (that key only
# exists in `show bgp neighbor prefix-counts json`). The summary is keyed by
# AFI/SAFI — {"ipv4Unicast": {..., "ribCount": N, ...}} — so sum ribCount.
# No default-to-zero: a VRF with no BGP AFI/SAFI emits no sample at all rather
# than a fake 0 that reads like a measured empty RIB.
for node in $PE_NODES; do
  for vrf in $VRFS; do
    json=$(vtj "$node" "show bgp vrf ${vrf} summary json") || continue
    echo "$json" | NODE=$node VRF=$vrf python3 -c '
import sys,os,json
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
fams=[v for v in d.values() if isinstance(v,dict) and "ribCount" in v]
if fams:
    print("bgp_vrf_prefix_count{device=\"%s\",vrf=\"%s\"} %d"
          % (os.environ["NODE"], os.environ["VRF"], sum(v["ribCount"] for v in fams)))
' 2>/dev/null || continue
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
echo "# HELP bgp_peer_established Distinct Established BGP peers on the default instance"
echo "# TYPE bgp_peer_established gauge"
# Peers activated in several AFI/SAFIs (ipv4Unicast AND ipv4Vpn here) appear once
# per family in the family-less summary. Count distinct peer addresses, else a
# healthy PE with 11 iBGP sessions reports 22.
for node in $PE_NODES; do
  json=$(vtj "$node" "show bgp summary json") || continue
  echo "$json" | NODE=$node python3 -c '
import sys,os,json
node=os.environ["NODE"]
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
peers=set()
for fam,fd in d.items():
    if isinstance(fd,dict):
        for addr,p in (fd.get("peers") or {}).items():
            if str(p.get("state"))=="Established": peers.add(addr)
print("bgp_peer_established{device=\"%s\"} %d" % (node,len(peers)))
' 2>/dev/null || continue
done
