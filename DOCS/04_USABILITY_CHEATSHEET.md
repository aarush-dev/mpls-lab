# Usability Cheatsheet — SD-WAN-over-MPLS NOC Lab

**Target Audience:** Anyone wanting to USE the lab — observe the network, run faults, check dashboards, get data.

**Format:** Copy-paste ready commands. Minimal prose. Maximum utility.

**See also:** [01 Project Overview](01_PROJECT_OVERVIEW.md) — context | [03 Technical Code Guide](03_TECHNICAL_CODE_GUIDE.md) — API & data format | [05 Technical Glossary](05_TECHNICAL_GLOSSARY.md) — term definitions

---

## 0. Prerequisites

The lab requires:
- Linux host: ~108 GB RAM, 19 cores (148 lab containers (70 FRR + 78 hosts) + 9 telemetry/infra ≈ 157 total)
- MPLS kernel modules: `mpls_router`, `mpls_gso`, `mpls_iptunnel`
- Tools: `containerlab`, `docker`, `docker-compose`, `python3` with `pandas`, `fastapi`, `uvicorn`

**Before starting:**
```bash
# Check MPLS kernel support
modprobe mpls_router 2>&1 | grep -q "^$" && echo "PASS: MPLS modules available" || echo "FAIL: enable MPLS in kernel"

# Check Phase 0 (detailed setup)
cat /root/LAB/DOCS/PHASE0ENVIRONMENT.md
```

---

## 1. Starting Everything

### Step 1: Generate all network configs from topology spec
```bash
cd /root/LAB/generator
python3 generate.py
# Output: /root/LAB/topology/clab.yml + per-node config dirs
# Expected: "Wrote 148 nodes to clab.yml" + "WireGuard keys cached"
```

### Step 2: Deploy the 148-container network
```bash
cd /root/LAB/topology
sudo containerlab deploy --topo clab.yml --recycle
# Expected: "deployed 148 nodes" (5–10 min on cold start; networking converges ~30s after)
```

### Step 3: Start the telemetry stack (Grafana, VictoriaMetrics, Loki, Telegraf)
```bash
cd /root/LAB/telemetry
docker compose up -d
# Expected: 6 containers running in ~10s
# Check: docker compose ps
```

### Step 4: Start the Data API (FastAPI on localhost:8000)
```bash
cd /root/LAB/dataapi
uvicorn app:app --host 127.0.0.1 --port 8000 &
# Expected: "Uvicorn running on http://127.0.0.1:8000"
# Test: curl http://127.0.0.1:8000/
```

### Step 5: Verify everything is healthy
```bash
# Containers running
docker ps | grep -E "tele-|clab-sdwan" | wc -l
# Expected: ~157 (148 network + 9 telemetry)

# Telemetry stack responsive
curl -s http://172.20.20.50:8428/api/v1/status/tsdb | jq '.status'
# Expected: "ok"

# Grafana reachable
curl -s http://172.20.20.51:3000/api/health | jq '.database'
# Expected: "ok"

# At least one metric from the network
curl -s "http://172.20.20.50:8428/api/v1/query?query=up" | jq '.data.result | length'
# Expected: > 0 (telemetry flowing from nodes)
```

---

## 2. Observing the Network — The Dashboard

### Grafana (the main UI)
```bash
# Open in browser
firefox http://172.20.20.51:3000 &

# Login (anonymous, auto-logged in as Admin)
# No password required (GF_AUTH_ANONYMOUS_ENABLED: true)
```

**Panels in the NOC Dashboard (11 total):**

| Panel | What It Shows | Best For |
|-------|---------------|----------|
| **Interface Utilization (Per Device)** | ifHCInOctets, ifHCOutOctets per site/interface | Spot traffic anomalies (load asymmetry, sudden drops) |
| **SD-WAN Tunnel Health** | latency_ms, jitter_ms, loss_pct per tunnel | Diagnose tunnel degradation precursors (ramp before loss) |
| **BGP/OSPF Events Log** | Syslog ADJCHANGE, neighbor state churn (Loki source) | Verify routing protocol churn during BGP flap faults |
| **Per-VRF Traffic** | ifHCInOctets grouped by vrf (CORP, VOICE, GUEST) | Isolate faults to a single VRF or verify cross-VRF isolation |
| **Controller State** | Path changes, rekey events, policy drift signals | Confirm SD-WAN controller reaction to faults |
| **OSPF Adjacency State** | ospf_neighbor_state{device,peer} 1=Full/0=not (P+PE) | Spot p_node_failure, srlg_cut, pop_isolation adjacency drops |
| **OSPF SPF Duration** | ospf_spf_last_duration_ms + ospf_spf_last_executed_ms | Detect area_flap → SPF churn / inter-area reconvergence |
| **MPLS LSP Count** | mpls_lsp_count{device} installed forwarding entries (P+PE) | Verify LSP table integrity after core faults |
| **BGP Peers Established** | bgp_peer_established{device} iBGP/VPNv4 established count | See rr_failure collapse cluster-wide (pe1 RR=22 peers) |

