# Clean Data API — schemas

The contract for the AI/ML/RAG team. **Universal join key = `device`** (node
name). Tags: `site_type`, `vrf`. All timestamps are **UTC**.

API runs local-only (`127.0.0.1:8000`). Start it from `dataapi/`:

```bash
uvicorn app:app --host 127.0.0.1 --port 8000
```

---

## Dataset Parquet (`/datasets`, built by `export.py`) — the ML-ready table

One row per **(device, entity, entity_type, ts-bucket)**. `ts` buckets are
`step`-second aligned UTC (default 30s). Fault labels are LEFT-joined on
`device` + `ts ∈ [t_start, t_end]`. Metric columns are **nullable per
entity_type** (interface rows have `if_*`; tunnel rows have `tunnel_*`).

| column             | type        | notes |
|--------------------|-------------|-------|
| `ts`               | string (UTC ISO-8601 `…Z`) | bucket start |
| `device`           | string      | node name — **join key** |
| `site_type`        | string      | `branch`\|`hub`\|`dc` (from metric tag; null for core) |
| `vrf`              | string/null | nullable — not on the live per-series telemetry |
| `entity`           | string      | interface name (e.g. `eth1`) or tunnel id (`ce_branch1-ce_hub1`) |
| `entity_type`      | string      | `interface` \| `tunnel` |
| `if_in_octets`     | float/null  | `interface_ifHCInOctets` (interface rows) |
| `if_out_octets`    | float/null  | `interface_ifHCOutOctets` |
| `if_oper_status`   | float/null  | `interface_ifOperStatus` (1=up) |
| `tunnel_latency_ms`| float/null  | `sdwan_tunnel_latency_ms` (tunnel rows) |
| `tunnel_jitter_ms` | float/null  | `sdwan_tunnel_jitter_ms` |
| `tunnel_loss_pct`  | float/null  | `sdwan_tunnel_loss_pct` |
| `tunnel_rekeys`    | float/null  | `sdwan_tunnel_rekeys_total` |
| `flow_bytes`       | float/null  | nfacctd flow bytes summed per device+bucket |
| `flow_packets`     | float/null  | nfacctd flow packets summed per device+bucket |
| `is_fault`         | bool        | true if bucket falls in a labeled fault window for the device |
| `scenario_id`      | string/null | label id (null when not a fault) |
| `fault_type`       | string/null | `congestion`\|`bgp_flap`\|`tunnel_degrade`\|`policy_drift`\|… |
| `severity`         | string/null | `low`\|`medium`\|`high` |
| `lead_time_s`      | float/null  | label `lead_time` (t_impact − t_start) |
| `time_to_impact_s` | float/null  | seconds from this bucket to t_impact (>0 before impact, <0 after) |

Sparse/nullable is expected — a tunnel row leaves `if_*`/`flow_*` null and vice
versa. The canonical column list/order is fixed in `export.COLUMNS`;
`check_dataset.py` asserts it.

---

## Endpoint response shapes

### `GET /metrics?query=<PromQL>[&start=&end=&step=]`
VictoriaMetrics passthrough. Instant if no `start`; range otherwise.
```json
{"result": [{"metric": {"__name__":"sdwan_tunnel_latency_ms","device":"ce_branch1",
  "tunnel":"ce_branch1-ce_hub1","site_type":"branch"}, "value":[1782054427,"14.86"]}]}
```
Range responses carry `"values": [[ts,val],…]` instead of `"value"`.

### `GET /events?[start=&end=&device=&limit=]` (default window now-1h)
Loki log lines flattened to rows.
```json
{"rows":[{"ts":"2026-06-21T14:56:43Z","device":"pe3","app":"bgpd",
  "severity":"informational","line":"… bgp_update_rec …"}]}
```

### `GET /flows?[limit=&device=]`
Recent nfacctd IPFIX purge records.
```json
{"rows":[{"ts":"2026-06-21 15:06:31","device":"ce_dc1","ip_src":"192.168.26.10",
  "ip_dst":"192.168.18.10","port_src":34897,"port_dst":19010,"proto":"tcp",
  "bytes":395832,"packets":50}]}
```

### `GET /labels`
Ground-truth fault timeline (full schema in `faults/README.md`).
```json
{"rows":[{"scenario_id":"congestion-ce_branch1-…","type":"congestion",
  "target":{"device":"ce_branch1","interface":"eth1"},"severity":"high",
  "t_start":"…Z","t_impact":"…Z","t_end":"…Z","lead_time":48.5,
  "impact_method":"vm_threshold","device":"ce_branch1"}]}
```

### `GET /topology`
Graph JSON derived from `topology/clab.yml` + `topology-spec.yaml`.
```json
{"nodes":[{"id":"ce_hub1","role":"ce_hub","site_type":"hub",
  "vrfs":["CORP","VOICE","GUEST"]}, {"id":"p1","role":"p"}],
 "links":[{"source":"p1","target":"p2","source_if":"eth1","target_if":"eth1"}]}
```
Roles: `p`, `pe`, `ce_branch`, `ce_hub`, `ce_dc`, `host`.

### `GET /datasets?[start=&end=&step=&build=]`
Returns the joined labeled Parquet as a file download. With `start` (or
`build=true`) it builds fresh for the window; otherwise returns the newest
dataset in `datasets/`.
