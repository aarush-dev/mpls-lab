---
name: harness-eval
description: Run the copilot /chat harness regression suite (issues #113-#128) and report a pass/fail scorecard. Trigger on "run the harness eval", "check for harness regressions", "did the fixes hold", "/harness-eval", after touching copilot/agent/loop.py, copilot/agent/gate.py, copilot/llm/http.py, copilot/tools/registry.py, or copilot/adapter/*, or when re-verifying the 2026-08-06 audit findings.
argument-hint: "[--smoke|--full]"
metadata:
  author: aarush
  version: "1.0.0"
---

# Harness eval

Regression suite for the `/chat` agent loop, built from the 2026-08-06 100-run
audit (`docs/audits/2026-08-06-harness-probe/`). Every check maps to one
GitHub issue (#113-#128) and asserts the specific failure that issue
describes does not recur.

## Steps

1. **Confirm the api is live and current.** `curl -sf localhost:8100/openapi.json`.
   If down, or if `copilot/agent/loop.py`, `copilot/agent/gate.py`,
   `copilot/llm/http.py`, `copilot/tools/registry.py`, or
   `copilot/adapter/*` changed more recently than the running process
   started (`ps -o lstart -p $(pgrep -f 'copilot.api.app:app')` vs file
   mtimes), restart it — a stale process silently re-triggers #113.
2. **Run it in the background**, `nohup` + redirect to a log file, not
   foreground — a full run is 100 real multi-round LLM investigations and
   routinely takes 15+ minutes:
   - `--smoke` (10 questions, one per probe category, ~2-4 min): fast
     iteration while actively fixing something.
   - `--full` (default, all 100): the real regression bar before calling a
     fix verified. Use this before closing an issue or claiming "confirmed
     fixed" to a human.
   - `--batch N` (default 10) caps concurrent in-flight requests. The
     upstream NIM endpoint rate-limits and occasionally 5xxs under
     concurrency (independent of any harness bug) — drop to `--batch 3-5`
     if a run shows spurious #113 failures with `ReadTimeout`/`503` in
     `/tmp/copilot-api.log` rather than a real stack trace from copilot's
     own code.
     `python3 -m copilot.eval.run_eval [--smoke] [--batch N]`
3. **Read the scorecard.** Exit code is nonzero on any real `FAIL`.
   - `FAIL` — a confirmed regression against a fixed issue. Read
     `/tmp/copilot-api.log` for the actual traceback before concluding
     it's the harness and not upstream flakiness (see batch note above).
   - `flag` — a heuristic hit, worth a human glance, not proof by itself.
   - `skip` — not automatable from a single-shot probe (documented per-check
     in `copilot/eval/checks.py` — #116, #123, #128 are structurally
     partial; extending them is real work, not a bug in the eval).
4. **Report FAIL rows with root cause**, not the raw scorecard pasted
   verbatim — name which file/line the regression traces to, same bar as
   the original audit tickets.

## Reference

- `copilot/eval/checks.py` — one function per issue, each docstring states
  exactly what it checks and why it can or can't be fully automated. This is
  the source of truth for what "passing" means per issue; don't restate the
  check logic elsewhere.
- `copilot/eval/questions.json` / `smoke_questions.json` — the probe set.
- `docs/audits/2026-08-06-harness-probe/` — original findings, raw
  transcripts for every run, the rendered report. Cross-reference here when
  a FAIL needs the original evidence for comparison.