### VictoriaMetrics (raw time-series DB)
```bash
# Open in browser
firefox http://172.20.20.50:8428/vmui &

# Example PromQL queries (paste into the query field):
# Tunnel latency for a specific device (e.g. ce_branch1)
max(sdwan_tunnel_latency_ms{device="ce_branch1"})

# BGP prefix count per PE
bgp_prefixes{device=~"pe[0-9]"}

# Interface packet loss over time
increase(interface_ifOutErrors[5m])

# All metrics for one device
{device="ce_branch1"}
```

### Loki (log aggregation via Grafana)
In Grafana, click "Explore" → select "Loki" datasource.

**Example queries:**
```
# BGP adjacency changes
{device="ce_branch1"} | "ADJCHANGE"

# All syslog from a site
{device=~"ce_hub.*"}

# Severity filtering
{severity="ERR"}
```

---

## 3. Running Fault Scenarios

### List available fault types
```bash
cd /root/LAB/faults
python3 orchestrator.py --list
# Output (21 scenarios):
# congestion              Link/interface congestion: netem delay+loss ramp
# bgp_flap                BGP/OSPF adjacency flap; routing churn
# tunnel_degrade          SD-WAN tunnel jitter/loss decay
# policy_drift            CE VRF route-map: local-preference drift
# node_failure            Kill bgpd; watchfrr restarts (recoverable)
# asymmetric_loss         Egress-only packet loss
# brownout                Hard rate cap; bandwidth starvation
# mpls_underlay_failure   Drop all core ifaces on a P router
# ldp_session_flap        Flap LDP session on a PE
# hub_spoke_congest       Congestion on hub CE toward MPLS
# bgp_cascade             Hub CE BGP kill → cascade to spokes
# controller_drift        SD-WAN controller policy drift
# p_node_failure          Down ALL core ifaces of one P (full node loss)
# pop_isolation           Down all inter-POP links of one POP [named test only]
# core_partition          Bisect backbone ring → two area-0 islands [named test only]
# srlg_cut                Down one SRLG conduit (correlated multi-link fibre cut)
# core_congestion         netem delay+loss ramp on a backbone P-P link
# ospf_area_flap          Flap inter-POP (area-0) adjacency → SPF churn
# path_asymmetry          Raise OSPF cost one direction → asymmetric paths
# rr_failure              Kill bgpd on a Route Reflector → VPNv4 degraded cluster-wide
# gray_failure            0.5–2% loss on backbone link, no link-down (hard to detect)
```

### Run a single fault scenario (synchronous)
```bash
# Syntax: orchestrator.py --scenario <type> --target <device> [--severity low|medium|high] [--duration secs]

# Example: congestion on ce_branch1, high severity, 90 sec
cd /root/LAB/faults
python3 orchestrator.py --scenario congestion --target ce_branch1 --severity high --duration 90

# Expected output (JSON, one event per line):
# {"event": "inject", "scenario_id": "congestion-ce_branch1-abc12345", "t_start": "2026-06-21T...Z"}
# {"event": "impact", "scenario_id": "...", "t_impact": "...", "observed": 45.3}
# {"event": "revert", "scenario_id": "...", "t_end": "...Z"}
# {"event": "label_written", "row": {...}}

# While it runs: open Grafana, watch max(sdwan_tunnel_latency_ms{device="ce_branch1"}) climb then drop
```

### Quick demo (60 sec, all defaults)
```bash
cd /root/LAB/faults
python3 orchestrator.py --demo
# Runs congestion on ce_branch1, severity=high, holds for 60s
# Outputs before/after latency snapshots
```

### Verify the fault was recorded (ground-truth labels)
```bash
# View the label JSONL file
cat /root/LAB/faults/labels/labels.jsonl | tail -1 | jq .

# Expected fields:
# {
#   "scenario_id": "congestion-ce_branch1-abc12345",
#   "type": "congestion",
#   "target": {"device": "ce_branch1", "interface": "eth1"},
#   "severity": "high",
#   "t_start": "2026-06-21T...Z",
#   "t_impact": "2026-06-21T...Z",
#   "t_end": "2026-06-21T...Z",
#   "lead_time": 15.3,
#   "device": "ce_branch1",
#   "signature": "latency+jitter creep then loss on the affected site's tunnels"
# }
```

### Verify the fault in telemetry (Grafana + PromQL)
```bash
# Query: find the time window of your fault
# Max tunnel latency on ce_branch1 during the scenario
curl -s 'http://172.20.20.50:8428/api/v1/query_range?query=max(sdwan_tunnel_latency_ms%7Bdevice%3D%22ce_branch1%22%7D)&start=1719003000&end=1719003600&step=30' | jq '.data.result[0].values | .[-5:]'

# Expected: latency climbs from ~5ms to ~80ms during the fault window, then drops back after revert
```

### Run a full randomized fault campaign
```bash
# Syntax: orchestrator.py --campaign --duration <total_secs> [--mean-gap <sec>] [--seed <n>]
# Campaign: fires random faults on random targets, Poisson-distributed arrivals

cd /root/LAB/faults
python3 orchestrator.py --campaign --duration 600 --mean-gap 120 --seed 42
# Expected output:
# {"event": "campaign_start", "campaign_id": "campaign-abc123def456", "total_duration": 600, ...}
# {"event": "campaign_inject", "campaign_id": "...", "scenario_id": "...", ...}
# ... (repeats: concurrent faults on different targets)
# {"event": "campaign_summary", ..., "total_incidents": 4, "by_type": {...}, "fault_pct": 25.3}

# mean-gap=120 → ~1 fault per 2 min on average over 10 min = ~5 faults
# seed=42 → reproducible (same targets/timings every run with same seed)
```

