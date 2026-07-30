#!/usr/bin/env python3
"""consume.py -- the two downstream pipelines, as two Kafka consumer GROUPS.

Both read the same topics that bridge.py publishes. They are independent: separate
group ids mean separate committed offsets, so neither can slow or starve the other,
and either can be stopped, restarted or rewound without touching the other.

  --pipeline predictive     group "noc-predictive"
      Wants: dense numeric history, per-device ordered, replayable.
      Reads:  noc.metrics (features) + noc.faults (ground truth for training)
      Offset: EARLIEST -- it replays history to build training windows.
      Emits:  fixed-length feature windows per (device, entity), label-joined.

  --pipeline copilot        group "noc-copilot"
      Wants: what is happening RIGHT NOW, in a form an LLM can read.
      Reads:  noc.events + noc.faults + noc.topology + noc.metrics
      Offset: LATEST -- history is the predictive pipeline's problem, not its own.
      Emits:  a rolling natural-language incident brief (RAG context).

The offset difference is the whole reason these are two groups rather than one
consumer fanning out: "replay everything" and "only what's live" are incompatible
in a single group, and Kafka gives both for free.

# ponytail: in-memory state, no Redis/RocksDB. Windows are deques and the copilot
#   state is a dict -- both bounded, both rebuildable from the topics on restart
#   (that is what the retention settings in bridge.TOPICS are for). Ceiling: state
#   is lost on crash and rebuilt by replay, which for the copilot means a cold
#   window until events refill. Upgrade path: commit state snapshots to a compacted
#   topic if cold-start latency ever matters.

Run:
  python3 consume.py --pipeline predictive --max-windows 5
  python3 consume.py --pipeline copilot --brief-every 20
  python3 consume.py --selftest        # no Kafka: window + brief logic
"""
import argparse
import collections
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(LAB, "dataapi"))
sys.path.insert(0, HERE)

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "127.0.0.1:9092")

# Non-feature columns: keys, tags and labels. Everything else in the canonical
# schema is a numeric channel. Derived from export.COLUMNS so a schema change does
# not need editing here too.
_NON_FEATURE = {"ts", "device", "site_type", "vrf", "entity", "entity_type",
                "is_fault", "scenario_id", "fault_type", "severity",
                "severity_label", "lead_time_s", "time_to_impact_s",
                # multi-label label columns (DEFECT 5b) -- supervision, not input
                "fault_types", "severities", "scenario_ids", "impact_methods",
                "n_concurrent",
                "fault_type_primary", "severity_primary", "scenario_id_primary"}


def feature_columns():
    from export import COLUMNS
    return [c for c in COLUMNS if c not in _NON_FEATURE]


def _epoch(ts):
    """Parse a UTC timestamp string to epoch seconds. None -> None.

    Accepts both shapes seen on the wire: the orchestrator's
    "2026-07-26T02:22:30Z" and a pandas-stringified "2026-07-26 02:22:30+00:00".
    """
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    s = str(ts).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        from datetime import datetime, timezone
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except ValueError:
        return None


def _consumer(topics, group, offset, bootstrap=None, timeout_ms=None):
    from kafka import KafkaConsumer
    return KafkaConsumer(
        *topics,
        bootstrap_servers=(bootstrap or BOOTSTRAP).split(","),
        group_id=group,
        auto_offset_reset=offset,
        # Manual commit: the predictive pipeline must not advance its offset until a
        # window is actually built, or a restart silently loses training data.
        enable_auto_commit=False,
        value_deserializer=lambda b: json.loads(b.decode()),
        key_deserializer=lambda b: b.decode() if b else None,
        consumer_timeout_ms=timeout_ms or 0,
        max_poll_records=500,
    )


