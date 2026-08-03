"""copilot.adapter -- Lane-Investigation (F2/I-lane). Tool adapter over dataapi: filters + caps + injection guard (ADR-0006/0015/0016).

F2: the `ToolAdapter` seam + mandatory-filter contract (`Filters`) + result shapes
(`Result`/`Evidence`) + untrusted-data framing + a canned `StubAdapter` (test double).
A1 (#40): `HttpAdapter` -- the real read over a live dataapi + the topology walk --
riding F2's shared `serve_rows` pipeline. `AdapterError` = a transport fault the tools
layer turns into an observation, not a raise.
"""
from copilot.adapter.contract import (
    EVIDENCE_CLOSE, EVIDENCE_OPEN, MAX_LIMIT, AdapterError, Evidence, FilterError, Filters,
    NodeState, Result, ToolAdapter, bfs_hops, frame, hops_within_links, row_text, sanitize,
    serve_rows,
)
from copilot.adapter.http import HttpAdapter
from copilot.adapter.stub import StubAdapter

__all__ = [
    "ToolAdapter", "StubAdapter", "HttpAdapter", "Filters", "FilterError", "AdapterError",
    "Result", "Evidence", "NodeState", "frame", "sanitize", "serve_rows", "row_text",
    "bfs_hops", "hops_within_links", "EVIDENCE_OPEN", "EVIDENCE_CLOSE", "MAX_LIMIT",
]
