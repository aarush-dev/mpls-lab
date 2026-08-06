"""copilot.adapter.contract -- the tool-adapter seam + mandatory-filter contract.

The single wrapper over the data API (dataapi/app.py -- /metrics /events /flows,
NOT a trusted-final contract, so only this layer knows their shape; ADR-0006).
Enforces small-by-construction reads (ADR-0015) and frames results as untrusted
data (ADR-0016). F2 ships the interface + shapes + enforcement; the real HTTP
adapter and the topology walk (I3) land later.

Contract (ADR-0015): every call carries a window (start,end) + a device/pattern
+ a hard low `limit` (<= MAX_LIMIT). Unfiltered / over-broad calls are rejected
with guidance. Results are capped, carry per-item provenance, and a paging handle.
"""
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from copilot.window import WindowContext

# ponytail: hard cap lives here as a constant, not in config -- ADR-0015 wants a
# *low* ceiling by design and copilot/config.py is another lane's file (F0). Lift
# it to config only if an operator ever needs to tune it.
MAX_LIMIT = 100      # was 50 -- kept low-ish on purpose (ADR-0015 context-size guard, not a
                     # runaway backstop), raised because 50 forced a wasted retry turn whenever
                     # the model's first guess (e.g. ranged=true's natural batch) exceeded it

# untrusted-data delimiters (ADR-0016): evidence content is wrapped so the model
# reads it as data, not instructions.
EVIDENCE_OPEN = "<<evidence>>"
EVIDENCE_CLOSE = "<<end-evidence>>"


class FilterError(ValueError):
    """Raised when a call violates the mandatory-filter contract. The message is
    guidance the agent can act on (ADR-0015: 'specify a device or pattern')."""


class AdapterError(RuntimeError):
    """A transport/backend fault talking to dataapi (connection refused, 5xx, bad body) --
    NOT model-fixable guidance like FilterError. The tools layer converts it into a tool
    observation (registry.dispatch) so a dataapi outage reads as data the model reacts to,
    not an unhandled raise that kills the SSE stream, and never as a false 'unknown device'
    from an empty walk (A1)."""


@dataclass(frozen=True)
class Filters:
    """The narrowing every tool call must carry. `device`/`pattern` are the two
    ways to scope; `offset` drives paging (the handle from a prior Result)."""
    start: int | None = None
    end: int | None = None
    device: str | None = None
    pattern: str | None = None
    limit: int = 10
    offset: int = 0
    t_snapshot: int | None = None   # forensic pin (ADR-0002); None => not frozen, no cap on end
    ranged: bool = False            # #56: metrics only -- return the multi-sample series (trend
                                    # evidence) instead of collapsing to the latest per-series sample

    def validate(self, max_limit: int = MAX_LIMIT) -> None:
        if self.start is None or self.end is None:
            raise FilterError("window required: pass start and end (epoch seconds)")
        if self.start >= self.end:
            raise FilterError("invalid window: start must be < end")
        # forensic freeze guard (ADR-0002): a frozen case is pinned at T_snapshot; reading past
        # it leaks live data into forensic reproduction. On the loop path end == t_snapshot, so
        # this defends the case layer (R5b/#40) that builds Filters directly with a wider end.
        if self.t_snapshot is not None and self.end > self.t_snapshot:
            raise FilterError(
                f"forensic window frozen at T_snapshot={self.t_snapshot}: end={self.end} "
                f"would read live data; pass end <= {self.t_snapshot}")
        if not (self.device or self.pattern):
            raise FilterError("over-broad: specify a device or pattern to narrow the query")
        if self.limit < 1:
            raise FilterError("limit must be >= 1")
        if self.limit > max_limit:
            raise FilterError(
                f"limit {self.limit} exceeds cap {max_limit}: request fewer rows and page for more")
        if self.offset < 0:
            raise FilterError("offset must be >= 0")


@dataclass(frozen=True)
class Evidence:
    """One retrieved item, carrying provenance (ADR-0006) and framed content."""
    id: str
    source: str           # metrics | events | flows
    device: str | None
    ts: int | None        # epoch seconds
    content: str          # framed + sanitized (untrusted)


@dataclass(frozen=True)
class Result:
    evidence: tuple[Evidence, ...] = ()
    next_page: str | None = None   # offset handle for the next page, or None if exhausted


def sanitize(text: str) -> str:
    """Neutralize the obvious breakout: escape the evidence delimiters so injected
    content can't close the frame, and drop ASCII control chars.

    ponytail: light, in-spec injection guard (ADR-0016) -- delimiter-escape +
    control-strip only; the quality gate's citation check is the real backstop.
    Upgrade path = a classifier / dual-LLM sanitizer if the threat model grows."""
    t = text.replace(EVIDENCE_OPEN, "<<evidence-lit>>").replace(EVIDENCE_CLOSE, "<<end-evidence-lit>>")
    return "".join(c for c in t if c in "\t\n" or ord(c) >= 32)


def frame(text: str) -> str:
    """Wrap content as untrusted evidence (ADR-0016)."""
    return f"{EVIDENCE_OPEN}\n{sanitize(text)}\n{EVIDENCE_CLOSE}"


def row_text(row: dict) -> str:
    """Compact `k=v` rendering; the payload (e.g. a log `line`) is where any injected
    instruction would live, so the caller runs the result through frame()/sanitize()."""
    return " ".join(f"{k}={v}" for k, v in row.items())