### MPLS depth fault scenarios
```bash
PYTHONPATH=/root/LAB python3 faults/orchestrator.py --scenario mpls_underlay_failure --target p1 --severity medium --duration 30
PYTHONPATH=/root/LAB python3 faults/orchestrator.py --scenario ldp_session_flap --target pe1 --severity medium --duration 20
PYTHONPATH=/root/LAB python3 faults/orchestrator.py --scenario hub_spoke_congest --target ce_hub1 --severity medium --duration 60
PYTHONPATH=/root/LAB python3 faults/orchestrator.py --scenario bgp_cascade --target ce_hub2 --severity high --duration 45
PYTHONPATH=/root/LAB python3 faults/orchestrator.py --scenario controller_drift --target ce_hub1 --duration 120
```

### MPLS core fault scenarios (Phase 6 — 9 new)

All commands run from `/root/LAB/faults` with `PYTHONPATH=/root/LAB`.

**Target reference:** P faults → `p1..p24`; POP faults → `pop1..pop6`; SRLG conduits →
`srlg_pop1_2`, `srlg_pop2_3`, `srlg_pop3_4`, `srlg_pop4_5`, `srlg_pop5_6`, `srlg_pop6_1`
(ring) + `srlg_pop1_4`, `srlg_pop2_5`, `srlg_pop3_6` (chords);
core_congestion / ospf_area_flap / path_asymmetry / gray_failure → an ABR P node
(`p1,p2,p5,p6,p9,p10,p13,p14,p17,p18,p21,p22`); rr_failure → `pe1` or `pe2`.

```bash
cd /root/LAB/faults

# P node failure — down ALL core interfaces of one P; all LSPs reroute
PYTHONPATH=/root/LAB python3 orchestrator.py --scenario p_node_failure --target p9 --duration 30

# POP isolation — cut all inter-POP links of one POP (region partition)
# NOTE: named test only — excluded from random campaign; run explicitly
PYTHONPATH=/root/LAB python3 orchestrator.py --scenario pop_isolation --target pop2 --duration 30

# Core partition — bisect the backbone ring → two area-0 islands
# NOTE: named test only — excluded from random campaign; run explicitly
PYTHONPATH=/root/LAB python3 orchestrator.py --scenario core_partition --target pop1 --duration 30

# SRLG cut — take down one SRLG conduit (both redundant inter-POP links together)
PYTHONPATH=/root/LAB python3 orchestrator.py --scenario srlg_cut --target srlg_pop1_2
PYTHONPATH=/root/LAB python3 orchestrator.py --scenario srlg_cut --target srlg_pop1_4  # chord

# Core congestion — netem delay+loss ramp on a P-P backbone link
PYTHONPATH=/root/LAB python3 orchestrator.py --scenario core_congestion --target p1 --severity high --duration 60

# OSPF area flap — flap an inter-POP (area-0) adjacency → SPF churn
PYTHONPATH=/root/LAB python3 orchestrator.py --scenario ospf_area_flap --target p2 --duration 30

# Path asymmetry — raise OSPF cost one direction → forward/return paths diverge
PYTHONPATH=/root/LAB python3 orchestrator.py --scenario path_asymmetry --target p5 --duration 60

# Route reflector failure — kill bgpd on RR → VPNv4 propagation degrades cluster-wide
PYTHONPATH=/root/LAB python3 orchestrator.py --scenario rr_failure --target pe1 --duration 30

# Gray failure — 0.5–2% loss on a backbone link, no link-down event (slow, hard to detect)
PYTHONPATH=/root/LAB python3 orchestrator.py --scenario gray_failure --target p10 --duration 120
```

**Campaign behaviour:** The Poisson campaign mixes edge + core + catastrophic + correlated faults ("chaos"). `pop_isolation` and `core_partition` are excluded from the random campaign pool (they are named Phase-6 tests). `--list` shows all 21 scenarios; `--campaign` picks from the 19-scenario pool.

### Revert a stuck fault manually (if needed)
```bash
# If a fault didn't revert cleanly, restore baseline netem on a device
cd /root/LAB/faults

# Option 1: revert a specific injector type
docker exec clab-sdwan_mpls_noc-ce_branch1 tc qdisc show dev eth1
# If netem is lingering, restore fq_codel baseline:
docker exec clab-sdwan_mpls_noc-ce_branch1 \
  tc qdisc replace dev eth1 parent 1:30 handle 30: fq_codel

# Option 2: check BGP flap is settled
docker exec clab-sdwan_mpls_noc-ce_branch1 vtysh -c "show bgp vrf vrf_CORP summary"
# Sessions should show "Up" state and a steady prefix count

# Option 3: check WireGuard tunnels are up
docker exec clab-sdwan_mpls_noc-ce_branch1 ip link show wg0
# Expected: "UP,LOWER_UP"
```

---

## 4. Querying the Data API

