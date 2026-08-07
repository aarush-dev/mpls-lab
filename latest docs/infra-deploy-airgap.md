# Infra, Deploy, Airgap, Controller

## Purpose

This subsystem gets the lab from a bare host to a running fabric and keeps it running with zero
runtime internet egress. `deploy/` provisions a Debian host and packages/restores the whole repo.
`airgap/` saves every container image to disk and proves at runtime that no container reaches the
public internet. `controller/` is the simulated SD-WAN controller container — it derives the
overlay topology from `topology-spec.yaml`, measures real WireGuard RTT, layers a calibrated
congestion/fault model on top, does loss/latency path failover, and serves Prometheus text +
a fault-overlay HTTP API that the fault orchestrator (`faults/orchestrator.py`, not owned here)
drives. `frr-node/` is the router container image (FRR + snmpd + pmacctd + rsyslog) that every
topology node runs. `streaming/` bridges the lab's telemetry (VictoriaMetrics/Loki/fault labels/
topology) into two independent Kafka consumer groups for the predictive-analysis and copilot
pipelines. Root-level scripts (`sim-up.sh`, `copilot-up.sh`, `watchdog.sh`) and the systemd units
sequence bring-up and keep it alive; the `verify_*.py` scripts are dataset-generation acceptance
gates, not runtime health checks.

## Entry points

**Bring-up (host):**
```
sudo ./deploy/provision-debian.sh          # bare Debian 12 -> lab-ready host (docker, containerlab, kernel modules, sysctls)
./deploy/package.sh                        # tar the whole repo + refresh airgap image bundle (needs internet unless SKIP_IMAGE_SAVE=1)
./deploy/restore.sh                        # on target host: load images, pip install, install systemd units
./sim-up.sh                                # containerlab + telemetry compose + controller/trafficgen + dataapi
./copilot-up.sh                            # copilot api(:8100) + predictor + forensic trigger (requires dataapi up)
./watchdog.sh                              # runs sim-up.sh + copilot-up.sh once, then polls forever, restarting on failure
```

**Systemd (production):**
```
systemctl start noc-copilot.service        # pulls in noc-lab.service (Requires=/After=)
systemctl start noc-pa.service             # PA inference :8001 (independent unit, not chained)
systemctl start noc-pa-alerts.service      # PA alert bridge :8002 (After=/Wants= noc-pa.service)
systemctl start noc-dataapi.service        # dataapi as a managed unit (alternative to sim-up.sh's nohup)
```

**Airgap:**
```
./airgap/pull-and-save.sh                  # docker save|xz every image -> airgap/images/*.tar.xz + manifest.txt (needs internet)
./airgap/load-offline.sh                   # on the air-gapped host: docker load every bundle, verify expected tags present
./airgap/verify-airgap.sh                  # proves zero container->public egress (tcpdump -i any, 30s) + no docker pull events
```

**Controller (also runs as a container via controller/Dockerfile, CMD below):**
```
python3 controller/controller.py                       # serve Prometheus :9362 + fault-overlay HTTP API, tick every 5s
python3 controller/controller.py --port 9362 --interval 5.0
python3 controller/controller.py --once                # one scrape to stdout, exit
python3 controller/controller.py --selftest             # hermetic model+HTTP self-check, exit nonzero on failure
python3 controller/topo.py                              # print derived hub/spoke/tunnel counts
```
Controller HTTP routes (`controller/controller.py:617-704`):
```
GET  /metrics                curl http://127.0.0.1:9362/metrics
GET  /fault/overlay          curl http://127.0.0.1:9362/fault/overlay        # active overlay registry, keyed by site
POST /fault/drift            curl -XPOST -d '{"site":"ce_branch1","latency_threshold_mult":5.0,"ttl_s":120}' http://127.0.0.1:9362/fault/drift
POST /fault/drift/clear      curl -XPOST -d '{"site":"ce_branch1"}' http://127.0.0.1:9362/fault/drift/clear
POST /fault/overlay          curl -XPOST -d '{"site":"ce_branch1","fault_type":"asymmetric_loss","duration":90,"severity":"high"}' http://127.0.0.1:9362/fault/overlay
POST /fault/overlay/clear    curl -XPOST -d '{"site":"ce_branch1"}' http://127.0.0.1:9362/fault/overlay/clear
```

**Streaming:**
```
python3 streaming/bridge.py --create-topics             # idempotent: create the 4 Kafka topics
python3 streaming/bridge.py                              # poll the live stack every --interval (30s), publish to Kafka
python3 streaming/bridge.py --replay path/to/main.parquet --speed 60   # replay a dataset with no lab running
streaming/start.sh                                        # same, with KAFKA_BOOTSTRAP=127.0.0.1:29092 (host-mapped listener)
python3 streaming/consume.py --pipeline predictive --max-windows 5     # build+print feature windows, group "noc-predictive"
python3 streaming/consume.py --pipeline copilot --brief-every 20       # rolling NL incident brief, group "noc-copilot"
python3 streaming/bridge.py --selftest ; python3 streaming/consume.py --selftest
```

**frr-node container:**
```
docker build -t frr-node -f frr-node/Dockerfile .
docker run --rm --cap-add NET_ADMIN frr-node vtysh -c "show version"
docker exec <ctr> snmpwalk -v2c -c public 127.0.0.1 1.3.6.1.2.1.1
```

