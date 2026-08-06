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
import dataclasses
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
    "query_metrics": ("metrics", "Read device metrics within the investigation window. A bare "
                      "`device` with no `pattern` returns BOTH hardware telemetry (fan/power/psu/"
                      "temp) and network telemetry (sdwan_tunnel_latency_ms/jitter_ms/loss_pct, "
                      "interface counters) interleaved -- hardware rows can fill the row cap before "
                      "any network metric appears. For a network-health question, pass `pattern` "
                      "(e.g. 'latency', 'jitter', 'loss', 'tunnel') to target it directly. Pass "
                      "ranged=true for a multi-sample trend series (cited evidence for "
                      "'ramped/climbed over N min' claims), not just the latest point. Charting "
                      "is out of scope -- Grafana owns charts (#56)."),
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
# #125: hops had no ceiling -- hops=1000 on the real 148-node topology dumped every node in
# one ~44.7KB observation (79s response). 10 hops comfortably covers any real blast-radius
# question on this lab's topology while still bounding a pathological request.
MAX_HOPS = 10

# schema advertised to a native function-calling backend. Read tools share one narrowing
# shape (start/end NOT exposed -- the loop owns the window, ADR-0002/0015); the two
# retrieval tools take a free-text `query` (+ k), and search_incidents adds the hop-filter
# args (device focus + radius). Two flat literals -- easier to read than a generated union.
TOOL_SPECS = [{
    "name": name,
    "description": desc,
    "parameters": {"type": "object", "properties": {
        "device": {"type": "string"},
        "pattern": {"type": "string", "description": "Case-insensitive literal substring match "
                   "(not a regex) -- 'error|fault|down' matches only that literal string, never "
                   "an alternation of the three words."},
        "limit": {"type": "integer"},
        "offset": {"type": "integer"},
        # ranged is metrics-only trend evidence (#56) -- advertising it on events/flows, which
        # already return multi-row, would just mislead the model.
        **({"ranged": {"type": "boolean"}} if name == "query_metrics" else {}),
    }},
} for name, (_, desc) in TOOLS.items()] + [
    {"name": "walk_topology_graph",
     "description": "Blast-radius / downstream: BFS the real topology from a focus `device` "
                    "(within `hops`) and enrich each node with its live status.",
     "parameters": {"type": "object", "required": ["device"], "properties": {
         "device": {"type": "string"}, "hops": {"type": "integer"}}}},
]

# #122: separated from TOOL_SPECS so the loop advertises these only when a retriever is
# wired (mirrors BASH_SPEC/PRESENT_SPEC) -- with no retriever, dispatch always answered
# "error: retrieval backend not available" and the model kept re-probing a dead backend.
RETRIEVAL_SPECS = [
    {"name": "search_runbooks", "description": RETRIEVAL_TOOLS["search_runbooks"][1],
     "parameters": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string"}, "k": {"type": "integer"}}}},
    {"name": "search_incidents", "description": RETRIEVAL_TOOLS["search_incidents"][1],
     "parameters": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string"}, "k": {"type": "integer"},
         "device": {"type": "string"}, "hops": {"type": "integer"}}}},
]


def dispatch(name: str, arguments: dict, adapter: ToolAdapter,
             window: WindowContext, retriever: Retriever | None = None,
             kg: dict[str, str] | None = None,
             call_index: int | None = None) -> tuple[str, tuple[Cite, ...]]:
    """Run one tool call: narrow -> validate -> read -> render. Returns
    (observation_text, cites) -- the structured `Cite`s feed the I4a quality gate
    (n_rows = len(cites)). An unknown tool or a FilterError comes back AS the observation
    with no cites (ADR-0015 guidance) so the model can correct and retry, never as a raise.

    `call_index` (#124, the loop's per-call counter) namespaces read-tool ids ("metrics:0"
    restarts at 0 every call, so two query_metrics calls in one investigation both cite
    "metrics:0" -- ambiguous which call's row is meant). None (default) leaves ids unchanged;
    retrieval/topology ids are already globally stable (doc id / device name) and untouched.
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
        # ranged (#56, metrics-only): opt-in trend series. A weak model may send a JSON string;
        # treat only real truthy / "true"/"1" as on so "false"/"0" doesn't read as on (ADR-0015).
        ranged = str(arguments.get("ranged")).lower() in ("true", "1")
        # a ranged read with no explicit limit must not clip to Filters' default (10): opting into
        # a trend means wanting the samples, so raise the default to the cap (still model-override-
        # able, still <= MAX_LIMIT). Otherwise "ramped over N min" evidence arrives truncated.
        if ranged and "limit" not in narrow:
            narrow["limit"] = MAX_LIMIT
        filters = Filters(start=window.start, end=window.end, ranged=ranged,
                          t_snapshot=window.t_snapshot, **narrow)
    except ValueError as e:
        return f"error: {e}", ()
    try:
        result = getattr(adapter, method)(filters)
    except FilterError as e:                             # mandatory-filter reject (ADR-0015)
        return f"error: {e}", ()
    except AdapterError as e:                            # dataapi transport fault (A1) -> observation
        return f"error: {e}", ()
    if call_index is not None:                            # #124: namespace ids by call
        result = dataclasses.replace(result, evidence=tuple(
            dataclasses.replace(ev, id=f"{ev.source}@{call_index}:{ev.id.split(':', 1)[1]}")
            for ev in result.evidence))
    # #119: "no rows" alone doesn't say WHY -- unknown device, a real device with genuinely no
    # data this window, or a malformed pattern all looked identical, so a misspelled device
    # burned 2-6 wasted reads before the model happened to call walk_topology_graph (the only
    # tool that gave this signal). Same topology data that tool already uses. An empty/unreach-
    # able topology can't prove absence, so it never asserts "unknown" in that case.
    if not result.evidence and narrow.get("device"):
        try:
            known = adapter.known_devices()
        except AdapterError:
            known = frozenset()
        if known and narrow["device"] not in known:
            return f"error: unknown device {narrow['device']!r}: not in the topology", ()
    cites = tuple(Cite(ev.id, ev.source, ev.device, ev.ts) for ev in result.evidence)
    return _render(result, narrow), cites


def _render(res: Result, narrow: dict | None = None) -> str:
    if not res.evidence:
        # echo the effective filters so a malformed pattern (or the wrong device) is visible
        # instead of silently indistinguishable from a clean negative result.
        filt = " ".join(f"{k}={narrow[k]!r}" for k in ("device", "pattern") if narrow and narrow.get(k))
        return f"no rows for {filt}" if filt else "no rows"
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
    # #117: a total the model can mechanically compare its own count claim against, instead of
    # trusting it to tally a one-per-line enumeration itself (audit runs invented totals).
    hop_counts: dict[int, int] = {}
    for s in states:
        hop_counts[s.hop] = hop_counts.get(s.hop, 0) + 1
    header = "total={} ({})".format(
        len(states), " ".join(f"hop{h}={hop_counts[h]}" for h in sorted(hop_counts)))
    lines = [header]
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
        hops = int(args["hops"]) if "hops" in args else DEFAULT_HOPS
    except (TypeError, ValueError):
        return None
    return min(hops, MAX_HOPS)


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