### Root endpoint (list all)
```bash
curl http://127.0.0.1:8000/
# {
#   "service": "noc-copilot-dataapi",
#   "endpoints": ["/metrics", "/events", "/flows", "/labels", "/topology", "/datasets"],
#   "join_key": "device"
# }
```

### /metrics — PromQL passthrough to VictoriaMetrics

**Instant query (snapshot now):**
```bash
curl 'http://127.0.0.1:8000/metrics?query=max(sdwan_tunnel_latency_ms)' | jq .
# {"result": [{"metric": {"device": "ce_branch1"}, "value": [1719003456, "45.3"]}]}
```

**Range query (time window, e.g., last hour):**
```bash
# Get hourly trend of interface packet drops
curl 'http://127.0.0.1:8000/metrics?query=increase(interface_ifOutErrors%5B5m%5D)&start=1719002400&end=1719006000&step=300' | jq .
# {"result": [{"metric": {...}, "values": [[1719002400, "0"], [1719002700, "5"], ...]}]}
```

### /events — Loki log rows for a device
```bash
# Get all syslog from ce_branch1 in the last 1 hour
curl 'http://127.0.0.1:8000/events?device=ce_branch1' | jq '.rows | length'
# Returns up to 1000 rows (adjustable with ?limit=500)

# Get all CRITICAL events
curl 'http://127.0.0.1:8000/events?limit=100' | jq '.rows[] | select(.severity == "CRIT")'
```

### /flows — Recent NetFlow records
```bash
# Last 500 flows from any device
curl 'http://127.0.0.1:8000/flows?limit=500' | jq '.rows | length'

# Flows from a specific site (e.g., hub)
curl 'http://127.0.0.1:8000/flows?device=ce_hub1&limit=100' | jq '.rows[0]'
# {
#   "device": "ce_hub1",
#   "flow_bytes": 1024000,
#   "flow_packets": 5000,
#   "timestamp": "2026-06-21T23:30:00Z",
#   ...
# }
```

### /labels — All ground-truth fault labels
```bash
# List every fault scenario that was run
curl 'http://127.0.0.1:8000/labels' | jq '.rows | length'

# Show all "congestion" faults
curl 'http://127.0.0.1:8000/labels' | jq '.rows[] | select(.type == "congestion")'

# Show faults on ce_branch1 with lead time > 10 sec
curl 'http://127.0.0.1:8000/labels' | jq '.rows[] | select(.device == "ce_branch1" and .lead_time > 10)'
```

### /topology — Network graph (nodes + links)
```bash
# Get the full topology as JSON
curl 'http://127.0.0.1:8000/topology' | jq '.nodes | length'
# Expected: 148 (70 routers + 78 hosts)

curl 'http://127.0.0.1:8000/topology' | jq '.nodes[] | select(.role == "PE") | .name'
# pe1, pe2, pe3, pe4, pe5, pe6, pe7, pe8, pe9, pe10, pe11, pe12
```

### /datasets — ML-ready labeled Parquet (the main one)

**Get the latest pre-built dataset:**
```bash
curl -o dataset.parquet 'http://127.0.0.1:8000/datasets'
# Downloads the most recent labeled Parquet to ./dataset.parquet (~100–500 MB)
```

**Build a fresh dataset for a specific time window:**
```bash
# Build for last 1 hour
START=$(date -d '1 hour ago' +%s)
END=$(date +%s)
curl -o dataset_fresh.parquet "http://127.0.0.1:8000/datasets?start=${START}&end=${END}&step=30&build=true"

# Expected: joins metrics + flows + events + labels into one table, 21 columns
# Size: ~500K–2M rows per hour (depends on step size and fault count)
```

---

## 5. Working with the Dataset

### Load Parquet in Python
```python
import pandas as pd

df = pd.read_parquet("dataset.parquet")
print(df.shape)  # (N rows, 21 columns)
print(df.columns.tolist())
# ['ts', 'device', 'site_type', 'vrf', 'entity', 'entity_type',
#  'if_in_octets', 'if_out_octets', 'if_oper_status',
#  'tunnel_latency_ms', 'tunnel_jitter_ms', 'tunnel_loss_pct', 'tunnel_rekeys',
#  'flow_bytes', 'flow_packets',
#  'is_fault', 'scenario_id', 'fault_type', 'severity', 'lead_time_s', 'time_to_impact_s']
```

### Quick EDA (exploratory data analysis)
```python
# Fault distribution
print(df[df['is_fault']]['fault_type'].value_counts())
# congestion        450
# bgp_flap          320
# tunnel_degrade    280
# ...

# Devices with the most faults
print(df[df['is_fault']]['device'].value_counts().head())

# Lead time statistics (precursor visibility)
print(df[df['is_fault']]['lead_time_s'].describe())
# count    1050
# mean        8.3
# min         0.1
# max        45.2

# Healthy vs. faulty rows
print(f"Healthy: {(~df['is_fault']).sum()}, Faulty: {df['is_fault'].sum()}")
```

### Filter by fault type
```python
# Get all tunnel degradation events
tunnel_faults = df[df['fault_type'] == 'tunnel_degrade']
print(f"{len(tunnel_faults)} rows during tunnel degradation")

# Get precursor data (rows with lead_time > 0, before impact)
precursor = df[(df['is_fault']) & (df['lead_time_s'] > 0)]
print(f"Precursor phase: {len(precursor)} observable rows")
```

