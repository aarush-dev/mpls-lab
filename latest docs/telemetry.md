# Telemetry pipeline

## Purpose

Collects the 4 telemetry pillars (metrics, logs, flows, environmental/modelled
sensors) off the 70-node Containerlab MPLS/SD-WAN lab and lands them in
VictoriaMetrics (metrics) and Loki (logs), with Grafana on top for viewing.
Sits between the FRR/controller/trafficgen containers (data producers) and
the copilot/predictive stack (data consumers, e.g. `dataapi`, Kafka bridge —
owned elsewhere). All services attach to the external `clab` docker network
on static `172.20.20.0/24` addresses so they share L2 with the lab nodes.
Defined in `telemetry/docker-compose.yml:1`.

## Entry points

- **Metrics scrape/push loop** — telegraf runs as a long-lived container, no
  manual invocation:
  `docker compose -f telemetry/docker-compose.yml up telegraf` (`telemetry/docker-compose.yml:69`).
- **`telemetry/env-metrics.py`** — `__main__` block, one-shot Prometheus text
  emitter to stdout. Run directly for a manual sample:
  `python3 telemetry/env-metrics.py` (`telemetry/env-metrics.py:427`).
  In the stack it is looped every 30s and piped to VictoriaMetrics import API
  by the `env-metrics` service entrypoint (`telemetry/docker-compose.yml:189`).
- **`telemetry/telegraf/ldp-metrics.sh`** — one-shot Prometheus text emitter,
  bash: `bash telemetry/telegraf/ldp-metrics.sh` (`telemetry/telegraf/ldp-metrics.sh:1`).
  Looped every 30s by the `ldp-metrics` service entrypoint (`telemetry/docker-compose.yml:169`).
- **`telemetry/envmodel.py`** — no CLI; `__main__` is a self-test:
  `python3 telemetry/envmodel.py` → prints `envmodel selftest: OK`
  (`telemetry/envmodel.py:231-296`).
- **`telemetry/test_env_metrics.py`** — run from `telemetry/`:
  `python3 telemetry/test_env_metrics.py` (loads `env-metrics.py` via
  `importlib` because the filename has a hyphen) (`telemetry/test_env_metrics.py:1-13`).
- **nfacctd flow collector** — passive UDP listener, no CLI to run; verify
  with `docker logs tele-nfacctd` (`telemetry/nfacctd/nfacctd.conf:1-3`).
- **promtail syslog receiver** — passive TCP+UDP listener on 1514, no CLI
  (`telemetry/promtail/promtail.yaml:11-13`).

## Modules

- **`telemetry/env-metrics.py`** — device-health sidecar: real (docker
  stats/tc/vtysh) + modelled (envmodel) sensors, emits Prometheus text.
  - `main()` `telemetry/env-metrics.py:303` — orchestrates one full scrape+emit cycle.
  - `docker_stats()` `telemetry/env-metrics.py:184` — one `docker stats` call → `{device: (cpu_pct, mem_pct)}`.
  - `queue_stats(device, iface)` `telemetry/env-metrics.py:202` — parses `tc -s qdisc show` → `(backlog_bytes, drops)`.
  - `bgp_msg_counts(device)` `telemetry/env-metrics.py:244` — sums BGP rx/tx msg counters across peers via vtysh.
  - `rib_routes(device)` `telemetry/env-metrics.py:258` — RIB size via vtysh.
  - `ospf_lsa_count(device)` `telemetry/env-metrics.py:267` — sums LSDB entries across OSPF areas.
  - `pop_index_map()` `telemetry/env-metrics.py:150` / `_fallback_pop(device)` `telemetry/env-metrics.py:173` — device → POP index, from `topology/topology-meta.json` or a 4-P/2-PE-per-POP fallback split.
  - `read_overlay()` `telemetry/env-metrics.py:99` / `overlay_prog(ov, now)` `telemetry/env-metrics.py:112` — pulls active fault overlays from the controller and turns each into a 0→1→0 ramp fraction.
  - `_diurnal_util()` `telemetry/env-metrics.py:82` — offered-load fraction from the shared `trafficgen/diurnal.py` curve (best-effort import; falls back to 0.5).
  - `load_state()`/`save_state()` `telemetry/env-metrics.py:287,295` — persists thermal-mass state (`prev` temps, `age_frac`) to `ENV_STATE` JSON between ticks.
