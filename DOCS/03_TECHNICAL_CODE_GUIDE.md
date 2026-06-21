# Technical Code Guide — Air-Gapped Predictive NOC Copilot

> Target reader: ML/AI engineer, Day 1. You know Python. You do not know networking.
> This guide tells you what the data is, where to get it, and how to ship a model on top of it.

**See also:** [02 Architecture Analogies](02_ARCHITECTURE_ANALOGIES.md) — network concepts | [05 Technical Glossary](05_TECHNICAL_GLOSSARY.md) — term lookup | [04 Usability Cheatsheet](04_USABILITY_CHEATSHEET.md) — quick commands

---

## 1. Quick Orientation: The Data Flow

Everything flows in one direction: physical network containers emit signals, a telemetry stack collects and stores them, a data API joins and labels them, and your ML model consumes the result.

```
90 Docker containers (FRR routers + hosts)
        |
        | SNMP polls every 30s        → Telegraf → VictoriaMetrics (PromQL)
        | Syslog (BGP events)         → Promtail → Loki (LogQL)
        | NetFlow/IPFIX packets       → nfacctd (JSON logs)
        |
        v
  Data API  (localhost:8000)
        |
        | GET /datasets?build=true
        v
  Labeled Parquet (21 columns, join key = "device")
        |
        v
  Your ML Model
```

The join key across every signal is the string `device` (for example `"ce_branch1"` or `"pe2"`). Every API endpoint accepts a `device` filter. Every row in the Parquet has a `device` column.

**Starting the stack** — two commands from the repo root:

```bash
# 1. Start the telemetry collectors (VictoriaMetrics, Loki, Grafana, Telegraf, nfacctd)
cd /root/LAB/telemetry && docker compose up -d

# 2. Deploy the 90-container network lab
cd /root/LAB && clab deploy -t topology/clab.yml

# 3. Start the data API
cd /root/LAB/dataapi && uvicorn app:app --host 127.0.0.1 --port 8000
```

Grafana dashboards: `http://172.20.20.51:3000` (admin/admin). VictoriaMetrics: `http://127.0.0.1:8428`. Data API: `http://localhost:8000`.

---

## 2. The Data API — Your Primary Interface

The Data API (`/root/LAB/dataapi/app.py`) is a thin FastAPI wrapper. All business logic lives in `sources.py` (data access) and `export.py` (the join that builds labeled Parquet). You call it with `curl` or `requests`.

```python
# /root/LAB/dataapi/app.py — root endpoint shows everything
@app.get("/")
def root():
    return {
        "service": "noc-copilot-dataapi",
        "endpoints": ["/metrics", "/events", "/flows", "/labels", "/topology", "/datasets"],
        "join_key": "device",
    }
```

### /metrics — Time-Series from VictoriaMetrics

PromQL is a query language for time-series data. Two things to know: (1) you filter by label inside `{}`, e.g. `{device="ce_branch1"}`; (2) range queries return a matrix of `[timestamp, value]` pairs at your chosen step interval.

```bash
# Instant query: current tunnel latency on one device
curl "localhost:8000/metrics?query=sdwan_tunnel_latency_ms{device=\"ce_branch1\"}"

# Range query: latency over the last hour, 30-second buckets
NOW=$(date +%s)
curl "localhost:8000/metrics?query=sdwan_tunnel_latency_ms{device=\"ce_branch1\"}&start=$((NOW-3600))&end=$NOW&step=30"
```

```python
import requests, time

api = "http://localhost:8000"
now = int(time.time())

# Range query returns list of {metric: {labels}, values: [[ts, val], ...]}
resp = requests.get(f"{api}/metrics", params={
    "query": 'sdwan_tunnel_latency_ms{device="ce_branch1"}',
    "start": now - 3600,
    "end": now,
    "step": 30,
})
series = resp.json()["result"]
# series[0]["values"] = [[1719123000, "24.3"], [1719123030, "25.1"], ...]
```

The `/metrics` endpoint is a passthrough to VictoriaMetrics. You can use any valid PromQL expression. See Section 9 for five useful queries.

### /events — Log Events from Loki

Loki stores structured syslog lines. BGP adjacency changes, routing events, and controller messages all land here. The `device` filter is a Loki label selector.

```bash
# All BGP events on ce_branch1 in the last hour
NOW=$(date +%s)
curl "localhost:8000/events?device=ce_branch1&start=$((NOW-3600))&end=$NOW"
```

```python
resp = requests.get(f"{api}/events", params={
    "device": "ce_branch1",
    "start": now - 3600,
    "end": now,
    "limit": 500,
})
for row in resp.json()["rows"]:
    print(row["ts"], row["app"], row["line"])
    # e.g. "2026-06-21T10:00:05Z", "bgp", "%BGP-5-ADJCHANGE: neighbor 10.1.0.1 Up"
```

Each row has: `ts` (ISO UTC), `device`, `app`, `severity`, `line` (raw log text).

### /flows — NetFlow Records

NetFlow records show which IP pairs are talking, on what ports, and how much data. Useful for detecting traffic anomalies that don't show up in SNMP counters.

```bash
curl "localhost:8000/flows?device=ce_branch1&limit=100"
```

```python
resp = requests.get(f"{api}/flows", params={"device": "ce_branch1", "limit": 100})
for row in resp.json()["rows"]:
    print(row["ip_src"], "->", row["ip_dst"], row["proto"], row["bytes"])
```

