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
from copilot.adapter import FilterError, Filters, Result, ToolAdapter

# name -> (adapter method, description advertised to the model). The adapter method
# is the seam: swapping stub<->HTTP never touches this table (ADR-0006).
TOOLS: dict[str, tuple[str, str]] = {
    "query_metrics": ("metrics", "Read device metrics within the investigation window."),
    "search_logs": ("events", "Search device logs/events within the investigation window."),
    "flows": ("flows", "Read network flow records within the investigation window."),
}

# schema advertised to a native function-calling backend. start/end are NOT exposed --
# the loop supplies the window, the model only narrows within it (ADR-0002/0015). All
# tools share one arg shape, so the spec is generated, not hand-repeated.
TOOL_SPECS = [{
    "name": name,
    "description": desc,
    "parameters": {
        "type": "object",
        "properties": {
            "device": {"type": "string"},
            "pattern": {"type": "string"},
            "limit": {"type": "integer"},
            "offset": {"type": "integer"},
        },
    },
} for name, (_, desc) in TOOLS.items()]


def dispatch(name: str, arguments: dict, adapter: ToolAdapter,
             window: tuple[int, int]) -> tuple[str, int]:
    """Run one tool call: narrow -> validate -> read -> render. Returns
    (observation_text, n_rows). An unknown tool or a FilterError comes back AS the
    observation (ADR-0015 guidance) so the model can correct and retry, never as a raise.
    """
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
