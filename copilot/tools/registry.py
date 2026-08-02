"""copilot.tools.registry -- the investigation tool table + one dispatch (I1).

Every investigation tool is the SAME filtered read -- window + device/pattern +
capped limit + paging (ADR-0015) -- against a different adapter method. So a tool is
just a (adapter method, description) pair, and the loop dispatches every call through
one table instead of a per-tool branch. query_metrics + search_logs + flows ride it
today; I3's topology walk and the search_* retrieval tools register here later.

ponytail: a dict + getattr, not a plugin system -- three tools that differ only by
which adapter method they hit and share one arg shape. Upgrade to per-tool arg
schemas only when a tool needs args beyond the shared narrowing (device/pattern/
limit/offset).
"""
from copilot.adapter import FilterError, Filters, MAX_LIMIT, Result, ToolAdapter, sanitize
from copilot.retrieval import Hit, Retriever

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
]


def dispatch(name: str, arguments: dict, adapter: ToolAdapter,
             window: tuple[int, int], retriever: Retriever | None = None) -> tuple[str, int]:
    """Run one tool call: narrow -> validate -> read -> render. Returns
    (observation_text, n_rows). An unknown tool or a FilterError comes back AS the
    observation (ADR-0015 guidance) so the model can correct and retry, never as a raise.
    """
    if name in RETRIEVAL_TOOLS:                          # I2b: KB search, not a windowed read
        return _retrieve(name, arguments, retriever, adapter)
    entry = TOOLS.get(name)
    if entry is None:                                    # unknown tool -> guidance, no crash
        return f"error: unknown tool {name!r}", 0
    method, _ = entry
    # ponytail: window is a bare (start,end) pair (F3 basic form); R3 swaps in the full
    # WindowContext {start,end,frozen} + the forensic end-freeze guard (ADR-0002).
    start, end = window
    # coerce args first: a non-int limit/offset from a weak model is guidance, not a
    # crash (ADR-0015). Caught narrowly so a real read error (R1's HTTP adapter) still
    # surfaces instead of masquerading as filter guidance.
    try:
        narrow = {k: arguments[k] for k in ("device", "pattern") if arguments.get(k)}
        for k in ("limit", "offset"):                    # let Filters own the defaults
            if k in arguments:
                narrow[k] = int(arguments[k])
        filters = Filters(start=start, end=end, **narrow)
    except ValueError as e:
        return f"error: {e}", 0
    try:
        result = getattr(adapter, method)(filters)
    except FilterError as e:                             # mandatory-filter reject (ADR-0015)
        return f"error: {e}", 0
    return _render(result), len(result.evidence)


def _render(res: Result) -> str:
    if not res.evidence:
        return "no rows"
    lines = [f"[{ev.id}] {ev.content}" for ev in res.evidence]
    if res.next_page:
        lines.append(f"(more: offset={res.next_page})")
    return "\n".join(lines)


def _retrieve(name: str, args: dict, retriever: Retriever | None,
              adapter: ToolAdapter) -> tuple[str, int]:
    """Run one retrieval tool: (for incidents with a focus device) resolve the topology-hop
    node set -> source+node-scoped KB search (prefiltered, so the top-k is WITHIN scope) ->
    render cited hits. Missing retriever/query and junk k/hops come back AS guidance
    (ADR-0015), never a raise -- a weak model may emit null/list/string for an int.
    """
    if retriever is None:                                # KB unwired (default get_retriever)
        return "error: retrieval backend not available", 0
    query = args.get("query")
    if not query:
        return f"error: {name} needs a 'query' to search for", 0
    source = RETRIEVAL_TOOLS[name][0]

    nodes = None
    focus = args.get("device")
    if name == "search_incidents" and focus:             # hop-proximity narrowing (ADR-0007)
        try:
            hops = int(args["hops"]) if "hops" in args else DEFAULT_HOPS
        except (TypeError, ValueError):
            return "error: hops must be an integer", 0
        # adapter owns the /topology shape (ADR-0006); node-less incidents fall out of the
        # `node IN (...)` prefilter, so proximity is enforced in the DB, not post-hoc.
        nodes = adapter.hops_within(focus, hops)
    try:
        k = int(args["k"]) if "k" in args else 5
    except (TypeError, ValueError):
        return "error: k must be an integer", 0
    k = max(1, min(k, MAX_LIMIT))                        # same low ceiling as the read cap
    hits = retriever.search(query, k=k, source=source, nodes=nodes)
    return _render_hits(hits), len(hits)


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
