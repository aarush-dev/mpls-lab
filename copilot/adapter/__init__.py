"""copilot.adapter -- Lane-Investigation (F2/I-lane). Tool adapter over dataapi: filters + caps + injection guard (ADR-0006/0015/0016).

F2: the `ToolAdapter` seam + mandatory-filter contract (`Filters`) + result shapes
(`Result`/`Evidence`) + untrusted-data framing + a canned `StubAdapter` (stub
boundary 2). Real HTTP adapter and the topology walk (I3) land later. Owner lane
builds here; do not edit from the other lane.
"""
from copilot.adapter.contract import (
    EVIDENCE_CLOSE, EVIDENCE_OPEN, MAX_LIMIT, Evidence, FilterError, Filters,
    Result, ToolAdapter, frame, hops_within_links, sanitize,
)
from copilot.adapter.stub import StubAdapter

__all__ = [
    "ToolAdapter", "StubAdapter", "Filters", "FilterError", "Result", "Evidence",
    "frame", "sanitize", "hops_within_links", "EVIDENCE_OPEN", "EVIDENCE_CLOSE", "MAX_LIMIT",
]
