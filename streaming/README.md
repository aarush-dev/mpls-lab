# streaming/ — real-time telemetry over Kafka, two independent consumers

One producer, four topics, two consumer **groups**. The predictive-analysis
pipeline and the copilot pipeline read the same records at different rates, from
different starting offsets, without touching each other.

```
                                          ┌── group "noc-predictive"  (offset: earliest)
 VictoriaMetrics ─┐                        │      replays history -> feature windows
 Loki ────────────┼─> bridge.py ─> Kafka ──┤
 labels.jsonl ────┤     (producer)   4     │
 topology-meta ───┘                topics  └── group "noc-copilot"     (offset: latest)
                                                  live state -> text incident brief
```

Two groups rather than one consumer fanning out, because **"replay everything"
and "only what is live" cannot coexist in one group**. Kafka gives each group its
own committed offsets, so each receives a full copy and neither can starve or
block the other.

## Topics

Every record is keyed by `device`. Kafka orders records within a partition, so a
device key turns that into a per-device ordering guarantee — which is exactly what
the windower needs and what a round-robin key would destroy.

| topic | parts | retention | payload | why that retention |
|---|---|---|---|---|
| `noc.metrics` | 6 | 1 day | one canonical 49-column row per (device, entity, 30 s bucket), label columns stripped | high volume; only the predictive pipeline replays it |
| `noc.events` | 6 | 7 days | discrete BGP/OSPF/LDP/link/WireGuard events at **exact** timestamps, templated | cheap, and both pipelines want history |
| `noc.faults` | 3 | 30 days | orchestrator label rows (ground truth) | outlives both pipelines; it is the training target |
| `noc.topology` | 1 | 30 days | static graph + the controller's live active-path selections | low volume, needs history to see topology *changes* |

`noc.events` exists because 30 s metric buckets cannot show a BGP session reset
and its reconvergence when both complete inside one bucket. That topic ships
`(template_id, params)`, not raw syslog — matched lines drop their raw text
entirely, unmatched lines keep it so the templater can be tuned against real
volume.

## Run

Broker is in the telemetry compose (`kafka`, KRaft mode, no Zookeeper):

```bash
cd telemetry && docker compose up -d kafka        # 172.20.20.60:9092 in-lab, 127.0.0.1:29092 on host
```

Two listeners on purpose: Kafka hands a client the **advertised** address after
bootstrap, so one listener cannot serve both in-lab containers and host processes.

Producer runs on the host next to `dataapi` (it imports `dataapi/export.py`, so
there is no extra image to build):

```bash
export KAFKA_BOOTSTRAP=127.0.0.1:29092
./start.sh                                  # poll the live stack every 30s
python3 bridge.py --create-topics           # topics only, then exit
```

Consumers:

```bash
python3 consume.py --pipeline predictive    # windows, from earliest
python3 consume.py --pipeline copilot       # briefs, from latest
```

No lab needed — replay a committed dataset instead:

```bash
python3 bridge.py --replay ../dataapi/datasets/dataset_1785032386_1785033870_30s.parquet --speed 400
```

Offline logic checks (no Kafka, no lab):

```bash
python3 bridge.py --selftest && python3 consume.py --selftest
```

## Verified

Against the broker + the committed 49,844-row real capture (lab down, replay mode):

| check | result |
|---|---|
| topics created with intended partitions/retention | 4/4, verified via `kafka-topics.sh --describe` |
| replay throughput | 49,844 metric + 17 fault records (every episode, `speed 0`) |
| predictive windows built | 8,442 windows, 28 feature channels, L=20 stride 4 |
| labels joined onto windows | 745 labelled windows across 10 fault types |
| **concurrent labels survive the wire** | 628 windows with 1 fault, 103 with 2, 14 with 3 |
| copilot live consumption | 49,861 records, brief rendered: 17 resolved incidents named |
| per-group offsets independent | `noc-predictive` / `noc-copilot` listed separately with own committed offsets |

Re-measured after the multi-label schema landed. `bridge.replay` explodes
`scenario_ids`, so every concurrent episode reaches `noc.faults` (17, not the 14 the
old single-winner collapse produced), and `n_concurrent` up to 3 arrives intact at
the windower. `consume.py` treats the whole multi-label set as supervision, not
input: the feature channel count is unchanged at 28.

The demo uses `--window 20`, not the intended `L=168`: the real capture is only 50
buckets deep, so a 168-bucket window cannot fill. Use the synthetic Parquet (2,880
buckets/day) for full-length windows.

## Two things that are wrong in an obvious-looking design, and why

**Cross-topic ordering does not exist.** Subscribing one consumer to
`noc.metrics` + `noc.faults` and labelling as records arrive produced **4,000
windows, 0 labelled** — the 14 label records arrived after the windows they should
have tagged. Kafka orders within a partition, never across topics. So
`drain_faults()` reads `noc.faults` to its captured end offsets *before* windowing
starts, using `assign()` with no group (labels are small and idempotent;
re-reading beats tracking a cursor). Live inference skips this entirely — there are
no labels at prediction time.

**Timestamps arrive in two formats.** The orchestrator writes
`2026-07-26T02:22:30Z`; a pandas-sourced value stringifies as
`2026-07-26 02:22:30+00:00`. Since `' ' < 'T'` in ASCII, a string comparison in the
overlap test returns the wrong answer whenever the two mix. `_epoch()` parses both.

## Known limitations

1. **The copilot's incident view is retrospective.** The orchestrator writes its
   label row once, in the `finally` block at revert (`orchestrator.py:689-707`), so
   a record on `noc.faults` means the fault has *already ended*. Classifying on
   `t_end is None` would report every real incident as resolved, so
   `partition_faults()` splits on **recency** instead. Making it genuinely live
   needs the orchestrator to publish its existing `campaign_inject` JSON — it
   already prints that at inject time, it just never reaches a topic.
2. **Replay publishes no topology.** `topology-meta.json` and `clab.yml` are
   generated artefacts (gitignored), so `--replay` on a machine without a deployed
   lab renders `FABRIC: 0 nodes`. Live mode reads them normally.
3. **Consumer state is in memory.** Bounded and rebuildable from the topics on
   restart, which is what the retention settings are for, but a copilot restart
   means a cold window until events refill.
4. `noc.events` is empty in replay mode — events come from Loki, which needs the
   lab running. The event path is exercised by `--selftest`, not by replay.
5. Single broker, replication factor 1. Fine for a lab; not a durability story.
