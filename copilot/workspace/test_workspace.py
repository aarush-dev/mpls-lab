"""Seam tests for the workspace tool family (B1, ADR-0011).

Prior art: dataapi/check_dataset.py (assert + __main__). Seam under test = WorkspaceTools
(read/write/edit) over a real per-session Workspace in a tmp dir -- no LLM/adapter doubles
needed; these tools touch only the filesystem cage. Each test pins one little-coder invariant.
Run:  python3 -m pytest copilot/workspace/test_workspace.py
"""
import os

import pytest

from copilot.workspace import PathPolicyError, WorkspaceTools, for_session


@pytest.fixture
def tools(tmp_path):
    return WorkspaceTools(for_session(str(tmp_path), "sess"))


def test_write_creates_new_file(tools):
    assert tools.write("a.txt", "hello").startswith("wrote")
    assert tools.read("a.txt") == "hello"


def test_write_refuses_existing_and_returns_edit_call(tools):
    tools.write("a.txt", "one")
    msg = tools.write("a.txt", "two")
    assert "exists" in msg and "edit(" in msg          # write = new only -> points at edit
    assert tools.read("a.txt") == "one"                # refused write did not overwrite


def test_edit_blocked_until_read_this_session(tools, tmp_path):
    # a file present on disk but not read this session cannot be edited.
    (tmp_path / "sess" / "scratchpad" / "b.txt").write_text("x=1")
    assert "read-before-edit" in tools.edit("b.txt", "x=1", "x=2")
    tools.read("b.txt")
    assert tools.edit("b.txt", "x=1", "x=2").startswith("edited")
    assert tools.read("b.txt") == "x=2"


def test_written_file_is_editable_without_a_separate_read(tools):
    tools.write("a.txt", "alpha beta")
    assert tools.edit("a.txt", "beta", "gamma").startswith("edited")
    assert tools.read("a.txt") == "alpha gamma"


def test_edit_exact_match_missing_and_ambiguous(tools):
    tools.write("a.txt", "xx")
    assert "not found" in tools.edit("a.txt", "zzz", "y")
    assert "empty" in tools.edit("a.txt", "", "y")                 # empty old_string -> refuse
    assert "matches 2" in tools.edit("a.txt", "x", "y")            # ambiguous -> refuse
    assert tools.edit("a.txt", "x", "y", replace_all=True).startswith("edited")
    assert tools.read("a.txt") == "yy"


def test_writes_and_edits_caged_to_scratchpad(tools):
    assert "error" in tools.write("../escape.txt", "no")
    assert "error" in tools.edit("/etc/passwd", "root", "pwn")


def test_copy_in_to_modify_leaves_external_untouched(tools, tmp_path):
    ext = tmp_path / "external.conf"
    ext.write_text("mtu 1500\n")
    body = tools.read(str(ext))                        # unrestricted read outside the cage
    tools.write("copy.conf", body)
    assert tools.edit("copy.conf", "1500", "9000").startswith("edited")
    assert ext.read_text() == "mtu 1500\n"             # original never edited in place


def test_read_set_is_per_session(tools, tmp_path):
    tools.write("a.txt", "hello")
    fresh = WorkspaceTools(for_session(str(tmp_path), "sess"))
    assert "read-before-edit" in fresh.edit("a.txt", "hello", "hi")


def test_selfcheck_runs():
    from copilot.workspace.tools import _selfcheck
    _selfcheck()
