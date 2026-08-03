"""copilot.tools.registry -- the investigation tool table + one dispatch (I1/I2b/I3).

The read tools (query_metrics + search_logs + flows) are the SAME filtered read -- window
+ device/pattern + capped limit + paging (ADR-0015) -- against a different adapter method,
so each is just a (adapter method, description) pair dispatched through one table. The
retrieval tools (search_runbooks/search_incidents, I2b) and the topology walk
(walk_topology_graph, I3) don't fit that shape, so `dispatch` routes them ahead of the table.

ponytail: a dict + getattr, not a plugin system -- three tools that differ only by
which adapter method they hit and share one arg shape. Upgrade to per-tool arg
schemas only when a tool needs args beyond the shared narrowing (device/pattern/
limit/offset).
"""
from dataclasses import dataclass

from copilot.adapter import (
    AdapterError, FilterError, Filters, MAX_LIMIT, Result, ToolAdapter, sanitize,
)
from copilot.retrieval import Hit, Retriever
from copilot.window import WindowContext


@dataclass(frozen=True)
class Cite:
    """One cited evidence item surfaced to the quality gate (I4a, ADR-0008): its citation id
    + the provenance the deterministic pre-gate checks.

    A deliberate content-BLIND projection, not an `Evidence`/`Doc`/`NodeState`: it unifies all
    three source shapes (adapter `Evidence.device`, retrieval `Doc.node`, topology node) into
    one {id, source, device, ts} the gate reasons over, and drops `content` on purpose so the
    gate can't be swayed by untrusted evidence text (ADR-0016) -- it decides on provenance
    only. The tools layer is the ONE place that knows each source's shape (ADR-0006), so it
    builds these; the gate never re-parses rendered observation text."""
    id: str
    source: str            # metrics | events | flows | runbook | incident | topo
    device: str | None
    ts: int | None

# name -> (adapter method, description advertised to the model). The adapter method
# is the seam: swapping stub<->HTTP never touches this table (ADR-0006).
TOOLS: dict[str, tuple[str, str]] = {
    "query_metrics": ("metrics", "Read device metrics within the investigation window."),
    "search_logs": ("events", "Search device logs/events within the investigation window."),
    "flows": ("flows", "Read network flow records within the investigation window."),
}

# I2b retrieval tools: name -> (KB source filter, description). Semantic KB search over
# the I2a Retriever (embedded LanceDB), scoped by provenance -- not the windowed adapter
# read. search_incidents also takes a focus `device` -> topology-hop proximity filter.
RETRIEVAL_TOOLS: dict[str, tuple[str, str]] = {
    "search_runbooks": ("runbook", "Semantic search of the runbook KB for a fault."),
    "search_incidents": ("incident", "Find similar past incidents; pass `device` to keep "
                         "only incidents within a few topology hops of it."),
}

# ponytail: hop radius default lives here as a constant (like adapter.MAX_LIMIT) -- ADR-0007
# wants a small proximity, and config.py is another lane's file. Lift to config if tuned.
DEFAULT_HOPS = 2

# schema advertised to a native function-calling backend. Read tools share one narrowing
# shape (start/end NOT exposed -- the loop owns the window, ADR-0002/0015); the two
# retrieval tools take a free-text `query` (+ k), and search_incidents adds the hop-filter
# args (device focus + radius). Two flat literals -- easier to read than a generated union.
TOOL_SPECS = [{
    "name": name,
    "description": desc,
    "parameters": {"type": "object", "properties": {
        "device": {"type": "string"},
        "pattern": {"type": "string"},
        "limit": {"type": "integer"},
        "offset": {"type": "integer"},
    }},
} for name, (_, desc) in TOOLS.items()] + [
    {"name": "search_runbooks", "description": RETRIEVAL_TOOLS["search_runbooks"][1],
     "parameters": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string"}, "k": {"type": "integer"}}}},
    {"name": "search_incidents", "description": RETRIEVAL_TOOLS["search_incidents"][1],
     "parameters": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string"}, "k": {"type": "integer"},
         "device": {"type": "string"}, "hops": {"type": "integer"}}}},
    {"name": "walk_topology_graph",
     "description": "Blast-radius / downstream: BFS the real topology from a focus `device` "
                    "(within `hops`) and enrich each node with its live status.",
     "parameters": {"type": "object", "required": ["device"], "properties": {
         "device": {"type": "string"}, "hops": {"type": "integer"}}}},
]


