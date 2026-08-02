"""copilot.adapter.stub -- canned tool adapter for deterministic tests (stub boundary 2).

Returns caller-supplied canned rows through the real contract (validate -> cap ->
provenance -> page -> frame), so the agent loop and tools are testable without
VictoriaMetrics/Loki running (spec §Testing).
"""
from collections.abc import Sequence

from copilot.adapter.contract import Evidence, Filters, Result, MAX_LIMIT, frame


class StubAdapter:
    """Serves canned rows per source through the mandatory-filter contract.

    Rows are plain dicts shaped like the dataapi endpoints: a `device`, a `ts`
    (epoch seconds), and payload fields (`msg` for events, values for metrics).
    """

    def __init__(self, metrics_rows: Sequence[dict] = (),
                 events_rows: Sequence[dict] = (),
                 flows_rows: Sequence[dict] = (),
                 max_limit: int = MAX_LIMIT):
        self._rows = {
            "metrics": list(metrics_rows),
            "events": list(events_rows),
            "flows": list(flows_rows),
        }
        self._max_limit = max_limit

    def metrics(self, filters: Filters) -> Result:
        return self._serve("metrics", filters)

    def events(self, filters: Filters) -> Result:
        return self._serve("events", filters)

    def flows(self, filters: Filters) -> Result:
        return self._serve("flows", filters)

    def _serve(self, source: str, filters: Filters) -> Result:
        filters.validate(self._max_limit)
        rows = self._rows[source]
        window = rows[filters.offset:filters.offset + filters.limit]
        evidence = tuple(
            Evidence(
                id=f"{source}:{i}",
                source=source,
                device=row.get("device"),
                ts=row.get("ts"),
                content=frame(_row_text(row)),
            )
            for i, row in enumerate(window, start=filters.offset)
        )
        end = filters.offset + filters.limit
        next_page = str(end) if end < len(rows) else None
        return Result(evidence=evidence, next_page=next_page)


def _row_text(row: dict) -> str:
    """Compact `k=v` rendering; the payload (e.g. a log `msg`) is where any
    injected instruction would live, so it goes through frame()/sanitize()."""
    return " ".join(f"{k}={v}" for k, v in row.items())