**Verify scripts (dataset acceptance gates, run against generator output — not services):**
```
python verify_full_generation.py <main.parquet|main_dir>     # pandas path; single moderate file
python verify_full_memsafe.py <main.parquet|main_dir>        # streamed batches; use for a full ~100M-row tranche
python verify_readiness.py <new.parquet> [events.parquet] [topology_edges.parquet] [paths.parquet]
python verify_regressions.py <new.parquet>
python verify_hardneg_paths.py <main.parquet> <paths.parquet>
```

**Test files (owned paths) — run only, not read line-by-line:**
```
python3 controller/test_overlay.py    # seam test: overlay ramp to calibrated peak, netem suppression, HTTP validation
```

## Modules

- `controller/controller.py` — SD-WAN controller: telemetry model, path selection, HTTP server, Prometheus exposition.
  - `TunnelState` — per-tunnel modelled+measured metrics (`controller.py:109`); `.update()` recomputes one tick (`:231`); `._measure_rtt()` pings the hub over wg0 (`:160`); `._read_netem()` reads injected tc qdisc on eth1 (`:198`).
  - `Controller` — fleet of `TunnelState` + path/overlay/drift registries (`:388`); `.set_overlay()` registers a fault episode (`:405`); `.select_paths()` failover/recovery logic (`:461`); `.tick()` advances one step (`:516`); `.render_prometheus()` exposition text (`:544`); `.refresh_measured()` background RTT pool (`:431`).
  - `_load_fault_signatures()` — loads calibrated fault→signature table from `synthetic/profile.json`, falls back to `signatures.default_signatures()` (`:59`).
  - `_handler_factory(ctrl)` — `BaseHTTPRequestHandler` subclass implementing the routes listed above (`:617`).
  - `serve()` — starts the threading HTTP server, warms + backgrounds the RTT pool, runs the tick loop (`:707`).
  - `_selftest()` — hermetic (no docker exec, no ping) model/HTTP self-check (`:726`).
- `controller/topo.py` — pure derivation of the overlay model (hubs/spokes/tunnels/VRFs) from `topology-spec.yaml`; shared by controller and trafficgen so both use identical node naming.
  - `load_spec()` reads YAML, falls back to `_default_spec()` if PyYAML/file missing (`:32`).
  - `build_model(spec=None)` — returns `{hubs, spokes, tunnels, vrfs, site_vrfs}`; tunnels = spokes × hubs (`:41`).
- `controller/Dockerfile` — build context is repo root; installs `docker:cli`'s static binary (no daemon), pyyaml+numpy; copies controller.py/topo.py + shared `trafficgen/diurnal.py` + `faults/signatures.py` + `topology-spec.yaml`; `EXPOSE 9362`.
- `controller/test_overlay.py` — seam test for issue #61 (overlay ramp correctness + netem suppression + HTTP validation). Run-only.
- `airgap/pull-and-save.sh` — pulls registry images (skips if cached), `docker save | xz -T0 -3` every image, writes `manifest.txt` atomically. Idempotent by image-created-time vs tarball-mtime comparison (`:64-71`).
- `airgap/load-offline.sh` — `xz -d | docker load` every `*.tar.xz` in `airgap/images/`, then verifies all 13 expected tags are present.
- `airgap/verify-airgap.sh` — 4 checks: containerlab `image-pull-policy: Never` coverage, telemetry images present locally, 30s `tcpdump -i any` capture for container-src→public-dst packets, and zero `docker events --filter event=pull` since script start.
- `airgap/manifest.txt` — generated inventory (image, digest, size, filename); ground truth is `pull-and-save.sh`'s output, not this file.
- `deploy/provision-debian.sh` — bare Debian 12 → lab host: apt packages, docker (get.docker.com), containerlab 0.76.1 (get.containerlab.dev), optional claude CLI, kernel modules (`/etc/modules-load.d/noc-lab.conf`), sysctls (`/etc/sysctl.d/99-mpls.conf`, `99-noc-inotify.conf`), then runs the Phase-0 kernel checklist inline (mpls_router, mpls label-impose, vrf, netem, veth, wireguard).
- `deploy/package.sh` — tars the whole repo (git-tracked + untracked, since topology/airgap-images/.env/keys/datasets are gitignored) into `../noc-lab-bundle-<ts>.tar.gz`, plus `../claude-context-<ts>.tar.gz`; refreshes the airgap image bundle first (calls `pull-and-save.sh`).
- `deploy/restore.sh` — on target host: hard-checks `$REPO == /root/LAB` (several files hardcode that path), loads docker images (`airgap/load-offline.sh`), `pip install --break-system-packages` every `requirements.txt` found, installs+enables `noc-lab.service`/`noc-copilot.service`, restores the Claude context bundle if present (gated on a `.migrated` marker, backs up any existing `~/.claude` first).
- `frr-node/Dockerfile` — base `quay.io/frrouting/frr:10.5.1`; adds net-snmp, pmacct, iproute2, wireguard-tools/wireguard-go, iptables, tcpdump, rsyslog; copies `rsyslog.conf`/`pmacctd.conf`/`start.sh`; `CMD ["/start.sh"]`. FRR's own AgentX SNMP sub-agent is left disabled (ABI mismatch note in file); Telegraf polls snmpd's IF-MIB directly instead.
- `frr-node/start.sh` — ordered startup: MPLS sysctl (best-effort) → rsyslogd (must precede FRR so `/dev/log` is consumed) → snmpd → pmacctd (backgrounded) → `exec /usr/lib/frr/docker-start` (foreground, keeps container alive). `envsubst` stamps `PROMTAIL_HOST`/`PROMTAIL_PORT`/`NFACCTD_ADDR` into `/run/{rsyslog,pmacctd}.conf` before each daemon reads its config.
- `frr-node/rsyslog.conf` — `imuxsock` input only; `omfwd` forward-all action to `${PROMTAIL_HOST}:${PROMTAIL_PORT}` UDP, RFC5424 (`RSYSLOG_SyslogProtocol23Format`) — promtail's syslog listener requires RFC5424, busybox syslogd only emits RFC3164.
- `frr-node/pmacctd.conf` — `pcap_interface: any`, `pcap_direction: in` (avoids double-counting routed packets), aggregates `src_host,dst_host,src_port,dst_port,proto,tos`, exports IPFIX v10 to `${NFACCTD_ADDR}`.
- `streaming/bridge.py` — publishes lab telemetry into 4 Kafka topics for two independent consumer groups.
  - `event_record()` — maps a Loki log line + labels to a typed event, using promtail's pre-extracted labels (`bgp_state`/`ldp_event`/`ospf_to`) (`:101`).
  - `templatize()` — masks IPs/numbers, hashes residue to a stable `template_id` (`:87`).
  - `Bridge` class — `.pump_metrics()` reuses `dataapi/export.py`'s collector (`:179`); `.pump_events()` queries Loki, dedupes by ns cursor (`:204`); `.pump_faults()` tails `faults/labels/*.jsonl` by byte offset (`:227`); `.pump_topology()` reads `topology/topology-meta.json` + live `sdwan_path_active` (`:258`); `.cycle()` runs all four (`:290`).
  - `replay()` — replays a dataset Parquet as if live, for offline dev/demo (`:301`).
  - `create_topics()` — idempotent topic creation with per-topic partitions/retention (`:145`).
