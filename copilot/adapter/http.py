"""copilot.adapter.http -- the real HTTP tool adapter over a running dataapi (A1).

The stub replacement (#40). This is the ONE layer coupled to dataapi's endpoint shapes
(ADR-0006 -- endpoints are NOT a trusted-final contract, spec #3), so every shape mismatch
between what dataapi emits and what the contract/gate expect is resolved HERE, never pushed
outward:

  - `ts` normalisation. `Evidence.ts` is epoch int and the gate does `start <= ts <= end`
    (numeric), but dataapi emits event ts as a UTC ISO string (sources.py) and flow ts as
    nfacctd's `stamp_updated` string. Both are parsed to epoch int here, or a TypeError would
    fire INSIDE the gate.
  - `/metrics` takes PromQL, not device/pattern/limit. A selector is synthesised from Filters;
    a result vector has no row concept, so a per-series latest sample becomes one Evidence.
  - `/events` has no `pattern`/`offset`; both are done adapter-side (fetch-then-filter), or
    `pattern` silently no-ops and `next_page` lies.
  - `/flows` windowing is `docker logs --since/--until` (log print time, approximate): a
    genuinely relevant row can land just outside [start,end] and read as out-of-window at the
    gate. Known ceiling of the source, recorded, not worked around.
  - transport faults (connection refused, dataapi 5xx) raise `AdapterError`, which the tools
    layer converts into a tool observation -- not an unhandled raise, not a false unknown-device.

Everything after the fetch is F2's shared `serve_rows` pipeline (validate -> cap -> provenance
-> page -> frame), identical to the stub, so the F2 guarantees hold on live data unchanged.

Self-check:  python3 -m copilot.adapter.test_http
"""
import json
import re
from datetime import datetime, timezone

import httpx

from copilot.adapter.contract import (
    AdapterError, Filters, MAX_LIMIT, NodeState, Result, bfs_hops, hops_within_links,
    row_text, sanitize, serve_rows,
)
from copilot.window import WindowContext

_TIMEOUT = 25.0      # > dataapi's /flows subprocess budget (docker logs, 20s, sources.py) so a
                     # slow-but-healthy flow read is not cut off client-side as a false outage.
_STEP = 30           # /metrics range step seconds (mirrors dataapi's default)
# ponytail: one bounded fetch per read, then filter+page adapter-side. The window (X min,
# ADR-0002) already bounds the row count, so this covers the low cap (<=50) and typical offsets.
# KNOWN CEILING: a window holding > _FETCH_CAP rows (only a fault storm) is truncated by the
# source, so the older/newer tail is dropped AND next_page reads exhausted off the truncated set
# -- partial coverage read as complete. Upgrade to server-side paging if that window is real.
_FETCH_CAP = 1000


def _iso_to_epoch(s) -> int | None:
    """dataapi event ts ('2026-08-03T05:20:07Z') and flow ts ('2026-08-03 10:30:31', space-sep,
    naive UTC) -> epoch int. Unparseable -> None, so serve_rows drops it as not-provably-in-window
    (never a TypeError at the gate). ponytail: fromisoformat covers both once 'Z' is normalised."""
    if not isinstance(s, str):
        return int(s) if isinstance(s, (int, float)) else None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:                       # flow stamp is naive -> it's UTC (sources.py)
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _selector(filters: Filters) -> str:
    """A PromQL instant selector from Filters. validate() guarantees device or pattern, so the
    matcher set is never empty. `pattern` narrows the metric NAME (substring, regex-escaped);
    json.dumps quotes the value so a hostile device/pattern can't break out of the selector."""
    m = []
    if filters.device:
        m.append("device=%s" % json.dumps(filters.device))
    if filters.pattern:
        m.append("__name__=~%s" % json.dumps(f".*{re.escape(filters.pattern)}.*"))
    return "{%s}" % ",".join(m)


def _latest_sample(series: dict):
    """Latest (ts, value) of a Prometheus series -- range `values` (ascending) or instant
    `value`. None if the series carries neither."""
    samples = series.get("values") or ([series["value"]] if "value" in series else [])
    return tuple(samples[-1]) if samples else None


def _matches(row: dict, pattern: str) -> bool:
    """`pattern` substring-narrow for endpoints that lack a server-side one (/events, /flows).
    Searched over the PAYLOAD fields only -- `ts` is excluded so a numeric pattern ('443')
    can't spuriously match an epoch/byte count; both endpoints search the same set so one tool
    arg has one contract (device/app/severity/line for events, the flow tuple for flows)."""
    p = pattern.lower()
    return p in row_text({k: v for k, v in row.items() if k != "ts"}).lower()


