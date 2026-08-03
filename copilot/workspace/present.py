"""copilot.workspace.present -- snapshot-on-present + the `artifact` payload (B3b, ADR-0009/0010).

A presented output (a chart or code the agent generated in its scratchpad) is COPIED into the
append-only `artifacts/` at present-time, so a later overwrite of the scratchpad source can't
change what was shown (ADR-0009: "snapshot of each presented file at present-time -- fixes
overwrite corruption"). The bytes are read once here and ride the `artifact` event, so the shown
artifact is frozen even before the copy hits disk.

Kind: an image extension -> "chart" (rendered inline as an image); anything else -> "code" (a
text/code block). The demo consuming the `artifact` event is another lane (C1/#27); this module
only PRODUCES the snapshot + the event payload -- no rendering here.

Source confinement reuses the B0 boundary (`Workspace.writable`): only a file inside the session
scratchpad can be presented, so the append-only case record can't be fed an arbitrary host file.

ponytail: append-only name = a zero-padded count prefix (never reused; single-writer per session,
ADR-0009 lock, so the count is race-free). Inline payload is capped -- a file over the cap ships as
a reference (name + on-disk path) without bytes, so a big artifact can't blow up events.jsonl.
Raise the cap / add a static-serve endpoint only if inline stops sufficing.

Self-check: python3 -m copilot.workspace.present
"""
import base64
import mimetypes
import os
import shutil
from dataclasses import dataclass

from copilot.workspace.policy import Workspace

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}
_INLINE_CAP = 512 * 1024        # bytes: inline an artifact under this; over -> reference only


@dataclass(frozen=True)
class Artifact:
    """One snapshotted presented output. `content`/`content_b64` are the INLINE payload for a
    renderer (text for code, base64 for a chart image) -- both None when the file is over the
    inline cap (reference-only). `event()` is the `artifact`-event data (ADR-0009 canonical type)."""

    name: str                       # filename inside artifacts/ (append-only, unique)
    path: str                       # absolute path of the snapshot on disk
    source: str                     # basename of the scratchpad file presented
    kind: str                       # "chart" | "code"
    mime: str
    size: int                       # bytes
    title: str | None = None
    content: str | None = None      # inline text for a code/text kind (an image kind uses content_b64)
    content_b64: str | None = None  # inline base64 (image bytes)

    def event(self) -> dict:
        """The `artifact` event payload. No `type`/`ts` keys -- the loop's Event owns those, and
        event_wire() guards against a payload shadowing them (round-trip into events.jsonl)."""
        d = {"name": self.name, "kind": self.kind, "mime": self.mime, "source": self.source,
             "size": self.size, "path": os.path.join("artifacts", self.name)}
        if self.title:
            d["title"] = self.title
        if self.content is not None:
            d["content"] = self.content
        if self.content_b64 is not None:
            d["content_b64"] = self.content_b64
        return d


def snapshot(ws: Workspace, path: str, *, title: str | None = None) -> Artifact:
    """Snapshot a scratchpad file into the append-only artifacts/ and return its Artifact.

    Copies the bytes NOW: a later overwrite of `path` leaves this snapshot (and its event payload)
    unchanged (ADR-0009 snapshot-on-present). Source is confined to scratchpad via Workspace.writable
    (a path outside raises PathPolicyError); a missing/unreadable file raises the usual OSError, which
    the loop turns into guidance (ADR-0015). Name = `NNNN-<basename>`, the first index whose file
    does not yet exist -- append-only, never overwrites an earlier snapshot (see _reserve)."""
    src = ws.writable(path)                       # confine to scratchpad (reuse B0 boundary)
    if not os.path.isfile(src):
        raise FileNotFoundError(f"no such file to present: {path}")
    with open(src, "rb") as f:                    # read the bytes NOW -> the snapshot is frozen
        raw = f.read()                            #   even if the source is overwritten next
    base = os.path.basename(src)
    name, dst = _reserve(ws.artifacts, base)      # reserve an unused name; never clobbers a snapshot
    with open(dst, "wb") as out:
        out.write(raw)
    size = len(raw)
    ext = os.path.splitext(base)[1].lower()
    kind = "chart" if ext in _IMAGE_EXT else "code"
    mime = mimetypes.guess_type(base)[0] or "application/octet-stream"
    content = content_b64 = None
    if size <= _INLINE_CAP:                       # inline for the renderer; else reference-only
        if kind == "chart":
            content_b64 = base64.b64encode(raw).decode("ascii")
        else:
            content = raw.decode("utf-8", "replace")
    return Artifact(name, dst, base, kind, mime, size, title, content, content_b64)