- `streaming/consume.py` — the two downstream Kafka consumer groups.
  - `PredictiveWindower` — fixed-length per-(device,entity) feature windows, multi-label fault-joined (`:110`); `.add_metric()` emits a window every `stride` buckets once `length` buckets buffered (`:135`); `._overlaps()` interval-overlap test with mixed-format epoch parsing (`:171`).
  - `drain_faults()` — reads `noc.faults` from earliest to current end-offset before any window building starts, to avoid the "windows built before labels arrive" ordering bug (`:189`).
  - `CopilotState` — rolling fabric view; `.brief()` renders the LLM-facing NL incident brief (`:333`); `.partition_faults()` splits active/resolved by recency, not by `t_end` presence, because labels are only written retrospectively at fault revert (`:296`).
- `streaming/start.sh` — sets `KAFKA_BOOTSTRAP=127.0.0.1:29092` (host-mapped Kafka listener, distinct from the in-lab `9092`) and execs `bridge.py`.
- `watchdog.sh` — brings up `sim-up.sh` + `copilot-up.sh` once, then polls 8 named health checks every `POLL_S`; a check failing `GRACE_TICKS` consecutive polls triggers `restart_lab` or `restart_copilot` depending on which check failed.
- `copilot-up.sh` — starts uvicorn `copilot.api.app:app` (:8100) + `copilot.emulator.predictor` + `copilot.forensic.trigger` via nohup+disown, preflights dataapi, then proves the predictor loop is live with a synthetic low-severity heartbeat probe fault (cleaned up on any exit path).
- `sim-up.sh` — deploys/rewires containerlab topology (detects "up but unwired" after a host restart via an `eth1` link probe), starts telemetry compose (excluding the tele-grafana service; the plugin Grafana owns :3000), controller/trafficgen/fault sidecars, plugin Grafana, dataapi; verifies 5 endpoints.
- `verify_full_generation.py` / `verify_full_memsafe.py` — dataset acceptance: per-fault-type instance counts, topology diversity, hard-negative volume, concurrent/cascade episode presence, lead-time CV, error-column non-regression, vrf list-typing. memsafe variant streams via `pyarrow.dataset` batches instead of loading the whole table.
- `verify_readiness.py` — checks specific schema columns/gates (G1 topology diversity, G4 hard negatives, G6 stream tagging, G7 cascade columns, G8 injection_seed) plus regression checks; optionally validates `events.parquet`/`topology_edges.parquet`/`paths.parquet` if passed.
- `verify_regressions.py` — checks two specific label-correctness defects (severity null-where-ignored for 5 named scenarios; tunnel `vrf` as `list<string>` not a `+`-delimited string) plus prior-pass non-regression.
- `verify_hardneg_paths.py` — checks hard-negative label purity (no leaked `time_to_impact_s`/severity/`impact_method`) and that `paths.parquet` captures real reroute intervals (open `valid_to`, multi-row path groups).

## Parameters