- **`telemetry/envmodel.py`** — single source of truth for modelled sensor
  physics, shared with `synthetic/generate.py` (not owned here). Pure
  functions + module constants, no I/O.
  - `power_watts(role, util, gauss)` `telemetry/envmodel.py:66`
  - `temp_c(prev_c, ambient_c, util, fault_heat_c, gauss)` `telemetry/envmodel.py:102`
  - `temp_failure_scale(temp)` `telemetry/envmodel.py:113`
  - `pop_ambient_c(pop_index)` `telemetry/envmodel.py:89`
  - `fault_heat_c(fault_type)` `telemetry/envmodel.py:141`, `optic_degrade(fault_type)` `telemetry/envmodel.py:157`
  - `fan_rpm(temp)` `telemetry/envmodel.py:169`, `psu_voltage_v(util, gauss)` `telemetry/envmodel.py:174`
  - `optical(chassis_temp_c, age_frac, degrade, gauss)` `telemetry/envmodel.py:188` → `(xcvr_temp_c, rx_power_dbm, tx_bias_ma)`
  - `role_of(device, site_type=None)` `telemetry/envmodel.py:215`
- **`telemetry/telegraf/ldp-metrics.sh`** — bash+vtysh+python3 one-shot
  emitter for MPLS/LDP/OSPF/BGP series SNMP cannot reach (frr-snmp AgentX ABI
  mismatch, `telemetry/telegraf/ldp-metrics.sh:2-3`).
- **`telemetry/telegraf/telegraf.conf`** — SNMP poll of all 70 nodes +
  Prometheus scrape of the controller; regex processors add `site_type`/`vrf`
  tags; output is Prometheus remote-write to VictoriaMetrics.
- **`telemetry/nfacctd/nfacctd.conf`** — IPFIX/NetFlow collector config,
  prints flow JSON to stdout only (no storage backend configured).
- **`telemetry/promtail/promtail.yaml`** — RFC5424 syslog receiver (tcp+udp
  1514) → Loki push, with regex pipeline stages extracting BGP/LDP/OSPF
  event fields as labels.
- **`telemetry/loki/loki.yaml`** — single-binary Loki, filesystem store.
- **`telemetry/docker-compose.yml`** — wires all of the above plus
  `victoriametrics`, `grafana`, `kafka`, and (not owned by this subsystem)
  `controller`/`trafficgen` sidecars onto the `clab` network.
- **`telemetry/grafana/provisioning/datasources/datasources.yml`** —
  provisions the VictoriaMetrics (Prometheus-proxy) and Loki datasources.
- **`telemetry/grafana/provisioning/dashboards/dashboards.yml`** — file
  provider pointing at `/var/lib/grafana/dashboards`.
- **`telemetry/grafana/dashboards/noc-overview.json`** — the "NOC Overview"
  dashboard: 9 panels over the metrics this pipeline produces.

## Parameters

