"""copilot.workspace -- the scoped coding-agent cage (Milestone B, ADR-0011/0013).

B0 (#28): per-session scratchpad/artifacts dirs + the path policy the workspace tools
gate writes/exec on. B1 (#29): read/write/edit + little-coder invariants (WorkspaceTools).
B2 (#30): Executor -- constrained subprocess (no-net, timeout, cwd). B3a (#31) wires the bash
tool. B3b (#32): snapshot() -- snapshot-on-present into artifacts/ + the `artifact` event payload.
"""
from copilot.workspace.executor import ExecResult, Executor
from copilot.workspace.policy import PathPolicyError, Workspace, for_session
from copilot.workspace.present import Artifact, snapshot
from copilot.workspace.tools import WorkspaceTools

__all__ = ["PathPolicyError", "Workspace", "for_session", "WorkspaceTools",
           "Executor", "ExecResult", "Artifact", "snapshot"]