### Train/test split by scenario_id (prevent data leakage)
```python
from sklearn.model_selection import train_test_split

# Split by unique scenario_id, not random rows
# This ensures an entire fault episode stays in one fold

fault_episodes = df[df['is_fault']]['scenario_id'].unique()
train_ids, test_ids = train_test_split(fault_episodes, test_size=0.2, random_state=42)

train_df = df[df['scenario_id'].isin(train_ids)]
test_df = df[df['scenario_id'].isin(test_ids)]

print(f"Train: {len(train_df)} rows ({len(train_ids)} scenarios)")
print(f"Test:  {len(test_df)} rows ({len(test_ids)} scenarios)")
```

### Plot tunnel latency around a fault event
```python
import matplotlib.pyplot as plt

# Pick one fault scenario
scenario_id = df[df['is_fault']]['scenario_id'].iloc[0]
device = df[df['scenario_id'] == scenario_id]['device'].iloc[0]

# Get all tunnel metrics for this device during this scenario + 5 min padding
scenario_rows = df[(df['scenario_id'] == scenario_id) & (df['device'] == device)]
start_ts = pd.to_datetime(scenario_rows['ts'].min()) - pd.Timedelta(minutes=5)
end_ts = pd.to_datetime(scenario_rows['ts'].max()) + pd.Timedelta(minutes=5)

window = df[(df['device'] == device) & 
            (pd.to_datetime(df['ts']) >= start_ts) & 
            (pd.to_datetime(df['ts']) <= end_ts)]

plt.figure(figsize=(12, 4))
plt.plot(pd.to_datetime(window['ts']), window['tunnel_latency_ms'], label='latency_ms')
plt.fill_between(pd.to_datetime(scenario_rows['ts']), 0, 100, alpha=0.3, color='red', label='fault window')
plt.xlabel('Time')
plt.ylabel('Latency (ms)')
plt.title(f"Tunnel Latency: {device} during {scenario_id}")
plt.legend()
plt.tight_layout()
plt.savefig(f"fault_{scenario_id}.png")
print(f"Saved to fault_{scenario_id}.png")
```

---

## 6. Generating More Synthetic Data

The lab includes a synthetic data generator calibrated to real network captures.

### Generate synthetic dataset (demo: 2 days, 10x scale)
```bash
cd /root/LAB/synthetic
python3 generate.py --days 2 --scale 10
# Expected output: synthetic_output_TIMESTAMP.parquet (~50M rows, ~500MB)
# Located: /root/LAB/synthetic/output/
```

### Scale up (7 days, 20x):
```bash
cd /root/LAB/synthetic
python3 generate.py --days 7 --scale 20 --step 30
# Expected: 8.89M rows (real-scale dataset for ML training)
# Time: ~5 min on 19 cores
```

### Scale down (1 day, 1x, test):
```bash
cd /root/LAB/synthetic
python3 generate.py --days 1 --scale 1
# Quick validation: ~250K rows, ~30s
```

### Adjust parameters
```bash
# Change time bucket size (default 30 sec)
python3 generate.py --days 1 --step 60  # 1-minute buckets

# Change fault injection rate (internal, use injector campaign for real faults)
# Edit synthetic/generate.py: FAULT_RATE_PER_DEVICE_PER_DAY parameter
```

### Load synthetic + real Parquet together
```python
import pandas as pd

# Real lab data
df_real = pd.read_parquet("/root/LAB/dataapi/datasets/dataset.parquet")

# Synthetic (matches schema exactly)
df_synth = pd.read_parquet("/root/LAB/synthetic/output/synthetic_output_*.parquet")

# Combine for training
df_combined = pd.concat([df_real, df_synth], ignore_index=True)
print(f"Combined: {len(df_combined)} rows")

# ML team can now train on 8.89M rows with full fault diversity
```

---

## 7. Scaling the Network Up/Down

All topology parameters are in one file: `/root/LAB/topology-spec.yaml`

### Current scale (148 containers, stable)
```yaml
knobs:
  p_count:             24
  pe_count:            12
  pop_count:           6
  p_per_pop:           4
  multi_area:          true
  igp_cost_intra:      10
  igp_cost_inter:      100
  inter_pop_redundancy: 2
  inter_pop_chords:    [[1,4],[2,5],[3,6]]
  branch_count:        24
  hub_count:           6
  dc_count:            4
  # Total: 24 P + 12 PE + (24+6+4) CE + 78 hosts = 148 lab containers
```

### Scale down (20 containers, dev/testing)
```bash
# Edit /root/LAB/topology-spec.yaml
nano /root/LAB/topology-spec.yaml
# Change:
# p_count:  2
# pe_count: 2
# branch_count: 4
# hub_count: 2
# dc_count: 2

# Regenerate
cd /root/LAB/generator
python3 generate.py

# Redeploy
cd /root/LAB/topology
sudo containerlab deploy --topo clab.yml --recycle
# Expected: 20 containers, deploy in ~2 min
```

