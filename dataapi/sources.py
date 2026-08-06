"""sources.py -- thin data-access layer for the clean data API.

All LIVE-data plumbing lives here so app.py (HTTP handlers) and export.py (the
join) reuse one implementation. Caveman+ponytail: stdlib + httpx only, no ORM,
no caching layer, no client classes -- just functions that hit the live stack.

Universal join key = `device` (node name). Tags: site_type, vrf. All time UTC.

Data sources (live, docker net clab 172.20.20.0/24; ports also mapped to
127.0.0.1 -- we prefer localhost):
  - metrics: VictoriaMetrics Prometheus API @ :8428
  - events:  Loki LogQL @ :3100
  - flows:   nfacctd JSON purge records via `docker logs tele-nfacctd`
  - labels:  faults/labels/*.jsonl
  - topology: derived from topology/clab.yml (+ topology-spec.yaml)
"""
import json
import glob
import os
import subprocess
import time
from datetime import datetime, timezone

import httpx
import yaml

# ponytail: mtime/TTL caches, module-level dicts -- ceiling is single-process
# dataapi; a multi-worker deploy would need a shared cache (out of scope).
_cache = {}

# --- endpoints (localhost-mapped; fall back to container IPs if remapped) ---
VM_URL = os.environ.get("VM_URL", "http://127.0.0.1:8428")
LOKI_URL = os.environ.get("LOKI_URL", "http://127.0.0.1:3100")
NFACCTD_CONTAINER = os.environ.get("NFACCTD_CONTAINER", "tele-nfacctd")

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABELS_GLOB = os.path.join(LAB, "faults", "labels", "*.jsonl")
CLAB_YML = os.path.join(LAB, "topology", "clab.yml")
SPEC_YML = os.path.join(LAB, "topology-spec.yaml")

_HTTP_TIMEOUT = 15.0