def dispatch(name: str, arguments: dict, adapter: ToolAdapter,
             window: WindowContext, retriever: Retriever | None = None,
             kg: dict[str, str] | None = None) -> tuple[str, tuple[Cite, ...]]:
    """Run one tool call: narrow -> validate -> read -> render. Returns
    (observation_text, cites) -- the structured `Cite`s feed the I4a quality gate
    (n_rows = len(cites)). An unknown tool or a FilterError comes back AS the observation
    with no cites (ADR-0015 guidance) so the model can correct and retry, never as a raise.
    """
    if name == "walk_topology_graph":                    # I3: topology walk, not a windowed read
        return _walk(arguments, adapter, window, kg)
    # I2b: KB search is NOT windowed -- KB ts is historical (ADR-0002 §Nuances).
    if name in RETRIEVAL_TOOLS:
        return _retrieve(name, arguments, retriever, adapter)
    entry = TOOLS.get(name)
    if entry is None:                                    # unknown tool -> guidance, no crash
        return f"error: unknown tool {name!r}", ()
    method, _ = entry
    # R3: the window is a WindowContext {start,end,frozen}; frozen/t_snapshot ride into
    # Filters so the forensic freeze guard (ADR-0002) fires at the adapter, one place.
    # coerce args first: a non-int limit/offset from a weak model is guidance, not a
    # crash (ADR-0015). Caught narrowly so a real read error (R1's HTTP adapter) still
    # surfaces instead of masquerading as filter guidance.
    try:
        narrow = {k: arguments[k] for k in ("device", "pattern") if arguments.get(k)}
        for k in ("limit", "offset"):                    # let Filters own the defaults
            if k in arguments:
                narrow[k] = int(arguments[k])
        filters = Filters(start=window.start, end=window.end,
                          t_snapshot=window.t_snapshot, **narrow)
    except ValueError as e:
        return f"error: {e}", ()
    try:
        result = getattr(adapter, method)(filters)
    except FilterError as e:                             # mandatory-filter reject (ADR-0015)
        return f"error: {e}", ()
    except AdapterError as e:                            # dataapi transport fault (A1) -> observation
        return f"error: {e}", ()
    cites = tuple(Cite(ev.id, ev.source, ev.device, ev.ts) for ev in result.evidence)
    return _render(result), cites


def _render(res: Result) -> str:
    if not res.evidence:
        return "no rows"
    lines = [f"[{ev.id}] {ev.content}" for ev in res.evidence]
    if res.next_page:
        lines.append(f"(more: offset={res.next_page})")
    return "\n".join(lines)