# ===========================================================================
# PREDICTIVE pipeline
# ===========================================================================
class PredictiveWindower:
    """Builds fixed-length per-entity feature windows from the metrics stream.

    L=168 buckets at 30 s = 84 min of context, stride 4 = one window per 2 min --
    the revised hyperparameters for a 30 s grid.

    Ordering matters and is guaranteed: bridge.py keys every record by `device`, so
    all rows for a device land in one partition and arrive in production order.
    """

    def __init__(self, length=168, stride=4, feature_cols=None):
        self.L = length
        self.stride = stride
        self.cols = feature_cols or feature_columns()
        self.buf = collections.defaultdict(lambda: collections.deque(maxlen=self.L))
        self.since_emit = collections.Counter()
        # device -> list of fault label rows (ground truth, joined at emit time)
        self.faults = collections.defaultdict(list)
        self.windows = 0

    def add_fault(self, row):
        dev = row.get("device")
        if dev:
            self.faults[dev].append(row)

    def add_metric(self, rec):
        """Returns a window dict when one is complete, else None."""
        key = (rec.get("device"), rec.get("entity"), rec.get("entity_type"))
        if key[0] is None or key[1] is None:
            return None
        self.buf[key].append(rec)
        self.since_emit[key] += 1
        if len(self.buf[key]) < self.L or self.since_emit[key] < self.stride:
            return None
        self.since_emit[key] = 0
        return self._emit(key)

    def _emit(self, key):
        rows = list(self.buf[key])
        dev, ent, etype = key
        # Feature matrix: L x C, missing channels as None (the loader decides how
        # to impute; silently zero-filling here would hide a dead metric pillar).
        matrix = [[r.get(c) for c in self.cols] for r in rows]
        t0, t1 = rows[0].get("ts"), rows[-1].get("ts")
        # Multi-label on purpose: a window can overlap several faults at once, and
        # collapsing them to one would erase the concurrency this lab is built to
        # produce. Mirrors the multi-label export change (review P0-2).
        overlapping = [f for f in self.faults.get(dev, [])
                       if self._overlaps(f, t0, t1)]
        self.windows += 1
        return {
            "device": dev, "entity": ent, "entity_type": etype,
            "t_start": t0, "t_end": t1, "n_buckets": len(rows),
            "feature_names": self.cols, "features": matrix,
            "fault_types": [f.get("type") for f in overlapping],
            "scenario_ids": [f.get("scenario_id") for f in overlapping],
            "impact_methods": [f.get("impact_method") for f in overlapping],
            "n_concurrent": len(overlapping),
            "is_fault": bool(overlapping),
        }

    @staticmethod
    def _overlaps(fault, t0, t1):
        """Window [t0,t1] vs fault [t_start,t_end] -- interval OVERLAP, not
        containment, matching the export join (export.py:184-209).

        Compares parsed epochs, not strings: the orchestrator writes
        "2026-07-26T02:22:30Z" but a pandas-sourced timestamp stringifies as
        "2026-07-26 02:22:30+00:00", and ' ' < 'T' in ASCII -- so a string compare
        silently returns the wrong answer whenever the two formats mix.
        """
        fs, fe = _epoch(fault.get("t_start")), _epoch(fault.get("t_end"))
        a, b = _epoch(t0), _epoch(t1)
        if fs is None or a is None or b is None:
            return False
        # fe is None => fault has no recorded end yet (still open) => unbounded.
        return fs <= b and (fe is None or fe >= a)


def drain_faults(bootstrap=None, timeout_ms=5000):
    """Read noc.faults from the beginning to its current end, then stop.

    WHY THIS EXISTS: Kafka orders records within a partition, not ACROSS topics.
    Subscribing to noc.metrics and noc.faults in one consumer gives no ordering
    between them, so the 14 label records can arrive after the windows they should
    have tagged -- observed: 3000 windows built, 0 labelled. A training consumer
    must therefore load the label set to completion FIRST.

    Uses assign(), not subscribe(): no group, no committed offsets. Labels are
    small and idempotent, so re-reading them on every start is cheaper than
    tracking a cursor -- and it guarantees a restart never sees a partial set.

    Live inference does not call this at all: there are no labels at prediction
    time, which is the entire point of the task.
    """
    from kafka import KafkaConsumer, TopicPartition
    c = KafkaConsumer(bootstrap_servers=(bootstrap or BOOTSTRAP).split(","),
                      value_deserializer=lambda b: json.loads(b.decode()),
                      consumer_timeout_ms=timeout_ms)
    parts = [TopicPartition("noc.faults", p)
             for p in (c.partitions_for_topic("noc.faults") or [])]
    rows = []
    if parts:
        c.assign(parts)
        ends = c.end_offsets(parts)
        for tp in parts:
            c.seek_to_beginning(tp)
        # Stop at the offsets captured above, not "when the poll goes quiet" -- a
        # slow fetch would otherwise look like end-of-topic and truncate labels.
        pending = {tp for tp in parts if ends.get(tp, 0) > 0}
        for msg in c:
            rows.append(msg.value)
            tp = TopicPartition(msg.topic, msg.partition)
            if msg.offset >= ends.get(tp, 0) - 1:
                pending.discard(tp)
            if not pending:
                break
    c.close()
    return rows