def _utc_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# METRICS -- VictoriaMetrics (Prometheus API)
# --------------------------------------------------------------------------
def vm_query(promql: str):
    """Instant query passthrough -> Prometheus result list."""
    r = httpx.get(f"{VM_URL}/api/v1/query", params={"query": promql}, timeout=_HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()["data"]["result"]


def vm_query_range(promql: str, start: int, end: int, step: int = 30):
    """Range query -> Prometheus matrix result list."""
    r = httpx.get(
        f"{VM_URL}/api/v1/query_range",
        params={"query": promql, "start": start, "end": end, "step": step},
        timeout=_HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["data"]["result"]


# --------------------------------------------------------------------------
# EVENTS -- Loki (LogQL)
# --------------------------------------------------------------------------
def loki_query_range(logql: str, start: int, end: int, limit: int = 1000):
    """LogQL range query. start/end in epoch seconds -> Loki streams."""
    r = httpx.get(
        f"{LOKI_URL}/loki/api/v1/query_range",
        params={
            "query": logql,
            "start": str(start) + "000000000",  # ns
            "end": str(end) + "000000000",
            "limit": limit,
            "direction": "forward",
        },
        timeout=_HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["data"]["result"]


def events_rows(start: int, end: int, device: str = None, limit: int = 1000):
    """Flatten Loki streams into per-line event rows tagged with device."""
    # json.dumps escapes quotes/backslashes -> a valid LogQL string literal, so a
    # caller-supplied device cannot break out of the selector.
    sel = '{job=~".+"}' if not device else "{device=%s}" % json.dumps(device)
    streams = loki_query_range(sel, start, end, limit=limit)
    rows = []
    for s in streams:
        labels = s["stream"]
        for ts_ns, line in s["values"]:
            rows.append({
                "ts": _utc_iso(int(ts_ns) / 1e9),
                "device": labels.get("device"),
                "app": labels.get("app"),
                "severity": labels.get("severity"),
                "line": line,
            })
    rows.sort(key=lambda r: r["ts"])
    return rows


# --------------------------------------------------------------------------
# FLOWS -- nfacctd JSON purge records (printed to its stdout/log)
# --------------------------------------------------------------------------
_FLOW_TTL_S = float(os.environ.get("FLOW_CACHE_TTL_S", "5"))


def flow_rows(limit: int = 500, device: str = None, since: int = None, until: int = None):
    """Parse recent nfacctd JSON flow records from `docker logs`.
    ponytail: nfacctd prints one JSON object per purged flow; we tail the log.
    `label` carries the source device (set via device_map.txt).

    since/until (epoch s) scope the fetch itself, so a window is not silently
    reduced to whatever happens to be in the newest log lines. Raises on any
    docker failure -- a dead source must not look like "there were no flows".

    ponytail: short-TTL cache -- `docker logs` is the heaviest call here and
    the plugin polls constantly; a few-second staleness is fine.
    """
    key = ("flow_rows", limit, device, since, until)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < _FLOW_TTL_S:
        return hit[1]
    rows = _flow_rows_uncached(limit, device, since, until)
    _cache[key] = (now, rows)
    return rows


def _flow_rows_uncached(limit, device, since, until):
    # 4 log lines per wanted record is empirical slack for non-purge chatter.
    cmd = ["docker", "logs", "--tail", str(limit * 4)]
    if since is not None:
        cmd += ["--since", _utc_iso(since)]
    if until is not None:
        cmd += ["--until", _utc_iso(until)]
    p = subprocess.run(cmd + [NFACCTD_CONTAINER], capture_output=True, text=True, timeout=20)
    if p.returncode != 0:
        raise RuntimeError(f"flow source unavailable: docker logs rc={p.returncode}: "
                           f"{p.stderr.strip()[:200]}")
    out = p.stdout
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("event_type") != "purge":
            continue
        dev = rec.get("label")
        if device and dev != device:
            continue
        rows.append({
            "ts": rec.get("stamp_updated"),
            "device": dev,
            "ip_src": rec.get("ip_src"),
            "ip_dst": rec.get("ip_dst"),
            "port_src": rec.get("port_src"),
            "port_dst": rec.get("port_dst"),
            "proto": rec.get("ip_proto"),
            "bytes": rec.get("bytes"),
            "packets": rec.get("packets"),
        })
    return rows[-limit:]


# --------------------------------------------------------------------------
# LABELS -- ground-truth fault timeline
# --------------------------------------------------------------------------
def _label_device(row: dict):
    """Mirror plugin's HttpDataClient.deviceOf: target.device, else device.
    `target` is sometimes a bare device-name string, not a dict."""
    target = row.get("target")
    return (target.get("device") if isinstance(target, dict) else None) or row.get("device")


def label_rows(device: str = None):
    paths = sorted(glob.glob(LABELS_GLOB))
    cache_key = ("label_rows", tuple(paths))
    mtime_key = tuple(os.path.getmtime(p) for p in paths)
    hit = _cache.get(cache_key)
    if hit and hit[0] == mtime_key:
        rows = hit[1]
    else:
        rows = []
        for path in paths:
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
        _cache[cache_key] = (mtime_key, rows)
    if device:
        rows = [r for r in rows if _label_device(r) == device]
    return rows


# --------------------------------------------------------------------------
# TOPOLOGY -- graph JSON derived from clab.yml
# --------------------------------------------------------------------------
def _role_of(node: str) -> str:
    if node.startswith("h_"):
        return "host"
    if node.startswith("ce_branch"):
        return "ce_branch"
    if node.startswith("ce_hub"):
        return "ce_hub"
    if node.startswith("ce_dc"):
        return "ce_dc"
    if node.startswith("pe"):
        return "pe"
    if node.startswith("p"):
        return "p"
    return "unknown"


_SITE_TYPE = {"ce_branch": "branch", "ce_hub": "hub", "ce_dc": "dc"}


def _vrfs_for(role: str, spec: dict) -> list:
    """Which VRFs a CE site participates in, per the spec."""
    site_type = _SITE_TYPE.get(role)
    if not site_type:
        return []
    return [v for v, cfg in spec.get("vrfs", {}).items() if site_type in cfg.get("sites", [])]


def topology_graph():
    paths = [CLAB_YML] + ([SPEC_YML] if os.path.exists(SPEC_YML) else [])
    mtime_key = tuple(os.path.getmtime(p) for p in paths)
    hit = _cache.get("topology_graph")
    if hit and hit[0] == mtime_key:
        return hit[1]
    graph = _topology_graph_uncached()
    _cache["topology_graph"] = (mtime_key, graph)
    return graph


def _topology_graph_uncached():
    clab = yaml.safe_load(open(CLAB_YML))
    spec = yaml.safe_load(open(SPEC_YML)) if os.path.exists(SPEC_YML) else {}
    nodes_in = clab["topology"]["nodes"]
    links_in = clab["topology"].get("links", [])

    nodes = []
    for name in nodes_in:
        role = _role_of(name)
        node = {"id": name, "role": role}
        st = _SITE_TYPE.get(role)
        if st:
            node["site_type"] = st
            node["vrfs"] = _vrfs_for(role, spec)
        nodes.append(node)

    links = []
    for lk in links_in:
        a, b = lk["endpoints"]
        an, ai = a.split(":")
        bn, bi = b.split(":")
        links.append({"source": an, "target": bn, "source_if": ai, "target_if": bi})

    return {"nodes": nodes, "links": links}


if __name__ == "__main__":
    g1 = topology_graph()
    g2 = topology_graph()
    assert g1 == g2 and g1 is _cache["topology_graph"][1] is g2, "topology cache mismatch"

    all_rows = label_rows()
    all_rows2 = label_rows()
    assert all_rows == all_rows2, "label cache mismatch"
    assert all_rows, "expected at least one label row for this self-check"
    dev = _label_device(all_rows[0])
    filtered = label_rows(device=dev)
    assert 0 < len(filtered) <= len(all_rows), "device filter did not narrow rows"
    assert all(_label_device(r) == dev for r in filtered), "device filter leaked rows"
    print("sources.py self-check OK")