Each row has: `ts`, `device`, `ip_src`, `ip_dst`, `port_src`, `port_dst`, `proto`, `bytes`, `packets`.

### /labels — Ground-Truth Fault Timeline

This is the label store. Every fault injection writes a JSONL record to `faults/labels/labels.jsonl`. The `/labels` endpoint returns them all.

```python
resp = requests.get(f"{api}/labels")
for label in resp.json()["rows"]:
    print(label["scenario_id"], label["type"], label["device"],
          label["t_start"], "->", label["t_impact"], "lead_time:", label["lead_time"])
```

A label record looks like:

```json
{
  "scenario_id": "congestion-ce_branch1-a3f92b1c",
  "type": "congestion",
  "device": "ce_branch1",
  "severity": "high",
  "t_start": "2026-06-21T10:00:00Z",
  "t_impact": "2026-06-21T10:00:52Z",
  "t_end": "2026-06-21T10:01:30Z",
  "lead_time": 52.0,
  "impact_method": "vm_threshold",
  "signature": "latency+jitter creep then loss on the affected site's tunnels"
}
```

`lead_time` is the gap in seconds between fault start and user-visible impact — this is what your ML model is trying to predict ahead of time.

### /topology — Graph JSON for GNNs

Returns the network graph: nodes (routers, hosts) and edges (physical links).

```python
resp = requests.get(f"{api}/topology")
graph = resp.json()
print(len(graph["nodes"]), "nodes,", len(graph["links"]), "links")
# nodes: [{"id": "ce_branch1", "role": "ce_branch", "site_type": "branch", "vrfs": ["CORP", "VOICE"]}, ...]
# links: [{"source": "ce_branch1", "target": "pe1", "source_if": "eth1", "target_if": "eth5"}, ...]
```

See Section 7 for how to load this as a NetworkX graph for GNNs.

### /datasets — The Main Event: Labeled Parquet

This is the endpoint your training pipeline will call. It joins all signals, applies labels, and returns a Parquet file.

```bash
# Build a fresh dataset for the last hour
NOW=$(date +%s)
curl "localhost:8000/datasets?start=$((NOW-3600))&end=$NOW&build=true" -o dataset.parquet

# Return the most recently built dataset (no rebuild)
curl "localhost:8000/datasets" -o dataset.parquet
```

```python
import pandas as pd
import requests

# Download and load in one shot
resp = requests.get(f"{api}/datasets", params={
    "start": now - 3600,
    "end": now,
    "build": True,
})
with open("/tmp/dataset.parquet", "wb") as f:
    f.write(resp.content)

df = pd.read_parquet("/tmp/dataset.parquet")
print(df.shape)           # (N_rows, 21)
print(df.dtypes)
print(df["is_fault"].value_counts())
print(df["fault_type"].value_counts())
```

**Start EDA immediately:**

```python
# Basic distribution of fault types
df[df["is_fault"]].groupby("fault_type")["time_to_impact_s"].describe()

# Tunnel latency during faults vs. healthy
df.groupby("is_fault")["tunnel_latency_ms"].agg(["mean", "median", "std"])

# Per-device fault rate
df.groupby("device")["is_fault"].mean().sort_values(ascending=False)

# The precursor window: is_fault=True but time_to_impact_s > 0 (fault started,
# impact not yet felt — this is what ML needs to learn)
precursor = df[(df["is_fault"]) & (df["time_to_impact_s"] > 0)]
print(f"{len(precursor)} precursor rows ({precursor['time_to_impact_s'].mean():.1f}s avg ahead)")
```

**Train/test split by fault scenario** (never split within a scenario — that leaks):

```python
from sklearn.model_selection import GroupShuffleSplit

# Use scenario_id as the group — keeps all rows of one incident together
groups = df["scenario_id"].fillna("healthy")
gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
train_idx, test_idx = next(gss.split(df, groups=groups))

train = df.iloc[train_idx]
test = df.iloc[test_idx]

print(f"Train: {len(train)} rows, {train['is_fault'].mean()*100:.1f}% fault")
print(f"Test:  {len(test)} rows, {test['is_fault'].mean()*100:.1f}% fault")
```

---

## 3. The Dataset Schema — What Every Column Means

The canonical 21-column schema is defined in `export.py` (`COLUMNS` list). Every Parquet file — both real and synthetic — has exactly these columns in this order.

```python
# From /root/LAB/dataapi/export.py
COLUMNS = [
    "ts", "device", "site_type", "vrf", "entity", "entity_type",
    "if_in_octets", "if_out_octets", "if_oper_status",
    "tunnel_latency_ms", "tunnel_jitter_ms", "tunnel_loss_pct", "tunnel_rekeys",
    "flow_bytes", "flow_packets",
    "is_fault", "scenario_id", "fault_type", "severity",
    "lead_time_s", "time_to_impact_s",
]
```

**Identity columns:**

| Column | Type | Description |
|--------|------|-------------|
| `ts` | str (ISO UTC) | 30-second bucket timestamp |
| `device` | str | Node name. Join key across all signals. E.g. `"ce_branch1"` |
| `site_type` | str | One of `branch`, `hub`, `dc`, `pe`, `core` |
| `vrf` | str (nullable) | Virtual network: `CORP`, `VOICE`, `GUEST`. Null on most rows (VRF not on live SNMP series). |
| `entity` | str | The specific interface or tunnel being measured. E.g. `"eth1"` or `"ce_branch1-ce_hub1"` |
| `entity_type` | str | Either `"interface"` or `"tunnel"` — splits every device into two row types |