| name | default | env-var/CLI-flag | units | what it controls | source (file:line) |
|---|---|---|---|---|---|
| telegraf collection interval | `30s` | `[agent].interval` | s | global SNMP scrape cadence | `telemetry/telegraf/telegraf.conf:7` |
| telegraf flush interval | `30s` | `[agent].flush_interval` | s | batch-write cadence to VM | `telemetry/telegraf/telegraf.conf:8` |
| SNMP version | `2` (v2c) | `inputs.snmp.version` | — | SNMP protocol version | `telemetry/telegraf/telegraf.conf:99` |
| SNMP community | `public` | `inputs.snmp.community` | — | SNMP auth string | `telemetry/telegraf/telegraf.conf:100` |
| SNMP timeout | `5s` | `inputs.snmp.timeout` | s | per-agent SNMP request timeout | `telemetry/telegraf/telegraf.conf:101` |
| SNMP retries | `1` | `inputs.snmp.retries` | count | SNMP retry count | `telemetry/telegraf/telegraf.conf:102` |
| controller scrape interval | `10s` | `inputs.prometheus.interval` | s | scrape cadence of controller `:9362/metrics` (faster than SNMP so the tunnel-queue term moves in time) | `telemetry/telegraf/telegraf.conf:208` |
| VictoriaMetrics retention | `30d` | `-retentionPeriod` | days | metrics TSDB retention | `telemetry/docker-compose.yml:29` |
| VictoriaMetrics HTTP addr | `:8428` | `-httpListenAddr` | — | metrics API/listen port | `telemetry/docker-compose.yml:30` |
| nfacctd print refresh | `30` | `print_refresh_time` | s | flow-record flush cadence to stdout | `telemetry/nfacctd/nfacctd.conf:9` |
| nfacctd print history | `30s` | `print_history` | s | flow aggregation bucket width | `telemetry/nfacctd/nfacctd.conf:10` |
| promtail idle timeout | `60s` | `syslog.idle_timeout` | s | syslog connection idle close | `telemetry/promtail/promtail.yaml:15,54` |
| Loki index period | `24h` | `schema_config.configs[0].index.period` | h | index shard period | `telemetry/loki/loki.yaml:27` |
| env-metrics state file | `/tmp/env-metrics-state.json` | `ENV_STATE` | path | persists thermal-mass temps + optic age across ticks | `telemetry/env-metrics.py:63` |
| controller URL | `http://172.20.20.56:9362` | `CTRL_URL` | — | fault-overlay registry endpoint | `telemetry/env-metrics.py:64` |
| overlay step | `5.0` | `OVERLAY_STEP` (const) | s | must match controller's `OVERLAY_STEP` dur-floor for `signatures.prog` ramp shape | `telemetry/env-metrics.py:65` |
| diurnal period | `3600` | `DIURNAL_PERIOD` | s | length of one simulated day for the offered-load curve | `telemetry/env-metrics.py:93` |
| push-loop sleep (env-metrics) | `30` | entrypoint `sleep 30` | s | cadence env-metrics.py is re-run and pushed | `telemetry/docker-compose.yml:189` |
| push-loop sleep (ldp-metrics) | `30` | entrypoint `sleep 30` | s | cadence ldp-metrics.sh is re-run and pushed | `telemetry/docker-compose.yml:169` |
| optic age-fraction increment | `1/86400` per tick | `age_frac` update (const expr) | fraction/tick | advances optic "service life" so laser bias trends up across runs (comment: "~30 simulated days") | `telemetry/env-metrics.py:312` |
| `ROLE_PMAX_W` | core 3200, pe 1650, hub 180, dc 180, branch 72 | module const | W | per-role chassis max power draw | `telemetry/envmodel.py:48-54` |
| `DEFAULT_PMAX_W` | `180.0` | module const | W | fallback max power for unknown role | `telemetry/envmodel.py:55` |
| `IDLE_FRAC` | `0.87` | module const | fraction of Pmax | idle power floor (Vishwanath 2014: measured 0.90) | `telemetry/envmodel.py:59` |
| `POWER_SIGMA_FRAC` | `0.35` | module const | fraction of load band | power-noise residual spread | `telemetry/envmodel.py:63` |
| `AMBIENT_C` | `22.0` | module const | °C | nominal cold-aisle inlet temp | `telemetry/envmodel.py:78` |
| `AMBIENT_SPREAD_C` | `4.0` | module const | °C | per-POP ambient spread | `telemetry/envmodel.py:79` |
| `K_LOAD_C` | `12.0` | module const | °C | full-load temperature rise above ambient | `telemetry/envmodel.py:80` |
| `THERMAL_ALPHA` | `0.2` | module const | fraction/tick | EMA pull toward target temp (thermal mass) | `telemetry/envmodel.py:81` |
| `TEMP_SIGMA_C` | `0.35` | module const | °C | temperature noise stddev | `telemetry/envmodel.py:82` |
| `TEMP_BETA` | `0.03` | module const | rel. rate/°C | linear error-rate coupling above `TEMP_REF_C` | `telemetry/envmodel.py:85` |
| `TEMP_REF_C` | `25.0` | module const | °C | reference temp for `temp_failure_scale` | `telemetry/envmodel.py:86` |
| `FAULT_HEAT_C` | see table, e.g. `core_congestion`=6.0, `node_failure`=-3.0 | module const | °C | extra chassis heat while a fault type is active (negative = daemon killed → cools) | `telemetry/envmodel.py:124-138` |
| `OPTIC_DEGRADE` | `gray_failure`=1.0, `brownout`=0.5 (else 0.0) | module const | fraction | active physical-layer optical degradation by fault type | `telemetry/envmodel.py:151-154` |
| `FAN_BASE_RPM` | `3000.0` | module const | RPM | fan floor speed | `telemetry/envmodel.py:162` |
| `FAN_RPM_PER_C` | `120.0` | module const | RPM/°C | fan ramp rate above knee | `telemetry/envmodel.py:163` |
| `FAN_KNEE_C` | `30.0` | module const | °C | temp above which fan ramps | `telemetry/envmodel.py:164` |
| `PSU_NOMINAL_V` | `12.0` | module const | V | nominal rail voltage | `telemetry/envmodel.py:165` |
| `PSU_SAG_V` | `0.25` | module const | V | full-load rail sag | `telemetry/envmodel.py:166` |
| `XCVR_BIAS_BASE_MA` | `28.0` | module const | mA | baseline laser bias current | `telemetry/envmodel.py:180` |
| `XCVR_BIAS_AGE_MA` | `6.0` | module const | mA | bias climb over full modelled service life | `telemetry/envmodel.py:181` |
| `XCVR_TX_DBM` | `-2.5` | module const | dBm | transmit optical power (constant, not emitted as a metric) | `telemetry/envmodel.py:182` |
| `XCVR_RX_BASE_DBM` | `-6.0` | module const | dBm | baseline received optical power | `telemetry/envmodel.py:183` |
| `XCVR_RX_FLOOR_DBM` | `-18.0` | module const | dBm | rx power at full degrade (link-error floor) | `telemetry/envmodel.py:184` |
| `XCVR_TEMP_OFFSET_C` | `8.0` | module const | °C | optic-over-chassis temp offset | `telemetry/envmodel.py:185` |
| CE queue-check interfaces | `eth0`, `eth1` | loop const | — | which interfaces get `tc -s qdisc` polled per CE (not a full walk) | `telemetry/env-metrics.py:347` |
| VRFs polled (ldp-metrics) | `CORP VOICE GUEST` | `VRFS` | — | VRFs queried for `bgp_vrf_prefix_count` | `telemetry/telegraf/ldp-metrics.sh:25` |

