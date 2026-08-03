"""copilot.workspace.policy -- path policy + per-session workspace dirs (B0, ADR-0011/0013).

Isolation is the file-handling policy at the tool layer, NOT a container (ADR-0013):
writes and execution are confined to the per-session `scratchpad/`; everything outside is
read-only; to change external data you copy it in and edit the copy. This module owns the
`scratchpad/`+`artifacts/` dirs (under the R2a session dir, `sessions/<id>/`) and the one
containment check the workspace tools gate every write/exec on -- B1 (#29) read/write/edit,
B2/B3 (#30-32) exec cwd. Reads are unrestricted here: read-only-outside == only writes are
gated, so a read tool needs no path check.

ponytail: an allowlist of one dir + realpath containment, no kernel jail. Harden to
container/seccomp only if deployed adversarially (ADR-0013 rejected-alternatives).

Self-check: python3 copilot/workspace/policy.py
"""
import os
import re


class PathPolicyError(Exception):
    """A write/exec was aimed outside the session scratchpad. Workspace tools return this to
    the model (a rejected call it can retry with an in-scratchpad path -- copy-in-to-modify)."""


class Workspace:
    """The per-session cage: `scratchpad/` (writable/executable) + `artifacts/` (append-only
    presented outputs, B3b) under one session dir. `make()` creates them; `writable()` is the
    boundary the tool layer enforces before any write."""

    def __init__(self, session_dir: str):
        self.session_dir = session_dir
        self.scratchpad = os.path.join(session_dir, "scratchpad")
        self.artifacts = os.path.join(session_dir, "artifacts")

    def make(self) -> "Workspace":
        """Create scratchpad/ + artifacts/. Idempotent -- a resumed session re-uses them
        (ADR-0013: never auto-wiped)."""
        os.makedirs(self.scratchpad, exist_ok=True)
        os.makedirs(self.artifacts, exist_ok=True)
        return self

    def writable(self, path: str) -> str:
        """Resolve `path` (relative -> under scratchpad) and return its absolute real path IFF
        it lands inside scratchpad/; else PathPolicyError. `os.path.realpath` collapses `..`
        AND follows symlinks on the existing parents, so neither a `../escape` nor a symlink
        planted in the scratchpad can point a write outside the cage (ADR-0011 boundary)."""
        root = os.path.realpath(self.scratchpad)
        target = path if os.path.isabs(path) else os.path.join(self.scratchpad, path)
        full = os.path.realpath(target)
        if full != root and not full.startswith(root + os.sep):
            raise PathPolicyError(f"write outside scratchpad rejected: {path!r}")
        return full


def artifact_path(sessions_root: str, sid: str, name: str) -> str:
    """Map an UNTRUSTED (sid, name) to a real file under sessions/<sid>/artifacts/, or
    PathPolicyError (#54: the static-serve GET turns it into a 404, never a traversal read).
    Sanitise each segment then realpath-contain -- mirrors resolve_case_dir (forensic/chat.py):
    the charset keeps `.`/`..`, so the equality + strict-prefix check is what actually blocks a
    `..` segment or a symlink planted in artifacts/ from escaping. Read-only: only serves an
    existing file inside artifacts/ (a browser render target), never writes."""
    root = os.path.realpath(sessions_root)
    safe = [re.sub(r"[^A-Za-z0-9._-]", "_", s or "") for s in (sid, name)]
    if any(not s or s in (".", "..") for s in safe):
        raise PathPolicyError(f"unknown artifact: {safe[0]}/{safe[1]}")
    want = os.path.join(root, safe[0], "artifacts", safe[1])
    full = os.path.realpath(want)
    if not (full == want and full.startswith(root + os.sep) and os.path.isfile(full)):
        raise PathPolicyError(f"unknown artifact: {safe[0]}/{safe[1]}")
    return full


def for_session(sessions_root: str, sid: str) -> Workspace:
    """Workspace for `sid`, rooted at `sessions_root/<sid>/` -- the same layout SessionStore
    uses (memory/session.py) so a session's log and its scratchpad live together. Dirs made."""
    return Workspace(os.path.join(sessions_root, sid)).make()


def _selfcheck():
    import shutil
    import tempfile

    root = tempfile.mkdtemp()
    try:
        ws = for_session(root, "s1")
        assert os.path.isdir(ws.scratchpad) and os.path.isdir(ws.artifacts), "dirs not made"
        assert for_session(root, "s1").scratchpad == ws.scratchpad, "not idempotent"

        # in-bounds writes: relative, nested, and an absolute path already inside.
        assert ws.writable("out.txt") == os.path.realpath(os.path.join(ws.scratchpad, "out.txt"))
        assert ws.writable("sub/deep/x").endswith("/sub/deep/x")
        inside = os.path.join(ws.scratchpad, "abs.txt")
        assert ws.writable(inside) == os.path.realpath(inside)

        # out-of-bounds writes are rejected.
        for bad in ("../escape.txt", "../../etc/passwd", "/etc/passwd",
                    "sub/../../sibling", os.path.join(ws.artifacts, "a")):
            try:
                ws.writable(bad)
            except PathPolicyError:
                pass
            else:
                raise AssertionError(f"expected rejection for {bad!r}")

        # a symlink planted in scratchpad can't be used to escape it.
        link = os.path.join(ws.scratchpad, "link")
        os.symlink("/etc", link)
        try:
            ws.writable("link/passwd")
        except PathPolicyError:
            pass
        else:
            raise AssertionError("symlink escape not rejected")

        # scratchpad prefix isn't fooled by a sibling sharing the name prefix.
        sibling = ws.scratchpad + "_evil"
        os.makedirs(sibling, exist_ok=True)
        try:
            ws.writable(os.path.join(sibling, "x"))
        except PathPolicyError:
            pass
        else:
            raise AssertionError("prefix-sibling escape not rejected")

        # artifact_path (#54): serves a real file inside artifacts/, rejects every escape.
        with open(os.path.join(ws.artifacts, "0000-c.png"), "wb") as f:
            f.write(b"PNG")
        assert artifact_path(root, "s1", "0000-c.png") == os.path.realpath(
            os.path.join(ws.artifacts, "0000-c.png")), "serves an in-bounds artifact"
        alink = os.path.join(ws.artifacts, "esc")        # symlink in artifacts/ can't escape
        os.symlink("/etc", alink)
        for bad_sid, bad_name in (("s1", "../../etc/passwd"), ("s1", ".."), ("..", "0000-c.png"),
                                  ("s1", "/etc/passwd"), ("s1", "esc"), ("s1", "nope.png"),
                                  ("s1", "")):
            try:
                artifact_path(root, bad_sid, bad_name)
            except PathPolicyError:
                pass
            else:
                raise AssertionError(f"expected rejection for {bad_sid}/{bad_name!r}")

        print("copilot.workspace.policy self-check OK")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    _selfcheck()
