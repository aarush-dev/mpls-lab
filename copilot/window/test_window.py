"""Assert-based self-check for WindowContext (R3, ADR-0002).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Run:  python3 -m copilot.window.test_window
"""
from dataclasses import dataclass

from copilot.window import WindowContext


@dataclass(frozen=True)
class _Cfg:
    window_x_min: int = 10          # only field WindowContext reads (ADR-0002 X)


NOW = 1_000_000


def test_live_is_rolling_and_unfrozen():
    w = WindowContext.live(_Cfg(), NOW)
    assert w.end == NOW and w.start == NOW - 600, "end=now, start=now-X (X=10min)"
    assert w.frozen is False and w.t_snapshot is None


def test_query_uses_named_period_and_is_not_frozen():
    w = WindowContext.query(500, 900, _Cfg(), NOW)
    assert (w.start, w.end) == (500, 900), "Query is the period the question names"
    assert w.frozen is False and w.t_snapshot is None, "a Query may span past AND live"


def test_query_falls_back_to_live_when_a_bound_is_missing():
    # ADR-0002: no period given -> rolling live default (the 'resolve' half; the 'ask' half
    # is the loop's existing ask-back, not this struct's job).
    for start, end in ((None, 900), (500, None), (None, None)):
        w = WindowContext.query(start, end, _Cfg(), NOW)
        assert (w.start, w.end, w.frozen) == (NOW - 600, NOW, False), f"{(start, end)} -> live"


def test_forensic_is_pinned_and_frozen():
    w = WindowContext.forensic(NOW, _Cfg())
    assert w.end == NOW and w.start == NOW - 600, "end=T_snapshot, start=T-X"
    assert w.frozen is True and w.t_snapshot == NOW, "frozen exposes T_snapshot for the guard"


def _run():
    test_live_is_rolling_and_unfrozen()
    test_query_uses_named_period_and_is_not_frozen()
    test_query_falls_back_to_live_when_a_bound_is_missing()
    test_forensic_is_pinned_and_frozen()
    print("copilot.window self-check OK")


if __name__ == "__main__":
    _run()