| name | default | env-var/CLI-flag | units | what it controls | source (file:line) |
|---|---|---|---|---|---|
| port | 9362 | `--port` | int | controller HTTP/metrics listen port | controller/controller.py:823 |
| interval | 5.0 | `--interval` | s | controller tick period | controller/controller.py:824 |
| PERIOD_SECONDS | 3600 | env `DIURNAL_PERIOD` | s | length of the compressed diurnal cycle (24h→this) | controller/controller.py:106 |
| MEASURE_RTT | off | env `MEASURE_RTT` | bool | gates the wg0 ping pool on/off (off in --selftest) | controller/controller.py:158 |
| refresh_measured workers | 16 | `refresh_measured(workers=)` | count | thread-pool size for the RTT ping sweep | controller/controller.py:431 |
| measure loop period | 45.0 | `_measure_loop(period=)` | s | cadence of background RTT cache refresh | controller/controller.py:447 |
| MEASURE_FLOOR_MS | 1.0 | const | ms | latency floor before/without a measured RTT | controller/controller.py:99 |
| OVERLAY_STEP | 5.0 | const | s | ramp-progress duration floor in `signatures.prog()` | controller/controller.py:49 |
| OVERLAY_SEVMUL | low:0.5 med:0.8 high:1.0 | const dict | multiplier | fault-overlay severity scaling | controller/controller.py:50 |
| OVERLAY_BASE_LAT/LOSS/JIT | 30.0 / 0.3 / 3.0 | const | ms / % / ms | fallback signature bases when no calibration profile present | controller/controller.py:55 |
| SITE_QUEUE_MULT | dc:0.6 hub:0.8 branch:1.3 | const dict | multiplier | per-site-type queueing sensitivity | controller/controller.py:90 |
| VOICE_SENSITIVITY | 1.4 | const | multiplier | loss/jitter scaling on VOICE-carrying tunnels | controller/controller.py:93 |
| FAILOVER_LOSS_PCT | 5.0 | const | % | loss threshold that marks a path degraded | controller/controller.py:103 |
| FAILOVER_LATENCY_MULT | 3.0 | const | multiplier of base_ms | latency threshold that marks a path degraded | controller/controller.py:104 |
| failover hysteresis | 0.85 | const | fraction | candidate must score < 85% of current path's score to fail over | controller/controller.py:493 |
| smoothing α (latency/jitter) | 0.3 | const | fraction | exponential-smoothing weight on new target | controller/controller.py:342-344 |
| smoothing weight (loss) | 0.55 new / 0.45 old | const | fraction | loss smoothed lighter so micro-bursts stay spiky | controller/controller.py:345 |
| jitter AR(1) memory | 0.85 | const | fraction | jitter random-walk autocorrelation | controller/controller.py:301 |
| queue_ms cap | 60.0 | const | ms | ceiling on M/M/1 queueing term near saturation | controller/controller.py:294 |
| rekey base interval | 120.0 | const | s | baseline WireGuard rekey cadence divisor | controller/controller.py:351 |
| VRF_PREFERRED_HUB | CORP/VOICE→hub1, GUEST→hub2 | const dict | mapping | per-VRF preferred hub before failover | controller/controller.py:80 |
| branch_count/hub_count/dc_count | 4 / 2 / 2 | `topology-spec.yaml` `knobs` (fallback const) | count | fleet size used to derive tunnels | controller/topo.py:23 |
| TOPO_SPEC | `../topology-spec.yaml` | env `TOPO_SPEC` | path | spec file topo.py reads | controller/topo.py:14-17 |
| xz save level | `-T0 -3` | const (pull-and-save.sh) | xz level | image tarball compression | airgap/pull-and-save.sh:69 |
| tcpdump capture window | 30 | const `CAPTURE_SECS` | s | airgap egress-proof capture duration | airgap/verify-airgap.sh:81 |
| containerlab version | 0.76.1 | const | version | pinned installer version | deploy/provision-debian.sh:28 |
| net.mpls.platform_labels | 1048575 | sysctl | count | MPLS label space size | deploy/provision-debian.sh:60 |
| net.mpls.default_ttl | 255 | sysctl | int | default MPLS TTL | deploy/provision-debian.sh:61 |
| fs.inotify.max_user_instances | 1024 | sysctl | count | inotify instance cap (large clab topology) | deploy/provision-debian.sh:66 |
| fs.inotify.max_user_watches | 1048576 | sysctl | count | inotify watch cap | deploy/provision-debian.sh:67 |
| NFACCTD_ADDR | 172.20.20.53:2055 | env | ip:port | IPFIX collector target | frr-node/start.sh:21 |
| PROMTAIL_ADDR | 172.20.20.55:1514 | env | ip:port | syslog forward target | frr-node/start.sh:22 |
| nfprobe_version | 10 (IPFIX) | const | version | pmacctd export protocol | frr-node/pmacctd.conf:22 |
| SCHEMA_VERSION | 1 | const `_v` field | int | Kafka record schema tag | streaming/bridge.py:50 |
| KAFKA_BOOTSTRAP (bridge, in-lab default) | 127.0.0.1:9092 | env `KAFKA_BOOTSTRAP` | host:port | broker address for bridge.py/consume.py direct run | streaming/bridge.py:51 |
| KAFKA_BOOTSTRAP (start.sh, host-mapped) | 127.0.0.1:29092 | env `KAFKA_BOOTSTRAP` | host:port | broker address when run via streaming/start.sh | streaming/start.sh:7 |
| TOPICS partitions/retention | metrics 6p/1d, events 6p/7d, faults 3p/30d, topology 1p/30d | const dict | count / ms | Kafka topic layout | streaming/bridge.py:58-63 |
| producer acks | 1 | const | ack mode | leader-ack only, not all-ISR | streaming/bridge.py:139 |
| producer linger_ms | 200 | const | ms | batching window | streaming/bridge.py:140 |
| bridge --interval | 30.0 | CLI flag | s | poll cycle length | streaming/bridge.py:426 |
| bridge --step | 30 | CLI flag | s | metric bucket size | streaming/bridge.py:427 |
| bridge --topology-every | 10 | CLI flag | cycles | topology republish cadence | streaming/bridge.py:429 |
| bridge --speed | 60.0 | CLI flag | x realtime | replay speedup | streaming/bridge.py:433 |
| PredictiveWindower length | 168 | `--window` / ctor `length=` | buckets | window size (84 min at 30s buckets) | streaming/consume.py:120, 493 |
| PredictiveWindower stride | 4 | `--stride` / ctor `stride=` | buckets | emit cadence (one window per 2 min) | streaming/consume.py:120, 494 |
| CopilotState event_ring | 200 | ctor `event_ring=` | count | rolling event buffer depth | streaming/consume.py:275 |
| CopilotState recent_s | 900.0 | ctor `recent_s=` | s | window for "still active" fault classification | streaming/consume.py:275 |
| max_poll_records | 500 | const (KafkaConsumer) | count | per-poll fetch cap | streaming/consume.py:103 |
| brief_every | 50 | `--brief-every` | records | copilot NL-brief print cadence | streaming/consume.py:497 |
| POLL_S | 5 | env `POLL_S` | s | watchdog poll interval | watchdog.sh:10 |
| GRACE_S | 30 | env `GRACE_S` | s | watchdog failure grace window | watchdog.sh:11 |
| GRACE_TICKS | ceil(GRACE_S/POLL_S) = 6 | derived | ticks | consecutive-fail count before restart | watchdog.sh:13 |
| noc-copilot TimeoutStartSec | 120 | unit file | s | systemd start timeout | noc-copilot.service:13 |
| noc-lab TimeoutStartSec | 0 (unbounded) | unit file | s | systemd start timeout (148-node deploy can take minutes) | noc-lab.service:12 |
| RestartSec (dataapi/pa/pa-alerts) | 3 | unit file | s | systemd restart backoff | noc-dataapi.service:11, noc-pa.service:16, noc-pa-alerts.service:16 |
| PA_ALERT_INTERVAL_S | 15 | unit env | s | PA alert bridge scoring cadence | noc-pa-alerts.service:12 |
| hard-negative floor | 800 / 4000 | const (verify script) | instances | per-fault-type / total hard-negative acceptance floor | verify_full_generation.py:65,71 |
| lead_time_s CV floor | 0.5 | const (verify script) | coefficient of variation | acceptance gate on lead-time diversity | verify_full_generation.py:75 |

