"""copilot.adapter.stub -- canned tool adapter for deterministic tests (stub boundary 2).

Returns caller-supplied canned rows through the real contract (validate -> cap ->
provenance -> page -> frame), so the agent loop and tools are testable without
VictoriaMetrics/Loki running (spec §Testing).
"""
from collections.abc import Sequence

from copilot.adapter.contract import (
    Filters, MAX_LIMIT, NodeState, Result, bfs_hops, hops_within_links, known_nodes,
    payload_matches, sanitize, serve_rows,
)
from copilot.window import WindowContext


class StubAdapter:
    """Serves canned rows per source through the mandatory-filter contract.

    Rows are plain dicts shaped like the dataapi endpoints: a `device`, a `ts`
    (epoch seconds), and payload fields (`msg` for events, values for metrics).
    """

    def __init__(self, metrics_rows: Sequence[dict] = (),
                 events_rows: Sequence[dict] = (),
                 flows_rows: Sequence[dict] = (),
                 topology: dict | None = None,
                 max_limit: int = MAX_LIMIT):
        self._rows = {
            "metrics": list(metrics_rows),
            "events": list(events_rows),
            "flows": list(flows_rows),
        }
        self._topology = topology or {"nodes": [], "links": []}
        self._max_limit = max_limit

    def metrics(self, filters: Filters) -> Result:
        return self._serve("metrics", filters)

    def events(self, filters: Filters) -> Result:
        return self._serve("events", filters)

    def flows(self, filters: Filters) -> Result:
        return self._serve("flows", filters)

    def hops_within(self, focus: str, n: int) -> set[str]:
        return hops_within_links(self._topology.get("links", ()), focus, n)

    def known_devices(self) -> frozenset[str]:
        return known_nodes(self._topology.get("nodes", ()), self._topology.get("links", ()))

    def walk_topology(self, focus: str, n: int, window: WindowContext) -> tuple[NodeState, ...]:
        # BFS the real edges, then enrich each node with its latest metric row. Ordered by
        # (hop, node) so the walk is deterministic (ADR-0007). Unknown focus -> () (never
        # fabricate a node that isn't in the topology). ponytail: the stub ignores `window`
        # (canned rows carry no live clock); the HTTP adapter batches a PromQL over it per
        # frontier. Status = raw latest metric summary -- health thresholds are a later ticket.
        links = self._topology.get("links", ())
        if focus not in self.known_devices():
            return ()
        hops = bfs_hops(links, focus, n)
        return tuple(
            NodeState(node=node, hop=hop, status=self._status(node))
            for node, hop in sorted(hops.items(), key=lambda kv: (kv[1], kv[0]))
        )

    def _status(self, device: str) -> str:
        rows = [r for r in self._rows["metrics"] if r.get("device") == device]
        if not rows:
            return "no metrics"
        latest = max(rows, key=lambda r: r.get("ts", 0))
        # /metrics labels are the untrusted side (ADR-0016): sanitize before the model sees it.
        return sanitize(" ".join(f"{k}={v}" for k, v in latest.items() if k not in ("device", "ts")))

    def _serve(self, source: str, filters: Filters) -> Result:
        # #119: filter by device/pattern like the real HttpAdapter does (server-side there,
        # here adapter-side over the canned rows) -- else the stub can't exercise "known
        # device, no data" vs "unknown device" vs a real device/pattern-scoped read.
        rows = self._rows[source]
        if filters.device:
            rows = [r for r in rows if r.get("device") == filters.device]
        if filters.pattern:
            rows = [r for r in rows if payload_matches(r, filters.pattern)]
        # F2's shared pipeline (validate -> window-filter -> page -> frame); canned rows in.
        return serve_rows(source, filters, rows, self._max_limit)
