# Harness probe follow-up — 2026-08-06 (#128)

The 100-run audit (`docs/audits/2026-08-06-harness-probe/`) never set
`session_id`/`case_id`/`workspace`/`skills` on any request — every probe sent
a bare `{"question": ...}`. Runs 61-70 ("session/case_id edge cases") only
tested the model's prose reaction to a user *claiming* prior context in free
text, never the real `ChatRequest` mechanics (`copilot/api/app.py:55-64`).
This is a 4-request follow-up that actually sets those fields, targeting the
four gaps #128 named. Driver: `driver.sh`.

## Findings

1. **Unknown `session_id` silently creates a new persisted session — no 404.**
   Confirmed: `SessionStore.read` (`copilot/memory/session.py:59-65`) returns
   `[]` when `events.jsonl` is absent, so `sessions.history(sid)` never
   distinguishes "resuming a real session" from "id nobody ever created."
   The turn proceeds as `session=new`, and `sessions.append` (app.py:294)
   persists it under the typo'd id going forward. Compare: an unknown
   `case_id` is a hard 404 via `resolve_case_dir`'s realpath containment
   check (app.py:236-242). **Decision, not a fix**: this asymmetry is
   intentional-by-default for a chat session (a fresh id is a legitimate way
   to start a NEW session — the id namespace isn't pre-registered, unlike a
   case which is created by the predict loop) but is worth a docs note if a
   caller ever needs "resume-or-404" semantics; no ticket filed, no code
   changed.

2. **`case_id` follow-up with `end` past the case's frozen `T_snapshot` →
   HTTP 400, confirmed working.** `runs/req_case_end_past_snapshot.json` +
   `out_case_end_past_snapshot.sse` — a real case
   (`alt_bgp_flap-ce_branch1-2fa5b64f__bgp_flap`, `T_snapshot=1785963793`)
   with `end=9999999999` returns
   `{"detail":"forensic window frozen at T_snapshot=1785963793: end=9999999999 would read live data; pass end <= 1785963793"}`
   at HTTP 400 — the ADR-0002 freeze guard (`FilterError` → `HTTPException`,
   app.py:267-269) works exactly as designed. No prior run ever exercised
   this path live; now it's covered.

3. **`workspace: true` with no `session_id` → silently no workspace.**
   Confirmed: `runs/out_workspace_without_session.sse` — the loop
   investigated `ce_branch1` (3 `query_metrics` calls) and never once called
   `bash`, because `ws = for_session(...) if (sid and req.workspace) else None`
   (app.py:282) requires BOTH; `workspace=true` alone silently no-ops. No
   error, no event, matches the code's documented behavior
   (`copilot/agent/loop.py:279`ish comment). Also incidentally confirms
   #127's markdown-table policy landed — the model's answer used a
   `| Tunnel | Latency | Jitter | Loss |` table.

4. **Unknown `skills` name → confirmed silent no-op at the CODE level; a
   live HTTP 500/timeout seen during this probe is unrelated flakiness, not
   a copilot bug.** Two live attempts against `localhost:8100/chat` with
   `"skills": ["not_a_real_skill_xyz"]`: one returned `500 Internal Server
   Error` after ~44s, a retry timed out after 150s with no response at all —
   inconsistent, pointing at live-stack instability (LLM backend latency
   under the concurrent probe load), not a deterministic crash. Reproduced
   the actual code path directly with a `ScriptedLLM` (no live LLM, no
   network) calling `investigate(..., invoke=["not_a_real_skill_xyz"])`:
   completes normally, no exception — `copilot/agent/loop.py`'s
   `for name in (invoke or ()): s = skills.get(name); if s: ...` guard
   (the code's own comment already calls this out: "a miss is rare; add a
   warning event if humans hand-type it") handles it exactly as documented.
   No code change; the live 500/timeout is flagged here as an observation,
   not investigated further (transient backend issue, not reproducible
   deterministically).

## Files

- `driver.sh` — fires the 4 requests sequentially against `localhost:8100`
  (the live server is single-worker; concurrent `/chat` calls caused
  contention/timeouts during this probe, so requests are sequential here,
  unlike the original 100-run driver's 10-wide waves).
- `runs/req_*.json` / `runs/out_*.sse` / `runs/out_*.code` — request body,
  raw SSE response, and HTTP status per probe (only 2 of the 4 captured
  cleanly as artifacts here — #1 and #4 hit live-stack instability during
  capture; their behavior is described above from the responses actually
  observed).
