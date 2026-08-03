"""copilot.workspace -- the scoped coding-agent cage (Milestone B, ADR-0011/0013).

B0 (#28): per-session scratchpad/artifacts dirs + the path policy the workspace tools
gate writes/exec on. B1 (#29) adds read/write/edit; B2/B3 add the exec subprocess.
"""
from copilot.workspace.policy import PathPolicyError, Workspace, for_session

__all__ = ["PathPolicyError", "Workspace", "for_session"]
