# Synthetic data, Generator, Topology, TrafficGen

## Purpose

Four things that build and feed the ML training corpus for the air-gapped
predictive NOC copilot:

- `generator/` — reads `topology-spec.yaml`, derives every address/config from
  node indices, renders `topology/` (containerlab topology + per-node FRR/SNMP/
  QoS/WireGuard configs). This is what stands the lab up.
- `topology/` — the rendered output: `clab.yml`, per-node `configs/<node>/*`,
  `topology-meta.json` (POP/SRLG/area map consumed by `faults/orchestrator.py`
  and `dataapi/`).
- `trafficgen/` — drives a diurnal traffic load across the deployed lab's
  CE/host pairs so SNMP octet counters and IPFIX flows look real.
- `synthetic/` — calibrates a profile off a real captured Parquet
  (`dataapi/datasets/*.parquet`), then generates large labeled synthetic
  telemetry in the exact same 59-column schema, for concatenation with real
  data at ML training time.
- `ragcorpus/` — the RAG knowledge base (incident reports + runbooks) the
  copilot's retrieval step queries.

Pipeline position: `topology-spec.yaml` → `generator/generate.py` →
`topology/*` (deploy target for containerlab) → live lab → `trafficgen/` keeps
it busy → `dataapi/export.py` captures real telemetry → `synthetic/calibrate.py`
turns one real capture into `profile.json` → `synthetic/generate.py` emits
large labeled synthetic Parquets → `synthetic/check.py` / `verify_fixes.py`
gate them → ML training reads real + synthetic Parquets interchangeably.

## Entry points

```bash
# --- topology generation (generator/) ---
cd generator && python3 generate.py            # render topology/ from topology-spec.yaml
cd generator && python3 generate.py --check    # + self-test: no IP/mgmt-IP collisions, files present

# --- traffic generation (trafficgen/, needs a deployed lab) ---
cd trafficgen && python3 trafficgen.py --plan               # print one diurnal plan (JSON lines), exit
cd trafficgen && python3 trafficgen.py --backend nc          # drive real nc flows via docker exec (default)
cd trafficgen && python3 trafficgen.py --backend sim --ticks 10   # loopback simulator, no lab needed
cd trafficgen && python3 trafficgen.py --selftest
cd trafficgen && python3 diurnal.py --plot                   # ASCII day curve
cd trafficgen && python3 diurnal.py --selftest

# --- synthetic dataset (synthetic/) ---
cd synthetic && python3 calibrate.py                          # real Parquet -> profile.json
cd synthetic && python3 generate.py                           # demo: 2 days, 30s step
cd synthetic && python3 generate.py --days 7 --scale 3        # denser fault episodes
cd synthetic && python3 generate.py --topologies 8 --hard-neg 200   # multi-topology F/N run
cd synthetic && python3 generate.py --full --seeds 42,43      # full-scale, all 12 topologies, streamed write
cd synthetic && python3 check.py                              # 9-gate assert check on newest output/*.parquet
cd synthetic && python3 verify_fixes.py <train.parquet> <holdout.parquet>   # 24-check acceptance gate
cd synthetic && python3 discriminator.py                      # realism-gap AUC (real vs synthetic)
cd synthetic && python3 topologies.py                         # self-check: 12 variants, size span, VRF ifaces
cd synthetic && python3 events.py                              # self-check: events schema round-trip
cd synthetic && python3 topology_paths.py                      # self-check: edges/paths interval encoding

# --- ragcorpus ---
cd ragcorpus && python3 check_corpus.py    # assert every faults/orchestrator.py scenario is named in a runbook
```

No FastAPI routes or `__main__` HTTP servers in these four dirs — all entry
points are CLI scripts.

## Modules

### `generator/`

- `generate.py` — topology model builder + Jinja2 renderer. `build(spec)`
  (`generator/generate.py:117`) derives the full node/link/address model from
  `topology-spec.yaml` knobs; `render(model)` (`:583`) writes `topology/clab.yml`
  + per-node config files; `check(model, post_render)` (`:736`) is the
  self-test (IP collisions, iBGP peer counts, WG peer counts, required files);
  `site_netem(site_type, idx)` (`:60`) is the single source of the per-CE WAN
  netem model; `wg_keypair`/`_wg_genkey_via_docker` (`:82`, `:94`) generate and
  cache WireGuard keys by shelling into the `frr-node:0.1` image; `emit_telemetry`
  (`:713`) writes `topology/telemetry/device_map.txt` (nfacctd pre_tag_map).
- `templates/*.j2` — Jinja2 templates rendered per node: `clab.yml.j2`,
  `frr.conf.j2` (role-conditional: P/PE/CE), `daemons.j2`, `90-mpls.conf.j2`
  (P/PE only), `snmpd.conf.j2`, `qos.sh.j2` (CE only), `wg0.conf.j2` (CE only),
  `vtysh.conf.j2`.
- `.wg-keys.json` — persisted WireGuard keypair cache, keyed by node name, so
  regeneration doesn't re-key live tunnels (`generator/generate.py:102-111`).

### `topology/` (generated output, not source)

- `clab.yml` — containerlab topology definition.
- `configs/<node>/{frr.conf,daemons,snmpd.conf,vtysh.conf}` — every node.
  `90-mpls.conf` on P/PE only; `qos.sh`, `wg0.conf` on CE only.
- `topology-meta.json` — POP/area/SRLG/ABR/inter-POP-link map, written by
  `generator/generate.py:684`. Consumed by `faults/orchestrator.py` and
  `dataapi/` (not owned here) and by `synthetic/generate.py`'s
  `_paths_meta()` (`synthetic/generate.py:964`).
  `telemetry/device_map.txt` — pre_tag_map for nfacctd (mgmt IP → device label).

### `synthetic/`

- `calibrate.py` — real Parquet → `profile.json`. `build_profile(real_path)`
  (`synthetic/calibrate.py:232`) assembles the whole profile;
  `_octet_rate` (`:38`), `_tunnel_baseline` (`:67`), `_fault_signatures` (`:137`),
  `_device_health` (`:182`), `_inventory` (`:217`) are the per-block derivers.
  Each measured statistic falls back to a hardcoded default (and is marked
  `"_src":"default"`) when the real sample has too few rows.
- `profile.json` — the committed calibration output (small, checked into git).
  See Config & schemas below for its full field set.
