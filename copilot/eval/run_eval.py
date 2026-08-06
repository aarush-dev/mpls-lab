#!/usr/bin/env python3
"""copilot.eval.run_eval -- fire the harness-probe question set at a live /chat, run the
per-issue regression checks (checks.py) against every trace, print a scorecard.

Usage:
    python3 -m copilot.eval.run_eval [--smoke] [--url http://127.0.0.1:8100] [--batch 10]

--smoke runs the 10-question representative subset (one per probe category, ~2 min);
default runs the full 100-question set in paced batches of --batch concurrent requests
(a batch waits for the prior one to fully drain -- see docs/audits/2026-08-06-harness-probe/
for why: NIM rate limits mean 10-wide waves already sit close to the ceiling).

Exit code is nonzero if any check reports "fail" (a real regression) -- CI-usable.
"failed" is reserved for a definite regression against a fixed issue; "flag" is a
heuristic that deserves a human look but isn't proof on its own.
"""
import argparse
import concurrent.futures
import json
import sys
from pathlib import Path

import httpx

from copilot.eval import checks as checks_mod

HERE = Path(__file__).parent


def _load_questions(smoke):
    path = HERE / ("smoke_questions.json" if smoke else "questions.json")
    return json.loads(path.read_text())


def _parse_sse(raw: str):
    events = []
    for block in raw.split("\n\n"):
        if not block.startswith("data: "):
            continue
        try:
            events.append(json.loads(block[len("data: "):]))
        except (ValueError, TypeError):
            continue
    return events


def _run_one(client, url, question):
    try:
        r = client.post(url, json={"question": question}, timeout=180.0)
        raw = r.text
    except httpx.HTTPError as e:
        raw = f"Internal Server Error: {e}"
    return {"question": question, "raw": raw, "events": _parse_sse(raw)}


def _run_batch(url, questions, batch_size):
    runs = []
    with httpx.Client() as client:
        for i in range(0, len(questions), batch_size):
            chunk = questions[i:i + batch_size]
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(chunk)) as ex:
                runs.extend(ex.map(lambda q: _run_one(client, url, q), chunk))
    return runs


def _score(runs):
    results = {}  # issue -> list of (status, detail, question)
    for num, fn in checks_mod.CHECKS.items():
        results[num] = [(*fn(run), run["question"][:60]) for run in runs]
    for num, fn in checks_mod.STATIC_CHECKS.items():
        status, detail = fn()
        results[num] = [(status, detail, "(static check, not per-run)")]
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="10-question fast subset instead of the full 100")
    ap.add_argument("--url", default="http://127.0.0.1:8100/chat")
    ap.add_argument("--batch", type=int, default=10)
    args = ap.parse_args()

    questions = _load_questions(args.smoke)
    print(f"firing {len(questions)} questions at {args.url} in batches of {args.batch}...")
    runs = _run_batch(args.url, questions, args.batch)
    results = _score(runs)

    any_fail = False
    print(f"\n{'issue':>6}  {'status':<6}  detail")
    print("-" * 90)
    for num in sorted(results):
        statuses = [s for s, _, _ in results[num]]
        if "fail" in statuses:
            overall = "FAIL"
            any_fail = True
        elif "flag" in statuses:
            overall = "flag"
        elif all(s == "skip" for s in statuses):
            overall = "skip"
        else:
            overall = "pass"
        n_fail = statuses.count("fail")
        n_flag = statuses.count("flag")
        detail = f"{n_fail} fail / {n_flag} flag / {len(statuses)} checked"
        print(f"#{num:<5}  {overall:<6}  {detail}")
        if overall in ("FAIL", "flag"):
            for status, msg, q in results[num]:
                if status in ("fail", "flag"):
                    print(f"         - [{status}] {q!r}: {msg}")

    print()
    if any_fail:
        print("REGRESSION DETECTED — see FAIL rows above.")
        sys.exit(1)
    print("No regressions. 'flag' rows are heuristics — worth a human glance, not proof of a bug.")


if __name__ == "__main__":
    main()