### Scale up (150+ containers, max stable)
```bash
# WARNING: requires > 150 GB RAM. Use only on high-end hardware.
nano /root/LAB/topology-spec.yaml
# Increase CE counts (P/PE core is already at designed capacity):
# branch_count: 32
# hub_count: 8
# dc_count: 8

cd /root/LAB/generator && python3 generate.py
cd /root/LAB/topology && sudo containerlab deploy --topo clab.yml --recycle
# Expected: ~15 min deploy, intense disk I/O (kernel page table creation)
```

### Time estimates
| Scale | Containers | Deploy Time | Convergence | RAM Used |
|-------|-----------|-------------|-------------|----------|
| dev   | 20        | 2 min       | 30s         | 20 GB    |
| prod  | 148       | 8 min       | 45s         | 108 GB   |
| max   | 150+      | 15 min      | 60s         | 200+ GB  |

---

## 8. Air-Gap Operations

The lab is packaged for offline deployment (zero internet egress at runtime).

### Step 1: Pull and save all images (on a machine with internet)
```bash
cd /root/LAB/airgap
./pull-and-save.sh

# Expected output:
# === Ensuring registry images are present ===
#   [pulling] victoriametrics/victoria-metrics:v1.103.0
#   ...
# === Saving images to /root/LAB/airgap/images ===
#   [save] frr-node:0.1 → frr-node_0_1.tar.xz ... done
#   ...
# Total bundle size: 4.2 GB

# Output: airgap/images/*.tar.xz + manifest.txt
```

### Step 2: Transfer to offline host
```bash
# On machine with internet:
cd /root/LAB/airgap
tar czf lab-images.tar.gz images/ manifest.txt
# or manually copy the images/ folder via USB/network

# On offline host: extract
scp -r <user>@<online>:/root/LAB/airgap/images/ /root/LAB/airgap/
# or copy from USB: cp -r /mnt/usb/images /root/LAB/airgap/
```

### Step 3: Load all images on offline host
```bash
cd /root/LAB/airgap
./load-offline.sh

# Expected output:
# === Loading 11 image bundle(s) into Docker ===
#   [load] frr_node_0_1.tar.xz ... Loaded image: frr-node:0.1
#   ...
# === Verification: confirming expected tags present ===
#   [ok] frr-node:0.1
#   ...
# All expected images present. Host is ready for offline deploy.
```

### Step 4: Verify air-gap (zero internet egress)
```bash
cd /root/LAB/airgap
./verify-airgap.sh

# Expected output:
# === 1. Containerlab image-pull-policy: Never ===
#   [PASS] All 90/90 node image entries have image-pull-policy: Never
# === 2. Telemetry stack images present locally (compose won't pull) ===
#   [PASS] Present: frr-node:0.1
#   ...
# === 3. Runtime egress: tcpdump on eth0 for container→public traffic (30s) ===
#   [PASS] Zero container→public packets in 30s (lab is air-gapped at runtime)
# === 4. Sanity: no running 'docker pull' processes ===
#   [PASS] No docker pull processes running
#
# ========================================
#   PASS: 14   FAIL: 0
# ========================================
# RESULT: AIR-GAP VERIFIED
```

### Step 5: Deploy on offline host
```bash
# Use the exact same deploy steps as online (Steps 1–5 in Section 1)
# All images are already loaded locally → no registry pulls needed

cd /root/LAB/topology
sudo containerlab deploy --topo clab.yml --recycle
# Expected: pulls images from local Docker → NO network egress
```

---

## 9. Debugging

### Check if a node is up
```bash
# List all deployed nodes
containerlab inspect --topo /root/LAB/topology/clab.yml

# Get live status of one node
docker exec clab-sdwan_mpls_noc-ce_branch1 ps aux | grep -E "bgpd|ospfd"
# Expected: bgpd and ospfd running, plus watchfrr

# Check node console logs
docker logs clab-sdwan_mpls_noc-ce_branch1 | tail -20

# Get FRR status (routing daemons)
docker exec clab-sdwan_mpls_noc-ce_branch1 vtysh -c "show version"
```

### Check if telemetry is flowing
```bash
# Query VictoriaMetrics for recent samples
curl -s 'http://172.20.20.50:8428/api/v1/query?query=up' | jq '.data.result | length'
# Expected: > 70 (at least one metric per FRR node; SNMP covers all 70)

# Count time-series per metric
curl -s 'http://172.20.20.50:8428/api/v1/label/__name__/values' | jq 'length'
# Expected: > 100 (hundreds of metric names)

# Check Telegraf scrape (SNMP collection)
docker logs tele-telegraf 2>&1 | grep -i "metric" | tail -5
```

### Get logs for a specific router
```bash
# Syslog (Loki)
curl -s 'http://127.0.0.1:8000/events?device=ce_branch1&limit=10' | jq '.rows[0]'

# Container logs
docker logs clab-sdwan_mpls_noc-ce_branch1 | tail -50

# FRR config validation (check if applied)
docker exec clab-sdwan_mpls_noc-ce_branch1 vtysh -c "show bgp vrf vrf_CORP summary"
```

### Check telemetry stack health
```bash
# All services up
docker compose -f /root/LAB/telemetry/docker-compose.yml ps

# VictoriaMetrics status
docker logs tele-victoriametrics 2>&1 | tail -10 | grep -i "started\|error"

# Grafana status
curl -s http://172.20.20.51:3000/api/health | jq .

# Loki ingest
curl -s http://172.20.20.54:3100/ready

# Telegraf scrape count
docker logs tele-telegraf 2>&1 | grep "metric" | tail -1
```