def run_predictive(args):
    w = PredictiveWindower(length=args.window, stride=args.stride)
    # Labels first, to completion -- see drain_faults().
    labels = drain_faults(args.bootstrap)
    for row in labels:
        w.add_fault(row)
    c = _consumer(["noc.metrics"], args.group or "noc-predictive",
                  "earliest", args.bootstrap, args.timeout_ms)
    print(json.dumps({"event": "predictive_up", "group": args.group or "noc-predictive",
                      "offset": "earliest", "window_L": w.L, "stride": w.stride,
                      "n_features": len(w.cols), "labels_preloaded": len(labels)}),
          flush=True)
    seen = 0
    try:
        for msg in c:
            seen += 1
            win = w.add_metric(msg.value)
            if win:
                print(json.dumps({"event": "window", "device": win["device"],
                                  "entity": win["entity"], "t_end": win["t_end"],
                                  "n_concurrent": win["n_concurrent"],
                                  "fault_types": win["fault_types"]}), flush=True)
                # Commit only after the window is built -- see enable_auto_commit.
                c.commit()
                if args.max_windows and w.windows >= args.max_windows:
                    break
    finally:
        c.close()
    print(json.dumps({"event": "predictive_done", "consumed": seen,
                      "windows": w.windows}), flush=True)
    return w


# ===========================================================================
# COPILOT pipeline
# ===========================================================================
class CopilotState:
    """Rolling view of the fabric, rendered as text for an LLM prompt.

    Deliberately NOT a feature matrix. A copilot needs named entities, discrete
    events with exact times, and the current topology -- the same three things the
    predictive pipeline throws away when it flattens to numbers.
    """

    def __init__(self, event_ring=200, recent_s=900.0):
        self.events = collections.deque(maxlen=event_ring)
        self.faults = collections.OrderedDict()   # scenario_id -> label row
        self.latest = {}            # (device, entity) -> last metric record
        self.topology = None
        self.active_paths = []
        self.counts = collections.Counter()
        self.recent_s = recent_s

    def add_event(self, rec):
        self.events.append(rec)
        self.counts[rec.get("event_type") or "log"] += 1

    def add_fault(self, row):
        sid = row.get("scenario_id")
        if not sid:
            return
        self.faults[sid] = row
        while len(self.faults) > 200:
            self.faults.popitem(last=False)

    def partition_faults(self, now=None):
        """(active, resolved) split by recency, NOT by presence of t_end.

        KNOWN LIMITATION, stated because it changes how the brief reads: the
        orchestrator writes its label row ONCE, in the finally block at revert
        (orchestrator.py:689-707). So a label arriving on noc.faults means the
        fault has ALREADY ended -- the copilot's incident view is retrospective by
        construction. Classifying on `t_end is None` would therefore report every
        real incident as resolved and never show anything active.

        Recency is the honest proxy: a fault that ended within recent_s is still
        what an operator is being asked about. Making this genuinely live needs the
        orchestrator to emit its existing `campaign_inject` event at INJECT time
        (it already prints that JSON to stdout -- it just never reaches a topic).
        """
        now = now if now is not None else time.time()
        active, resolved = [], []
        for row in self.faults.values():
            end = _epoch(row.get("t_end"))
            if end is None or end >= now - self.recent_s:
                active.append(row)
            else:
                resolved.append(row)
        return active, resolved

    def add_metric(self, rec):
        self.latest[(rec.get("device"), rec.get("entity"))] = rec

    def add_topology(self, rec):
        self.topology = rec.get("graph")
        self.active_paths = rec.get("active_paths") or []

    def hot_devices(self, n=5):
        """Devices with the most events in the ring -- where to look first."""
        c = collections.Counter(e.get("device") for e in self.events if e.get("device"))
        return c.most_common(n)

    def brief(self, now=None):
        """Natural-language incident brief. This is the copilot's RAG context."""
        L = []
        nodes = len(self.topology["nodes"]) if self.topology else 0
        L.append(f"FABRIC: {nodes} nodes, {len(self.active_paths)} active overlay paths.")
        active, resolved = self.partition_faults(now)
        if active:
            L.append(f"ACTIVE INCIDENTS ({len(active)}):")
            for f in active[:10]:
                L.append(f"  - {f.get('type')} on {f.get('device')} "
                         f"severity={f.get('severity')} since {f.get('t_start')}"
                         f" | {f.get('signature') or ''}".rstrip(" |"))
        else:
            L.append("ACTIVE INCIDENTS: none.")
        if resolved:
            L.append(f"RESOLVED ({len(resolved)}), most recent: " +
                     ", ".join(f"{x.get('type')}@{x.get('device')}"
                               for x in resolved[-3:]))
        hot = self.hot_devices()
        if hot:
            L.append("MOST EVENTFUL: " + ", ".join(f"{d} ({n})" for d, n in hot))
        if self.counts:
            L.append("EVENT MIX: " + ", ".join(f"{k}={v}" for k, v in
                                               self.counts.most_common(6)))
        recent = list(self.events)[-8:]
        if recent:
            L.append("LAST EVENTS:")
            for e in recent:
                ent = f" {e.get('entity')}" if e.get("entity") else ""
                L.append(f"  {e.get('ts')} {e.get('device')}{ent} "
                         f"{e.get('event_type')} {e.get('template') or ''}"[:160])
        return "\n".join(L)