def _retrieve(name: str, args: dict, retriever: Retriever | None,
              adapter: ToolAdapter) -> tuple[str, tuple[Cite, ...]]:
    """Run one retrieval tool: (for incidents with a focus device) resolve the topology-hop
    node set -> source+node-scoped KB search (prefiltered, so the top-k is WITHIN scope) ->
    render cited hits. Missing retriever/query and junk k/hops come back AS guidance
    (ADR-0015), never a raise -- a weak model may emit null/list/string for an int.
    """
    if retriever is None:                                # KB unwired (default get_retriever)
        return "error: retrieval backend not available", ()
    query = args.get("query")
    if not query:
        return f"error: {name} needs a 'query' to search for", ()
    source = RETRIEVAL_TOOLS[name][0]

    nodes = None
    focus = args.get("device")
    if name == "search_incidents" and focus:             # hop-proximity narrowing (ADR-0007)
        hops = _hops(args)
        if hops is None:
            return "error: hops must be an integer", ()
        # adapter owns the /topology shape (ADR-0006); node-less incidents fall out of the
        # `node IN (...)` prefilter, so proximity is enforced in the DB, not post-hoc.
        try:
            nodes = adapter.hops_within(focus, hops)
        except AdapterError as e:                        # /topology transport fault (A1) -> observation
            return f"error: {e}", ()
    try:
        k = int(args["k"]) if "k" in args else 5
    except (TypeError, ValueError):
        return "error: k must be an integer", ()
    k = max(1, min(k, MAX_LIMIT))                        # same low ceiling as the read cap
    hits = retriever.search(query, k=k, source=source, nodes=nodes)
    # KB ts is HISTORICAL (the past incident's time), so the gate exempts runbook/incident
    # from its window check (ADR-0008) -- source rides each Cite to make that distinction.
    cites = tuple(Cite(h.doc.id, h.doc.source, h.doc.node, h.doc.ts) for h in hits)
    return _render_hits(hits), cites


def _walk(args: dict, adapter: ToolAdapter, window: WindowContext,
          kg: dict[str, str] | None) -> tuple[str, tuple[Cite, ...]]:
    """I3 topology walk (ADR-0007): BFS the real edges from a focus `device` + per-node live
    status (the adapter owns the topology+/metrics join). The curated KG, if enabled, only
    APPENDS a hint per node -- structure + status come from real topology+metrics alone, so
    correctness is identical with it off (never load-bearing). Missing device / bad hops come
    back AS guidance (ADR-0015), never a raise.
    """
    focus = args.get("device")
    if not focus:
        return "error: walk_topology_graph needs a focus 'device'", ()
    hops = _hops(args)
    if hops is None:
        return "error: hops must be an integer", ()
    try:
        states = adapter.walk_topology(focus, hops, window)
    except AdapterError as e:                            # dataapi fault (A1): observation, NOT a
        return f"error: {e}", ()                         # false 'unknown device' from an empty walk
    if not states:                                       # unknown focus -> no fabricated subgraph
        return f"error: unknown device {focus!r}: not in the topology", ()
    lines = []
    for s in states:
        # [topo:node] is the citable id (the gate checks citations, I4a); status is already
        # sanitized at the adapter (ADR-0016). KG hint (if enabled) is appended, additive-only.
        line = f"[topo:{s.node}] hop {s.hop}: {s.status}"
        if kg and s.node in kg:                          # never load-bearing (ADR-0007)
            line += f"  [kg: {sanitize(kg[s.node])}]"
        lines.append(line)
    # topology nodes carry no ts -> the gate exempts source "topo" from the window check.
    cites = tuple(Cite(f"topo:{s.node}", "topo", s.node, None) for s in states)
    return "\n".join(lines), cites


def _hops(args: dict) -> int | None:
    """Coerce the optional `hops` arg (default DEFAULT_HOPS); None if it's junk (a weak model
    may emit null/list/string) so the caller returns guidance, never a raise (ADR-0015)."""
    try:
        return int(args["hops"]) if "hops" in args else DEFAULT_HOPS
    except (TypeError, ValueError):
        return None


def _render_hits(hits: list[Hit]) -> str:
    if not hits:
        return "no matches"
    # [id] cites the doc (the gate checks citations, I4a); full provenance triple (source,
    # node, ts) rides each line -- required by ADR-0006/the gate. sanitize the KB text --
    # incidents can carry log excerpts (untrusted, ADR-0016).
    return "\n".join(
        f"[{h.doc.id}] {sanitize(h.doc.text)} "
        f"(source={h.doc.source} node={h.doc.node} ts={h.doc.ts} score={h.score:.2f})"
        for h in hits)