**Interface metrics** (only populated when `entity_type == "interface"`):

| Column | Type | Description |
|--------|------|-------------|
| `if_in_octets` | float64 | Cumulative inbound byte counter (SNMP ifHCInOctets). Monotonically increasing. Diff it between rows to get rate. |
| `if_out_octets` | float64 | Cumulative outbound byte counter |
| `if_oper_status` | float64 | Interface operational status: `1.0` = up, `0.0` = down |

**Tunnel metrics** (only populated when `entity_type == "tunnel"`):

| Column | Type | Description |
|--------|------|-------------|
| `tunnel_latency_ms` | float64 | WireGuard tunnel RTT in milliseconds. **Measured baseline** (real ping over wg0, refreshed ~every 45 s) plus additive modelled congestion (diurnal queue, fault netem readback, noise). Not fully synthetic. |
| `tunnel_jitter_ms` | float64 | Latency variance. **Measured** as ping max−min from the RTT cache, plus an AR(1) random walk. High jitter hurts VoIP (VOICE VRF) before loss starts. |
| `tunnel_loss_pct` | float64 | Packet loss percentage. `max(measured_loss, modelled_floor) + micro-bursts`. Rises last in congestion, first in asymmetric_loss. |
| `tunnel_rekeys` | float64 | Cumulative WireGuard handshake counter. Clustering = rekey anomaly. |

> **How the baseline is produced:** Each CE container has a per-site netem delay on `eth0` set by the lab generator (branch ~41 ms, hub ~17 ms, dc ~12 ms). The controller `ping`s the WireGuard peer IP from inside the spoke container every ~45 s (`MEASURE_RTT=1` env gate) to read this TRUE physical RTT. On top of that it adds a diurnal congestion model, live netem readback from fault injection on `eth1`, and small noise. Result: `avg by(site_type)(sdwan_tunnel_latency_ms)` shows branch ~51 ms > dc ~32 ms in live data.

**Flow aggregates** (joined from NetFlow, bucketed per device):

| Column | Type | Description |
|--------|------|-------------|
| `flow_bytes` | float64 | Total bytes across all flows for this device in this 30s bucket |
| `flow_packets` | float64 | Total packets. Null in many rows (NetFlow not always available). |

**Label columns — the training targets:**

| Column | Type | Description |
|--------|------|-------------|
| `is_fault` | bool | **The label.** True if this row falls inside a known fault window. |
| `scenario_id` | str (nullable) | Unique ID for this fault instance. Use for GroupShuffleSplit. |
| `fault_type` | str (nullable) | Which fault: `congestion`, `bgp_flap`, `tunnel_degrade`, `policy_drift`, `node_failure`, `asymmetric_loss`, `brownout` |
| `severity` | str (nullable) | `low`, `medium`, or `high` — maps to impairment magnitude |
| `lead_time_s` | float64 | Seconds between fault start and user-visible impact. The **prediction horizon**. E.g. `52.0` means the model had 52 seconds of precursor signal before anyone felt anything. |
| `time_to_impact_s` | float64 | Seconds from THIS ROW until impact. Positive = before impact (precursor). Zero = impact moment. Negative = post-impact. |

**What a good prediction looks like:**

```
Fault: congestion on ce_branch1
t_start = 10:00:00   (fault injected)
t_impact = 10:00:52  (latency crosses threshold; users feel it)
t_end   = 10:01:30   (fault reverted)

Rows:
ts=10:00:00  is_fault=True  time_to_impact_s=+52.0   <- precursor starts
ts=10:00:30  is_fault=True  time_to_impact_s=+22.0   <- latency rising
ts=10:00:52  is_fault=True  time_to_impact_s=0.0     <- IMPACT
ts=10:01:00  is_fault=True  time_to_impact_s=-8.0    <- post-impact
ts=10:01:30  is_fault=True  time_to_impact_s=-38.0   <- fault ends
ts=10:02:00  is_fault=False (null)                   <- recovered
```

A model that predicts `is_fault=True` at `ts=10:00:00` (52 seconds early) scores full lead time. A model that fires at `ts=10:01:00` (post-impact) is reactive, not predictive. The evaluation metric is `lead_time_s` at prediction time, not just accuracy.

---

## 4. Fault Scenarios — The Training Signal

Seven fault types are implemented: four mandated by the problem statement, three adversarial extras to harden the model.

### All 7 Fault Types

| Scenario | What it simulates in plain English | Primary metric signal |
|----------|-------------------------------------|----------------------|
| `congestion` | Progressive packet delay + loss buildup on a branch uplink. Like a cable getting increasingly congested. Ramps over 6 steps. | `tunnel_latency_ms` rising, then `tunnel_loss_pct` |
| `bgp_flap` | BGP routing sessions drop and reconnect repeatedly. Routes withdraw then re-advertise. Short transient (2–4s impact). | `bgp` ADJCHANGE bursts in Loki events |
| `tunnel_degrade` | WireGuard tunnel jitter and loss climbing, plus abnormal handshake retries. Models a degraded ISP path. | `tunnel_jitter_ms` + `tunnel_loss_pct` + `tunnel_rekeys` |
| `policy_drift` | Route-map config change causes traffic to take a suboptimal path. Observable as a BGP path selection change. | BGP soft-clear in Loki; subtle metric shift |
| `node_failure` | The routing daemon (bgpd) is killed hard. Watchdog restarts it within seconds–minutes. | Interface down + BGP process gap in events |
| `asymmetric_loss` | Loss only on the outbound direction. Latency stays normal. Hard to diagnose manually. | `tunnel_loss_pct` up, `tunnel_latency_ms` near-normal |
| `brownout` | Hard rate cap on uplink bandwidth. Queue builds, latency rises, loss comes late. | `tunnel_latency_ms` climb under load |