## Data flow

**Metrics — SNMP/IF-MIB (telegraf → VM):**
telegraf polls SNMP agents on 70 static mgmt IPs `172.20.20.101-170`
(`telemetry/telegraf/telegraf.conf:27-98`) → walks `sysName` (device tag) +
`ifTable`/`ifXTable` (ifIndex, interface, ifOperStatus, ifHCIn/OutOctets,
ifIn/OutDiscards, ifIn/OutErrors) → `processors.regex` derives `site_type`
from the device name pattern and `vrf` from `vrf_<NAME>` interface names
(`telemetry/telegraf/telegraf.conf:160-196`) → Prometheus remote-write
(snappy+protobuf) to `http://172.20.20.50:8428/api/v1/write`
(`telemetry/telegraf/telegraf.conf:12-18`).

**Metrics — controller (telegraf → VM):** telegraf scrapes
`http://172.20.20.56:9362/metrics` every 10s (`sdwan_*` series; controller
itself is not owned here) → same remote-write output
(`telemetry/telegraf/telegraf.conf:201-208`).

**Metrics — MPLS/LDP/OSPF/BGP control-plane (ldp-metrics sidecar → VM):**
`ldp-metrics.sh` runs `docker exec <node> vtysh -c "<show ... json>"`
per node/VRF (`telemetry/telegraf/ldp-metrics.sh:27`) → parses JSON with
inline python3 → Prometheus text on stdout → piped by the container
entrypoint to `POST http://172.20.20.50:8428/api/v1/import/prometheus`
every 30s (`telemetry/docker-compose.yml:169`).

**Metrics — environmental (env-metrics sidecar → VM):** `env-metrics.py`
gathers `docker stats` (CPU/mem, one call), `tc -s qdisc` (CE queue
backlog/drops), and vtysh JSON (BGP msg counts, RIB size, OSPF LSDB size)
for REAL series; for MODELLED series it reads POP membership from
`topology/topology-meta.json` (not owned here), the offered-load curve from
`trafficgen/diurnal.py` (not owned here), and active fault overlays from the
controller's `/fault/overlay` HTTP endpoint, then runs those through
`envmodel.py`'s pure functions → Prometheus text on stdout → piped by the
container entrypoint to the same VM import endpoint every 30s
(`telemetry/docker-compose.yml:189`).

**Flows — IPFIX/NetFlow (nfacctd):** pmacctd exporters on the FRR nodes
(not owned here) send IPFIX to `udp/2055` → nfacctd aggregates by
`src_host, dst_host, src_port, dst_port, proto, in_iface, peer_src_ip, label`
(`telemetry/nfacctd/nfacctd.conf:16`), resolving `peer_src_ip` → device
`label` via `pre_tag_map` loaded from `topology/telemetry/device_map.txt`
(generator-owned, mounted read-only, `telemetry/docker-compose.yml:89`) →
prints flow records as JSON to stdout every 30s
(`telemetry/nfacctd/nfacctd.conf:7-9`). No persistent store — `docker logs
tele-nfacctd` is the only read path.