def run_copilot(args):
    st = CopilotState()
    c = _consumer(["noc.events", "noc.faults", "noc.topology", "noc.metrics"],
                  args.group or "noc-copilot", "latest", args.bootstrap,
                  args.timeout_ms)
    print(json.dumps({"event": "copilot_up", "group": args.group or "noc-copilot",
                      "offset": "latest"}), flush=True)
    seen = 0
    try:
        for msg in c:
            seen += 1
            if msg.topic == "noc.events":
                st.add_event(msg.value)
            elif msg.topic == "noc.faults":
                st.add_fault(msg.value)
            elif msg.topic == "noc.topology":
                st.add_topology(msg.value)
            else:
                st.add_metric(msg.value)
            if args.brief_every and seen % args.brief_every == 0:
                print("\n--- BRIEF ---\n" + st.brief() + "\n", flush=True)
                c.commit()
            if args.max_records and seen >= args.max_records:
                break
    finally:
        c.close()
    print("\n--- FINAL BRIEF ---\n" + st.brief(), flush=True)
    print(json.dumps({"event": "copilot_done", "consumed": seen}), flush=True)
    return st


# ===========================================================================
def _selftest():
    """Window assembly + brief rendering, no Kafka."""
    cols = ["if_in_octets", "cpu_pct"]
    w = PredictiveWindower(length=4, stride=2, feature_cols=cols)
    w.add_fault({"device": "pe1", "scenario_id": "s1", "type": "congestion",
                 "t_start": "2026-07-30T00:00:30Z", "t_end": "2026-07-30T00:02:00Z",
                 "impact_method": "vm_threshold"})
    # A second, overlapping fault on the same device: both must survive to the
    # window (the multi-label property).
    w.add_fault({"device": "pe1", "scenario_id": "s2", "type": "gray_failure",
                 "t_start": "2026-07-30T00:01:00Z", "t_end": "2026-07-30T00:03:00Z",
                 "impact_method": "modelled"})
    out = []
    for k in range(6):
        r = {"device": "pe1", "entity": "eth1", "entity_type": "interface",
             "ts": f"2026-07-30T00:0{k}:00Z", "if_in_octets": 100.0 * k,
             "cpu_pct": 5.0}
        got = w.add_metric(r)
        if got:
            out.append(got)
    assert out, "no window emitted after L buckets + stride"
    win = out[0]
    assert win["n_buckets"] == 4, win["n_buckets"]
    assert len(win["features"]) == 4 and len(win["features"][0]) == 2
    # First emit is the bucket that completes L (index 3 -> 100*3), not index 4.
    assert win["features"][-1][0] == 300.0, win["features"]
    assert win["n_concurrent"] == 2, win["fault_types"]
    assert set(win["fault_types"]) == {"congestion", "gray_failure"}
    assert set(win["impact_methods"]) == {"vm_threshold", "modelled"}

    # Stride: after the first window, the next needs `stride` more buckets, not 1.
    # 6 buckets at L=4/stride=2 -> emits at index 3 and 5.
    assert len(out) == 2, f"expected 2 windows from 6 buckets at stride 2, got {len(out)}"
    assert out[1]["features"][-1][0] == 500.0, out[1]["features"]

    # A window on a device with no fault must be clean, not inherit pe1's labels.
    w2 = PredictiveWindower(length=2, stride=1, feature_cols=cols)
    w2.add_fault({"device": "pe1", "scenario_id": "s1", "type": "congestion",
                  "t_start": "2026-07-30T00:00:00Z", "t_end": "2026-07-30T00:05:00Z"})
    for k in range(2):
        got = w2.add_metric({"device": "pe9", "entity": "eth1",
                             "entity_type": "interface",
                             "ts": f"2026-07-30T00:0{k}:00Z",
                             "if_in_octets": 1.0, "cpu_pct": 1.0})
    assert got and got["n_concurrent"] == 0, "label leaked across devices"

    # Non-overlapping fault must not attach.
    assert not PredictiveWindower._overlaps(
        {"t_start": "2026-07-30T10:00:00Z", "t_end": "2026-07-30T10:01:00Z"},
        "2026-07-30T00:00:00Z", "2026-07-30T00:02:00Z")
    # Mixed timestamp formats must still compare correctly. A string compare gets
    # this WRONG (' ' < 'T'), which is why _overlaps parses to epoch.
    assert PredictiveWindower._overlaps(
        {"t_start": "2026-07-30 00:01:00+00:00", "t_end": None},
        "2026-07-30T00:00:00Z", "2026-07-30T00:02:00Z")
    assert not PredictiveWindower._overlaps(
        {"t_start": "2026-07-30 23:00:00+00:00", "t_end": None},
        "2026-07-30T00:00:00Z", "2026-07-30T00:02:00Z"), \
        "a fault starting 23h later must not attach"
    assert _epoch("2026-07-30T00:00:00Z") == _epoch("2026-07-30 00:00:00+00:00")
    assert _epoch(None) is None and _epoch("not-a-time") is None

    # Copilot: active-vs-resolved is decided by RECENCY, not by t_end presence --
    # every real label carries t_end because it is written at revert.
    st = CopilotState(recent_s=900.0)
    st.add_topology({"graph": {"nodes": [1, 2, 3], "links": []},
                     "active_paths": [{"device": "ce_branch1"}]})
    st.add_fault({"scenario_id": "s9", "type": "tunnel_degrade", "device": "ce_hub1",
                  "severity": "high", "t_start": "2026-07-30T00:00:00Z",
                  "t_end": "2026-07-30T00:02:00Z", "signature": "jitter+loss climb"})
    st.add_event({"ts": "2026-07-30T00:00:05Z", "device": "ce_hub1",
                  "event_type": "wg_rekey", "template": "rekey <N>"})
    # 3 min after it ended: still inside recent_s -> ACTIVE (a closed label must
    # not vanish from the brief the instant it arrives).
    now = _epoch("2026-07-30T00:05:00Z")
    b = st.brief(now=now)
    assert "ACTIVE INCIDENTS (1)" in b and "tunnel_degrade" in b, b
    assert "3 nodes" in b, b
    # 2 h later: outside recent_s -> RESOLVED.
    b2 = st.brief(now=_epoch("2026-07-30T02:00:00Z"))
    assert "ACTIVE INCIDENTS: none." in b2 and "RESOLVED (1)" in b2, b2
    # A label with no t_end at all is treated as open indefinitely.
    st.add_fault({"scenario_id": "s10", "type": "congestion", "device": "ce_dc1",
                  "t_start": "2026-07-30T00:00:00Z"})
    a, _ = st.partition_faults(now=_epoch("2026-07-31T00:00:00Z"))
    assert [x["scenario_id"] for x in a] == ["s10"], a
    print("consume selftest: OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pipeline", choices=["predictive", "copilot"])
    ap.add_argument("--bootstrap", default=BOOTSTRAP)
    ap.add_argument("--group", help="override the consumer group id")
    ap.add_argument("--window", type=int, default=168, help="predictive: L buckets")
    ap.add_argument("--stride", type=int, default=4, help="predictive: window stride")
    ap.add_argument("--max-windows", type=int, help="predictive: stop after N windows")
    ap.add_argument("--max-records", type=int, help="copilot: stop after N records")
    ap.add_argument("--brief-every", type=int, default=50,
                    help="copilot: print a brief every N records")
    ap.add_argument("--timeout-ms", type=int, default=0,
                    help="exit after this long with no records (0 = block forever)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return
    if not args.pipeline:
        ap.error("--pipeline is required (or --selftest)")
    if args.pipeline == "predictive":
        run_predictive(args)
    else:
        run_copilot(args)


if __name__ == "__main__":
    main()