### Triggering a Single Fault Programmatically

```python
import subprocess, json

# From the repo root, trigger a high-severity congestion fault on ce_branch1
result = subprocess.run(
    ["python3", "faults/orchestrator.py",
     "--scenario", "congestion",
     "--target", "ce_branch1",
     "--severity", "high",
     "--duration", "90"],
    cwd="/root/LAB",
    capture_output=True, text=True
)
# Each event is a JSON line on stdout
for line in result.stdout.splitlines():
    print(json.loads(line))
```

Or call the orchestrator directly from Python:

```python
import sys
sys.path.insert(0, "/root/LAB/faults")
import orchestrator

# Returns the label row dict and writes to faults/labels/labels.jsonl
label = orchestrator.run_scenario(
    name="tunnel_degrade",
    target="ce_branch1",
    severity="medium",
    duration=60,
)
print(label["lead_time"], "seconds of precursor signal")
```

### Running a Full Fault Campaign

The campaign scheduler uses Poisson-distributed arrivals (realistic burstiness) and runs concurrent faults on different devices. This is how the 8.89M-row dataset was collected.

```bash
# 1-hour campaign, ~1 fault per 2 minutes, reproducible seed
python3 /root/LAB/faults/orchestrator.py \
  --campaign \
  --duration 3600 \
  --mean-gap 120 \
  --seed 42
```

```python
# Or from Python
summary = orchestrator.run_campaign(
    total_duration=3600,   # 1 hour
    mean_gap=120,          # avg ~1 fault per 2 minutes
    seed=42,               # reproducible for dataset versioning
)
print(summary["total_incidents"], "faults injected")
print(summary["by_type"])         # breakdown per scenario
print(summary["fault_pct"], "% of time was faulted")
```

After the campaign, build the labeled Parquet:

```bash
NOW=$(date +%s)
curl "localhost:8000/datasets?start=$((NOW-3600))&end=$NOW&build=true" -o campaign.parquet
```

### The lead_time_s Concept

`lead_time_s` is the gap between when the fault starts and when users feel the impact. For `congestion`, the ramp creates a precursor window of ~50 seconds: latency is already rising, but loss has not yet crossed the detection threshold. That 50-second window is what the ML model must exploit.

```python
# Understanding the lead time distribution
df[df["is_fault"]].groupby("fault_type")["lead_time_s"].agg(["mean", "min", "max"])

#                  mean   min    max
# bgp_flap          2.0   2.0    2.0   (transient; hard to catch early)
# brownout         55.0  55.0   55.0   (slow buildup; easiest to predict)
# congestion       52.0  38.0   66.0   (ramp; 38-66s window)
# tunnel_degrade   40.0  25.0   55.0
# policy_drift      3.0   3.0    3.0   (nearly instant)
# node_failure      1.0   1.0    1.0   (no precursor)
# asymmetric_loss  30.0  20.0   45.0
```

---

## 5. Adding New Fault Types

The injector/orchestrator architecture makes adding fault types a two-file change.

### Step 1: Add an Injector Class in injectors.py

Every injector implements `apply()` and `revert()`. Here is a minimal skeleton — and a concrete `dns_flood` example:

```python
# /root/LAB/faults/injectors.py

class DnsFlood:
    """Simulate a DNS amplification flood: send high-rate UDP to port 53.
    Observable as traffic spike on flow records and if_in_octets.
    revert() kills the flood process.
    """

    def __init__(self, device, target_ip="192.168.0.1", rate_pps=5000, duration_s=60):
        self.device = device
        self.target_ip = target_ip
        self.rate_pps = rate_pps
        self.duration_s = duration_s
        self._pid = None

    def apply(self):
        # hping3 is available on host containers; run in background
        result = dexec(
            self.device,
            "hping3", "--udp", "-p", "53",
            "--flood", "--rand-source",
            self.target_ip,
            "--count", str(self.rate_pps * self.duration_s),
        )
        # parse PID from output if needed, or track via a background thread
        return {
            "injector": "dns_flood",
            "device": self.device,
            "target_ip": self.target_ip,
            "rate_pps": self.rate_pps,
        }

    def revert(self):
        # Kill any hping3 processes on the container
        dexec(self.device, "pkill", "-f", "hping3")
        return {"reverted": "dns_flood", "device": self.device}
```

### Step 2: Add a Scenario Builder in orchestrator.py

```python
# /root/LAB/faults/orchestrator.py

def scen_dns_flood(target, severity, duration):
    """(extra) DNS amplification flood: traffic spike visible in flow records."""
    s = SEVERITY[severity]
    rate = int(1000 + 4000 * s)          # low=1k pps, high=5k pps
    injector = inj.DnsFlood(target, rate_pps=rate, duration_s=int(duration))
    probe = f'rate(interface_ifHCInOctets{{device="{target}"}}[60s])'
    return {
        "type": "dns_flood",
        "target": {"device": target},
        "injector": injector, "ramp": False, "duration": duration,
        "probe": probe, "threshold": 5e6,         # 5 MB/s threshold
        "impact_method": "vm_threshold",
        "signature": "UDP port-53 flood; traffic spike in flows and SNMP counters",
    }

# Then register it:
SCENARIOS["dns_flood"] = scen_dns_flood

# And add it to the campaign pool:
CAMPAIGN_POOLS["dns_flood"] = _CE_ALL
_DURATION_BOUNDS["dns_flood"] = (20, 60)
```