**Logs — syslog (promtail → Loki):** FRR nodes forward RFC5424 syslog to
`tcp/udp 1514` (comment: contingent on another agent adding rsyslog to the
FRR image, `telemetry/promtail/promtail.yaml:2`) → promtail relabels
`__syslog_message_hostname`→`device`, `__syslog_message_severity`→`severity`,
`__syslog_message_app_name`→`app` (`telemetry/promtail/promtail.yaml:21-28`)
→ regex pipeline stages extract `bgp_peer`/`bgp_state`,
`ldp_peer`/`ldp_event`, `ospf_peer`/`ospf_to` as extra labels
(`telemetry/promtail/promtail.yaml:33-47`) → pushed to
`http://172.20.20.54:3100/loki/api/v1/push` (`telemetry/promtail/promtail.yaml:8`).

**Viewing:** Grafana (`172.20.20.51:3000`, anonymous Admin,
`telemetry/docker-compose.yml:44-47`) is provisioned with a VictoriaMetrics
(Prometheus-proxy) datasource and a Loki datasource
(`telemetry/grafana/provisioning/datasources/datasources.yml`), and loads
`noc-overview.json` from the file-provider dashboard path
(`telemetry/grafana/provisioning/dashboards/dashboards.yml`).

**Join key = `device`.** Every metrics series (SNMP, controller, ldp-metrics,
env-metrics) and the Loki log stream carry a `device` label with the same
node-name values (`p1`…`p24`, `pe1`…`pe12`, `ce_branch1`…`ce_dc4`), so any
two pillars can be joined on it directly. The dashboard's template variable
uses this same field: `label_values(interface_ifHCInOctets, device)`
(`telemetry/grafana/dashboards/noc-overview.json:25`).

## Calculations

- **Interface throughput (bps)**, Grafana panel expr, not stored as a series:
  `rate(interface_ifHCInOctets[5m]) * 8` and same for Out
  (`telemetry/grafana/dashboards/noc-overview.json:57,83`). Inputs:
  `ifHCInOctets`/`ifHCOutOctets` counters from SNMP (`telemetry/telegraf/telegraf.conf:132,135`).

- **Chassis power draw** `power_watts(role, util, gauss)`
  (`telemetry/envmodel.py:66-74`):
  `P = P_idle + (P_max - P_idle) * clamp01(util) + gauss * (P_max - P_idle) * POWER_SIGMA_FRAC`,
  `P_idle = P_max * IDLE_FRAC`. Inputs: `role` (from `role_of`, envmodel.py:215),
  `util` (see below), `gauss` = caller `random.Random.gauss(0,1)` sample
  (`telemetry/env-metrics.py:404`, passed in per-emit).

- **Offered-load `util`** (`telemetry/env-metrics.py:384`):
  `util = max(diurnal_util(now), min(1.0, cpu_pct/100.0))` — the larger of
  the shared diurnal traffic curve and measured container CPU%, so a
  reconvergence CPU spike can push chassis heat/power above the diurnal
  baseline even off-peak.

- **Chassis temperature** `temp_c(prev_c, ambient_c, util, fault_heat_c,
  gauss)` (`telemetry/envmodel.py:102-110`):
  `target = ambient_c + K_LOAD_C * clamp01(util) + fault_heat_c + gauss * TEMP_SIGMA_C`;
  `temp = prev_c + THERMAL_ALPHA * (target - prev_c)` — first-order EMA, so
  temperature lags a load step by design. Inputs: previous tick's temp
  (persisted in `ENV_STATE`), `ambient_c` (see below), `util`,
  `fault_heat_c = FAULT_HEAT_C[fault_type] * overlay_prog(...)`
  (`telemetry/env-metrics.py:395,399`).

- **Per-POP ambient temperature** `pop_ambient_c(pop_index)`
  (`telemetry/envmodel.py:89-99`):
  `frac = (pop_index * 0.6180339887) % 1.0` (golden-ratio sequence, deterministic);
  `ambient = AMBIENT_C + (frac - 0.5) * 2 * AMBIENT_SPREAD_C`. Input:
  `pop_index` from `pop_index_map()`/`_fallback_pop()`
  (`telemetry/env-metrics.py:150,173`).

