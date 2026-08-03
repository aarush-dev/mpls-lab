"""copilot.workspace.tools -- the read/write/edit workspace tools + little-coder invariants
(B1, ADR-0011).

Three tools over the B0 cage (policy.Workspace). ADR-0011 adopts little-coder's *invariants*
and rejects its permission model:

  - read-before-edit  -- edit refuses a path not read this session (self._read).
  - write = new only  -- write refuses an existing file and returns the edit call to make instead.
  - edit = exact match -- old_string matched verbatim (whitespace incl.); ambiguous match needs
    `replace_all` or more context, so an edit never silently hits the wrong span.

Reads are unrestricted (ADR-0011: read-only-OUTSIDE == only writes are gated), so `read` takes
any path; writes/edits go through `Workspace.writable` (B0), which confines them to scratchpad/
and is how copy-in-to-modify works: read an external file -> write a copy into scratchpad ->
edit the copy. read-before-edit state is per WorkspaceTools instance == per session (B3a hands
one to the loop), so it resets when the session ends.

Errors come back AS a string, never raised (ADR-0015, like tools/registry.py): a weak model
gets a guidance line it can act on (the refused write returns "use edit", a missing edit target
returns "read it first"), not a stack trace that aborts the turn.

ponytail: a set of read paths + three thin file ops, no journal/undo. Add versioning only if a
tool ever needs to roll an edit back (B3b snapshots presented files, a different concern).

Self-check: python3 -m copilot.workspace.tools
"""
import os

from copilot.workspace.policy import PathPolicyError, Workspace


class WorkspaceTools:
    """Per-session read/write/edit over one `Workspace`. Holds the set of realpaths read this
    session -- the read-before-edit gate. One instance per session (B3a wires it into the loop);
    a new session starts with an empty read set."""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self._read: set[str] = set()          # realpaths seen this session (read or written)

    def read(self, path: str) -> str:
        """Return the file's text and record it as read this session (unlocking a later edit).
        Path unrestricted -- reads outside scratchpad are allowed (read-only-outside, ADR-0011);
        this is the read half of copy-in-to-modify."""
        full = self._resolve(path)
        try:
            with open(full) as f:
                text = f.read()
        except (FileNotFoundError, IsADirectoryError, PermissionError) as e:
            return f"error: cannot read {path!r}: {e.strerror or e}"
        self._read.add(full)
        return text

    def write(self, path: str, content: str) -> str:
        """Create a NEW file inside scratchpad. Refuses an existing file and returns the edit
        call to make instead (write = new only, ADR-0011). Path gated to scratchpad by B0."""
        try:
            full = self.ws.writable(path)
        except PathPolicyError as e:
            return f"error: {e}"
        if os.path.exists(full):
            return (f"error: {path!r} exists; write refuses existing files -- "
                    f"edit(path={path!r}, old_string=..., new_string=...) instead")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        self._read.add(full)                  # you wrote it, so it's seen -> editable next
        return f"wrote {path!r} ({len(content)} bytes)"

    def edit(self, path: str, old_string: str, new_string: str,
             replace_all: bool = False) -> str:
        """Replace `old_string` with `new_string` in a scratchpad file already read this session.
        Exact match, whitespace included; a non-unique match needs `replace_all` or more context
        (edit = exact match, ADR-0011). Path gated to scratchpad by B0."""
        try:
            full = self.ws.writable(path)
        except PathPolicyError as e:
            return f"error: {e}"
        if full not in self._read:
            return f"error: read {path!r} before editing it (read-before-edit)"
        try:
            with open(full) as f:
                text = f.read()
        except (FileNotFoundError, IsADirectoryError, PermissionError) as e:
            return f"error: cannot edit {path!r}: {e.strerror or e}"
        if not old_string:
            return f"error: old_string is empty; pass the exact text to replace in {path!r}"
        n = text.count(old_string)
        if n == 0:
            return f"error: old_string not found in {path!r}"
        if n > 1 and not replace_all:
            return (f"error: old_string matches {n} places in {path!r}; add surrounding "
                    f"context to make it unique, or pass replace_all=true")
        text = text.replace(old_string, new_string) if replace_all \
            else text.replace(old_string, new_string, 1)
        with open(full, "w") as f:
            f.write(text)
        how_many = f"{n} occurrences" if replace_all else "1 occurrence"
        return f"edited {path!r} ({how_many})"

    def _resolve(self, path: str) -> str:
        """Absolute realpath for a read: relative paths resolve under scratchpad (the cwd the
        model works in); absolute paths pass through (reads are unrestricted)."""
        target = path if os.path.isabs(path) else os.path.join(self.ws.scratchpad, path)
        return os.path.realpath(target)