## Data flow

**Controller telemetry (per 5s tick, `controller.py:516`):**
`topology-spec.yaml` → `topo.build_model()` → `TunnelState` per (spoke,hub) pair → each tick: (a) `docker exec <clab-container> ping -I wg0 <hub_wg>` on a 45s background cadence populates `._measured` (avg/jitter/loss); (b) `docker exec <clab-container> tc qdisc show dev eth1` once per site per tick reads injected netem, feeding the FAULT term; (c) `diurnal.util()`/`diurnal.hour_of_cycle()`/`diurnal.week_scale()` (external, `trafficgen/diurnal.py`) drive the congestion proxy; (d) an active `_overlay[site]` record (set via `POST /fault/overlay`) is layered in as the authoritative fault term, suppressing the netem readback to avoid double-counting; (e) outputs are smoothed into `latency_ms`/`jitter_ms`/`loss_pct`/`rekeys` and exposed as Prometheus text on `GET /metrics`, which Telegraf scrapes.

**Fault overlay:** the fault orchestrator (`faults/orchestrator.py`, not owned here) `POST`s to `/fault/overlay` with `{site, fault_type, severity, duration}` → `Controller.set_overlay()` looks up the calibrated signature (`synthetic/profile.json` "fault_signatures", falling back to `signatures.default_signatures()`) → registers `{t_start, t_impact, t_end, dur, sevmul, p_cross}` → every tick after, `TunnelState.update()` reads it and ramps toward the signature peak via `signatures.prog()`/`signatures.tunnel_ramp_targets()` (external, `faults/signatures.py`).

**Path selection:** `Controller.select_paths()` reads each tunnel's smoothed `loss_pct`/`latency_ms` → scores candidates → on sustained degradation (or drift override via `POST /fault/drift`) flips `self.active[(site,vrf)]` to the better hub, emitting a `path_change` event (stdout JSON) and updating the `sdwan_path_active` gauge that `streaming/bridge.py`'s `pump_topology()` later reads back.

**Airgap image supply:** `docker pull`/local build → `airgap/pull-and-save.sh` (`docker save | xz`) → `airgap/images/*.tar.xz` + `airgap/manifest.txt` → copied to the air-gapped host → `airgap/load-offline.sh` (`xz -d | docker load`) → `airgap/verify-airgap.sh` proves no egress at runtime.

