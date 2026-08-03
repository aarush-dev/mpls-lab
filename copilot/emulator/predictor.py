"""copilot.emulator.predictor -- the periodic predictor firing loop (R4b, ADR-0014).

ADR-0014: the PA (or emulator, ADR-0003) runs as a periodic predictor -- every ~predict_interval_s
it predicts on the current window and writes a Prediction Record to the Event Ledger. This module
is that loop for the emulator: `predict_once` is one tick (predict -> persist), `run_predictor` is
the sleep-loop driver (cron floors ~60s, so a 10s cadence is a sleep-loop, ADR-0014). The record
is idempotent by alert_id, so a fault episode that alerts for minutes lands ONE ledger row
(ADR-0014 one-case-per-episode), not one per tick.

The firing off `decision.alert == true` -- freeze the window, open the case, spawn chats -- is the
Forensic pipeline (R5); this ticket delivers the record stream it consumes.

Self-check:  python3 -m copilot.emulator.test_predictor
"""
import time

from copilot.emulator.emulate import fetch_labels, persist, prediction


def predict_once(cfg, labels: list[dict], ledger, now: str, *, drift_tick: int = 0):
    """One predictor tick: predict on the window ending `now`, persist any record (ADR-0014).
    Returns the record (or None if no fault is active). `drift_tick` is the run's tick index -- it
    evolves the faked drift/health scalar over the run. Idempotent by alert_id: re-emitting the
    same active episode on a later tick is a no-op in the ledger."""
    rec = prediction(cfg, labels, now=now, drift_tick=drift_tick)
    if rec is not None:
        persist(ledger, rec)
    return rec


def run_predictor(cfg, base_url: str, ledger, *, now_fn, stop_fn,
                  sleep=time.sleep, fetch=None) -> int:
    """The periodic predictor (ADR-0014). Fetch the ground-truth timeline ONCE (a run's timeline is
    fixed), then until `stop_fn()`: predict on the window ending `now_fn()`, persist, sleep
    `cfg.predict_interval_s` (its first reader). `now_fn`/`stop_fn`/`sleep`/`fetch` are injected so
    a test drives the loop deterministically; in production `now_fn` is a UTC clock, `stop_fn` a
    shutdown flag, `sleep` is time.sleep. Returns the tick count. ponytail: a sleep-loop, not cron;
    restart-safety rides persist's alert_id idempotency (ADR-0014), no separate cursor needed yet."""
    labels = fetch_labels(base_url, fetch=fetch)
    tick = 0
    while not stop_fn():
        predict_once(cfg, labels, ledger, now_fn(), drift_tick=tick)
        tick += 1
        sleep(cfg.predict_interval_s)
    return tick