### Common failure modes and fixes

| Symptom | Cause | Fix |
|---------|-------|-----|
| 172.20.20.50 unreachable | Telemetry stack not running | `docker compose -f /root/LAB/telemetry/docker-compose.yml up -d` |
| No metrics in Grafana | Telegraf not scraping | Check `/root/LAB/telemetry/telegraf/telegraf.conf` targets, restart telemetry stack |
| BGP sessions flapping | OSPF not converged yet | Wait 30–60s for convergence, check `docker exec clab-sdwan_mpls_noc-p1 vtysh -c "show ip route"` |
| WireGuard tunnel down | Node crashed or netem stuck | `docker restart clab-sdwan_mpls_noc-<device>`, verify with `ip link show wg0` |
| Data API 502 error | VictoriaMetrics unreachable | `curl http://172.20.20.50:8428/api/v1/status/tsdb` (should return 200) |
| Fault didn't revert | Netem/BGP session stuck | Manual revert: see Section 3 "Revert a stuck fault manually" |
| Parquet download hangs | export.py still building | Check `ps aux | grep export.py`; wait or Ctrl+C and retry |

---

## 10. Check Link Latency / Measured RTT

Per-site netem baselines are always active on `eth0` (transport interface toward the PE): branch ≈41 ms, hub ≈17 ms, DC ≈12 ms.

### See the per-site netem impairment
```bash
# Branch (expect ~41ms netem delay)
docker exec clab-sdwan_mpls_noc-ce_branch1 tc qdisc show dev eth0

# DC (expect ~12ms netem delay)
docker exec clab-sdwan_mpls_noc-ce_dc1 tc qdisc show dev eth0
```

### Measure real tunnel RTT over WireGuard
```bash
# Ping the hub tunnel endpoint from a branch CE
# RTT ≈ branch netem (41ms) + hub netem (17ms) = ~58ms base
docker exec clab-sdwan_mpls_noc-ce_branch1 ping -c5 -I wg0 172.16.0.1
```

### Confirm site-type latency tiers in metrics
```bash
# VictoriaMetrics PromQL: average tunnel latency grouped by site type
# Expected: branch > hub > dc
curl -sg 'http://172.20.20.50:8428/api/v1/query?query=avg+by+(site_type)(sdwan_tunnel_latency_ms)' | jq '.data.result'
```

---

## 11. MPLS Depth

### Verify BFD sessions
```bash
docker exec clab-sdwan_mpls_noc-pe1 vtysh -c "show bfd peers brief"
```

### Verify route-reflector clients (pe3–pe12 should peer only to pe1+pe2)
```bash
docker exec clab-sdwan_mpls_noc-pe3 vtysh -c "show bgp summary" | grep "10.255.2"  # should only show pe1+pe2
```

### LDP session metrics
```bash
curl -s "http://172.20.20.50:8428/api/v1/query?query=mpls_ldp_session_state" | python3 -m json.tool | head -20
```

### BGP VRF prefix counts
```bash
curl -s "http://172.20.20.50:8428/api/v1/query?query=bgp_vrf_prefix_count" | python3 -m json.tool | head -20
```

### MPLS core telemetry metrics (new in Phase 6)

These metrics are emitted by the `noc-ldp-metrics` sidecar (container `172.20.20.58`) via
vtysh JSON polling, pushed to VictoriaMetrics. SNMP now covers all 70 FRR nodes (70 agents).

| Metric | Labels | Coverage | Notes |
|--------|--------|----------|-------|
| `ospf_neighbor_state` | `{device,peer}` | P+PE (~156 series) | 1=Full, 0=not; drops reveal node/link faults |
| `ospf_spf_last_duration_ms` | `{device}` | P+PE | Last SPF compute time; jumps on area_flap |
| `ospf_spf_last_executed_ms` | `{device}` | P+PE | Boot-relative timestamp of last SPF run |
| `mpls_lsp_count` | `{device}` | P+PE | Installed MPLS forwarding entries (~107/node) |
| `bgp_peer_established` | `{device}` | PE | Established iBGP/VPNv4 peers (RR=22, client=4) |

```bash
# OSPF neighbor state on P node (should be 1.0 for all peers when healthy)
curl -s "http://172.20.20.50:8428/api/v1/query?query=ospf_neighbor_state%7Bdevice%3D%22p1%22%7D" | python3 -m json.tool

# SPF churn — watch this spike during ospf_area_flap fault
curl -s "http://172.20.20.50:8428/api/v1/query?query=ospf_spf_last_duration_ms" | python3 -m json.tool | head -20

# BGP peers established (pe1 as RR should show 22 when healthy; drops during rr_failure fault)
curl -s "http://172.20.20.50:8428/api/v1/query?query=bgp_peer_established%7Bdevice%3D%22pe1%22%7D" | python3 -m json.tool

# MPLS forwarding table size on a core P node
curl -s "http://172.20.20.50:8428/api/v1/query?query=mpls_lsp_count%7Bdevice%3D%22p1%22%7D" | python3 -m json.tool
```

---

## 12. Quick Reference Card

### Services and Endpoints