def payload_matches(row: dict, pattern: str) -> bool:
    """`pattern` substring-narrow, case-insensitive, over the PAYLOAD fields only -- `ts` is
    excluded so a numeric pattern ('443') can't spuriously match an epoch/byte count. Shared
    by both adapters (#119) so the stub's canned rows behave like the real one: `pattern` is a
    literal substring, never a regex/alternation -- 'error|fault|down' matches none of those
    words, it matches the LITERAL string 'error|fault|down'."""
    p = pattern.lower()
    return p in row_text({k: v for k, v in row.items() if k != "ts"}).lower()


def serve_rows(source: str, filters: Filters, rows, max_limit: int = MAX_LIMIT) -> Result:
    """F2's shared read pipeline: validate -> ts-window filter -> page -> provenance -> frame.
    Both adapters ride it so the mandatory-filter/cap/framing guarantees are byte-identical on
    canned (stub) and live (HTTP) data; the two adapters differ ONLY in how they fetch `rows`.

    `rows` is either a list of dicts (stub: canned) or a ZERO-ARG CALLABLE returning them (HTTP:
    the dataapi fetch). validate() runs FIRST, then the callable -- so an over-broad or
    freeze-violating call (ADR-0015/0002) is rejected BEFORE any network read fires; passing an
    already-fetched list would leak a wire read past T_snapshot before the guard bites.

    Rows are plain dicts shaped like a dataapi endpoint: a `device`, a `ts` (epoch seconds), and
    payload fields. A row outside [start,end] -- or with no ts to prove it in-window (as
    gate.pre_gate does over WINDOWED_SOURCES, ADR-0002) -- is not served; paging offsets index
    the IN-WINDOW rows, not raw."""
    filters.validate(max_limit)
    if callable(rows):
        rows = rows()
    rows = [r for r in rows
            if r.get("ts") is not None and filters.start <= r["ts"] <= filters.end]
    window = rows[filters.offset:filters.offset + filters.limit]
    evidence = tuple(
        Evidence(
            id=f"{source}:{i}",
            source=source,
            device=row.get("device"),
            ts=row.get("ts"),
            content=frame(row_text(row)),
        )
        for i, row in enumerate(window, start=filters.offset)
    )
    end = filters.offset + filters.limit
    # #125: landing exactly at the hard cap is indistinguishable from "there were exactly
    # max_limit rows, no more" -- flag it as potentially truncated too (paging in gets a
    # clean "no rows" if there really was nothing more).
    next_page = str(end) if end < len(rows) or len(evidence) == max_limit else None
    return Result(evidence=evidence, next_page=next_page)


def bfs_hops(links, focus: str, n: int) -> dict[str, int]:
    """Deterministic BFS on the undirected topology from `focus` -> {node: hop-distance}
    for every node within `n` hops (focus at hop 0). `links` is the /topology link list --
    this is the ONE place that knows its `{source,target}` shape, so adapters share it and
    callers never touch raw link dicts (ADR-0006). Cheap: ~148 nodes (ADR-0007)."""
    adj: dict[str, set[str]] = {}
    for lk in links:
        a, b = lk["source"], lk["target"]
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    hop = {focus: 0}
    frontier = {focus}
    for d in range(1, n + 1):
        frontier = {nb for node in frontier for nb in adj.get(node, ()) if nb not in hop}
        if not frontier:
            break
        for node in frontier:
            hop[node] = d
    return hop


def hops_within_links(links, focus: str, n: int) -> set[str]:
    """Every node within `n` hops of `focus` (inclusive). I2b's incident hop-filter needs
    only the set; I3's walk needs the distances -> both derive from `bfs_hops`."""
    return set(bfs_hops(links, focus, n))


def known_nodes(nodes, links) -> frozenset[str]:
    """#119: every device id the topology actually knows -- same set walk_topology already
    computes inline to tell a real focus from a fabricated one. Shared here so the read
    tools (query_metrics/search_logs/flows) can give the same 'unknown device' signal
    instead of an ambiguous 'no rows' when a misspelled/nonexistent device is queried."""
    return frozenset({n["id"] for n in nodes} | {x for lk in links for x in (lk["source"], lk["target"])})


@dataclass(frozen=True)
class NodeState:
    """One node in a topology walk (I3): its BFS hop-distance from the focus + a compact
    live-status string enriched from /metrics. The subgraph is a tuple of these."""
    node: str
    hop: int
    status: str


@runtime_checkable
class ToolAdapter(Protocol):
    """The stable seam the investigation tools ride on. Concrete adapters (stub
    now, HTTP later) satisfy it structurally, so swapping is config-only."""

    def metrics(self, filters: Filters) -> Result: ...
    def events(self, filters: Filters) -> Result: ...
    def flows(self, filters: Filters) -> Result: ...
    # Topology proximity (ADR-0007): every node within `n` hops of `focus`, inclusive.
    # The adapter owns the /topology shape (ADR-0006: only this layer knows endpoint
    # shapes) so callers never touch raw link dicts. I2b's incident hop-filter uses it.
    def hops_within(self, focus: str, n: int) -> set[str]: ...
    # #119: every device id the topology knows, so a read tool can tell "unknown device"
    # apart from "device exists, no data this window" instead of both reading as "no rows".
    def known_devices(self) -> frozenset[str]: ...
    # Topology walk (I3, ADR-0007): deterministic BFS on real edges from `focus` within `n`
    # hops, each node enriched with live status from /metrics in `window`. The adapter owns
    # the topology+metrics join (a batched /metrics query per frontier for the HTTP adapter).
    def walk_topology(self, focus: str, n: int, window: WindowContext) -> tuple[NodeState, ...]: ...