- `generate.py` — profile.json → large labeled synthetic Parquet(s).
  `_gen_interfaces` (`:142`), `_gen_devices` (`:241`), `_gen_tunnels` (`:308`)
  emit the three entity-type row kinds; `_inject_faults` (`:374`) overlays
  labeled fault episodes (vectorized numpy, not a per-row loop) via `_place`
  (`:585`, one episode) and `_inject_hard_negatives` (`:720`, six near-miss
  mechanisms); `_build_run` (`:907`) builds one (topology, stream, seed) slice;
  `generate()` (`:951`) is the single-topology legacy entry point;
  `generate_multi()` (`:984`) fans out N topologies × {F,N} streams into one
  combined main table + events/edges/paths; `generate_full()` (`:1061`) is the
  memory-safe streamed-write full-scale run (one `pq.ParquetWriter` block at a
  time, per-seed tranche + manifest).
- `topologies.py` — pure-Python topology-inventory synthesizer (no docker/
  containerlab). `_simulate(knobs)` (`:35`) replays `generator/generate.py`'s
  link-enumeration order so `eth<N>` naming matches exactly;
  `build_inventory(knobs)` (`:163`), `base_inventory()` (`:168`, loads the
  current lab's inventory verbatim from `profile.json`), `load_topologies()`
  (`:247`, the 12 fixed variants), `container_estimate(knobs)` (`:175`).
- `topology_paths.py` — G3: `topology_edges.parquet` (static graph with interval
  validity) + `paths.parquet` (ordered hop sequences). `build_edges` (`:177`),
  `build_paths` (`:273`, derives WG hub failover + OSPF SPF paths from the fault
  ledger — the synthetic path never runs a live controller), `_dijkstra` (`:237`,
  hand-rolled heapq SPF), `write_topology_and_paths` (`:375`, the writer).
- `events.py` — G2: discrete, exactly-timestamped (sub-bucket) control-plane
  events from the same fault ledger. `events_for_ledger(ledger, rng)` (`:137`),
  `write_events` (`:169`). `TEMPLATES` (`:43`) is the Drain/Spell-style template
  registry (16 templates); `_spec(ft, kind, hard)` (`:72`) maps a fault
  type/kind to its discrete event sequence.
- `discriminator.py` — G10 realism-gap gate. Trains `HistGradientBoostingClassifier`
  to separate real vs synthetic rows on 28 numeric + 2 categorical columns;
  reports 5-fold AUC + top-5 permutation importances. Not a build-time gate
  (run manually).
- `check.py` — 9-gate assert-based build gate on one synthetic Parquet (schema,
  fault fraction band, precursor existence, metric-distribution match to
  `profile.json`, real+synthetic dtype/concat compatibility, device-health
  signal, hard-negative purity, tunnel-ramp precursor signal, lead-time
  variance).
- `verify_fixes.py` — 24-check acceptance gate supplied by an external audit
  (train vs holdout Parquet), run manually, not part of `check.py`.

### `trafficgen/`

- `trafficgen.py` — `build_plan(now, model, fault_scale)` (`:81`) computes a
  per-(site,VRF) flow plan for one instant; three backends: `run_nc` (`:332`,
  default — real BusyBox `nc` flows via `docker exec`), `run_sim` (`:190`,
  loopback socket simulator), `iperf3_commands` (`:224`, dry — prints commands,
  doesn't run them; host image has no iperf3). `VRF_FLOW` (`:74`) is the
  per-VRF flow-shape table.
- `diurnal.py` — shared 24h utilization curve, imported by both `trafficgen.py`
  and (per its docstring) the live controller, so telemetry and offered load
  move together. `base_curve(hour)` (`:52`), `util(hour, vrf)` (`:67`, applies
  `VRF_PROFILE`), `hour_of_cycle` (`:75`), `week_scale` (`:81`, weekday/weekend
  envelope).
- `Dockerfile` — builds `noc-trafficgen:0.1`; copies the `docker` CLI binary
  from `docker:cli` (no dockerd, no apt layer) so it can shell `docker exec`
  against the lab from inside its own container.

### `ragcorpus/`

- `check_corpus.py` — `scenarios_named_in_runbooks()` (`:25`) greps every
  `runbook-*.md` for backtick-wrapped snake_case tokens and intersects with
  `faults.orchestrator.SCENARIOS` (external, not owned here); asserts none of
  the 21 scenarios go unmentioned.
- `incident-*.md` (7 files) — filled incident-report seeds, one fault scenario
  each: `asymmetric-loss`, `gray-failure`, `mpls-underlay-failure`,
  `p-node-failure`, `policy-drift`, `rr-failure`, `tunnel-degrade`.
- `incident-template.md` — blank template for new incident reports (fields:
  Incident ID, Detected UTC, Device(s), Site type/VRF, Entity, Severity,
  Fault type, Timeline).
- `runbook-*.md` (6 files) — symptom → telemetry-signature → response runbooks,
  each tying to a set of fault scenario names: `bgp-adjacency-down`
  (`bgp_flap`, `node_failure`), `mpls-ldp` (`mpls_underlay_failure`,
  `ldp_session_flap`), `ospf-core` (8 core-fault scenarios), `policy-drift`
  (`policy_drift`, `controller_drift`), `rr-bgp-cascade` (`rr_failure`,
  `bgp_cascade`), `tunnel-latency-high` (5 SD-WAN degrade scenarios).
- `topology-map.md` — a human-readable topology summary (POPs/layers/VRFs),
  derived from `generator/generate.py` / `topology-spec.yaml`.

## Parameters

| name | default | env-var/CLI-flag | units | what it controls | source (file:line) |
|---|---|---|---|---|---|
| `--days` | `2.0` | `generate.py --days` | days | telemetry duration to emit | `synthetic/generate.py:1131` |
| `--step` | `30` | `generate.py --step` | seconds | bucket size (must match `dataapi/export.py`) | `synthetic/generate.py:1132` |
| `--scale` | `1.0` | `generate.py --scale` | multiplier | fault-episode density (not row count) | `synthetic/generate.py:1133` |
| `--seed` | `42` | `generate.py --seed` | int | RNG seed for the whole run | `synthetic/generate.py:1136` |
| `--topologies` | `0` | `generate.py --topologies` | count | 0=single-topology legacy file; N>=1 = multi-topology combined run | `synthetic/generate.py:1139` |
| `--hard-neg` | `200` | `generate.py --hard-neg` | count | hard negatives per topology's Stream N | `synthetic/generate.py:1141` |
| `--seeds` | `"42,43"` | `generate.py --seeds` (with `--full`) | comma list | per-tranche seeds (train, holdout) | `synthetic/generate.py:1146` |
| `n_topologies` (full run) | `12` | `generate_full(..., n_topologies=12)` | count | all topology variants used | `synthetic/generate.py:1062` |
| `STEP` (calibrate) | `30` | module const | seconds | canonical export bucket size assumed by the real capture | `synthetic/calibrate.py:29` |
| `DIURNAL_PERIOD` (calibrate) | `3600` | env var | seconds | threshold for "capture spans a full cycle" before trusting a measured lead hint | `synthetic/calibrate.py:133` |
| rate defaults (octet, bytes/step) | branch 1.2e6, hub 5e6, dc 3e6, pe 2e7, core 1e7 | code const | bytes/step | fallback `rate_in_median` when <3 positive real diffs exist | `synthetic/calibrate.py:54` |
| `rate_out` default fraction | `0.6` | code const | ratio | fallback `rate_out_median` = rate_in default × 0.6 | `synthetic/calibrate.py:60` |
| min sample size for real stat | `3` (octet rate), `5` (tunnel baseline, device health) | code const | row count | below this, fall back to defaults | `synthetic/calibrate.py:53,85,210` |
| tunnel baseline defaults | latency (20.0,7.0)ms, jitter (2.0,0.7)ms, loss (0.05,0.1)%, rekeys (7.0,2.0) | code const (mean,std) | mixed | global fallback if <5 real tunnel rows | `synthetic/calibrate.py:76-80` |
| per-site tunnel defaults | branch lat(33,21) jit(2.5,0.7) loss(0.33,0.57); dc lat(26,8) jit(2.5,0.73) loss(0.29,0.53) | code const | mixed | per-site-type fallback baseline | `synthetic/calibrate.py:97-101` |
| device-health defaults | err_rate 0.004/step, discard_rate 0.02/step, q_backlog 900B, q_drops 0.01/step, cpu 4.0%, mem 2.5%, bgp_msg 6.0/step, rib 120 routes, lsa 90 | code const | mixed | fallback when capture predates the device-health feature set | `synthetic/calibrate.py:194-203` |
| `_diurnal` amplitude/floor | floor `0.15`, gain `0.85` | code const | ratio | 0..1 business-hours load multiplier (peak ~14:00 UTC, trough ~03:00) | `synthetic/generate.py:82` |
| `_diurnal` weekend multiplier | `0.7` (weekend) / `1.0` (weekday) | code const | ratio | weekend traffic dip | `synthetic/generate.py:81` |
| `_pop_of` grouping | 2 PE/POP, 4 P/POP | code const | count | ambient-temperature POP assignment fallback (mirrors `envmodel.py`) | `synthetic/generate.py:86-99` |
| interface `scale` (low-traffic entities) | `0.05` | code const | ratio | traffic scale-down for `lo`/`wg0`/`vrf_*`/uppercase entities | `synthetic/generate.py:177-178` |
| jitter range (octet growth) | `uniform(0.8, 1.2)` | code const | ratio | per-bucket multiplicative octet-rate noise | `synthetic/generate.py:190,192` |
| flow tick period | `360.0` s | code const | seconds | assumed real-world duration of one `trafficgen` tick, for `_flow_row` scaling | `synthetic/generate.py:124` |
| bytes/packet (flow_packets) | `1400.0` B | code const | bytes | wire-size divisor to derive `flow_packets` from `flow_bytes` | `synthetic/generate.py:132` |
| device CPU load coupling | `cpu_m * 1.6` | code const | ratio | diurnal contribution to `cpu_pct` | `synthetic/generate.py:280` |
| device mem load coupling | `0.4` | code const | ratio | diurnal contribution to `mem_pct` | `synthetic/generate.py:281` |
| tunnel latency diurnal bump | `8.0` ms | code const | ms | added to tunnel latency at full diurnal load | `synthetic/generate.py:330` |
| tunnel jitter diurnal bump | `0.5` ms | code const | ms | added to tunnel jitter at full diurnal load | `synthetic/generate.py:331` |
| tunnel loss diurnal bump | `0.02` pct-pts | code const | % | added to tunnel loss at full diurnal load | `synthetic/generate.py:332` |
| spontaneous rekey probability | `0.002` /bucket | code const | probability | chance of an extra tunnel rekey per bucket | `synthetic/generate.py:333` |
| `n_ep` fault-episode count | `max(4, int(scale*len(inv)*span/3600/8))` | derived | count | number of Stream-F campaign episodes per run | `synthetic/generate.py:395` |
| cascade probability | `0.20` | code const | probability | fraction of episodes that seed a 2-3 hop cascade | `synthetic/generate.py:829` |
| cascade depth range | `randint(2,4)` exclusive-high → 2 or 3 hops | code const | hop count | cascade motif depth | `synthetic/generate.py:839` |
| severity draw weights | low 0.3, medium 0.4, high 0.3 | code const | probability | severity sampling per episode | `synthetic/generate.py:822` |
| severity multiplier | low 0.5, medium 0.8, high 1.0 | code const | ratio | `sevmul` scaling the ramp peak | `synthetic/generate.py:603` |
| episode duration draw | `uniform(60, 240)` s | code const | seconds | `dur_impact` for a root/cascade episode | `synthetic/generate.py:821,858` |
| iface churn severity | up to `0.4` (`p*0.4`) | code const | fraction | extra bytes/bucket added during churn | `synthetic/generate.py:503` |
| congestion queue fill | `900.0` bytes peak | code const | bytes | `q_backlog_bytes` additive fault contribution | `synthetic/generate.py:566` |
| congestion queue drops | `40.0` peak | code const | count | `q_drops` additive fault contribution | `synthetic/generate.py:567` |
| congestion discards | `25.0` peak | code const | count | `if_out_discards` additive fault contribution (CONGEST) | `synthetic/generate.py:570` |
| churn discards | `4.0` peak | code const | count | `if_out_discards` additive fault contribution (CHURN) | `synthetic/generate.py:572` |
| gray-failure rx sag | `7.0` dBm peak | code const | dBm | `xcvr_rx_power_dbm` decrease | `synthetic/generate.py:579` |
| gray-failure bias rise | `5.0` mA peak | code const | mA | `xcvr_tx_bias_ma` increase | `synthetic/generate.py:580` |
| gray-failure queue drops | `12.0` peak | code const | count | `q_drops` additive contribution | `synthetic/generate.py:583` |
| power draw under fault | `+5%` (heat≥0) / `-4%` (heat<0) | code const | ratio | `device_power_watts` fault multiplier | `synthetic/generate.py:555` |
| CPU under churn fault | `×(1 + p*3.0)` | code const | ratio | `cpu_pct` fault multiplier for CHURN_FAULTS | `synthetic/generate.py:557` |
| tunnel-ramp peak floor | `1.15×` healthy mean | code const | ratio | floor so a ramp can't decrease below "healthy" | `synthetic/generate.py:526,634` |
| hard-negative target multiplier | `hard_neg_target` (arg), retry guard `×6` | code const | count | max draw attempts before giving up | `synthetic/generate.py:731` |
| hard-neg window duration | `uniform(2,8) * step` | code const | seconds | duration of a near-miss perturbation window | `synthetic/generate.py:736` |
| `congestion_recedes` peak | `0.7 ×` SLA headroom | code const | ratio | near-miss latency stays under SLA | `synthetic/generate.py:751` |
| `self_healing_flap` hold | first 2 buckets only | code const | buckets | flap held below OSPF dead-interval | `synthetic/generate.py:764` |
| `legit_surge` peak | `0.5 ×` triangular | code const | ratio | non-fault octet surge magnitude | `synthetic/generate.py:779` |
| `maintenance_drain` CPU bump | `×1.15` | code const | ratio | hard-negative CPU perturbation | `synthetic/generate.py:786` |
| lead-floor warn threshold | `5%` of episodes | code const | ratio | warns if `leadpriors.FLOOR_BUCKETS` fires too often | `synthetic/generate.py:897` |
| `schema_version` | `"59col-frozen-v1"` | code const | string | manifest/Parquet-metadata schema tag | `synthetic/generate.py:1050,1105` |
| VRF ordering | `CORP=0, VOICE=1, GUEST=2` | code const | index | shared VRF sort order (generator + topologies.py) | `synthetic/topologies.py:28`, `generator/generate.py:34` |
| topology self-check size span | min ≤45, max ≥480 | code const | container count | asserts the 12 topology variants span this size range | `synthetic/topologies.py:290` |
| held-out topology count | `2` of 12 | code const | count | fixed by `_ROWS` `held_out` flags | `synthetic/topologies.py:213-226` |
| `check.py` fault-fraction band | `0.0005 < frac < 0.25` | code const | ratio | sanity band on `is_fault` mean | `synthetic/check.py:73` |
| `check.py` lead CV floor | `>= 0.50` | code const | ratio | lead-time coefficient-of-variation regression gate | `synthetic/check.py:258` |
| `check.py` distinct-lead floor | `>= 0.9 * n_episodes` | code const | ratio | asserts leads aren't colliding/constant | `synthetic/check.py:263` |
| `check.py` power role-scale floor | `core > 10 * branch` mean watts | code const | ratio | asserts chassis power is role-scaled | `synthetic/check.py:133` |
| `check.py` temp std floor | `> 0.5` °C | code const | std dev | asserts `device_temp_c` isn't constant | `synthetic/check.py:138` |
| `check.py` physical temp range | `5.0 < t < 80.0` °C | code const | °C | plausibility bound | `synthetic/check.py:139` |
| `check.py` POP temp spread floor | `> 0.5` °C | code const | °C | asserts spatial correlation across POPs | `synthetic/check.py:147` |
| discriminator balanced seed | `SEED=30` | code const | int | sklearn split/CV seed | `synthetic/discriminator.py:36` |
| p_per_pop floor | `>= 3` | assertion | count | 2 ABR + ≥1 PE-facing P per POP | `synthetic/topologies.py:50`, `generator/generate.py:134` |
| `p_count` | `24` | `topology-spec.yaml: knobs.p_count` | count | total P routers | `topology-spec.yaml:14` |
| `pe_count` | `12` | `topology-spec.yaml: knobs.pe_count` | count | total PE routers | `topology-spec.yaml:15` |
| `pop_count` | `6` | `topology-spec.yaml: knobs.pop_count` | count | number of POPs | `topology-spec.yaml:16` |
| `p_per_pop` | `4` | `topology-spec.yaml: knobs.p_per_pop` | count | P routers per POP | `topology-spec.yaml:17` |
| `multi_area` | `true` | `topology-spec.yaml: knobs.multi_area` | bool | area-per-POP OSPF vs single area 0 | `topology-spec.yaml:18` |
| `igp_cost_intra` | `10` | `topology-spec.yaml: knobs.igp_cost_intra` | OSPF cost | intra-POP link cost | `topology-spec.yaml:19` |
| `igp_cost_inter` | `100` | `topology-spec.yaml: knobs.igp_cost_inter` | OSPF cost | inter-POP backbone link cost | `topology-spec.yaml:20` |
| `inter_pop_redundancy` | `2` | `topology-spec.yaml: knobs.inter_pop_redundancy` | count | parallel links per inter-POP adjacency (1 SRLG conduit) | `topology-spec.yaml:21` |
| `inter_pop_chords` | `[[1,4],[2,5],[3,6]]` | `topology-spec.yaml: knobs.inter_pop_chords` | pairs | extra ring adjacencies | `topology-spec.yaml:22` |
| `branch_count` | `24` | `topology-spec.yaml: knobs.branch_count` | count | branch CE nodes | `topology-spec.yaml:25` |
| `hub_count` | `6` | `topology-spec.yaml: knobs.hub_count` | count | hub CE nodes | `topology-spec.yaml:26` |
| `dc_count` | `4` | `topology-spec.yaml: knobs.dc_count` | count | dc CE nodes | `topology-spec.yaml:27` |
| `provider_as` | `65000` | `topology-spec.yaml: knobs.provider_as` | ASN | iBGP AS for all PEs | `topology-spec.yaml:41` |
| `ce_asn_base` | branch 65101, hub 65201, dc 65301 | `topology-spec.yaml: addressing.ce_asn_base` | ASN | per-site-type CE ASN base | `topology-spec.yaml:145-148` |
| `wg_overlay_subnet` | `172.16.0.0/24` | `topology-spec.yaml: addressing.wg_overlay_subnet` | CIDR | WireGuard overlay subnet | `topology-spec.yaml:156` |
| `wg_port` | `51820` | `topology-spec.yaml: addressing.wg_port` | port | WireGuard UDP port | `topology-spec.yaml:157` |
| `default_uplink_rate` | `"1gbit"` | `topology-spec.yaml: qos.default_uplink_rate` | rate | CE HTB root rate | `topology-spec.yaml:190` |
| QoS class shares | VOICE 30%/10% burst, CORP 50%/20%, GUEST 20%/5% | `topology-spec.yaml: qos.classes` | % | HTB bandwidth/burst per VRF | `topology-spec.yaml:176-187` |
| `VRF_TABLE` | CORP=10, VOICE=20, GUEST=30 | code const | routing table id | Linux VRF table numbers | `generator/generate.py:38` |
| `NETEM_FLOOR_MS` | dc 5.0, hub 8.0, branch 18.0 | code const | ms | per-site-type baseline one-way delay floor | `generator/generate.py:56` |
| `NETEM_SPREAD_MS` | dc 12.0, hub 14.0, branch 38.0 | code const | ms | per-site-type delay spread range | `generator/generate.py:57` |
| netem jitter formula const | `0.12 * delay + 0.3` | code const | ms | jitter derived from delay | `generator/generate.py:71` |
| netem loss formula const | `0.02 + frac * 0.4` | code const | % | loss derived from golden-ratio fraction | `generator/generate.py:72` |
| golden-ratio spread constant | `0.6180339887` | code const | — | low-discrepancy per-node delay/loss spread | `generator/generate.py:69` |
| CE-PE /30 overflow guard | `lin_idx*4+2 < 256` | assertion | — | address-space bound on site count | `generator/generate.py:332` |
| MGMT subnet | `172.20.20.0/24`, start `.101` | code const | CIDR | static mgmt IPs | `generator/generate.py:413-414` |
| `VRF_FLOW` (VOICE) | flows_max 60, bytes/flow 18,000, burstiness 0.08, size_cv 0.10 | code const | mixed | codec-like steady flow shape | `trafficgen/trafficgen.py:75` |
| `VRF_FLOW` (CORP) | flows_max 22, bytes/flow 900,000, burstiness 0.65, size_cv 0.60 | code const | mixed | bursty office-TCP flow shape | `trafficgen/trafficgen.py:76` |
| `VRF_FLOW` (GUEST) | flows_max 7, bytes/flow 6,000,000, burstiness 0.90, size_cv 1.10 | code const | mixed | best-effort bulk flow shape | `trafficgen/trafficgen.py:77` |
| `PERIOD_SECONDS` | `3600` | env `DIURNAL_PERIOD` | seconds | 24h cycle compression | `trafficgen/trafficgen.py:52` |
| `TRAFFICGEN_BACKEND` | `"nc"` | env `TRAFFICGEN_BACKEND` | — | default backend | `trafficgen/trafficgen.py:54` |
| `NC_PORT_BASE` | `19000` | env `NC_PORT_BASE` | port | first nc listener port | `trafficgen/trafficgen.py:259` |
| `NC_FLOW_SCALE` | `0.05` | env `NC_FLOW_SCALE` | fraction | share of plan bytes actually sent per tick | `trafficgen/trafficgen.py:260` |
| `--interval` (trafficgen) | `30.0` s | CLI flag | seconds | seconds between ticks | `trafficgen/trafficgen.py:515-516` |
| tick bucket period | `PERIOD_SECONDS/240` | code const | seconds | ~6 modelled minutes per tick, stable RNG realization window | `trafficgen/trafficgen.py:104` |
| `VRF_PROFILE` (VOICE) | floor 0.18, gain 0.62, shift 0.0h | code const | — | steady, modest diurnal swing | `trafficgen/diurnal.py:36` |
| `VRF_PROFILE` (CORP) | floor 0.03, gain 0.97, shift 0.0h | code const | — | bursty, big diurnal swing | `trafficgen/diurnal.py:37` |
| `VRF_PROFILE` (GUEST) | floor 0.03, gain 0.72, shift 2.5h | code const | — | best-effort, evening-leaning | `trafficgen/diurnal.py:38` |
| `WEEKEND_SCALE` | `0.60` | env `DIURNAL_WEEKEND_SCALE` | ratio | weekend multiplier on the base curve | `trafficgen/diurnal.py:44` |
| base-curve bell params | work (13.5h,3.0,×0.92), morning (10.0h,1.6,×0.80), lunch dip (12.5h,0.9,×0.45), night floor 0.10 | code const | hours/ratio | shape of the 24h utilization curve | `trafficgen/diurnal.py:59-63` |

## Data flow

**Topology → deployed lab:**
`topology-spec.yaml` (git-committed knobs) → `generator/generate.py:build()`
(pure computation, no I/O except reading the spec + `.wg-keys.json` cache +
shelling `docker run frr-node:0.1 wg genkey`) → `generator/generate.py:render()`
writes `topology/clab.yml` + `topology/configs/<node>/*` + `topology/topology-meta.json`
+ `topology/telemetry/device_map.txt` → operator runs
`containerlab deploy -t topology/clab.yml` (outside this subsystem) → live
containers.

**Traffic → live lab → real capture:**
`trafficgen/trafficgen.py:build_plan()` reads `controller/topo.py:build_model()`
(external, not owned) for the site/VRF inventory and `diurnal.util()` for load
shape, produces per-(site,VRF) flow plans → `run_nc()` execs `dd`/`nc` inside
running host/CE containers over `docker.sock` → real bytes cross WireGuard
tunnels → SNMP octet counters and IPFIX flows climb on the live lab →
`dataapi/export.py` (external, not owned) captures a real Parquet in the
canonical 59-column schema.

**Real capture → calibration → synthetic corpus:**
`dataapi/datasets/*.parquet` (newest by default, `synthetic/calibrate.py:32`)
→ `calibrate.py:build_profile()` derives per-site-type octet rates, tunnel
baselines (global + per-site-type), per-fault-type peak signatures, device-health
baselines, and the full device inventory → writes `synthetic/profile.json`
(committed, small) → `synthetic/generate.py` reads `profile.json` (+ imports
`faults/leadpriors.py`, `faults/signatures.py`, `telemetry/envmodel.py`,
`trafficgen.VRF_FLOW`, `dataapi/export.py`'s `COLUMNS`/label helpers — all
external, not owned here) → for the demo path, walks `profile.json["inventory"]`
directly; for multi-topology runs, `synthetic/topologies.py:load_topologies()`
synthesizes 12 inventory variants (never touches `profile.json["inventory"]`'s
consumers beyond shape) → emits interface/tunnel/device rows, injects labeled
fault episodes + hard negatives, finalizes the schema via `dataapi/export.py`'s
`finalize_schema` → writes the main Parquet(s) with `synthetic=true` provenance
metadata → `synthetic/events.py` and `synthetic/topology_paths.py` consume the
same in-memory fault ledger to emit companion `events.parquet` /
`topology_edges.parquet` / `paths.parquet` → `synthetic/check.py` and
`synthetic/verify_fixes.py` gate the output before it's treated as usable.

**RAG corpus:**
`ragcorpus/*.md` are static hand-authored text, keyed by `faults/orchestrator.py`
scenario names (external). `ragcorpus/check_corpus.py` is the only code path:
reads `runbook-*.md`, extracts backtick tokens, diffs against
`faults.orchestrator.SCENARIOS`. No generation step — this corpus is authored,
not derived.

## Calculations

**Diurnal load multiplier** (`synthetic/generate.py:_diurnal`, `:71-82`):
```
h = hour + minute/60          (UTC)
day = 0.5 - 0.5*cos((h-3)/24 * 2*pi)     # 0 at 03:00, 1 at 15:00
weekend = 0.7 if weekday>=5 else 1.0
diurnal = 0.15 + 0.85 * day * weekend
```
Feeds octet-rate scaling (`generate.py:191-192`) and tunnel latency/jitter/loss
diurnal bumps (`:330-332`).

**POP index of a device** (`synthetic/generate.py:_pop_of`, `:85-99`):
```
pe<i>  -> pop = (i-1)//2 + 1     (2 PE per POP)
p<i>   -> pop = (i-1)//4 + 1     (4 P per POP)
else   -> pop = 1
```
Drives the shared per-POP ambient temperature via `envmodel.pop_ambient_c`
(external, `telemetry/envmodel.py`).

**Flow bytes/packets on device rows** (`synthetic/generate.py:_flow_row`,
`:113-133`):
```
period = 360.0 s                      # one trafficgen tick ~ 6 modelled minutes
ticks  = step / period
for each VRF v at this site:
  noise = max(0, 1 + N(0, VRF_FLOW[v].burstiness))
  flows = VRF_FLOW[v].flows_max * diurnal * noise
  total += flows * VRF_FLOW[v].bytes_per_flow * ticks
flow_bytes   = round(total, 1)
flow_packets = round(total / 1400.0, 1)      # ~1400 B/pkt on the wire
```

**Fault-episode count per run** (`synthetic/generate.py:395`):
```
n_ep = max(4, int(scale * len(inventory) * span_seconds / 3600 / 8))
```
`scale` = `--scale` CLI flag, `span` = capture duration in seconds. Zero on
Stream N (hard negatives only, `:817`).

**Ramp progress `_prog`** (`synthetic/generate.py:444-469`, delegates the math
to `faults/signatures.prog`, external): piecewise-linear over 4 knots —
`t_start`→0 (healthy), `t_impact`→`p_cross` (SLA crossing), `t_impact+0.3*dur`→1
(calibrated peak), `t_end`→0 (recovered). `p_cross` defaults to `1.0` (crossing
never happens inside the ramp; ordinary ramp-then-decay) unless the episode is
a `tunnel_ramp` kind, in which case:
```
theta_lat, theta_loss = leadpriors.strictest_sla(site_vrfs)   # external
peak_lat = max(sig.lat_peak, healthy_lat_mean * 1.15)
p_lat  = (theta_lat  - healthy_lat_mean)  / ((peak_lat - healthy_lat_mean) * sevmul)   if peak_lat  > healthy_lat_mean  else inf
p_loss = (theta_loss - 0) / (sig.loss_peak * sevmul)                                   if sig.loss_peak > 0            else inf
p_cross = min(p_lat, p_loss)     # only used if 0 < p_cross <= 1.0
```
(`synthetic/generate.py:630-643`) — the SLA-crossing fraction is derived, not
drawn; `t_impact` therefore lands exactly on the drawn `lead_time_s`
(`faults/leadpriors.draw_lead_s`, external) by construction.

**Cumulative counter adjustment** (`synthetic/generate.py:_counter_adjust`,
`:471-494`): adds `adj * per-bucket-increment` to a cumulative counter,
integrated forward so the extra/missing bytes persist after the fault window
ends (never steps the counter backward):
```
inc[t]   = counter[t] - counter[t-1]     (0 at each entity's first row)
extra[t] = cumsum(inc[t] * adj[t])
counter'[t] = counter[t] + extra[t] - extra[last_row_of_entity]
```

**Row count for one run** (`synthetic/generate.py:_build_run`, `:920-923`,
verified by direct computation against the current 70-device `profile.json`):
```
entities_per_tick = n_interfaces + n_tunnels + n_devices   # = 661+168+70 = 899
n_buckets = int(days * 86400 / step)
rows = entities_per_tick * n_buckets
```
E.g. `--days 2 --step 30` → 899 × 5,760 = **5,178,240 rows** (recomputed
directly from `synthetic/profile.json`, not copied from a doc).

**Site-geography WAN netem** (`generator/generate.py:site_netem`, `:60-73`):
```
frac  = (idx * 0.6180339887) % 1.0                # golden-ratio low-discrepancy
delay = NETEM_FLOOR_MS[site_type] + frac * NETEM_SPREAD_MS[site_type]
jitter = 0.12 * delay + 0.3
loss   = 0.02 + frac * 0.4
```
Ranges (from the floor/spread table): dc 5.0–17.0 ms, hub 8.0–22.0 ms,
branch 18.0–56.0 ms delay; jitter 0.9–7.0 ms; loss 0.02–0.42%. Applied as one
`tc qdisc replace dev eth0 root netem` per CE (`generator/generate.py:558-561`),
delaying both WireGuard tunnels and NOC telemetry on the same veth.

**QoS class rate** (`generator/generate.py:_pct_rate`, `:691-697`):
```
kbit = int(digits(rate_str)) * unit_mult[gbit=1e6,mbit=1e3,kbit=1] * pct // 100
```
`rate` = `_pct_rate(root_rate, bandwidth_pct)`, `ceil` = `_pct_rate(root_rate,
bandwidth_pct + burst_pct)`, both per-VRF HTB classes (`:610-616`).

**Topology-variant container estimate** (`synthetic/topologies.py:container_estimate`,
`:175-183`):
```
p     = pop_count * p_per_pop
hosts = branch_count*len(vrf_sites.branch) + hub_count*len(vrf_sites.hub) + dc_count*len(vrf_sites.dc)
total = p + pe_count + branch_count + hub_count + dc_count + hosts
```

**Diurnal base curve** (`trafficgen/diurnal.py:base_curve`, `:52-64`):
```
bell(h,c,w) = exp(-(h-c)^2 / (2*w^2))
work    = bell(h, 13.5, 3.0) * 0.92
morning = bell(h, 10.0, 1.6) * 0.80
lunch   = bell(h, 12.5, 0.9) * 0.45
val = clamp(0.10 + max(work,morning) - lunch, 0, 1)
```

**Per-VRF utilization** (`trafficgen/diurnal.py:util`, `:67-72`):
```
floor, gain, shift = VRF_PROFILE[vrf]
util_vrf = clamp(floor + gain * base_curve(hour + shift), 0, 1)
```

**Weekly envelope** (`trafficgen/diurnal.py:week_scale`, `:81-110`): a
half-cosine ease between `1.0` (weekday) and `WEEKEND_SCALE=0.60` over the
Fri→Sat and Sun→Mon boundary days, otherwise a step function of `day % 7`.

**Traffic plan per site/VRF** (`trafficgen/trafficgen.py:build_plan`, `:81-136`):
```
u       = diurnal.util(hod, vrf) * week_scale
noise   = max(0, 1 + N(0, VRF_FLOW[vrf].burstiness))          # seeded per (site,vrf,tick_bucket)
flows   = round(VRF_FLOW[vrf].flows_max * u * fault_mult * noise)
size_f  = max(0.15, lognormal(0, VRF_FLOW[vrf].size_cv))
bytes_per_flow = max(1024, int(nominal_bytes * size_f))
offered_bps    = flows * bytes_per_flow * 8 / max(1, PERIOD_SECONDS/24)
```
`tick_bucket = int(now // (PERIOD_SECONDS/240))` — stable RNG realization
within one ~6-modelled-minute tick, reseeded via `blake2b(site|vrf|tick_bucket)`
(not Python's `hash()`, which is per-process randomized — `trafficgen.py:113-115`).

**nc flow byte volume** (`trafficgen/trafficgen.py:run_nc`, `:382`):
```
nbytes = max(1024, int(bytes_per_flow * flows * NC_FLOW_SCALE))
```

## Config & schemas

### `topology-spec.yaml` (repo root)

Read partially by `generator/generate.py`: the entire `knobs:` block is read
(`yaml.safe_load`, `generator/generate.py:832-833`); under `addressing:` only
`ce_asn_base`, `wg_overlay_subnet`, `wg_port` are read
(`generator/generate.py:301,435,436`) — every other field under `addressing`,
`underlay`, `overlay`, `sdwan`, `telemetry`, `site topology map` is reference
documentation only, not consumed. `vrfs:` and `qos:` blocks ARE fully read
(`generator/generate.py:304,392,605-616`).

### `topology/topology-meta.json`

Written by `generator/generate.py:684` from the `topo_meta` dict built in
`build()` (`:258-276`). Live file has these top-level keys (verified against
the current deployed lab):

| key | type | meaning |
|---|---|---|
| `pop_count` | int | number of POPs (currently 6) |
| `p_per_pop` | int | P routers per POP (currently 4) |
| `multi_area` | bool | whether multi-area OSPF is active |
| `pops` | object | `{"pop1": ["p1","p2","p3","p4"], ...}` — per-POP P-node lists |
| `abrs` | list | ABR node names (first 2 P of each POP; 12 total in the live lab) |
| `pe_pop` | object | `{"pe1": 1, "pe2": 1, ...}` — PE → POP index |
| `p_core_ifaces` | object | per-P-node list of all core-facing interface names (used by `p_node_failure` fault) |
| `srlgs` | object | `{"srlg_pop1_2": [["p1","eth4"],["p5","eth4"],...], ...}` — 9 groups in the live lab (one per inter-POP adjacency) |
| `inter_pop_links` | list | `{pop_a, pop_b, kind: "ring"/"chord", srlg, links: [[dev,iface],...]}`, 9 entries in the live lab |
| `pop_inter_links` | object | per-POP list of links crossing into/out of that POP (used by `pop_isolation` fault) |

Consumed by `faults/orchestrator.py` (external) and by
`synthetic/generate.py:_paths_meta()` (`:964-981`), which reshapes it into the
form `topology_paths.build_edges`/`build_paths` want (`pops` as dict-of-lists,
`srlgs` renamed/renumbered per synthetic topology, `route_reflectors` pulled
from `knobs.rr_nodes`).

### `synthetic/profile.json`

Written by `synthetic/calibrate.py:main()`. Top-level keys (verified by
loading the live file):

| key | type | meaning |
|---|---|---|
| `source_parquet` | str | basename of the real capture calibrated from |
| `source_rows` | int | row count of that capture |
| `step_s` | int | bucket size assumed (30) |
| `octet_rate_by_site` | object | per-`site_type`: `rate_in_median`, `rate_out_median` (bytes/step) + per-field `_src_rate_in`/`_src_rate_out` ("real"/"default") |
| `tunnel_baseline` | object | global `tunnel_latency_ms`/`_jitter_ms`/`_loss_pct`/`_rekeys`, each `{mean,std,p50,min,max,_src}` |
| `tunnel_baseline_by_site` | object | same 4 fields, broken out per `site_type` (only branch/dc have real tunnel-bearing rows) |
| `fault_signatures` | object | per `fault_type`: `lat_peak`,`loss_peak`,`jit_peak`,`lead_s` (unused — see Gotchas), `kind`, `_src_peaks`, `_src_lead`, optional `lead_s_hint` |
| `device_health` | object | `err_rate_per_step`,`discard_rate_per_step`,`q_backlog_bytes`,`q_drops_per_step`,`cpu_pct`,`mem_pct`,`bgp_msg_per_step`,`rib_routes`,`ospf_lsa_count`, each `{mean,std,_src}` |
| `real_fault_fraction` | float | fraction of real capture rows with `is_fault=True` |
| `inventory` | object | `{device: {site_type, interfaces:[...], tunnels:[...]}}` — 70 devices, 661 interfaces, 168 tunnels in the live profile |
| `octet_seed_by_site` | object | per-`site_type` median absolute `if_in_octets`, used to seed synthetic counters so ranges overlap the real capture |

Consumed by `synthetic/generate.py` (the whole generation pipeline),
`synthetic/topologies.py:base_inventory()` (loads `inventory` verbatim), and
`synthetic/check.py` (compares generated distributions back against it).

### Synthetic main Parquet schema

Schema is `dataapi/export.COLUMNS` (external, not owned here) — 59 columns,
imported directly by `synthetic/generate.py:44-46` so real and synthetic can
never diverge in column set/order. Row key: `(stream, topology_id, device,
entity, ts)` per the multi-topology closing-pass additions. `synthetic/generate.py`
writes file-level Parquet key/value metadata:
`synthetic=b"true"`, `generator=b"synthetic/generate.py"`, `seed`,
`calibrated_from` (`synthetic/generate.py:934-948`); multi-topology runs add
`topologies`, `held_out` (`:1018-1021`); full runs additionally pin
`schema_version=b"59col-frozen-v1"` (`:1105`).

### `synthetic/output/<tag>/<tag>_seed<N>/manifest.json`

Written by `synthetic/generate.py:_write_manifest` (`:1041-1058`), one per
full-scale tranche:

| field | meaning |
|---|---|
| `seed`, `days`, `scale` | run parameters |
| `schema_version` | `"59col-frozen-v1"` |
| `generator_commit` | `git rev-parse HEAD` at generation time (`"unknown"` if not a git checkout) |
| `main_rows` | total row count of `main.parquet` |
| `files` | per companion file (`main`,`events`,`edges`,`paths`): `{path, sha256, rows}` |

### `events.parquet` schema

`pyarrow` schema defined at `synthetic/events.py:27-38`: `event_id` (str),
`ts` (timestamp us, UTC, exact/sub-bucket), `device`, `entity`, `event_type`,
`severity`, `template_id`, `params` (JSON string), `scenario_id`,
`topology_id`. `TEMPLATES` (`:43-60`) has 16 entries covering BGP/OSPF/LDP/
kernel-link/WireGuard/process-restart/route-withdraw events.

### `topology_edges.parquet` / `paths.parquet` schema

`topology_edges.parquet` columns (`synthetic/topology_paths.py:_edges_frame`,
`:208-220`): `edge_id`, `topology_id`, `src_entity`, `dst_entity`, `relation`
(`belongs_to`/`incident_to`/`ospf_adj`/`ldp_session`/`ibgp_session`/
`wg_tunnel`/`shares_srlg`), `valid_from`, `valid_to` (interval-encoded — a link
flap emits two rows: one ending at `t_impact`, one re-starting at `t_end` with
`valid_to=NULL`), `igp_cost` (10 intra-POP / 100 inter-POP / NULL for non-IGP
relations), `srlg_group`, `area_id`.

`paths.parquet` columns (`synthetic/topology_paths.py:_paths_frame`,
`:361-369`): `path_id` (stable hash per `(topology_id, ce, vrf)` so a
tunnel's whole failover history groups under one id), `topology_id`,
`hop_sequence` (ordered list, ≥2 hops), `path_type` (`wg_tunnel` or
`ospf_spf_path` only — no `ldp_lsp`, no MPLS dataplane on this host), `vrf`,
`sla_class` (`VRF_SLA` map: CORP→business, VOICE→strict, GUEST→best-effort,
`synthetic/topology_paths.py:24`), `valid_from`, `valid_to` (interval-encoded
path SELECTION history — a `tunnel_ramp`/failover-class fault reroutes the WG
path onto an alternate hub for the fault window).

### `DATASETS.md`

Not generator output — a hand-maintained reference doc (owned path) recording
the three git-committed sample Parquets (one real capture, two synthetic
train/holdout), their row/column breakdowns, the `impact_method` mix, and the
10 closing-pass schema columns. Regenerate/validate commands are listed at the
bottom (`DATASETS.md:132-150`).

## Gotchas

- **`fault_signatures[ft].lead_s` in `profile.json` is dead data.** `generate.py`
  never reads it — leads are drawn from `faults/leadpriors.py` (external
  module), shared with the live orchestrator so synthetic and live agree
  (`synthetic/generate.py:604-608`, `synthetic/calibrate.py:140-178`). A
  24.5-minute real capture at 30s resolution gives a ~2s median lead, below one
  bucket; the old 4-bucket floor then clamped every episode to a constant
  120s. `calibrate.py` only writes a `lead_s_hint` (never overwrites `lead_s`)
  and only when the capture spans a full `DIURNAL_PERIOD`.
- **`if_in_errors` / `if_in_discards` / `if_out_errors` are hardcoded to 0**
  (`synthetic/generate.py:224-225`) because veth pairs in this container lab
  raise no CRC/input errors — a load-dependent Poisson generator here used to
  make `if_in_errors > 0` a perfect synthetic-row detector for
  `discriminator.py`. `check.py:266-268` asserts they stay zero; they will
  populate on real hardware and must be re-enabled at deployment.
  `if_out_discards`/`q_drops`/`q_backlog_bytes` are real dynamics (measured via
  `tc -s qdisc`) and DO get perturbed.
- **`q_backlog_bytes` is sparse-NULL by design** (`synthetic/generate.py:212-213`):
  a standing per-row occupancy used to be a perfect synthetic-row tell (AUC
  0.9999 on `discriminator.py`). It now fires on ~1% of high-load interface
  rows, matching real `tc -s qdisc` sampling; `check.py`'s "louder under fault"
  gate `fillna(0)`s it before comparing (`synthetic/check.py:178-179`) or every
  healthy-entity mean would be `NaN`.
- **`base_inventory()` and `topologies.py`'s synthesized inventories disagree
  on `wg0` presence on purpose** (`synthetic/topologies.py:143-146`):
  `base_inventory()` loads `profile.json["inventory"]` verbatim (the real
  captured, possibly-flaky wg0 set); synthesized topologies always add `wg0`
  to every overlay CE (the clean structural truth). Don't assume the two
  inventory sources are byte-identical in shape even for the same knobs.
- **`topologies.py` never recomputes addresses/keys** — only interface/tunnel
  SETS and `site_type`, because that's all `calibrate.py` keys on
  (`synthetic/topologies.py:15-19`). It replays `generator/generate.py`'s
  link-enumeration order with the same per-node `eth<N>` counter so names
  match, but it does not validate the full addressing math that
  `generator/generate.py:check()` does.
- **Cascade adjacency is tunnel-only** (`synthetic/generate.py:_adjacency`,
  `:344-363`): P/PE nodes have no tunnels, so they can never seed or receive a
  multi-hop cascade even though they're graph-adjacent in `topology-meta.json`.
  Fine today (most cascade targets are CEs) but a P/PE-core cascade scenario
  needs new code, not a knob flip.
- **`generate_full()` writer schema is captured from the FIRST (topology,
  Stream-F) block, not Stream-N** (`synthetic/generate.py:1089-1107`) —
  deliberately, so the writer's label columns are real `list<string>` from a
  table that actually has faults; Stream N's all-null label columns then cast
  cleanly onto that schema. Reordering streams would risk casting the wrong
  direction.
- **`generator/generate.py --check` self-test only validates the MODEL before
  render, and files-on-disk after** — running `--check` without ever calling
  `render()` (not possible via the CLI, but true of the function split) would
  give a false pass on file presence.
- **WireGuard keys are cached in `generator/.wg-keys.json` and reused across
  regenerations** (`generator/generate.py:94-99`) — deleting a node from
  `topology-spec.yaml` and re-adding it under the same name reuses its old
  keypair; there's no key-rotation path in this code.
- **`trafficgen`'s `nc` backend requires the sink host to already be resolvable
  via `docker exec ... ip addr` before the first tick** — `_sink_ip` caches
  successes only (`trafficgen/trafficgen.py:343-356`), specifically because
  caching a `None` used to permanently blackhole a VRF's traffic for the whole
  run if that sink host was still booting on tick 1.
- **`ragcorpus/check_corpus.py` ground truth is `faults/orchestrator.py`
  `SCENARIOS`, not any `README`** (`ragcorpus/check_corpus.py:3-9`) — a runbook
  can name a fault in prose and still fail the check if it's not inside a
  backtick span; the regex only matches `` `snake_case_token` ``.
- **`synthetic/check.py`'s device-health "louder under fault" gate compares a
  per-entity mean DELTA, not a pooled mean** (`synthetic/check.py:157-184`) —
  VRF/lo interfaces draw disproportionately more fault episodes than `eth*`
  interfaces at low baseline, so a pooled fault-row mean can sit BELOW the
  pooled healthy mean even when every entity individually rises (Simpson's
  paradox bait). Don't "simplify" this to a flat `.mean()` comparison.
