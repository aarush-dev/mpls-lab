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
    """The periodic predictor (ADR-0014). Until `stop_fn()`: re-read the ground-truth timeline,
    predict on the window ending `now_fn()`, persist, sleep `cfg.predict_interval_s` (its first
    reader). `now_fn`/`stop_fn`/`sleep`/`fetch` are injected so a test drives the loop
    deterministically; in production `now_fn` is a UTC clock, `stop_fn` a shutdown flag, `sleep`
    is time.sleep. Returns the tick count. #55: the timeline is fetched EVERY tick, not once at
    boot -- a fault injected after the daemon started would otherwise be invisible forever (the loop
    only ever saw the boot snapshot). ponytail: a sleep-loop, not cron; restart-safety rides
    persist's alert_id idempotency (ADR-0014), no separate cursor needed yet."""
    tick = 0
    while not stop_fn():
        # #55: one try guards fetch+predict so a transient dataapi outage skips the tick instead of
        # silently killing the daemon (the pipeline-goes-dead ceiling this ticket closes). ponytail:
        # broad except by design -- a daemon must outlive any single-tick fault; upgrade path is a
        # typed retry/backoff on the transport if a specific error class needs distinct handling.
        try:
            predict_once(cfg, fetch_labels(base_url, fetch=fetch), ledger, now_fn(), drift_tick=tick)
        except Exception as e:  # noqa: BLE001 -- keep-alive is the point
            print(f"predictor tick {tick} failed (transient?): {e}", flush=True)
        tick += 1
        sleep(cfg.predict_interval_s)
    return tick


def _main():  # pragma: no cover -- #55 process entrypoint, exercised by copilot-up.sh, not units
    """Run the predictor as a headless daemon (copilot-up.sh / noc-copilot.service). Wall-clock UTC
    now, ledger + dataapi from env (shared with the trigger + api), SIGTERM flips the stop flag.
    ponytail: SIGTERM lands within one predict_interval_s (PEP 475 retries the sleep), fine for a
    ~10s cadence; upgrade path is a threading.Event with a wakeable wait if instant stop matters."""
    import os
    import signal
    from datetime import datetime, timezone

    from copilot.config import load
    from copilot.memory import Ledger

    cfg = load()
    base_url = os.environ.get("COPILOT_DATAAPI_URL", cfg.dataapi_url)
    ledger = Ledger(os.environ.get("COPILOT_LEDGER_PATH", "ledger.db"))
    stop = {"v": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("v", True))
    run_predictor(cfg, base_url, ledger,
                  now_fn=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  stop_fn=lambda: stop["v"])


if __name__ == "__main__":
    _main()