- **Fault ramp fraction** `overlay_prog(ov, now)` (`telemetry/env-metrics.py:112-126`):
  reconstructs `dur = t_end - t_impact` and calls the shared
  `signatures.prog(now, t_start, t_impact, t_end, dur, sevmul, OVERLAY_STEP)`
  (external module, `faults/signatures.py`, not owned here — mounted at
  `/app/faults` in-container or resolved at `../faults` on host,
  `telemetry/env-metrics.py:46-51`). Returns 0.0 if `signatures` failed to
  import, the overlay dict is malformed, or `now` is outside
  `[t_start, t_end]`. `sevmul` defaults to `1.0` if the registry record omits
  it (older registries) (`telemetry/env-metrics.py:125`).

- **Temperature-driven failure-rate multiplier** `temp_failure_scale(temp)`
  (`telemetry/envmodel.py:113-119`): `max(1.0, 1.0 + TEMP_BETA * (temp -
  TEMP_REF_C))` — linear, not Arrhenius (El-Sayed 2012). Not called anywhere
  else under `telemetry/`; only exercised by the module's own self-test
  (`telemetry/envmodel.py:259-264`).

- **Fan speed** `fan_rpm(temp)` (`telemetry/envmodel.py:169-171`):
  `FAN_BASE_RPM + FAN_RPM_PER_C * max(0, temp - FAN_KNEE_C)`. Input: this
  tick's `temp_c` output.

- **PSU rail voltage** `psu_voltage_v(util, gauss)` (`telemetry/envmodel.py:174-176`):
  `PSU_NOMINAL_V - PSU_SAG_V * clamp01(util) + gauss * 0.02`.

- **Optical transceiver** `optical(chassis_temp_c, age_frac, degrade, gauss)`
  (`telemetry/envmodel.py:188-207`):
  - `xcvr_temp_c = chassis_temp_c + XCVR_TEMP_OFFSET_C + gauss * 0.3`
  - `tx_bias_ma = XCVR_BIAS_BASE_MA + XCVR_BIAS_AGE_MA * clamp01(age_frac) + 9.0 * clamp01(degrade) + gauss * 0.15`
  - `rx_power_dbm = XCVR_RX_BASE_DBM - 1.2 * clamp01(age_frac) - (XCVR_RX_BASE_DBM - XCVR_RX_FLOOR_DBM) * clamp01(degrade) + gauss * 0.25`
  - Inputs: this tick's `temp_c` output, `age_frac` (service-life fraction,
    state-persisted, incremented `1/86400` per tick,
    `telemetry/env-metrics.py:311-312`), `degrade = OPTIC_DEGRADE[fault_type]
    * overlay_prog(...)` (`telemetry/env-metrics.py:396`).

- **BGP RIB size** `rib_routes(device)` (`telemetry/env-metrics.py:258-264`):
  prefers the `"Totals"` row from `show ip route summary json`; falls back to
  summing every row's `rib` field if no Totals row is present.

- **OSPF LSDB size** `ospf_lsa_count(device)` (`telemetry/env-metrics.py:267-283`):
  sums `len(list)` over every key ending `LinkStates` or `Lsa` across every
  OSPF area in `show ip ospf database json` (ABRs counted once per area, by
  design — they legitimately appear in two areas).

- **BGP peer Established count (ldp-metrics)**
  (`telemetry/telegraf/ldp-metrics.sh:122-136`): dedupes on peer IP address
  before counting, so a peer active in multiple AFI/SAFI families (e.g.
  ipv4Unicast + ipv4Vpn) is not double-counted.

- **BGP VRF prefix count (ldp-metrics)**
  (`telemetry/telegraf/ldp-metrics.sh:44-64`): sums `ribCount` across every
  AFI/SAFI family returned by `show bgp vrf <vrf> summary json`; a VRF with
  no BGP families emits no sample (not a fabricated 0).

- **tc queue backlog/drops** `queue_stats(device, iface)`
  (`telemetry/env-metrics.py:202-224`): sums `backlog` (bytes, parsed via
  `_bytes()` which handles `b`/`Kb`/`Mb` suffixes,
  `telemetry/env-metrics.py:227-240`) and `dropped` tokens across every
  qdisc line in `tc -s qdisc show dev <iface>` output.

## Config & schemas

**`telemetry/docker-compose.yml`** — service inventory (image, static IP, key ports):

