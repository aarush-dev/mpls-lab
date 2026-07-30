#!/usr/bin/env python3
"""bridge.py -- publish live lab telemetry into Kafka for two independent consumers.

WHY KAFKA HERE: the predictive-analysis pipeline and the copilot pipeline want the
same events at different rates, different retention and different starting
offsets. Kafka consumer GROUPS give exactly that for free -- two groups on one
topic each get a full, independent copy with their own committed offsets. So this
bridge publishes ONCE and neither pipeline can starve or block the other.

    noc.metrics ---+--> group "noc-predictive"  (earliest offset; replays history)
    noc.events   --+
    noc.faults   --+--> group "noc-copilot"     (latest offset; only what's live)
    noc.topology -+

Every record is keyed by `device`, so Kafka's per-partition ordering guarantee
becomes a per-device ordering guarantee -- which is what the predictive windower
needs and what a naive round-robin key would destroy.

Sources are the ones already running (nothing new to instrument):
  metrics   VictoriaMetrics range query, reusing dataapi/export.py's metric maps
  events    Loki, reusing the label extraction promtail already does
  faults    faults/labels/labels.jsonl (append-only; tailed by offset)
  topology  topology/topology-meta.json + the controller's live path selections

# ponytail: JSON values, not Avro/protobuf. No schema registry to run, no codegen,
#   and `kafka-console-consumer` stays readable for debugging. Every record carries
#   `_v` so a future breaking change is detectable. Upgrade path: if throughput
#   becomes the bottleneck (it will not at 899 entities / 30 s), swap the
#   serializer for Avro and add a registry -- the topic/key layout is unchanged.

Run:
  python3 bridge.py                      # live: poll the running stack
  python3 bridge.py --replay <parquet>   # offline: replay a dataset, no lab needed
  python3 bridge.py --create-topics      # create the 4 topics then exit
  python3 bridge.py --selftest           # no Kafka, no lab: check record shaping
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(LAB, "dataapi"))

SCHEMA_VERSION = 1
BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "127.0.0.1:9092")
LABELS_GLOB = os.path.join(LAB, "faults", "labels", "*.jsonl")
META_PATH = os.path.join(LAB, "topology", "topology-meta.json")

# topic -> (partitions, retention_ms). Retention differs on purpose: the
# predictive pipeline replays metrics for training windows, the copilot only ever
# needs recent state, and fault labels are the ground truth so they outlive both.
TOPICS = {
    "noc.metrics":  (6, 24 * 3600 * 1000),        # 1 day  -- high volume
    "noc.events":   (6, 7 * 24 * 3600 * 1000),    # 7 days -- discrete, cheap
    "noc.faults":   (3, 30 * 24 * 3600 * 1000),   # 30 days -- ground truth
    "noc.topology": (1, 30 * 24 * 3600 * 1000),   # 30 days -- low volume, needs history
}


# --------------------------------------------------------------------------- events
# promtail already extracts bgp_state / ldp_event / ospf_to into Loki LABELS
# (telemetry/promtail/promtail.yaml:33-47), so the common cases need no reparsing
# here -- read the label. Anything else falls through to the templater below.
def _event_type(labels):
    st = labels.get("bgp_state")
    if st:
        return "bgp_session_up" if st == "Up" else "bgp_session_down"
    ev = labels.get("ldp_event")
    if ev:
        return "ldp_session_down" if ev == "Down" else "ldp_session_up"
    to = labels.get("ospf_to")
    if to:
        return "ospf_adj_up" if to == "Full" else "ospf_adj_down"
    return None


_NUM = re.compile(r"\d+")
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def templatize(line):
    """Drain-lite: mask IPs and numbers, hash the residue -> (template_id, template).

    # ponytail: 3 lines instead of a log-parsing dependency. FRR log lines are
    #   highly regular, so masking variables is enough to collapse them into
    #   stable templates. Ceiling: no tree-based merging, so two messages that
    #   differ only in a WORD stay distinct templates. Upgrade path: Drain3.
    """
    t = _IP.sub("<IP>", line)
    t = _NUM.sub("<N>", t)
    t = " ".join(t.split())[:200]
    return "t" + hashlib.blake2b(t.encode(), digest_size=4).hexdigest(), t


def event_record(ts_iso, labels, line):
    """One noc.events record. ts is EXACT, never bucketed -- that is the point of
    this topic: 30 s metric buckets cannot show a session reset and reconvergence
    that both complete inside one bucket."""
    et = _event_type(labels)
    tid, tmpl = templatize(line)
    params = {k: v for k, v in labels.items()
              if k in ("bgp_peer", "bgp_state", "ldp_peer", "ldp_event",
                       "ospf_peer", "ospf_iface", "ospf_from", "ospf_to")}
    return {
        "_v": SCHEMA_VERSION,
        "ts": ts_iso,
        "device": labels.get("device"),
        "entity": params.get("ospf_iface") or params.get("bgp_peer")
                  or params.get("ldp_peer"),
        "event_type": et or "log",
        "severity": labels.get("severity"),
        "app": labels.get("app"),
        "template_id": tid,
        "template": tmpl,
        "params": params,
        # Raw text kept ONLY when the line did not match a known event shape, so
        # the copilot can still read it and the templater can be tuned against
        # real unmatched volume. Matched lines ship templated (10-50x smaller).
        "raw": None if et else line[:400],
    }


# --------------------------------------------------------------------------- producer
def make_producer(bootstrap=None):
    from kafka import KafkaProducer
    return KafkaProducer(
        bootstrap_servers=(bootstrap or BOOTSTRAP).split(","),
        key_serializer=lambda k: (k or "").encode(),
        value_serializer=lambda v: json.dumps(v, default=str).encode(),
        # acks=1: the leader must ack. acks=0 would drop records silently on a
        # broker restart; acks="all" costs latency we do not need for telemetry
        # that is already sampled at 30 s.
        acks=1,
        linger_ms=200,          # small batching window; 899 rows/cycle batch well
        compression_type="gzip",
    )


def create_topics(bootstrap=None):
    """Idempotent topic creation. Without this the topics are auto-created with 1
    partition and default retention, which silently discards the per-device
    ordering and retention design above."""
    from kafka.admin import KafkaAdminClient, NewTopic
    from kafka.errors import TopicAlreadyExistsError
    admin = KafkaAdminClient(bootstrap_servers=(bootstrap or BOOTSTRAP).split(","))
    want = [NewTopic(name=t, num_partitions=p, replication_factor=1,
                     topic_configs={"retention.ms": str(r)})
            for t, (p, r) in TOPICS.items()]
    made = []
    for nt in want:
        try:
            admin.create_topics([nt])
            made.append(nt.name)
        except TopicAlreadyExistsError:
            pass
    admin.close()
    return made


class Bridge:
    def __init__(self, producer, step=30):
        self.p = producer
        self.step = step
        self._loki_cursor = None      # ns timestamp of the last event shipped
        self._label_offset = {}       # labels file -> bytes already read
        self.counts = {t: 0 for t in TOPICS}

    def send(self, topic, key, value):
        self.p.send(topic, key=key, value=value)
        self.counts[topic] += 1

    # --- metrics ----------------------------------------------------------
    def pump_metrics(self, start, end):
        """One row per (device, entity, bucket), same shape as the export Parquet.

        Reuses export.py's metric maps and collector so a schema change lands in
        the stream and the Parquet together -- there is no second copy of the
        column list to drift.
        """
        import export
        n = 0
        for mmap, ekey, etype in ((export._IF_METRICS, "interface", "interface"),
                                  (export._TUN_METRICS, "tunnel", "tunnel"),
                                  (export._DEV_METRICS, "device", "device")):
            df = export._collect(mmap, ekey, etype, start, end, self.step)
            if not len(df):
                continue
            for rec in df.to_dict("records"):
                rec["_v"] = SCHEMA_VERSION
                # NOTE: no label columns here. Live inference has no labels -- that
                # is the whole point. Ground truth arrives on noc.faults and the
                # consumer joins it when training.
                self.send("noc.metrics", rec.get("device"), rec)
                n += 1
        return n

    # --- events -----------------------------------------------------------
    def pump_events(self, start, end):
        import sources
        n = 0
        try:
            streams = sources.loki_query_range('{job=~".+"}', int(start), int(end),
                                               limit=5000)
        except Exception as e:
            print(json.dumps({"event": "loki_error", "error": f"{type(e).__name__}: {e}"}),
                  flush=True)
            return 0
        for s in streams:
            labels = s["stream"]
            for ts_ns, line in s["values"]:
                ts_ns = int(ts_ns)
                if self._loki_cursor is not None and ts_ns <= self._loki_cursor:
                    continue
                rec = event_record(sources._utc_iso(ts_ns / 1e9), labels, line)
                self.send("noc.events", rec["device"], rec)
                self._loki_cursor = max(self._loki_cursor or 0, ts_ns)
                n += 1
        return n

    # --- faults -----------------------------------------------------------
    def pump_faults(self):
        """Tail labels.jsonl by byte offset. The file is append-only
        (orchestrator.write_label opens in 'a'), so an offset is a valid cursor."""
        n = 0
        for path in sorted(glob.glob(LABELS_GLOB)):
            off = self._label_offset.get(path, 0)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size < off:      # truncated/rotated -> restart from 0
                off = 0
            if size == off:
                continue
            with open(path) as fh:
                fh.seek(off)
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue    # partial write: a full line arrives next cycle
                    row["_v"] = SCHEMA_VERSION
                    self.send("noc.faults", row.get("device"), row)
                    n += 1
                self._label_offset[path] = fh.tell()
        return n

    # --- topology ---------------------------------------------------------
    def pump_topology(self):
        """Static graph + the controller's LIVE active-path selections.

        The path half is real data, not reconstruction: controller.select_paths()
        already decides the active hub per (site, vrf) every tick and publishes it
        as sdwan_path_active, so reading that metric gives the true active path.
        """
        import sources
        rec = {"_v": SCHEMA_VERSION, "ts": sources._utc_iso(time.time()),
               "topology_id": "sdwan_mpls_noc"}
        try:
            with open(META_PATH) as fh:
                rec["meta"] = json.load(fh)
        except Exception:
            rec["meta"] = None
        try:
            rec["graph"] = sources.topology_graph()
        except Exception:
            rec["graph"] = None
        paths = []
        try:
            for s in sources.vm_query("sdwan_path_active"):
                m = s["metric"]
                paths.append({"device": m.get("device"), "site": m.get("site"),
                              "vrf": m.get("vrf"), "hub": m.get("hub"),
                              "path_type": "wg_tunnel"})
        except Exception:
            pass
        rec["active_paths"] = paths
        self.send("noc.topology", rec["topology_id"], rec)
        return 1

    def cycle(self, start, end, with_topology=False):
        out = {"metrics": self.pump_metrics(start, end),
               "events": self.pump_events(start, end),
               "faults": self.pump_faults()}
        if with_topology:
            out["topology"] = self.pump_topology()
        self.p.flush()
        return out


# --------------------------------------------------------------------------- replay
def replay(parquet, producer, step=30, speed=60.0, limit=None):
    """Replay a committed dataset Parquet as if it were live.

    Lets both consumer pipelines be developed and demoed with the lab DOWN, and
    is how this bridge is tested offline. Labels present in the Parquet are split
    back out onto noc.faults so the topic layout matches the live path -- a
    consumer cannot tell replay from live except by wall-clock rate.
    """
    import pandas as pd
    df = pd.read_parquet(parquet)
    if limit:
        df = df.head(int(limit))
    br = Bridge(producer, step=step)
    # Label columns are stripped from the metric records and republished on
    # noc.faults. The list columns arrived with the multi-label schema (DEFECT
    # 5b); leaving them in a metric record would ship the answer with the
    # question, and numpy arrays are not JSON-serialisable anyway.
    label_cols = ["is_fault", "scenario_id", "fault_type", "severity",
                  "severity_label", "lead_time_s", "time_to_impact_s",
                  "fault_types", "severities", "scenario_ids", "impact_methods",
                  "n_concurrent", "fault_type_primary", "severity_primary",
                  "scenario_id_primary"]
    # ts is a timestamp since DEFECT 6b; the wire format stays ISO-8601 text.
    df = df.assign(ts=pd.to_datetime(df["ts"], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
    # One row per (row, concurrent episode) for the label side only.
    lab = df[df["is_fault"].fillna(False).astype(bool)] if "is_fault" in df else df.head(0)
    if len(lab) and "scenario_ids" in lab.columns:
        lab = lab.assign(_sid=lab["scenario_ids"].map(
            lambda v: list(v) if v is not None else [])).explode("_sid")
        lab = lab.assign(_ft=lab["fault_type_primary"], _sev=lab["severity_label"])
        spans = lab.groupby("_sid")["ts"].agg(["min", "max"])
    else:
        spans = None
    seen_scenarios = set()
    for ts, chunk in df.groupby("ts", sort=True):
        for rec in chunk.drop(columns=[c for c in label_cols if c in chunk.columns],
                              errors="ignore").to_dict("records"):
            rec["_v"] = SCHEMA_VERSION
            br.send("noc.metrics", rec.get("device"), rec)
        if spans is not None:
            for _, r0 in lab[lab["ts"] == ts].drop_duplicates("_sid").iterrows():
                sid = r0["_sid"]
                if sid in seen_scenarios or sid is None:
                    continue
                seen_scenarios.add(sid)
                # t_end from the FULL frame, not this bucket: the label must carry
                # the whole window or a consumer's overlap test sees a fault one
                # bucket wide. Recovering it from the labelled rows is exact --
                # they are precisely the buckets the export join marked.
                br.send("noc.faults", r0["device"], {
                    "_v": SCHEMA_VERSION, "scenario_id": sid,
                    "type": str(sid).split("-")[0], "severity": r0.get("_sev"),
                    "device": r0.get("device"),
                    "t_start": spans.loc[sid, "min"], "t_end": spans.loc[sid, "max"],
                    "lead_time": float(r0["lead_time_s"]) if pd.notna(r0.get("lead_time_s")) else None,
                    "replayed_from": os.path.basename(parquet),
                })
        br.p.flush()
        if speed > 0:
            time.sleep(step / speed)
    return br.counts


# --------------------------------------------------------------------------- selftest
def _selftest():
    """Record shaping only -- no Kafka, no lab. Checks the parts that can be wrong
    offline: event typing, templating stability, and the fault-file cursor."""
    r = event_record("2026-07-30T00:00:00Z",
                     {"device": "pe1", "bgp_peer": "10.255.2.2", "bgp_state": "Down",
                      "severity": "warning", "app": "bgpd"},
                     "BGP: %ADJCHANGE: neighbor 10.255.2.2 Down")
    assert r["event_type"] == "bgp_session_down", r["event_type"]
    assert r["device"] == "pe1" and r["entity"] == "10.255.2.2"
    assert r["raw"] is None, "matched events must ship templated, not raw"

    r2 = event_record("2026-07-30T00:00:01Z",
                      {"device": "p3", "ospf_iface": "eth4", "ospf_to": "Full"},
                      "OSPF: interface eth4 neighbor 10.0.0.1 changed from Init to Full")
    assert r2["event_type"] == "ospf_adj_up", r2["event_type"]
    r3 = event_record("2026-07-30T00:00:02Z", {"device": "pe2", "ldp_event": "Down"},
                      "LDP: neighbor 10.255.1.9 Down")
    assert r3["event_type"] == "ldp_session_down"

    # Unmatched line keeps its raw text so the templater can be tuned.
    r4 = event_record("2026-07-30T00:00:03Z", {"device": "ce_hub1"},
                      "zebra: something unexpected on eth7")
    assert r4["event_type"] == "log" and r4["raw"], "unmatched line lost its raw text"

    # Two lines differing only in numbers/IPs must collapse to ONE template.
    a, _ = templatize("BGP: neighbor 10.0.0.1 Down after 30 seconds")
    b, _ = templatize("BGP: neighbor 10.9.9.9 Down after 900 seconds")
    assert a == b, "templater does not mask variables"
    c, _ = templatize("OSPF: adjacency lost")
    assert c != a, "templater collapsed unrelated messages"

    # Fault cursor: a second pass over an unchanged file must ship nothing.
    import tempfile
    global LABELS_GLOB
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "labels.jsonl")
        with open(p, "w") as fh:
            fh.write(json.dumps({"scenario_id": "x-1", "device": "pe1"}) + "\n")
        LABELS_GLOB = os.path.join(d, "*.jsonl")
        sent = []

        class FakeP:
            def send(self, topic, key=None, value=None):
                sent.append((topic, key))

            def flush(self):
                pass
        br = Bridge(FakeP())
        assert br.pump_faults() == 1, "first pass missed the row"
        assert br.pump_faults() == 0, "cursor did not hold; row shipped twice"
        with open(p, "a") as fh:
            fh.write(json.dumps({"scenario_id": "x-2", "device": "pe2"}) + "\n")
        assert br.pump_faults() == 1, "append not picked up"
        assert [k for _, k in sent] == ["pe1", "pe2"], sent

    print("bridge selftest: OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bootstrap", default=BOOTSTRAP)
    ap.add_argument("--interval", type=float, default=30.0,
                    help="poll cycle seconds (match telegraf's 30s)")
    ap.add_argument("--step", type=int, default=30, help="metric bucket seconds")
    ap.add_argument("--topology-every", type=int, default=10,
                    help="republish topology every N cycles")
    ap.add_argument("--create-topics", action="store_true")
    ap.add_argument("--replay", help="replay a dataset Parquet instead of polling")
    ap.add_argument("--speed", type=float, default=60.0,
                    help="replay speedup (60 = 1 bucket per 0.5s at step=30)")
    ap.add_argument("--limit", type=int, help="replay: cap rows (smoke test)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return
    if args.create_topics:
        print(json.dumps({"event": "topics_created", "new": create_topics(args.bootstrap),
                          "all": list(TOPICS)}), flush=True)
        return

    create_topics(args.bootstrap)      # idempotent; safe on every start
    producer = make_producer(args.bootstrap)

    if args.replay:
        counts = replay(args.replay, producer, step=args.step, speed=args.speed,
                        limit=args.limit)
        print(json.dumps({"event": "replay_done", "counts": counts}), flush=True)
        return

    br = Bridge(producer, step=args.step)
    print(json.dumps({"event": "bridge_up", "bootstrap": args.bootstrap,
                      "topics": list(TOPICS), "interval_s": args.interval}), flush=True)
    i = 0
    while True:
        end = time.time()
        start = end - args.interval - args.step   # overlap; cursors dedupe events
        try:
            out = br.cycle(start, end, with_topology=(i % args.topology_every == 0))
            print(json.dumps({"event": "cycle", "i": i, **out}), flush=True)
        except Exception as e:
            # A dead source must not kill the bridge: the other three topics keep
            # flowing and the error is visible in `docker logs`.
            print(json.dumps({"event": "cycle_error", "i": i,
                              "error": f"{type(e).__name__}: {e}"}), flush=True)
        i += 1
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