That is the entire extension. The label schema, the `/datasets` join, and the synthetic generator all pick it up automatically once the scenario key exists.

---

## 6. Synthetic Data

The synthetic generator (`/root/LAB/synthetic/generate.py`) produces Parquet files in the exact same 21-column schema as the real data API output. Real and synthetic are `pd.concat`-compatible with no transformation.

**What was built:** 8.89M rows covering 7 days of simulated telemetry across all 34 network devices, with fault episodes injected at calibrated rates. Fault signatures (how much latency rises, how long the precursor lasts) are derived from real lab captures via `calibrate.py`.

### Generating More Data

```bash
cd /root/LAB/synthetic

# First calibrate from real data (run once after a lab capture session)
python3 calibrate.py   # reads newest dataapi/datasets/*.parquet, writes profile.json

# Generate 7 days at default density
python3 generate.py --days 7 --step 30 --scale 1.0

# Generate more fault episodes (scale=4 = 4x more fault events per hour)
python3 generate.py --days 7 --step 30 --scale 4.0

# Output lands in synthetic/output/
ls synthetic/output/*.parquet
```

```python
# Combine real + synthetic for training
import pandas as pd, glob

real = pd.read_parquet("dataapi/datasets/dataset_*.parquet")   # or specific file
synth_files = glob.glob("synthetic/output/*.parquet")
synth = pd.concat([pd.read_parquet(f) for f in synth_files], ignore_index=True)

combined = pd.concat([real, synth], ignore_index=True)
print(combined.shape)
print(combined["is_fault"].value_counts())
```

### Adding a New Fault Type to the Synthetic Generator

In `synthetic/generate.py`, fault injection calls `_inject_faults()`, which reads fault signatures from `profile.json`. Add your new scenario there:

```python
# synthetic/calibrate.py — add to the defaults dict inside _fault_signatures()
defaults = {
    # ... existing entries ...
    "dns_flood": {
        "lat_peak": 5.0,      # latency barely changes
        "loss_peak": 0.1,     # minimal loss
        "jit_peak": 1.0,
        "lead_s": 5.0,        # traffic spike is nearly instant
        "kind": "iface_churn",  # if_in_octets spikes
    },
}
```

Then re-run `calibrate.py` to regenerate `profile.json`, and `generate.py` picks it up.

**Note on `kind`:** Three injection patterns exist in the synthetic generator:

| kind | What it does |
|------|-------------|
| `tunnel_ramp` | Ramps `tunnel_latency_ms`, `tunnel_jitter_ms`, `tunnel_loss_pct` toward peak values |
| `iface_churn` | Spikes `if_in_octets` by up to 40% above baseline |
| `iface_down` | Sets `if_oper_status = 0.0` after impact moment |

---

## 7. The Topology Graph for GNNs

The `/topology` endpoint returns the full network graph derived from `topology/clab.yml`. This is the input for any graph-aware model.

```python
import requests, networkx as nx

resp = requests.get("http://localhost:8000/topology")
topo = resp.json()

# Build a NetworkX graph
G = nx.Graph()

for node in topo["nodes"]:
    G.add_node(node["id"],
               role=node["role"],
               site_type=node.get("site_type"),
               vrfs=node.get("vrfs", []))

for link in topo["links"]:
    G.add_edge(link["source"], link["target"],
               source_if=link["source_if"],
               target_if=link["target_if"])

print(f"{G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

# Explore the graph
import networkx as nx
print("Shortest path ce_branch1 -> ce_dc1:", nx.shortest_path(G, "ce_branch1", "ce_dc1"))
print("Degree (most connected):", sorted(G.degree(), key=lambda x: x[1], reverse=True)[:5])
```

**Node attributes** your model can use:

```python
# Node roles: "p" (core), "pe" (provider edge), "ce_branch", "ce_hub", "ce_dc", "host"
# These map directly to the site_type in the Parquet dataset

# Get all CE branch nodes
branches = [n for n, d in G.nodes(data=True) if d["role"] == "ce_branch"]

# Find all neighbors of a faulted device (for fault propagation modeling)
def affected_neighbors(G, faulted_device, hops=2):
    return list(nx.single_source_shortest_path_length(G, faulted_device, cutoff=hops).keys())

affected = affected_neighbors(G, "ce_branch1")
# Use this to build a GNN subgraph centered on the fault
```

**For PyTorch Geometric:**

```python
import torch
from torch_geometric.data import Data
from torch_geometric.utils import from_networkx

# Convert to PyG format
pyg_graph = from_networkx(G)

# Add per-node features from the Parquet (align by device name)
node_order = list(G.nodes())  # graph node ordering
device_to_idx = {d: i for i, d in enumerate(node_order)}

# Build feature matrix: [latency, jitter, loss] for each node
df_tunnel = df[df["entity_type"] == "tunnel"].groupby("device")[
    ["tunnel_latency_ms", "tunnel_jitter_ms", "tunnel_loss_pct"]
].mean()

X = torch.zeros(len(node_order), 3)
for device, row in df_tunnel.iterrows():
    if device in device_to_idx:
        i = device_to_idx[device]
        X[i] = torch.tensor([row["tunnel_latency_ms"],
                              row["tunnel_jitter_ms"],
                              row["tunnel_loss_pct"]])
pyg_graph.x = X
```