| service | image | IP | ports |
|---|---|---|---|
| victoriametrics | victoria-metrics:v1.103.0 | .50 | 8428 |
| grafana | grafana:11.1.0 | .51 | 3000 |
| telegraf | telegraf:1.31.1 | .52 | — |
| nfacctd | pmacct/nfacctd:v1.7.9 | .53 | 2055/udp |
| loki | loki:3.1.0 | .54 | 3100 |
| promtail | promtail:3.1.0 | .55 | 1514 tcp+udp |
| controller (not owned here) | noc-controller:0.1 | .56 | 9362 (scraped) |
| trafficgen (not owned here) | noc-trafficgen:0.1 | .57 | — |
| ldp-metrics | noc-controller:0.1 | .58 | — (push sidecar) |
| env-metrics | noc-controller:0.1 | .59 | — (push sidecar) |
| kafka | apache/kafka:3.9.1 | .60 | 9092 internal / 29092 host |

All images pinned, `pull_policy: never` — air-gap requires pre-loading via
`docker load` (`telemetry/docker-compose.yml:6-9`).

**`telemetry/grafana/provisioning/datasources/datasources.yml`** — two
datasources: `VictoriaMetrics` (uid `victoriametrics`, type `prometheus`,
`http://172.20.20.50:8428`, `httpMethod: POST`, default) and `Loki`
(`http://172.20.20.54:3100`).

**`telemetry/grafana/provisioning/dashboards/dashboards.yml`** — file
provider, path `/var/lib/grafana/dashboards`, `allowUiUpdates: true`.

**`telemetry/grafana/dashboards/noc-overview.json`** — dashboard `uid:
noc-overview`, 9 panels, 30s refresh, `device` template variable
(multi-select, includeAll) sourced from
`label_values(interface_ifHCInOctets, device)`. Panels: interface RX/TX bps,
SD-WAN tunnel latency/loss, MPLS LDP session state, BGP VRF prefix count,
controller drift active, OSPF adjacency state, OSPF SPF duration, MPLS LSP
count, BGP peers Established.

**Prometheus text schema — REAL series (`env-metrics.py`)**, all gauges
unless noted, label `device` always present:

| metric | labels | unit | source |
|---|---|---|---|
| `node_cpu_pct` | device | % | `telemetry/env-metrics.py:328` |
| `node_mem_pct` | device | % | `telemetry/env-metrics.py:331` |
| `iface_queue_backlog_bytes` | device, interface | bytes | `telemetry/env-metrics.py:353` |
| `iface_queue_drops` (counter) | device, interface | count | `telemetry/env-metrics.py:354` |
| `bgp_msg_rx_total` (counter) | device | count | `telemetry/env-metrics.py:367` |
| `bgp_msg_tx_total` (counter) | device | count | `telemetry/env-metrics.py:368` |
| `rib_routes` | device | count | `telemetry/env-metrics.py:369` |
| `ospf_lsa_count` | device | count | `telemetry/env-metrics.py:370` |

**Prometheus text schema — MODELLED series (`env-metrics.py`)**, all gauges:

| metric | labels | unit | source |
|---|---|---|---|
| `device_temp_c` | device, role, pop | °C | `telemetry/env-metrics.py:415` |
| `device_power_watts` | device, role | W | `telemetry/env-metrics.py:416` |
| `device_fan_rpm` | device | RPM | `telemetry/env-metrics.py:417` |
| `device_psu_voltage_v` | device | V | `telemetry/env-metrics.py:418` |
| `xcvr_temp_c` | device, interface | °C | `telemetry/env-metrics.py:419` |
| `xcvr_rx_power_dbm` | device, interface | dBm | `telemetry/env-metrics.py:420` |
| `xcvr_tx_bias_ma` | device, interface | mA | `telemetry/env-metrics.py:421` |

**Prometheus text schema — `ldp-metrics.sh`**, all gauges:

| metric | labels | unit | source |
|---|---|---|---|
| `mpls_ldp_session_state` | device, peer | 5=OPERATIONAL/1=down | `telemetry/telegraf/ldp-metrics.sh:30-41` |
| `bgp_vrf_prefix_count` | device, vrf | count | `telemetry/telegraf/ldp-metrics.sh:44-64` |
| `ospf_neighbor_state` | device, peer | 1=Full/0=not | `telemetry/telegraf/ldp-metrics.sh:66-82` |
| `ospf_spf_last_duration_ms` | device | ms | `telemetry/telegraf/ldp-metrics.sh:84-100` |
| `ospf_spf_last_executed_ms` | device | ms-since-boot | `telemetry/telegraf/ldp-metrics.sh:84-100` |
| `mpls_lsp_count` | device | count | `telemetry/telegraf/ldp-metrics.sh:102-114` |
| `bgp_peer_established` | device | count (distinct) | `telemetry/telegraf/ldp-metrics.sh:116-136` |

