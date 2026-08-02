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

# ponytail: hard cap lives here as a constant, not in config -- ADR-0015 wants a
# *low* ceiling by design and copilot/config.py is another lane's file (F0). Lift
# it to config only if an operator ever needs to tune it.
MAX_LIMIT = 50

# untrusted-data delimiters (ADR-0016): evidence content is wrapped so the model
# reads it as data, not instructions.
EVIDENCE_OPEN = "<<evidence>>"
EVIDENCE_CLOSE = "<<end-evidence>>"


class FilterError(ValueError):
    """Raised when a call violates the mandatory-filter contract. The message is
    guidance the agent can act on (ADR-0015: 'specify a device or pattern')."""


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

    def validate(self, max_limit: int = MAX_LIMIT) -> None:
        if self.start is None or self.end is None:
            raise FilterError("window required: pass start and end (epoch seconds)")
        if self.start >= self.end:
            raise FilterError("invalid window: start must be < end")
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


def hops_within_links(links, focus: str, n: int) -> set[str]:
    """BFS the undirected topology from `focus`, returning every node within `n` hops
    (inclusive of focus at hop 0). `links` is the /topology link list -- this is the ONE
    place that knows its `{source,target}` shape, so adapters share it and callers don't.
    Cheap: ~148 nodes (ADR-0007)."""
    adj: dict[str, set[str]] = {}
    for lk in links:
        a, b = lk["source"], lk["target"]
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    seen, frontier = {focus}, {focus}
    for _ in range(n):
        frontier = {nb for node in frontier for nb in adj.get(node, ()) if nb not in seen}
        if not frontier:
            break
        seen |= frontier
    return seen


@runtime_checkable
class ToolAdapter(Protocol):
    """The stable seam the investigation tools ride on. Concrete adapters (stub
    now, HTTP later) satisfy it structurally, so swapping is config-only."""

    def metrics(self, filters: Filters) -> Result: ...
    def events(self, filters: Filters) -> Result: ...
    def flows(self, filters: Filters) -> Result: ...
    # Topology proximity (ADR-0007): every node within `n` hops of `focus`, inclusive.
    # The adapter owns the /topology shape (ADR-0006: only this layer knows endpoint
    # shapes) so callers never touch raw link dicts. I2b's incident hop-filter uses it;
    # I3's walk_topology_graph builds BFS + /metrics enrich on the same wiring.
    def hops_within(self, focus: str, n: int) -> set[str]: ...