---

## 8. Scaling the Lab

The entire 90-container lab is defined by a single YAML file: `/root/LAB/topology-spec.yaml`. Changing counts there and re-running the generator regenerates all 90 node configs.

### The Scaling Knobs

```yaml
# /root/LAB/topology-spec.yaml
knobs:
  p_count:  5          # P-routers (MPLS core, OSPF only)
  pe_count: 5          # PE-routers (BGP, VPNv4, connects to CEs)

  branch_count: 16     # Branch sites (small, CORP+VOICE only)
  hub_count:    4      # Hub sites (CORP+VOICE+GUEST, WireGuard hubs)
  dc_count:     4      # DC sites (CORP+VOICE+GUEST, WireGuard spokes)
```

Host count is derived automatically: `branch * 2 + (hub + dc) * 3 = 56`. Total containers = `p + pe + branch + hub + dc + hosts = 90`.

**What the numbers mean for your ML dataset:**

| Change | Effect on dataset |
|--------|------------------|
| `branch_count: 32` | 2x more branch CE devices, 2x more tunnel metrics, 2x more fault targets |
| `hub_count: 8` | More hub concentrators = more WireGuard tunnels per spoke |
| `pe_count: 10` | Larger MPLS core; PE-PE BGP sessions scale as C(n,2) |

**After changing knobs**, regenerate configs and redeploy:

```bash
cd /root/LAB
python3 generator/generate.py          # reads topology-spec.yaml, writes topology/
clab destroy -t topology/clab.yml      # tear down old lab
clab deploy -t topology/clab.yml       # bring up new lab
```

### Adding a New VRF

In `topology-spec.yaml`, under the `vrfs:` section:

```yaml
vrfs:
  # ... existing CORP, VOICE, GUEST ...
  IOT:
    rd_community: "65000:40"
    dscp_class: BE
    qos_priority: 4
    sites: [branch, hub]    # which site types get this VRF
```

The generator derives all CE VRF configs, PE route-distinguishers, and QoS classes from this. No per-node config editing needed.

---

## 9. Telemetry Queries — Getting Raw Signals

These queries work directly against VictoriaMetrics (`http://127.0.0.1:8428`) or via the `/metrics` endpoint.

### 5 Useful PromQL Queries for EDA

```python
import requests, time, pandas as pd

def vm_range(query, minutes=60, step=30):
    """Helper: run a PromQL range query, return a tidy DataFrame."""
    now = int(time.time())
    r = requests.get("http://127.0.0.1:8428/api/v1/query_range", params={
        "query": query, "start": now - minutes*60, "end": now, "step": step,
    })
    rows = []
    for series in r.json()["data"]["result"]:
        m = series["metric"]
        for ts, val in series["values"]:
            rows.append({**m, "ts": int(ts), "value": float(val)})
    return pd.DataFrame(rows)

# 1. Interface utilization (bytes/s) on all CE uplinks
df_util = vm_range('rate(interface_ifHCInOctets{site_type="branch"}[60s])')

# 2. Tunnel latency over time — spot anomalies
df_lat = vm_range('sdwan_tunnel_latency_ms')

# 3. BGP prefix count — drops signal routing instability
# (BGP "withdraws" show as count drops)
df_bgp = vm_range('bgp_prefixes_received')

# 4. Packet loss rate across all tunnels
df_loss = vm_range('sdwan_tunnel_loss_pct')

# 5. Cross-site traffic volume (aggregate)
df_flows = vm_range('sum by (device) (rate(interface_ifHCInOctets[60s]))')
```

### Querying Loki for BGP Events Around a Fault

```python
import requests

def loki_query(logql, start_epoch, end_epoch, limit=500):
    r = requests.get("http://127.0.0.1:3100/loki/api/v1/query_range", params={
        "query": logql,
        "start": str(start_epoch) + "000000000",  # Loki uses nanoseconds
        "end":   str(end_epoch)   + "000000000",
        "limit": limit,
        "direction": "forward",
    })
    rows = []
    for stream in r.json()["data"]["result"]:
        for ts_ns, line in stream["values"]:
            rows.append({"ts": int(ts_ns) / 1e9, "line": line,
                         **stream["stream"]})
    return rows

# Get all BGP adjacency changes on ce_branch1
import time
now = int(time.time())
events = loki_query('{device="ce_branch1", app="bgp"}', now - 3600, now)
for e in events:
    if "ADJCHANGE" in e["line"]:
        print(e["ts"], e["line"])
```

### Joining Metrics + Events + Labels in Pandas

```python
import pandas as pd, requests, time

api = "http://localhost:8000"
now = int(time.time())
window = {"start": now - 3600, "end": now}

# 1. Load the labeled Parquet
resp = requests.get(f"{api}/datasets", params={**window, "build": True})
with open("/tmp/ds.parquet", "wb") as f: f.write(resp.content)
df = pd.read_parquet("/tmp/ds.parquet")
df["ts_epoch"] = pd.to_datetime(df["ts"]).astype("int64") // 1e9

# 2. Load BGP events
events = requests.get(f"{api}/events", params=window).json()["rows"]
events_df = pd.DataFrame(events)
events_df["ts_epoch"] = pd.to_datetime(events_df["ts"]).astype("int64") // 1e9
bgp_events = events_df[events_df["line"].str.contains("ADJCHANGE", na=False)]

# 3. For each fault window, count BGP events in that window
def count_events_in_window(device, t_start_epoch, t_end_epoch):
    mask = (
        (bgp_events["device"] == device) &
        (bgp_events["ts_epoch"] >= t_start_epoch) &
        (bgp_events["ts_epoch"] <= t_end_epoch)
    )
    return bgp_events[mask].shape[0]

# 4. Add event count as a feature to the dataset
labels = requests.get(f"{api}/labels").json()["rows"]
for lab in labels:
    t0 = pd.Timestamp(lab["t_start"]).timestamp()
    t1 = pd.Timestamp(lab["t_end"]).timestamp()
    n_bgp = count_events_in_window(lab["device"], t0, t1)
    print(f"{lab['type']} on {lab['device']}: {n_bgp} BGP events in window")
```

