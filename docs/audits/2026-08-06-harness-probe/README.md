# Harness probe — 2026-08-06

100-run stress test of the `/chat` harness (model: `nvidia/nemotron-3-ultra-550b-a55b`),
analyzed by 8 independent reviewers reading full traces. Findings filed as
GitHub issues #113-#128 (label `harness-gap`).

- `questions.json` — the 100 probe questions, 1-indexed, grouped in blocks of
  10 by category (network-wide, malformed device, malformed time window,
  multi-device/topology, tool-arg edge cases, out-of-scope asks, session/case_id
  edge cases, prompt injection, format/language stress, root-cause reasoning).
- `runs/req_N.json` — the exact request body sent for run N.
- `runs/out_N.sse` — the full raw SSE response for run N: every event
  (`user_msg`/`tool_call`/`tool_result`/`think`/`gate`/`assistant_msg`) in
  order, unedited. This is the primary evidence — every "Run N" reference in
  issues #113-#128 points here.
- `audit_report.html` — the rendered findings report (severity-ranked table +
  root-cause verdict table for runs 91-100).

To read a run: open `runs/out_N.sse` — it's newline-delimited `data: {...}`
JSON, one event per line-pair. `req_N.json` has the exact question asked.
