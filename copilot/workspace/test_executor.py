"""REAL-sandbox seam tests for the exec runner (B2, ADR-0013).

Seam under test = Executor.run over a real per-session Workspace. These are NOT stubbed: the
security boundaries (no-net, timeout) must actually bite at the kernel, so the tests run real
subprocesses under `unshare -n`. Skipped where the netns can't be created (unshare unavailable) --
there the fail-closed refusal is asserted instead.
Run:  python3 -m pytest copilot/workspace/test_executor.py
"""
import os

import pytest

from copilot.workspace import for_session
from copilot.workspace.executor import Executor, _nonet_ok

needs_nonet = pytest.mark.skipif(not _nonet_ok(), reason="unshare -n unavailable on this host")


@pytest.fixture
def ex(tmp_path):
    return Executor(for_session(str(tmp_path), "sess"))


@needs_nonet
def test_basic_stdout_and_exit_code(ex):
    r = ex.run("echo hi")
    assert r.returncode == 0 and r.stdout.strip() == "hi"
    assert not r.timed_out and not r.refused


@needs_nonet
def test_cwd_confined_to_scratchpad(ex):
    assert ex.run("pwd").stdout.strip() == os.path.realpath(ex.ws.scratchpad)
    ex.run("echo data > out.txt")
    assert os.path.exists(os.path.join(ex.ws.scratchpad, "out.txt"))


@needs_nonet
def test_network_attempt_fails(ex):
    # security boundary: a real connect() from executed code must fail under the netns.
    r = ex.run("python3 -c \"import socket; socket.create_connection(('1.1.1.1',80),3)\"")
    assert r.returncode != 0


@needs_nonet
def test_timeout_kills_child_promptly(ex):
    r = ex.run("sleep 10", timeout=1)
    assert r.timed_out and r.returncode != 0
    assert r.duration_s < 5                         # killed ~1s, not after the full 10s


@needs_nonet
def test_timeout_kills_whole_process_group(ex):
    # a backgrounded grandchild must not outlive the cap (process-group SIGKILL).
    r = ex.run("sleep 30 & echo go; wait", timeout=1)
    assert r.timed_out and r.duration_s < 5


@needs_nonet
def test_per_call_timeout_clamped_to_ceiling(tmp_path):
    ex = Executor(for_session(str(tmp_path), "s"), timeout_s=1, max_timeout_s=2)
    r = ex.run("sleep 30", timeout=999)             # asks 999s, ceiling is 2s
    assert r.timed_out and r.duration_s < 6


@needs_nonet
def test_output_is_capped(tmp_path):
    ex = Executor(for_session(str(tmp_path), "s"), output_cap=100)
    r = ex.run("python3 -c \"print('x'*10000)\"")
    assert "[truncated" in r.stdout and len(r.stdout) < 300


def test_fails_closed_when_nonet_unavailable(ex, monkeypatch):
    # if the netns can't be established, run() refuses rather than exec with the host network.
    monkeypatch.setattr(ex, "_nonet", False)
    r = ex.run("echo should-not-run")
    assert r.refused and r.returncode == 126 and "refusing" in r.stderr


def test_selfcheck_runs():
    from copilot.workspace.executor import _selfcheck
    _selfcheck()