---

## 10. The RAG Corpus

The RAG corpus (`/root/LAB/ragcorpus/`) contains the documents the LLM copilot (Phase 3/4) will retrieve as context when explaining a fault. There are currently four files:

| File | Purpose |
|------|---------|
| `topology-map.md` | Layer diagram of all devices, VRFs, and site roles. The LLM reads this to know what `ce_branch1` is. |
| `runbook-bgp-adjacency-down.md` | Triage steps for BGP session drops. Tied to `bgp_flap` and `node_failure` scenarios. |
| `runbook-tunnel-latency-high.md` | Triage steps for SD-WAN tunnel degradation. Tied to `congestion`, `tunnel_degrade`, `asymmetric_loss`, `brownout`. |
| `incident-template.md` | Structured template the copilot fills in when generating an operator-facing alert. |

Each runbook links to Data API endpoints directly (`GET /metrics?query=...`, `GET /events?device=...`), so the LLM can suggest exact commands.

### Adding New Runbooks

Follow the existing format. The critical elements:

```markdown
# Runbook — <descriptive title>

> RAG seed. Ties to fault scenarios `<scenario_a>` and `<scenario_b>`.

## Symptom
[One paragraph: what the operator sees]

## Telemetry signature
- **Metrics** (`/metrics`): [which PromQL query, what to look for]
- **Dataset rows**: [which columns change, what `entity_type` to filter on]
- **Events** (`/events`): [Loki label selectors and keywords]

## Triage
[Numbered steps. Reference the data API by endpoint.]

## Resolution
[What fixes it in the lab vs. production]
```

Naming convention: `runbook-<fault-type>-<symptom>.md`. After adding a file, re-run the RAG ingestion script (Phase 4) to re-embed it into the vector database.

**The LLM copilot should use these documents as retrieval context.** For every structured model alert (is_fault prediction + fault_type), retrieve the matching runbook and inject it into the LLM context window before generation. The `fault_type` column in the Parquet maps directly to the scenario names referenced in the runbook `> RAG seed` headers.

---

## 11. Recommended ML Approach

This section maps the four competition objectives to specific model choices. The dataset you have makes these tractable.

### Objective 1: Time-Series Forecasting for Congestion

**Target:** Predict `is_fault=True` before `time_to_impact_s` reaches zero.

**Signal:** `tunnel_latency_ms`, `tunnel_jitter_ms`, `tunnel_loss_pct` over the last N timesteps. These values are **measured-baseline + modelled-congestion** — not purely synthetic — so they reflect real physical delays from the lab topology.

**Recommended models:**
- **LSTM / GRU** on sequences of 20 timesteps (10 minutes at 30s cadence). Input: `[latency, jitter, loss]` per tunnel. Output: `P(fault in next K seconds)`.
- **TCN (Temporal Convolutional Network)** — faster to train than LSTM, handles long-range dependencies via dilated convolutions.
- **Prophet** (Facebook) for univariate anomaly detection: baseline `tunnel_latency_ms` during healthy periods, flag deviations.

```python
# Sequence preparation for LSTM
import numpy as np

SEQ_LEN = 20   # 20 timesteps = 10 minutes of lookback

def make_sequences(df_tunnel, seq_len=SEQ_LEN):
    """Build (X, y) from tunnel rows, grouped by (device, entity)."""
    features = ["tunnel_latency_ms", "tunnel_jitter_ms", "tunnel_loss_pct"]
    X, y = [], []
    for (dev, ent), g in df_tunnel.sort_values("ts").groupby(["device", "entity"]):
        arr = g[features].fillna(0).to_numpy()
        labels = g["is_fault"].to_numpy()
        for i in range(seq_len, len(arr)):
            X.append(arr[i-seq_len:i])
            y.append(labels[i])
    return np.array(X), np.array(y)

X, y = make_sequences(df[df["entity_type"] == "tunnel"])
print(X.shape, y.shape)  # (N_sequences, 20, 3), (N_sequences,)
```

**Evaluation metric:** Precision/recall on `is_fault`, plus mean `lead_time_s` at prediction — penalize predictions that fire only post-impact (negative `time_to_impact_s`).

### Objective 2: Routing Instability Detection

**Target:** Detect `bgp_flap` and `node_failure` early. These have very short lead times (1–2 seconds), so the approach is event-rate anomaly detection rather than metric forecasting.

**Signal:** Count of BGP `ADJCHANGE` events in rolling 60-second windows from Loki. Also `if_oper_status` dropping to 0.

**Recommended approach:**
- **Isolation Forest / One-Class SVM** trained on healthy BGP event counts.
- **Simple threshold + rolling window** on ADJCHANGE rate is often sufficient and explainable.