| Service | Container | Port | URL | Purpose |
|---------|-----------|------|-----|---------|
| **Grafana** | tele-grafana | 3000 | http://172.20.20.51:3000 | NOC dashboards, log explorer |
| **VictoriaMetrics** | tele-victoriametrics | 8428 | http://172.20.20.50:8428 | Metrics time-series DB, PromQL |
| **Loki** | tele-loki | 3100 | http://172.20.20.54:3100 | Log aggregation (Syslog sink) |
| **Telegraf** | tele-telegraf | — | 172.20.20.52 (internal) | SNMP collector (push to VM) |
| **nfacctd** | tele-nfacctd | 2055/udp | 172.20.20.53 | IPFIX flow collector |
| **Controller** | noc-controller | 9362 | http://172.20.20.56:9362 | SD-WAN path selection (Prometheus metrics) |
| **Traffic Gen** | noc-trafficgen | — | (internal) | Diurnal traffic simulator (drives flows) |
| **Data API** | (host) | 8000 | http://127.0.0.1:8000 | ML-ready endpoints: /metrics, /flows, /labels, /datasets |

### Most-Used Commands (One Per Line)

```bash
# Inspect/status
containerlab inspect --topo /root/LAB/topology/clab.yml
docker ps | grep -E "tele-|clab-sdwan" | wc -l
docker logs clab-sdwan_mpls_noc-ce_branch1 | tail -20

# Start/stop
cd /root/LAB/topology && sudo containerlab deploy --topo clab.yml --recycle
cd /root/LAB/telemetry && docker compose up -d
cd /root/LAB/dataapi && uvicorn app:app --host 127.0.0.1 --port 8000 &

# Faults
cd /root/LAB/faults && python3 orchestrator.py --demo
cd /root/LAB/faults && python3 orchestrator.py --scenario congestion --target ce_branch1 --severity high
cd /root/LAB/faults && python3 orchestrator.py --campaign --duration 600 --mean-gap 120

# Data
curl http://127.0.0.1:8000/labels | jq '.rows | length'
curl -o dataset.parquet 'http://127.0.0.1:8000/datasets'
cd /root/LAB/synthetic && python3 generate.py --days 7 --scale 10

# Config
cd /root/LAB/generator && python3 generate.py
nano /root/LAB/topology-spec.yaml

# Air-gap
cd /root/LAB/airgap && ./pull-and-save.sh
cd /root/LAB/airgap && ./load-offline.sh
cd /root/LAB/airgap && ./verify-airgap.sh
```

### Key File Locations

| File | Purpose | Edit To |
|------|---------|---------|
| `/root/LAB/topology-spec.yaml` | Network scale + addressing | Scale the lab (PE/CE counts, VRFs) |
| `/root/LAB/generator/generate.py` | Topology generator (Jinja2) | Add new device types or address schemes |
| `/root/LAB/faults/orchestrator.py` | Fault orchestration + labeler | Add new fault scenario types |
| `/root/LAB/dataapi/app.py` | Data API endpoints | Add new queries or export formats |
| `/root/LAB/dataapi/export.py` | Join metrics+labels→Parquet | Change canonical column schema |
| `/root/LAB/telemetry/docker-compose.yml` | Telemetry stack config | Add new collectors or change image tags |
| `/root/LAB/telemetry/grafana/dashboards/*.json` | Grafana panels | Customize dashboard visualizations |
| `/root/LAB/synthetic/generate.py` | Synthetic data generator | Tweak diurnal curves or fault injection rates |
| `/root/LAB/airgap/pull-and-save.sh` | Air-gap bundler | Update image list for new services |
| `/root/LAB/airgap/verify-airgap.sh` | Air-gap validator | Change egress filter rules (rare) |

### Dataset Schema (21 columns)

```
ts, device, site_type, vrf, entity, entity_type,
if_in_octets, if_out_octets, if_oper_status,
tunnel_latency_ms, tunnel_jitter_ms, tunnel_loss_pct, tunnel_rekeys,
flow_bytes, flow_packets,
is_fault, scenario_id, fault_type, severity, lead_time_s, time_to_impact_s
```

**Join key for all telemetry:** `device` (e.g., "ce_branch1", "pe1", "p3")

---

## Summary

1. **Start the lab:** generate → deploy → telemetry stack → data API (5 commands)
2. **Observe:** Grafana dashboards or PromQL queries (instant access)
3. **Run faults:** demo, single scenario, or campaign mode (3 commands)
4. **Verify:** labels in JSONL, metrics in VictoriaMetrics, logs in Loki (curl queries)
5. **Get data:** /datasets endpoint → Parquet for ML (1 curl command)
6. **Scale:** Edit topology-spec.yaml + regenerate + redeploy (3 commands)
7. **Air-gap:** pull-and-save → load-offline → verify-airgap (3 bash scripts)

**All commands are copy-paste ready.** No manual intervention needed once the lab is running.

For detailed architecture, see `/root/LAB/PLAN.md`.
For environment checklist, see `/root/LAB/DOCS/PHASE0ENVIRONMENT.md`.

---

**Navigation:** ← [03 Technical Code Guide](03_TECHNICAL_CODE_GUIDE.md) | [05 Technical Glossary](05_TECHNICAL_GLOSSARY.md) →