def _selfcheck():
    import shutil
    import tempfile

    from copilot.workspace.policy import for_session

    root = tempfile.mkdtemp()
    try:
        tools = WorkspaceTools(for_session(root, "s1"))

        # write = new only: first write lands, second refuses and points at edit.
        assert tools.write("a.txt", "hello world").startswith("wrote"), "new write should land"
        assert os.path.exists(os.path.join(tools.ws.scratchpad, "a.txt")), "file not created"
        refuse = tools.write("a.txt", "again")
        assert "exists" in refuse and "edit(" in refuse, "existing write must refuse + return edit"
        assert tools.read("a.txt") == "hello world", "refused write must not have overwritten"

        # read-before-edit: an unread file can't be edited; reading unlocks it.
        with open(os.path.join(tools.ws.scratchpad, "b.txt"), "w") as f:
            f.write("x=1\n")
        assert "read-before-edit" in tools.edit("b.txt", "x=1", "x=2"), "unread edit must block"
        assert tools.read("b.txt") == "x=1\n"
        assert tools.edit("b.txt", "x=1", "x=2").startswith("edited"), "read file should edit"
        assert tools.read("b.txt") == "x=2\n", "edit did not apply"

        # a written file is seen -> immediately editable (no separate read).
        assert tools.edit("a.txt", "world", "there").startswith("edited"), "written file editable"
        assert tools.read("a.txt") == "hello there"

        # edit = exact match: missing string and ambiguous match both refuse.
        assert "not found" in tools.edit("a.txt", "nope", "x"), "missing old_string must refuse"
        assert "empty" in tools.edit("a.txt", "", "x"), "empty old_string must refuse"
        tools.write("dup.txt", "aa")
        assert "matches 2" in tools.edit("dup.txt", "a", "b"), "ambiguous edit must refuse"
        assert tools.edit("dup.txt", "a", "b", replace_all=True).startswith("edited"), "replace_all"
        assert tools.read("dup.txt") == "bb", "replace_all did not replace both"

        # writes/edits are caged to scratchpad (B0 boundary), returned as guidance not a raise.
        assert "error" in tools.write("../escape.txt", "no"), "outside write must be refused"
        assert "error" in tools.edit("../../etc/passwd", "root", "pwn"), "outside edit must refuse"

        # copy-in-to-modify: read an external file, write a copy in, edit the copy.
        ext = os.path.join(root, "external.conf")
        with open(ext, "w") as f:
            f.write("mtu 1500\n")
        original = tools.read(ext)                         # unrestricted read outside scratchpad
        assert original == "mtu 1500\n"
        tools.write("copy.conf", original)
        assert tools.edit("copy.conf", "1500", "9000").startswith("edited"), "edit the copy"
        assert open(ext).read() == "mtu 1500\n", "external file must be untouched"

        # a fresh session does not inherit the read set (read-before-edit is per session).
        fresh = WorkspaceTools(for_session(root, "s1"))    # same dir, new instance
        assert "read-before-edit" in fresh.edit("a.txt", "hello", "hi"), "read set must not persist"

        print("copilot.workspace.tools self-check OK")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    _selfcheck()
