"""copilot.workspace.executor -- the constrained subprocess runner (B2, ADR-0013).

The exec half of the semi-sandbox. **No container** (ADR-0013): containment is three tool-layer
constraints on the child process --

  - no-net   -- run under a fresh network namespace (`unshare -n`): no interfaces are up, so any
                connect() fails at the kernel. This is the real, enforced boundary, not an env trick.
  - cwd      -- the process STARTS in the session `scratchpad/` (the B0 cage), so relative writes
                land there. This is not a filesystem jail: raw `bash -c` can still write elsewhere by
                absolute path. FS containment is the *file tools'* job (B0/B1 path policy); raw exec
                trades it for the no-net + timeout boundary -- that is what "semi" means (ADR-0013:
                trust boundary is the tool layer, not a kernel jail).
  - timeout  -- a wall-clock cap; on expiry the whole process group is SIGKILLed (a child that
                forks grandchildren can't outlive the cap).

**Fail closed** (ADR-0013 "no-net even in dev"): if the netns can't be established (unshare missing
or not permitted), `run()` REFUSES rather than executing with the host network. no-net is not
optional, so a box that can't provide it can't exec.

`unshare -n` needs either root or unprivileged-userns support -- the deploy runs the copilot as root
(no container), so this holds; probed once at construction.

Whitelisted libs (pandas/matplotlib, ADR-0013) are the *available* env, not a kernel-enforced import
filter -- blocking imports robustly needs a real interpreter sandbox, which ADR-0013 rejected. The
netns is the boundary that bites; the lib list is a convention the prompt states.

ponytail: `unshare -n` + Popen with process-group kill; no seccomp, no cgroup. Harden to
container/seccomp only if deployed adversarially (ADR-0013 rejected-alternatives).

Self-check: python3 -m copilot.workspace.executor
"""
import os
import signal
import subprocess
import time
from dataclasses import dataclass

from copilot.workspace.policy import Workspace

_NO_NET = ("unshare", "-n")     # fresh network namespace: no interfaces -> connect() fails


def _nonet_ok() -> bool:
    """True iff `unshare -n` can create a network namespace here (probed once, fail-closed gate)."""
    try:
        return subprocess.run([*_NO_NET, "true"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@dataclass(slots=True)
class ExecResult:
    """Outcome of one run: exit code, captured (capped) stdout/stderr, wall time, timeout flag.
    `refused` is set when the run never started (no-net sandbox unavailable) -- distinct from a
    child that ran and failed."""

    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    refused: bool = False


class Executor:
    """Run a shell command in the session cage: cwd=scratchpad, no network, bounded time+output.
    One primitive (`run`) -- B3a wires the `bash` tool onto it; the model writes a .py into the
    scratchpad and runs `python3 foo.py` through the same path."""

    def __init__(self, ws: Workspace, timeout_s: int = 30, max_timeout_s: int = 300,
                 output_cap: int = 65536):
        self.ws = ws
        self.timeout_s = timeout_s
        self.max_timeout_s = max_timeout_s
        self.output_cap = output_cap
        self._nonet = _nonet_ok()

    def run(self, command: str, timeout: int | None = None) -> ExecResult:
        """Execute `command` via `bash -c` inside the no-net namespace, cwd=scratchpad. `timeout`
        (seconds) is clamped to (0, max_timeout_s]; None uses the default. On timeout the whole
        process group is killed and `timed_out` is set. Fails closed if no-net is unavailable."""
        if not self._nonet:
            return ExecResult(126, "", "no-net sandbox unavailable (unshare -n failed); "
                              "refusing to exec", 0.0, refused=True)
        t = self.timeout_s if timeout is None else max(1, min(int(timeout), self.max_timeout_s))
        # ponytail: cwd sets the START dir, it does NOT confine filesystem writes -- raw `bash -c`
        # can still `echo > /etc/x`. True write-confinement of exec'd code needs a mount ns +
        # pivot_root, which ADR-0013 rejected (no container). The netns is the boundary that bites;
        # B0/B1's path policy confines the *tool* writes, not this subprocess. Add a mount ns only
        # if exec is deployed adversarially (ADR-0013 rejected-alternatives).
        argv = [*_NO_NET, "bash", "-c", command]
        # start_new_session -> child leads a new process group; killpg reaps its grandchildren too.
        # ponytail: communicate() buffers output in memory, bounded only by the timeout; a runaway
        # printer could balloon RAM before the cap fires. Stream-truncate only if a run ever OOMs.
        started = time.monotonic()
        p = subprocess.Popen(argv, cwd=self.ws.scratchpad, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, start_new_session=True)
        timed_out = False
        try:
            out, err = p.communicate(timeout=t)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            out, err = p.communicate()
            timed_out = True
            err = (err or "") + f"\n[timeout: killed after {t}s]"
        return ExecResult(p.returncode, self._cap(out), self._cap(err),
                          time.monotonic() - started, timed_out=timed_out)

    def _cap(self, s: str) -> str:
        """Truncate captured output to `output_cap` chars, marking the cut. (chars not bytes --
        output is decoded text=True; the cap only bounds what's handed to the model.)"""
        if s and len(s) > self.output_cap:
            return s[:self.output_cap] + f"\n[truncated at {self.output_cap} chars]"
        return s


def _selfcheck():
    import shutil
    import tempfile

    from copilot.workspace.policy import for_session

    root = tempfile.mkdtemp()
    try:
        ex = Executor(for_session(root, "s1"), timeout_s=30, max_timeout_s=300)
        if not ex._nonet:
            print("copilot.workspace.executor self-check SKIPPED (unshare -n unavailable here)")
            return

        # basic: command runs, stdout captured, cwd is the scratchpad.
        r = ex.run("echo hi")
        assert r.returncode == 0 and r.stdout.strip() == "hi", "echo failed"
        assert not r.timed_out and not r.refused
        assert ex.run("pwd").stdout.strip() == os.path.realpath(ex.ws.scratchpad), "cwd not caged"

        # a relative write lands inside the scratchpad (cwd confinement, real).
        assert ex.run("echo data > out.txt").returncode == 0
        assert os.path.exists(os.path.join(ex.ws.scratchpad, "out.txt")), "write not in scratchpad"

        # no-net actually bites: a real connect() from the child fails.
        net = ex.run("python3 -c \"import socket; socket.create_connection(('1.1.1.1',80),3)\"")
        assert net.returncode != 0, "network attempt must fail under unshare -n"

        # timeout actually bites: a 10s sleep under a 1s cap is killed ~1s, flagged.
        slow = ex.run("sleep 10", timeout=1)
        assert slow.timed_out and slow.returncode != 0, "timeout must kill the child"
        assert slow.duration_s < 5, f"timeout did not fire promptly ({slow.duration_s:.1f}s)"

        # timeout kills the whole group: a backgrounded grandchild does not outlive the cap.
        grp = ex.run("sleep 30 & echo started; wait", timeout=1)
        assert grp.timed_out and grp.duration_s < 5, "process group not killed"

        # per-call timeout is clamped to max_timeout_s (can't exceed the ceiling).
        capped = Executor(for_session(root, "s2"), timeout_s=1, max_timeout_s=2)
        slow2 = capped.run("sleep 30", timeout=999)
        assert slow2.timed_out and slow2.duration_s < 6, "timeout not clamped to ceiling"

        # output is capped.
        big = Executor(for_session(root, "s3"), output_cap=100)
        r = big.run("python3 -c \"print('x'*10000)\"")
        assert "[truncated" in r.stdout and len(r.stdout) < 300, "output not capped"

        print("copilot.workspace.executor self-check OK")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    _selfcheck()