```python
# Feature: BGP event count in rolling 60s windows
# Build by joining /events onto the Parquet ts grid
# Then detect anomalous burst rates
from sklearn.ensemble import IsolationForest

healthy_bgp_rates = ...   # BGP event counts per 30s window during is_fault=False
model = IsolationForest(contamination=0.05, random_state=42)
model.fit(healthy_bgp_rates.reshape(-1, 1))
```

### Objective 3: Tunnel Health Degradation Scoring

**Target:** Continuous health score per tunnel; threshold crossings trigger alerts.

**Signal:** `tunnel_latency_ms`, `tunnel_jitter_ms`, `tunnel_loss_pct`, `tunnel_rekeys` multivariate.

**Recommended approach:** **Autoencoder anomaly detection.** Train on healthy tunnel rows, flag high reconstruction error as degradation. The reconstruction error trajectory mirrors `time_to_impact_s`: it rises through the precursor window.

```python
# Composite degradation score (simple baseline before deep learning)
def tunnel_health_score(row):
    """0 = healthy, 1 = severe degradation. Linear combination."""
    lat_norm  = min(row["tunnel_latency_ms"] / 100.0, 1.0)   # 100ms = max
    jit_norm  = min(row["tunnel_jitter_ms"] / 20.0, 1.0)
    loss_norm = min(row["tunnel_loss_pct"] / 10.0, 1.0)
    return 0.4 * lat_norm + 0.3 * loss_norm + 0.3 * jit_norm

df_tunnel = df[df["entity_type"] == "tunnel"].copy()
df_tunnel["health_score"] = df_tunnel.apply(tunnel_health_score, axis=1)
```

### Objective 4: LLM NOC Copilot (RAG)

**Architecture:** Quantized LLM (e.g. Mistral-7B-Q4 via llama.cpp) + vector database (ChromaDB or FAISS, both pip-installable, no network needed) + the ragcorpus documents.

**The RAG pipeline for each alert:**

```python
# Pseudocode for the copilot response loop
def copilot_respond(alert):
    # 1. Retrieve relevant runbook
    relevant_docs = vector_db.search(
        query=f"{alert['fault_type']} {alert['device']}",
        n_results=2
    )

    # 2. Pull live telemetry context
    metrics_snapshot = get_current_metrics(alert["device"])
    label_context = get_label_row(alert["scenario_id"])

    # 3. Build structured context for LLM
    context = f"""
    Fault detected: {alert['fault_type']} on {alert['device']}
    Confidence: {alert['confidence']:.0%}
    Time to impact: {alert['time_to_impact_s']:.0f} seconds
    Lead time available: {alert['lead_time_s']:.0f} seconds

    Current metrics: {metrics_snapshot}

    Relevant runbook:
    {relevant_docs[0]['content']}
    """

    # 4. Generate operator-facing response
    response = llm.generate(
        system="You are a NOC operator assistant. Be concise and actionable.",
        user=context,
    )
    return response
```

**Structured output the copilot should produce** (per the problem statement):

```
Predicted issue: [fault_type] on [device]
Confidence: [%]
Root cause hypothesis: [from runbook]
Affected scope: [device] + [topology neighbors within 2 hops]
Estimated time to impact: [time_to_impact_s]s
Recommended action: [runbook resolution section]
```

### Evaluation Summary

| Metric | How to compute | Target |
|--------|---------------|--------|
| `is_fault` precision/recall | Standard sklearn metrics | >0.85 precision, >0.80 recall |
| Mean lead time at prediction | `time_to_impact_s` at first True prediction per incident | >30s across all scenarios |
| Per-scenario recall | Separate metrics per `fault_type` | All 7 types >0.70 |
| Copilot accuracy | Human evaluation of runbook grounding | Zero hallucinated device names |

The hardest scenarios to predict early are `bgp_flap` (lead_time=2s) and `node_failure` (lead_time=1s). Acceptable to catch these post-impact. The highest-value early predictions are `brownout` (lead_time=55s) and `congestion` (lead_time=52s).

---

## Appendix: File Map

| File | Role |
|------|------|
| `/root/LAB/topology-spec.yaml` | Single source of truth for lab scale |
| `/root/LAB/dataapi/app.py` | FastAPI endpoint definitions |
| `/root/LAB/dataapi/sources.py` | Data access: VM, Loki, flows, labels, topology |
| `/root/LAB/dataapi/export.py` | Join logic: produces labeled Parquet |
| `/root/LAB/faults/orchestrator.py` | Fault scheduler + label writer |
| `/root/LAB/faults/injectors.py` | Fault primitives: NetemImpair, BgpFlap, PolicyDrift, etc. |
| `/root/LAB/faults/labels/labels.jsonl` | Ground truth written by each fault run |
| `/root/LAB/synthetic/calibrate.py` | Derives profile.json from real Parquet |
| `/root/LAB/synthetic/generate.py` | Generates 8.89M-row synthetic dataset |
| `/root/LAB/synthetic/profile.json` | Calibration parameters (written by calibrate.py) |
| `/root/LAB/ragcorpus/` | Runbooks + topology map for LLM RAG retrieval |
| `/root/LAB/telemetry/docker-compose.yml` | VictoriaMetrics, Loki, Grafana, Telegraf stack |

---

**Navigation:** ← [02 Architecture Analogies](02_ARCHITECTURE_ANALOGIES.md) | [04 Usability Cheatsheet](04_USABILITY_CHEATSHEET.md) →