class HttpAdapter:
    """ToolAdapter over a live dataapi. `fetch` is the injectable transport seam (path, params)
    -> parsed JSON; the default hits dataapi with httpx and maps any HTTP fault to AdapterError.
    Tests pass a canned/faulty fetch to exercise the shape-mapping without a live stack."""

    def __init__(self, base_url: str, *, timeout: float = _TIMEOUT, fetch=None,
                 transport=None, max_limit: int = MAX_LIMIT):
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport      # test seam: an httpx.MockTransport drives _http_get
        self._max_limit = max_limit
        self._fetch = fetch or self._http_get

    # -- transport -----------------------------------------------------------
    def _http_get(self, path: str, params: dict) -> dict:
        # ponytail: a short-lived client per call (closed by `with`) -- no pool to leak, matches
        # dataapi/sources.py's per-call httpx.get; <=6 calls/investigation makes reuse moot.
        try:
            with httpx.Client(base_url=self._base, timeout=self._timeout,
                              transport=self._transport) as c:
                r = c.get(path, params=params)
                r.raise_for_status()
                return r.json()
        # httpx.HTTPError = connect refusal / 5xx / timeout; ValueError = a non-JSON body
        # (JSONDecodeError, e.g. an HTML proxy error page -- NOT an httpx.HTTPError);
        # httpx.InvalidURL = a malformed COPILOT_DATAAPI_URL. None may escape as a raise.
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as e:
            raise AdapterError(f"dataapi {path} unavailable: {e}") from e

    # -- windowed reads (share F2's serve_rows) ------------------------------
    # Fetch is passed as a THUNK: serve_rows validates FIRST, so an over-broad / freeze-violating
    # call is rejected before any network read fires (never a wire read past T_snapshot).
    def metrics(self, filters: Filters) -> Result:
        return serve_rows("metrics", filters, lambda: self._metrics_rows(filters), self._max_limit)

    def events(self, filters: Filters) -> Result:
        return serve_rows("events", filters, lambda: self._events_rows(filters), self._max_limit)

    def flows(self, filters: Filters) -> Result:
        return serve_rows("flows", filters, lambda: self._flows_rows(filters), self._max_limit)

    def _metrics_rows(self, filters: Filters) -> list[dict]:
        data = self._fetch("/metrics", {"query": _selector(filters), "start": filters.start,
                                        "end": filters.end, "step": _STEP})
        rows = []
        for series in data.get("result", ()):
            labels = series.get("metric", {})
            sample = _latest_sample(series)
            if sample is None:
                continue
            ts, val = sample
            # drop the names row.update sets below too, so a Prometheus label literally called
            # `metric`/`value`/`ts` can't be reported in place of the real field.
            row = {k: v for k, v in labels.items()
                   if k not in ("__name__", "device", "metric", "value", "ts")}
            row.update(metric=labels.get("__name__"), value=val,
                       device=labels.get("device"), ts=int(float(ts)))
            rows.append(row)
        return rows

    def _events_rows(self, filters: Filters) -> list[dict]:
        # /events has no pattern/offset: fetch the window, normalise ISO ts, then filter by
        # pattern (substring over the payload, _matches) adapter-side; serve_rows owns offset.
        data = self._fetch("/events", {"start": filters.start, "end": filters.end,
                                       "device": filters.device, "limit": _FETCH_CAP})
        rows = [{**r, "ts": _iso_to_epoch(r.get("ts"))} for r in data.get("rows", ())]
        if filters.pattern:
            rows = [r for r in rows if _matches(r, filters.pattern)]
        return rows

    def _flows_rows(self, filters: Filters) -> list[dict]:
        # /flows windows via `docker logs --since/--until` = log PRINT time (approximate):
        # a relevant flow can print outside [start,end] and read as out-of-window at the gate.
        # No pattern/offset at the endpoint -> pattern filtered adapter-side over the payload.
        data = self._fetch("/flows", {"limit": _FETCH_CAP, "device": filters.device,
                                      "start": filters.start, "end": filters.end})
        rows = [{**r, "ts": _iso_to_epoch(r.get("ts"))} for r in data.get("rows", ())]
        if filters.pattern:
            rows = [r for r in rows if _matches(r, filters.pattern)]
        return rows

    # -- topology ------------------------------------------------------------
    def hops_within(self, focus: str, n: int) -> set[str]:
        graph = self._fetch("/topology", {})
        return hops_within_links(graph.get("links", ()), focus, n)

    def walk_topology(self, focus: str, n: int, window: WindowContext) -> tuple[NodeState, ...]:
        # I3 (ADR-0007): /topology -> bfs_hops (shared, real link shape) -> ONE batched PromQL
        # over the walk's nodes scoped to the window -> reduce to a per-node status. Unknown
        # focus -> () (never fabricate a node); a transport fault raises AdapterError BEFORE
        # that, so an outage is an observation, not a false 'unknown device' (A1 acceptance).
        graph = self._fetch("/topology", {})
        links = graph.get("links", ())
        known = ({node["id"] for node in graph.get("nodes", ())}
                 | {x for lk in links for x in (lk["source"], lk["target"])})
        if focus not in known:
            return ()
        hops = bfs_hops(links, focus, n)
        status = self._walk_status(list(hops), window)
        return tuple(
            NodeState(node=node, hop=hop, status=status.get(node, "no metrics"))
            for node, hop in sorted(hops.items(), key=lambda kv: (kv[1], kv[0]))
        )

    def _walk_status(self, nodes: list[str], window: WindowContext) -> dict[str, str]:
        """One batched range query over every walk node -> {device: sanitized latest k=v}.
        /metrics labels are the untrusted side (ADR-0016) -> sanitize before the model sees it."""
        if not nodes:
            return {}
        sel = "{device=~%s}" % json.dumps("|".join(re.escape(nb) for nb in sorted(nodes)))
        data = self._fetch("/metrics", {"query": sel, "start": window.start,
                                        "end": window.end, "step": _STEP})
        latest: dict[str, dict[str, str]] = {}
        for series in data.get("result", ()):
            labels = series.get("metric", {})
            dev, name = labels.get("device"), labels.get("__name__")
            sample = _latest_sample(series)
            if not dev or sample is None:
                continue
            latest.setdefault(dev, {})[name] = sample[1]
        return {dev: sanitize(" ".join(f"{k}={v}" for k, v in sorted(metrics.items())))
                for dev, metrics in latest.items()}
