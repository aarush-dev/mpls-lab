"""events_push.py -- thin Loki push wrapper for live FRR control-plane precursors.

During a live buildup, run_scenario emits the SAME discrete FRR events the
dataset generator writes -- synthetic/events.py `_spec`/`_params`/`_event_id`,
reused UNCHANGED -- straight into Loki, so the log-side precursors match the
training event stream (#65). Only this push wrapper is new.

Best-effort by construction: an unreachable Loki, a missing pandas/pyarrow (the
events module's deps), or an unmapped fault_type all degrade to a silent no-op
that returns 0 and never raises into the injector.

Only the `impact`-anchored templates are emitted (the down/withdraw/flood burst
that PRECEDES a fault) -- the `end`-anchored recovery events (session UP, link
UP) are dropped: a precursor stream must not say "session up" before the fault
even fires. Every event is stamped at the caller's buildup-start, so the whole
burst lands before the physical impact, down-before-up order preserved.

Stream labels mirror the real syslog->promtail path (device/app/severity) plus
the discrete signature as first-class labels (event_type/template_id), so a
pushed precursor is queryable exactly like a dataset event. The line is the
dataset event ROW verbatim (events.py schema: params as a JSON string, plus
device/entity/severity), so both paths deserialize to one shape.
"""
import json
import os
import sys
import urllib.request

# Loki push endpoint (same host promtail ships to). Env-overridable for tests /
# alt deploys. LOKI_URL is the base; /loki/api/v1/push is the ingest path.
LOKI_URL = os.environ.get("LOKI_URL", "http://172.20.20.54:3100")
_SYN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "synthetic")


def _events_mod():
    """Import synthetic/events lazily -- it pulls pandas/pyarrow at module load,
    which the stdlib-only injector path must not require. Any failure -> None so
    the caller no-ops."""
    try:
        if _SYN_DIR not in sys.path:
            sys.path.insert(0, _SYN_DIR)
        import events  # noqa: E402
        return events
    except Exception:
        return None


def build_events(ev, fault_type, kind, device, entity, vrf, severity,
                 scenario_id, topology_id, precursor_ts):
    """Reuse ev._spec/_params/_event_id (UNCHANGED) to build the discrete PRECURSOR
    events for one live episode, all stamped at `precursor_ts` (buildup-start).
    Only `impact`-anchored templates are kept; the sub-second offsets stagger them
    (and keep down-before-up). Returns (ts_epoch, labels, line) tuples; line is the
    dataset event row (events.py schema)."""
    out = []
    for tid, anchor, off in ev._spec(fault_type, kind, False):
        if anchor != "impact":
            continue  # recovery events are not precursors -- drop them
        etype = ev.TEMPLATES[tid]
        ts_epoch = precursor_ts + off
        ts_us = int(round(ts_epoch * 1e6))
        labels = {"job": "fault-events", "device": device, "app": "frr",
                  "event_type": etype, "template_id": tid}
        if severity is not None:
            labels["severity"] = str(severity)
        line = json.dumps({
            "event_id": ev._event_id(scenario_id, etype, ts_us),
            "ts": ts_us, "device": device, "entity": entity,
            "event_type": etype, "severity": severity, "template_id": tid,
            "params": json.dumps(ev._params(etype, device, entity, vrf)),
            "scenario_id": scenario_id, "topology_id": topology_id,
        })
        out.append((ts_epoch, labels, line))
    return out


def push_events(events_list):
    """POST the (ts_epoch, labels, line) tuples to Loki's push API. One stream
    per event (labels differ per event, so grouping buys nothing). Returns the
    count pushed, or 0 on ANY failure (unreachable Loki, bad payload)."""
    if not events_list:
        return 0
    streams = [{"stream": labels, "values": [[str(int(ts * 1e9)), line]]}
               for ts, labels, line in events_list]
    body = json.dumps({"streams": streams}).encode()
    req = urllib.request.Request(
        f"{LOKI_URL}/loki/api/v1/push", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=5)
        return len(streams)
    except Exception as e:
        print(json.dumps({"event": "loki_push_error",
                          "error": f"{type(e).__name__}: {e}"}), flush=True)
        return 0


def emit_precursors(fault_type, device, entity, vrf, severity, scenario_id,
                    topology_id, precursor_ts):
    """Full path: resolve `kind` from the shared signature table, build the
    discrete FRR precursor events, push them to Loki. Best-effort -- returns the
    count pushed (0 if events/pandas/Loki are unavailable or the fault has no
    discrete signature). NEVER raises. Logs a one-line reason on a 0 so a host
    missing pandas is distinguishable from a fault with no signature."""
    ev = _events_mod()
    if ev is None:
        print(json.dumps({"event": "precursor_noop", "reason": "events_unavailable",
                          "scenario_id": scenario_id}), flush=True)
        return 0
    try:
        import signatures
        sig = signatures.default_signatures(0.0, 0.0, 0.0).get(fault_type)
        if sig is None:
            return 0
        events_list = build_events(
            ev, fault_type, sig["kind"], device, entity, vrf, severity,
            scenario_id, topology_id, precursor_ts)
        return push_events(events_list)
    except Exception as e:
        print(json.dumps({"event": "precursor_build_error",
                          "error": f"{type(e).__name__}: {e}"}), flush=True)
        return 0