**SNMP-derived series (telegraf)**: `interface` measurement with fields
`ifIndex`, `ifOperStatus`, `ifHCInOctets`, `ifHCOutOctets`, `ifInDiscards`,
`ifInErrors`, `ifOutDiscards`, `ifOutErrors`; tags `device` (sysName),
`interface` (ifDescr), `site_type` (regex-derived), `vrf` (regex-derived,
absent when interface name has no `vrf_` prefix) (`telemetry/telegraf/telegraf.conf:110-196`).

**`env-metrics.py` state file** (`ENV_STATE`, default
`/tmp/env-metrics-state.json`) — JSON: `{"temps": {device: last_temp_c},
"age_frac": float, "ts": unix_time}` (`telemetry/env-metrics.py:295-300,423`).

**`telemetry/nfacctd/nfacctd.conf`** — `print[stdout]` plugin, JSON output,
30s refresh/history; `aggregate` key set as documented above;
`pre_tag_map` maps `peer_src_ip` (IPFIX exporter mgmt IP) → `label` (device
name), file is generator-owned (`topology/telemetry/device_map.txt`, not
read here).

**`telemetry/loki/loki.yaml`** — filesystem store at `/loki`, TSDB index
schema v13 from `2024-01-01`, `replication_factor: 1`, in-memory ring,
`analytics.reporting_enabled: false` (air-gap).

## Gotchas

- Telegraf's SNMP agent list (`telemetry/telegraf/telegraf.conf:27-98`,
  70 hardcoded IPs) duplicates `topology/telemetry/snmp_agents.toml`
  (generator-owned). Nothing wires them together — rescaling the topology
  means hand-editing this file too (`telemetry/telegraf/telegraf.conf:21-23`).
- `env-metrics.py`'s `signatures` import is best-effort
  (`telemetry/env-metrics.py:53-60`): if the sidecar image lacks
  `faults/signatures.py` + numpy, `overlay_prog()` always returns `0.0`
  (`telemetry/env-metrics.py:119-120`) — fault ramps silently stop moving
  temp/optics without any error signal.
- `read_overlay()` is best-effort against the controller HTTP endpoint
  (`telemetry/env-metrics.py:99-109`): controller down or unreachable →
  `{}` → every device's fault term is 0, again with no error surfaced.
- Thermal-mass state lives in `ENV_STATE` (default `/tmp/...`, not a mounted
  volume in the compose file) — a container recreate loses `temps`/`age_frac`
  and restarts the EMA from ambient (`telemetry/env-metrics.py:63,287-292`).
- CE queue polling only checks `eth0`/`eth1`, not every interface — a
  deliberate scope cut (per-VRF uplink index varies per node, and a full
  walk would be ~5 docker execs per CE per cycle) (`telemetry/env-metrics.py:340-347`).
- nfacctd has **no persistent store** — `print[stdout]` only
  (`telemetry/nfacctd/nfacctd.conf:7`). Flow data is only as durable as
  `docker logs`; nothing in this pillar ships flows to VM or Loki.
- nfacctd's join field is named `label`, not `device`
  (`telemetry/nfacctd/nfacctd.conf:16`) — semantically the same device name
  via `pre_tag_map`, but the field name breaks a naive cross-pillar query
  that assumes every pillar's join key is literally called `device`.
- promtail's syslog input depends on FRR nodes actually forwarding RFC5424
  syslog to `1514` — the config comment notes this is contingent on rsyslog
  being added to the FRR image by another workstream
  (`telemetry/promtail/promtail.yaml:2`); if that isn't wired, `job=syslog`
  streams are empty and Loki has nothing.
- `age_frac` increment (`telemetry/env-metrics.py:312`) is
  `1.0 / (86400.0/30.0 * 30.0)` = `1/86400` per tick; at the 30s push
  interval that's `~0.0333`/simulated day of wall-clock time, not the
  "~30 simulated days" the inline comment describes at face value — read the
  actual constant, not the comment, if tuning ageing rate.
- `OVERLAY_STEP = 5.0` (`telemetry/env-metrics.py:65`) must track the
  controller's own `OVERLAY_STEP` constant (not owned here) — the comment
  flags this as a manually-kept invariant, not enforced in code.
- Grafana panel "Interface RX/TX throughput" is a Grafana-side `rate(...)*8`
  expression, not a stored series — nothing named `*_bps` exists in
  VictoriaMetrics; querying it directly requires reproducing the same PromQL.