def _reserve(artifacts_dir: str, base: str) -> tuple[str, str]:
    """Reserve the next append-only name for `base` and CREATE the (empty) file exclusively, so two
    snapshots of the same basename can never resolve to the same path and clobber each other. The
    entry count is only a starting hint -- O_EXCL is what guarantees no overwrite even if an artifact
    was removed or a foreign file sits in the dir. Returns (name, absolute path); the caller writes
    the bytes into the reserved path."""
    n = len(os.listdir(artifacts_dir))
    while True:
        name = f"{n:04d}-{base}"
        dst = os.path.join(artifacts_dir, name)
        try:
            os.close(os.open(dst, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
            return name, dst
        except FileExistsError:
            n += 1


def _selfcheck():
    import tempfile

    from copilot.workspace.policy import PathPolicyError, for_session

    root = tempfile.mkdtemp()
    try:
        ws = for_session(root, "s1")

        # present a chart -> a "chart" artifact, base64-inlined, append-only name 0000-.
        chart = os.path.join(ws.scratchpad, "chart.png")
        with open(chart, "wb") as f:
            f.write(b"PNG-v1")
        a1 = snapshot(ws, "chart.png", title="cpu")
        assert a1.kind == "chart" and a1.name == "0000-chart.png", "chart kind + append-only name"
        assert a1.content_b64 == base64.b64encode(b"PNG-v1").decode() and a1.content is None
        assert a1.title == "cpu"

        # snapshot-on-present (acceptance): overwriting the source does NOT change the shown artifact.
        with open(chart, "wb") as f:
            f.write(b"PNG-v2-DIFFERENT")
        with open(a1.path, "rb") as f:
            assert f.read() == b"PNG-v1", "snapshot must be frozen at present-time, not follow the source"
        assert a1.content_b64 == base64.b64encode(b"PNG-v1").decode(), "the event payload is frozen too"

        # presenting again -> a NEW append-only artifact (never overwrites the first).
        a2 = snapshot(ws, "chart.png")
        assert a2.name == "0001-chart.png" and a2.name != a1.name, "append-only: a new file each present"
        with open(a2.path, "rb") as f:
            assert f.read() == b"PNG-v2-DIFFERENT", "second snapshot has the new bytes"
        assert os.path.exists(a1.path), "the first snapshot still exists (append-only)"

        # no-clobber under a count collision: remove 0000 so the entry count (1) now points at the
        # LIVE name 0001 -- _reserve must O_EXCL-retry past it, never overwriting the 0001 snapshot.
        os.remove(a1.path)                                 # dir == {0001-chart.png}, count == 1
        a3 = snapshot(ws, "chart.png")
        assert a3.name == "0002-chart.png", "must skip the live 0001 name, not reuse it"
        with open(a2.path, "rb") as f:
            assert f.read() == b"PNG-v2-DIFFERENT", "the surviving 0001 snapshot is NOT clobbered"

        # a code/text file inlines as text, not base64.
        code = os.path.join(ws.scratchpad, "analyze.py")
        with open(code, "w") as f:
            f.write("print('hi')\n")
        ac = snapshot(ws, "analyze.py")
        assert ac.kind == "code" and ac.content == "print('hi')\n" and ac.content_b64 is None

        # event() shape: no type/ts (round-trips in events.jsonl), path is artifacts-relative.
        ev = a1.event()
        assert "type" not in ev and "ts" not in ev, "payload must not shadow Event.type/ts"
        assert ev["path"] == "artifacts/0000-chart.png" and ev["kind"] == "chart"
        assert ev["content_b64"] == a1.content_b64 and ev["title"] == "cpu"

        # source is confined to scratchpad: an outside path is refused (can't snapshot host files).
        outside = os.path.join(root, "secret.txt")
        with open(outside, "w") as f:
            f.write("x")
        try:
            snapshot(ws, outside)
        except PathPolicyError:
            pass
        else:
            raise AssertionError("presenting a file outside scratchpad must be refused")

        # a missing file is an OSError (loop turns it into guidance), not a snapshot.
        try:
            snapshot(ws, "nope.png")
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("presenting a missing file must raise")

        # over the inline cap -> reference-only (no inline bytes), so events.jsonl stays small.
        big = os.path.join(ws.scratchpad, "big.bin")
        with open(big, "wb") as f:
            f.write(b"0" * (_INLINE_CAP + 1))
        ab = snapshot(ws, "big.bin")
        assert ab.content is None and ab.content_b64 is None and ab.size > _INLINE_CAP
        assert "content" not in ab.event() and "content_b64" not in ab.event()

        print("copilot.workspace.present self-check OK")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    _selfcheck()
