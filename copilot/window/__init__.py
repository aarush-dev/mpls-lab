"""copilot.window -- Lane-Runtime (R3). WindowContext {start,end,frozen} (ADR-0002).

The investigation window the loop threads into every tool call (the model never sets it,
ADR-0002/0015). One struct, four resolutions:
- live(cfg, now)            -- rolling: end=now, start=now-X. Not frozen.
- query(start, end, cfg, now) -- the period the human's question names (arbitrary/historical);
                              missing -> falls back to live. Not frozen (a Query may span past
                              AND live, so it is a distinct case, not Live-rolling).
- forensic(t_snapshot, cfg) -- pinned: end=T_snapshot, start=T-X, frozen. The agent may not read
                              past T_snapshot -- the freeze guard (copilot.adapter Filters.validate)
                              rejects end > T_snapshot so no live data leaks into a frozen case.
- salvage(buildup_start, now, cfg) -- anchored-live: end=now, start pinned at buildup_start,
                              clamped to now-X_max. Not frozen (live, just anchored not rolling).

X = cfg.window_x_min, X_max = cfg.window_x_max (ADR-0002). start<end is enforced downstream by
Filters.validate (single point), so this stays a plain struct.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class WindowContext:
    start: int
    end: int
    frozen: bool = False

    @classmethod
    def live(cls, cfg, now: int) -> "WindowContext":
        return cls(now - cfg.window_x_min * 60, now, frozen=False)

    @classmethod
    def query(cls, start: int | None, end: int | None, cfg, now: int) -> "WindowContext":
        """The window the human's question names; missing either bound -> rolling live default
        (ADR-0002). This is the 'resolve' half only -- the model still asks a clarifying
        question via the loop's existing ask-back when it can't pin a period (no new machinery)."""
        if start is None or end is None:
            return cls.live(cfg, now)
        return cls(start, end, frozen=False)

    @classmethod
    def forensic(cls, t_snapshot: int, cfg) -> "WindowContext":
        return cls(t_snapshot - cfg.window_x_min * 60, t_snapshot, frozen=True)

    @classmethod
    def salvage(cls, buildup_start: int, now: int, cfg) -> "WindowContext":
        """Anchored-live (ADR-0002): start pinned at the fault's buildup-start so the earliest
        precursor never scrolls out, clamped to now-X_max on a long episode so the window can't
        grow unbounded. end=now, never frozen (salvage is live, just anchored, not rolling)."""
        return cls(max(buildup_start, now - cfg.window_x_max * 60), now, frozen=False)

    @property
    def t_snapshot(self) -> int | None:
        """The frozen end bound the adapter freeze guard enforces; None when not frozen."""
        return self.end if self.frozen else None