**Bring-up chain:** `deploy/provision-debian.sh` (bare host) → `deploy/restore.sh` (loads images, installs systemd units) → `noc-lab.service`/`sim-up.sh` (containerlab + telemetry compose + controller/trafficgen + dataapi) → `noc-copilot.service`/`copilot-up.sh` (copilot api/predictor/trigger, `Requires=`/`After=noc-lab.service`) → `watchdog.sh` supervises both indefinitely.

**Streaming bridge (30s cycle, `bridge.py:290`):** `dataapi/export.py` collectors (VictoriaMetrics range query) → `noc.metrics`; Loki `loki_query_range` → `event_record()` templating → `noc.events`; `faults/labels/*.jsonl` tail-by-offset → `noc.faults`; `topology/topology-meta.json` + `sdwan_path_active` metric → `noc.topology` (every `--topology-every` cycles). Two independent consumer groups read the same topics: `noc-predictive` (offset `earliest`, builds `PredictiveWindower` feature windows) and `noc-copilot` (offset `latest`, builds `CopilotState`'s rolling NL brief).

**frr-node telemetry egress:** FRR/kernel logs → `/dev/log` → rsyslogd `imuxsock` → `omfwd` UDP RFC5424 → promtail:1514. Packet counters → pmacctd (`pcap_interface: any`, ingress-only) → IPFIX v10 → nfacctd:2055. SNMP polling: Telegraf → UDP 161 → snmpd (IF-MIB).

## Calculations

All formulas below are in `controller/controller.py` unless noted.

- **Congestion proxy** `cong` (`:255-256`): `cong = clamp(max(diurnal.util(hod, v) for v in tunnel.vrfs) * wk, 0, 0.985)` where `hod = diurnal.hour_of_cycle(now, PERIOD_SECONDS)`, `wk = diurnal.week_scale(now, PERIOD_SECONDS)` (external `trafficgen/diurnal.py`). Feeds queueing, jitter, loss.
- **Queueing delay** `queue_ms` (`:294`): `min(60.0, queue_mult * 9.0 * rho / (1 - rho))`, M/M/1-style, `rho = cong`, `queue_mult = SITE_QUEUE_MULT[site_type]`.
- **Jitter AR(1) walk** (`:300-301`): `amp = 0.25 + 1.6*rho + netem_delay*0.12`; `jit_walk = 0.85*jit_walk_prev + 0.15*N(0, amp)`.
- **Target jitter** (`:304`): `target_jit = max(0, meas_jit + (0.4 + |jit_walk| + 0.4*rho) * voice_k)`, `voice_k = VOICE_SENSITIVITY if "VOICE" in vrfs else 1.0`.
- **Loss floor + congestion tail** (`:311-312`): `floor_loss = max(0, N(0.08, 0.06))`; `cong_tail = max(0, rho - 0.80)**2 * 22.0` (only bites past 80% utilization).
- **Micro-burst loss** (`:317-321`): each tick, if idle, `p_burst = 0.004 + rho*0.05`; on trigger, `burst_ticks = randint(1,4)`, `burst_loss = uniform(0.6, 3.5) * voice_k`, held for `burst_ticks` ticks.
- **Target loss** (`:323-324`): `modelled_loss = (floor_loss + cong_tail) * voice_k`; `target_loss = max(meas_loss, modelled_loss) + burst_loss + netem_loss`.
- **Target latency** (`:327`): `target_lat = meas_avg + queue_ms + netem_delay + N(0, 0.4)`.
- **Overlay ramp fraction** `ov_p` (`:268-270`): `signatures.prog(now, t_start, t_impact, t_end, dur, sevmul, OVERLAY_STEP, p_cross)` (external `faults/signatures.py`), applied only while an overlay is registered; when active, `netem_delay = netem_loss = 0` to avoid double-counting a real tc action.
- **Overlay-adjusted targets** (`:335-338`): `lat_t, jit_t = signatures.tunnel_ramp_targets(sig, target_lat, target_jit)`; `target_lat += ov_p*(lat_t - target_lat)`; `target_jit += ov_p*(jit_t - target_jit)`; `target_loss += ov_p * sig["loss_peak"]`.
- **Exponential smoothing** (`:342-345`): `latency_ms = max(0.1, 0.7*latency_ms_prev + 0.3*target_lat)`; `jitter_ms` same weights; `loss_pct = max(0, 0.45*loss_pct_prev + 0.55*target_loss)` (lighter smoothing so bursts stay visible).
- **Rekey cadence + clustering** (`:351-359`): `rekey_interval = 120.0 / (1.0 + loss_pct*0.5)`; stress trigger: `if loss_pct > 2.0 and rand() < (loss_pct - 2.0)*0.04: debt += randint(1,3)`; a rekey fires when debt is nonzero or `now - last_rekey >= rekey_interval`.
- **Failover decision** (`:482, 489-493`): `score(t) = t.loss_pct*10.0 + t.latency_ms`; `degraded = cur is None or loss_pct >= FAILOVER_LOSS_PCT or latency_ms >= base_ms*eff_mult` (`eff_mult` = drift override or `FAILOVER_LATENCY_MULT`); switch fires when `best.hub != cur and degraded and score(best) < score(cur)*0.85` (15% hysteresis band).
- **Overlay episode timing** (`:415-418`): `lead_s = sig["lead_s"]` unless overridden; `t_impact = t0 + lead_s`; `t_end = t_impact + duration`.
- **Hub wg IP** (`controller/topo.py:57`): `172.16.0.{hub_index}`, 1-indexed.
- **Watchdog grace ticks** (`watchdog.sh:13`): `GRACE_TICKS = (GRACE_S + POLL_S - 1) / POLL_S` (ceiling division), default `(30+5-1)/5 = 6` ticks.
- **Kafka template id** (`streaming/bridge.py:87-98`): mask IPs (`\b\d{1,3}(\.\d{1,3}){3}\b` → `<IP>`) and digit runs (`\d+` → `<N>`), collapse whitespace, truncate to 200 chars, `template_id = "t" + blake2b(digest_size=4).hexdigest()` of the masked text.
- **Image bundle skip logic** (`airgap/pull-and-save.sh:64-65`): re-save only if `stat(tarball).mtime <= docker inspect --format='{{.Created}}' <image>` — a tarball newer than the image it claims to hold is considered stale-safe to skip; otherwise the image is re-saved even if a same-named tarball exists.
- **Dataset lead-time CV** (`verify_full_generation.py:74`): `cv = sqrt(lead_sqsum/n - mean²) / mean`, streamed accumulator (never materializes the full column), must be `>= 0.5`.

## Config & schemas

**`airgap/manifest.txt`** — fixed-width text table, columns `IMAGE | DIGEST | SIZE | FILE`. Written by `pull-and-save.sh:83-90`; `DIGEST` is the registry `RepoDigests[0]` when present, else `local:<first 19 chars of image id>...` for locally-built images (`frr-node`, `noc-controller`, `noc-trafficgen`). Consumed only visually / by `load-offline.sh`'s hardcoded `EXPECTED` tag list (not parsed from the manifest).

**Controller `POST /fault/overlay` request** (`controller.py:661-686`): `{site: str, fault_type: str, severity?: "low"|"medium"|"high" (default "high"), duration?: float (default 60.0), lead_s?: float (default = signature's calibrated lead), t_start?: epoch float (default now)}`. Validated at the boundary: unknown `site` (must be in `ctrl._sites`), unknown/non-`tunnel_ramp` `fault_type`, `lead_s < 0`, `duration < 2*OVERLAY_STEP` (10s), or unknown `severity` → HTTP 400. Response: `{ok, site, fault_type, t_impact, t_end}`.

**Controller `POST /fault/drift`** (`:650-656`): `{site: str, latency_threshold_mult?: float (default 2.0), ttl_s?: float}` → sets `ctrl._drift[site] = {latency_threshold_mult, expires}`; `expires=None` if `ttl_s` omitted (never auto-clears). Pruned in `tick()` in-place (`:524-526`).

**Controller `GET /fault/overlay`** (`:620-629`): `{site: {fault_type, t_start, t_impact, t_end, expires, sevmul}}` for every active overlay — read by the env-metrics sidecar (same image) to ramp optics/thermal telemetry at the same severity.

**Prometheus exposition** (`render_prometheus()`, `:544-608`): gauges `sdwan_tunnel_latency_ms`/`_jitter_ms`/`_loss_pct` (labels `device,tunnel,site,site_type,hub`), counter `sdwan_tunnel_rekeys_total`, gauge `sdwan_path_active` (labels `device,site,site_type,vrf,hub`, value 1 on the active hub), counter `sdwan_path_changes_total` (fabric-wide, unlabelled — HELP text warns it also moves from modelled loss bursts with no fault injected), gauge `sdwan_controller_drift_active{site}`, gauge `sdwan_overlay_active{site,fault_type}`. HELP text on every latency/jitter/loss metric explicitly states these are SIMULATED (measured wg0 RTT + modelled congestion + netem-config readback), not raw measurements.

**Kafka topic schemas** (`streaming/bridge.py`): every record carries `_v: 1` (`SCHEMA_VERSION`). `noc.metrics` — one row per (device, entity, bucket), same columns as `dataapi/export.COLUMNS` (external). `noc.events` — `{_v, ts, device, entity, event_type, severity, app, template_id, template, params, raw}` (`raw` is `None` for matched event types, present only for unmatched log lines). `noc.faults` — tailed verbatim from `faults/labels/*.jsonl` plus `_v` (schema owned by the fault orchestrator, not this subsystem). `noc.topology` — `{_v, ts, topology_id, meta (topology-meta.json), graph (sources.topology_graph()), active_paths: [{device, site, vrf, hub, path_type:"wg_tunnel"}]}`.

**`PredictiveWindower` emitted window** (`consume.py:147-169`): `{device, entity, entity_type, t_start, t_end, n_buckets, feature_names, features: [[float|None]], fault_types: [str], scenario_ids: [str], impact_methods: [str], n_concurrent: int, is_fault: bool}`. Multi-label by design — a window can overlap multiple concurrent faults.

**systemd units** — `noc-lab.service` (oneshot, `RemainAfterExit=yes`, `Requires=/After=docker.service`, `TimeoutStartSec=0`, `ExecStart=/root/LAB/sim-up.sh`); `noc-copilot.service` (oneshot, `Requires=/After=noc-lab.service`, `TimeoutStartSec=120`, `ExecStart=/root/LAB/copilot-up.sh`, no `Restart=` — a proc dying mid-run is invisible to systemd, documented as an accepted gap); `noc-dataapi.service` (`Type=simple`, `Restart=on-failure`, `RestartSec=3`, `Environment=PA_ALERTS_URL=http://127.0.0.1:8002`); `noc-pa.service` (`Type=simple`, `ExecStartPre` runs `dataapi/topology_edges.py`, `ExecStart=uvicorn src.serve.app:app --port 8001`, `Environment=RUN_DIR/GRAPH_DATA_ROOT/PYTHONPATH`); `noc-pa-alerts.service` (`After=/Wants=noc-pa.service`, `ExecStart=uvicorn pa_alerts.service:app --port 8002`, `Environment=PA_URL/PA_ALERTS_URL/PA_ALERT_MODE=rank/PA_ALERT_INTERVAL_S=15`).

## Gotchas

- **`airgap/verify-airgap.sh` must capture on `any`, not `eth0`** (`:70-76`): the host's iptables MASQUERADE rewrites the source before it hits `eth0`, so an `eth0` capture is a guaranteed-empty no-op regardless of real egress. Also: an empty/absent pcap file is scored FAIL, not PASS — tcpdump writing zero packets and tcpdump never running look identical unless you check the pcap header was written (`:99-102`).
- **Fault overlay suppresses netem readback while active** (`controller.py:266-271`): if you inject netem manually on a tunnel's uplink AND `POST /fault/overlay` for the same site, the overlay wins — the real tc impairment is present on the wire but zeroed out of the exposed metric. Don't expect to see both stack.
- **`_measure_rtt()` 100%-loss handling is intentionally asymmetric** (`:192-196`): a total outage returns `(None, 0.0, loss>=100)`, which is cached and dominates the loss term; an unparseable/failed ping returns `None` entirely and the previous cache is kept — conflating the two would make total outages look pre-outage-healthy.
- **One netem readback per site per tick, not per tunnel** (`controller.py:530-536`): 6 tunnels can share a spoke's uplink; `tick()` hoists the `docker exec tc qdisc show` call once per site to avoid 168 execs/tick instead of 28.
- **Deterministic per-tunnel RNG via crc32, not `hash()`** (`:132-136`): `PYTHONHASHSEED` randomizes `hash(str)` per process, which would reseed every tunnel's noise on every controller restart; `zlib.crc32` is stable.
- **`--selftest` sets `_SKIP_NETEM=True` and `_MEASURE_RTT=False`** (`:727-728`, mirrored in `test_overlay.py:21-22`) — these are class-level attribute overrides on `TunnelState`, not instance state; forgetting to reset them before other code paths in the same process would silently disable both live-measurement features.
- **`deploy/restore.sh` hard-fails outside `/root/LAB`** (`:14`): several files hardcode that absolute path (systemd `ExecStart=`, `sim-up.sh` cwd assumptions, generator scripts) — a checkout at any other path breaks silently elsewhere, so restore.sh refuses to proceed instead.
- **`deploy/package.sh` hard-fails if the checkout dir isn't literally named `LAB`** (`:24`): the tarball extracts as `<dirname>` under the parent, and `restore.sh`'s path check would then fail on the target.
- **`sim-up.sh`'s "container exists" check is not a health check** (`:14-23`): a host/docker restart keeps containers but drops containerlab's veth links (only `eth0`/`lo` survive) — `wired()` probes `p1:eth1` specifically to detect this and forces `destroy && deploy` instead of assuming a running container is a wired one.
- **`copilot-up.sh`'s heartbeat probe writes into the ground-truth labels directory** (`:70-98`): `faults/labels/zzz-copilot-heartbeat-probe.jsonl` is a real file the predictor reads like any other label; it's prefixed `zzz-` so a real active fault still wins primary classification, and it's removed on every exit path via `trap ... EXIT` — a crash between write and trap setup would leak it (trap is set immediately after the first cleanup call, mitigating but not eliminating the window).
- **`noc-copilot.service` cannot stop its own children** (`noc-copilot.service:14-17`, `copilot-up.sh:33`): `nohup ... & disown` reparents the api/predictor/trigger procs out of the unit's cgroup, so `systemctl stop noc-copilot` does not signal them — documented as accepted for the demo; use `pkill` to actually stop the procs.
- **`frr-node`'s `omfwd` action must not be loaded as a plugin module** (`frr-node/rsyslog.conf:8-9`): `omfwd` is an rsyslog builtin; `module(load="omfwd")` fails and kills rsyslogd at boot on this Alpine build — the action is used directly with no corresponding `module()` line.
- **pmacctd's `tos` aggregate primitive must be listed even though nothing downstream groups by it** (`frr-node/pmacctd.conf:13-17`): the nfprobe plugin only populates IPFIX fields named on its own aggregate line; omitting `tos` exports `ipClassOfService` as 0 forever regardless of nfacctd-side config.
- **`controller/controller.py` reaches into containers via a mounted `docker.sock`, not `ip netns exec`** (`:204-210`): the netns path needs host-net privileges the controller container doesn't have; shelling out to `docker exec` through the mounted socket works unprivileged. Any failure (lab not deployed, socket not mounted) is swallowed to `(0,0)` — the controller runs fine standalone, just with no netem/measured-RTT signal.
