"""copilot.tools -- Lane-Investigation (I1/I3). The investigation tool registry.

I1: query_metrics + search_logs + flows on the F2 adapter contract -- one table
(`TOOLS`), one generated schema (`TOOL_SPECS`), one `dispatch` the loop routes every
call through. Same mandatory filter + caps + provenance + paging as F2 (ADR-0006/0015).
I3's topology walk and the search_* retrieval tools register here later. Owner lane
builds here; do not edit from the other lane.
"""
from copilot.tools.registry import TOOLS, TOOL_SPECS, dispatch

__all__ = ["TOOLS", "TOOL_SPECS", "dispatch"]
