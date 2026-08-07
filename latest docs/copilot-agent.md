# Copilot — Agent loop & gate

## Purpose

F3 (agent loop, ADR-0005) + I4a/I4b (two-stage quality gate, ADR-0008). Sits between
the F1 LLM seam (`copilot.llm`) / F2 tool adapter (`copilot.adapter`) and F4 (trace
stream/persistence). `investigate()` is the single entry point: given a question, a
`WindowContext`, an `LLMClient`, a `ToolAdapter`, and a `Config`, it runs a
think→tool→observe→decide→answer loop, dispatching tool calls through the I1 registry
(`copilot.tools`), and before returning gates the draft answer through stage 1
(deterministic pre-gate, `gate.py`) then stage 2 (LLM self-judge, `loop.self_judge`).
Callers outside this path (predict loop, forensic chat, the API layer) supply the
window, wired adapters, and optional PA-derived hints (`fault_type`, `abstain`,
`drift_state`); this subsystem owns none of those callers.

## Entry points

No CLI or HTTP route lives in `copilot/agent/` — it is a library seam, called by
runtime code elsewhere (API layer / predict loop, out of scope here). The two entry
points are the programmatic function `investigate()` and the module self-checks.

**Programmatic** (`copilot/agent/loop.py:256`):
```python
from copilot.adapter import StubAdapter
from copilot.agent import investigate
from copilot.config import Config
from copilot.llm import ScriptedLLM, final, tool_call
from copilot.window import WindowContext

llm = ScriptedLLM([
    tool_call("query_metrics", {"device": "r1", "limit": 5}, id="c1"),
    final("r1 cpu is pegged [metrics:0]"),
    final('{"pass": true}'),              # stage-2 self-judge verdict
])
out = investigate("why is r1 slow?", WindowContext(100, 200),
                   llm=llm, adapter=StubAdapter(metrics_rows=[{"device": "r1", "ts": 150, "cpu": 91}]),
                   cfg=Config())
print(out.answer)   # "r1 cpu is pegged [metrics:0]"
```

**Self-checks** (assert + `__main__`, no pytest framework, per module docstrings):
```
python3 -m copilot.agent.test_agent   # loop.py:6 in module docstring / test_agent.py:6
python3 -m copilot.agent.test_gate    # gate.py:14 / test_gate.py:6
```
`test_agent.py` can also run under pytest directly (`pytest copilot/agent/`); its
`__main__` block (`test_agent.py:856-857`) calls a curated subset of the `test_*`
functions by name — see Gotchas for tests that exist but are never called there.

## Modules

- **`loop.py`** (599 lines) — the loop itself: `investigate()` (the state machine),
  `self_judge()` (I4b stage 2), event/outcome types, tool specs for the 3 non-registry
  tools (`load_skill`, `bash`, `present`), history compaction, and the owned JSON
  tool-call fallback parser.
  - `investigate(question, window, *, llm, adapter, cfg, ...) -> Outcome` — `loop.py:256`
  - `self_judge(llm, messages, answer) -> GateResult` — `loop.py:479`
  - `Event` (frozen dataclass: `type`, `data`, `ts`) — `loop.py:62`
  - `Outcome` (frozen dataclass: `answer`, `events`, `stopped`, `cites`) — `loop.py:79`
  - `event_wire(e) -> dict` (canonical stream/store shape) — `loop.py:90`
  - `compact_history(history, *, max_chars) -> list[dict]` (I6/ADR-0015 §5) — `loop.py:207`
  - `parse_tool_calls(content) -> tuple[ToolCall, ...]` (owned fallback parser) — `loop.py:180`
  - `_load_skill` / `_run_bash` / `_present` — non-registry tool handlers — `loop.py:504,514,534`
  - `_capped`, `_assistant_turn`, `_tool_call_json`, `_first_json_object` — internal helpers — `loop.py:553,559,569,583`

- **`gate.py`** (180 lines) — I4a stage-1 deterministic pre-gate + citation check, plus
  the T1 trust banner. Pure functions, no LLM call.
  - `GateResult` (frozen dataclass: `ok`, `missing`) — `gate.py:70`
  - `extract_entities(question) -> frozenset[str]` — `gate.py:80`
  - `tool_calls_ok(errors) -> GateResult` — `gate.py:85`
  - `pre_gate(cites, *, window, entities, min_evidence, soft=False) -> GateResult` — `gate.py:91`
  - `citation_check(answer, valid_ids) -> GateResult` — `gate.py:118`
  - `run_gate(answer, cites, *, window, question, min_evidence, tool_errors=(), abstain=False, prior_cites=()) -> GateResult` — `gate.py:136`
  - `trust_banner(drift_state, *, distrust_at) -> str | None` (T1, flags not blocks) — `gate.py:159`

- **`__init__.py`** (18 lines) — re-exports the F3 public surface
  (`investigate`, `Event`, `Outcome`, `event_wire`, `parse_tool_calls`,
  `compact_history`, `EVENT_TYPES`, `SYSTEM_PROMPT`, `TOOL_SPECS`) — `__init__.py:10-18`.

- **`test_agent.py`** (857 lines, assert-based) — run `python3 -m copilot.agent.test_agent`
  or `pytest copilot/agent/test_agent.py`.
- **`test_gate.py`** (237 lines, assert-based) — run `python3 -m copilot.agent.test_gate`
  or `pytest copilot/agent/test_gate.py`.

## Parameters

All `cfg.*` fields are read from `copilot.config.Config` (defaults declared in
`copilot/config.py`, overridable via `copilot/config.yaml`; no per-field env var except
where noted). Module-local constants have no config surface — code-only tunables.

| name | default | env-var/CLI-flag | units | what it controls | source (file:line) |
|---|---|---|---|---|---|
| `cfg.step_cap` | `100` | none | loop turns | outer-loop iteration cap (runaway backstop) | config.py:75; consumed loop.py:345 |
| `cfg.tool_call_cap` | `100` | none | tool calls | PRODUCTIVE tool-call budget — only calls that gathered a `Cite` (or a non-errored skill/bash/present) spend it | config.py:78; consumed loop.py:444,469 |
| `cfg.gate_min_evidence` | `2` | none | count of `Cite`s | sufficiency floor in `pre_gate`; skipped when `abstain=True` | config.py:73; gate.py:101 |
| `cfg.gate_max_retries` | `2` | none | retries | bounded agentic re-enters on a stage-1/stage-2 gate fail | config.py:74; loop.py:406 |
| `cfg.gate_enabled` | `True` | `COPILOT_GATE_DISABLE=1` (api.py override — outside this subsystem, per config.py:61-64 comment) | bool | `False` skips BOTH gate stages entirely | config.py:61; loop.py:369 |
| `cfg.drift_distrust_at` | `"R3"` | none | drift rung id (R0–R5) | floor rung at/above which a passing answer gets the trust banner prepended | config.py:80; gate.py:170-176 |
| `cfg.history_compaction` | `False` | none | bool | gates whether `compact_history` runs on resumed `history` | config.py:66; loop.py:332 |
| `cfg.history_max_chars` | `6000` | none | chars | char budget `compact_history` keeps resumed history under | config.py:67; loop.py:333,222-223 |
| `cfg.empty_answer_max_retries` | **none — referenced but not a `Config` field** | n/a | retries | intended: separate budget for consecutive blank terminal turns | usage loop.py:392; absent from config.py (see Gotchas) |
| `dispatch_cap` (local) | `step_cap * tool_call_cap` (= 10000 at defaults) | n/a | tool dispatches | hard ceiling on ALL dispatched calls (including empty/errored), so a model packing many free calls per turn still terminates | loop.py:338, checked loop.py:444 |
| `note_cap` (local, in `compact_history`) | `max(200, history_max_chars // 5)` | n/a | chars | reserved slice of the budget for the collapsed "investigation so far" digest note; the rest (`tail_budget`) is kept verbatim | loop.py:222-223 |
| `_RANGE_RE` span cap | `64` | n/a | ids | max `hi - lo` a `src:lo-hi` citation range is expanded to; larger spans are left as a literal (unrecognized) token | gate.py:58-60 |
| `ENTITY_RE` | role-prefix regex (`rr\|r\|pe\|p\|ce_branch\|ce_hub\|ce\|asbr` + digits, or `h_\w+`) | n/a | n/a | which tokens in a question/sentence count as a "device entity" requiring evidence/citation | gate.py:32-33 |
| `CITE_RE` (gate.py) | `\[([a-z][a-z0-9_]...)\]` id grammar | n/a | n/a | which bracketed tokens count as a citation (anchored to real id grammar, not any `[...]`) | gate.py:37-38 |
| `WINDOWED_SOURCES` | `{"metrics","events","flows"}` | n/a | n/a | which `Cite.source` values must have `ts` inside the window; KB/topo sources are exempt | gate.py:25 |
| `DRIFT_LADDER` | `("R0","R1","R2","R3","R4","R5")` | n/a | n/a | the fixed model-health rung ordering `trust_banner` compares against | gate.py:67 |
| `ASK_BACK_RE` | interrogative-phrasing regex (`which\|what\|who\|where\|when\|how\|could you\|can you\|please (specify\|clarify\|confirm)`) | n/a | n/a | which zero-evidence terminal turns count as an ask-back (bypasses the gate) even without a trailing `?` | loop.py:46-48 |

## Data flow

**Inputs to `investigate()`** (loop.py:256-268):
- `question: str`, `window: WindowContext` — supplied by the caller (out of scope; e.g. predict loop / forensic chat).
- `llm: LLMClient` (F1 seam, `copilot.llm`), `adapter: ToolAdapter` (F2 seam, `copilot.adapter`).
- `cfg: Config` — loaded once via `copilot.config.load()` by the caller.
- `retriever`, `kg` — optional; `kg` is the ADR-0007 curated-KG hint map, passed only when `cfg.kg_enabled` (loop.py:270-271).
- `skills: dict[str, Skill]` — the I5 progressive-disclosure catalog (ADR-0012); `invoke` names skills to preload in full.
- `executor`, `workspace` — B2/B3 per-session sandboxes; presence alone turns on the `bash`/`present` tool specs (loop.py:327-329).
- `history: list[dict]` — prior `{"role","content"}` turns a `SessionStore` reconstructs from a session's `events.jsonl` on resume (loop.py:279-282).
- `request_context`, `fault_type`, `abstain`, `drift_state` — `fault_type`/`abstain` come from the current Prediction Record (via `copilot.emulator.fault_type` / `is_abstain`, read by the caller); `drift_state` from the record's `health.drift_state` (loop.py:284-291).

**Transform (the loop body)**:
1. Build `system` prompt = `SYSTEM_PROMPT` + resolved window bounds + optional `request_context` + optional skills catalog/fault-type hint + optional manually-invoked skill bodies (loop.py:304-326).
2. Build `tool_specs` = registry `TOOL_SPECS` (`copilot.tools`) + conditional `LOAD_SKILL_SPEC`/`BASH_SPEC`/`PRESENT_SPEC` (loop.py:327-329).
3. Build `messages` = `[system]` + (optionally compacted) `history` + `[user: question]` (loop.py:330-335).
4. Loop up to `cfg.step_cap` turns: `llm.chat(messages, tools=tool_specs)` → native `reply.tool_calls` or the owned fallback `parse_tool_calls(reply.content)` (loop.py:346-348).
5. Each tool call dispatches through `copilot.tools.dispatch(name, args, adapter, window, retriever, kg)` (registry tools) or the three local handlers (`load_skill`/`bash`/`present`); `Cite`s accumulate into `evidence` (loop.py:456-458).
6. On a terminal (no-calls) turn: ask-back check → gate (stage 1 `run_gate`, then stage 2 `self_judge` over survivors) → retry-or-return (loop.py:350-428).

**Outputs**: `Outcome(answer, events, stopped, cites)` (loop.py:79-87) — `events` is the ADR-0009 canonical trace (consumed by F4 for the live SSE stream and `events.jsonl` persistence, out of scope here); `cites` is the structured evidence this run gathered, inherited by an R6b master synthesis (`gate.run_gate`'s `prior_cites` param, gate.py:144-150).

## Calculations

- **`dispatch_cap = cfg.step_cap * cfg.tool_call_cap`** (loop.py:338) — total-dispatch hard ceiling; independent of the productive `tool_call_cap` budget, exists so a turn packing many empty/errored calls can't loop forever within one `step_cap` turn (checked loop.py:444).

- **`gathered` (which calls spend the `tool_call_cap` budget)** (loop.py:466-469):
  ```
  gathered = (not observation.startswith("error:")) if name in {load_skill, bash, present}
             else bool(cites)
  ```
  A registry call spends budget only if it actually returned `Cite`s; an empty/errored read is free. `tool_calls += 1` iff `gathered` (loop.py:469).

- **`compact_history` budget split** (loop.py:222-223):
  ```
  note_cap    = max(200, max_chars // 5)
  tail_budget = max_chars - note_cap
  ```
  Newest messages are kept verbatim while their running total stays `<= tail_budget` (at least the latest turn is always kept, loop.py:226-230); everything older collapses into one digest note.

- **`_digest_note` body truncation** (loop.py:243-253):
  ```
  cites = unique cite-ids found in the dropped turns' concatenated prose (CITE_RE)
  tail  = " cites: " + " ".join(cites)              # if any
  body  = prose[: max(0, cap - len(head) - len(tail))]
  ```
  Cites always win the budget — reserved space is subtracted from prose truncation, not the other way round. Two documented ceilings the `max_chars` bound can still be exceeded by: (1) a single oversized recent turn is kept whole (loop.py:228, comment loop.py:215-218); (2) if dropped-turn cite ids alone exceed `note_cap` they are still all kept in full (loop.py:215-218).

- **Citation range expansion `_expand_cite`** (gate.py:52-60):
  ```
  tok = tok.translate(unicode-hyphen -> ascii "-")
  if tok matches "<src>:<lo>-<hi>":
      reject (leave literal) if hi < lo or hi - lo > 64
      else expand to {"<src>:<n>" for n in lo..hi}
  else: {tok}
  ```
  Feeds `citation_check`'s fabricated-id check (gate.py:123-126) so a compressed range like `[metrics:3-5]` is legitimate only if every expanded id is a real gathered `Cite.id`.

- **`pre_gate` missing-list assembly** (gate.py:91-115), inputs = `cites: list[Cite]`, `window: WindowContext`, `entities: frozenset[str]` (from `extract_entities(question)`), `min_evidence: int`, `soft: bool` (= `abstain`):
  - sufficiency (skipped when `soft`): `len(cites) < min_evidence` → `"thin evidence: N item(s) < required M"`.
  - per-cite integrity (always checked): for `c.source in WINDOWED_SOURCES`, `c.ts is None or not (window.start <= c.ts <= window.end)` → out-of-window; `entities and c.device.lower() not in entities` → off-topic.
  - sufficiency (skipped when `soft`): every entity in `entities` must appear in `{c.device.lower() for c in cites if c.device}` → else `"off-topic: no evidence for entity ..."`.

- **`citation_check`** (gate.py:118-133): `cited = CITE_RE.findall(answer)` (after zero-width-char stripping, gate.py:120) → `expanded = union(_expand_cite(tok) for tok in cited)` → `unknown = expanded - valid_ids` → fabricated-citation failure if non-empty. Separately, every sentence (split by `_sentences`, gate.py:179-180) that matches `ENTITY_RE` but has no `CITE_RE` match → `"uncited claim: ..."`. A non-empty answer with zero citations and no other failure → `"uncited answer: ..."`.

- **`run_gate` combination** (gate.py:136-156): `all_cites = (*cites, *prior_cites)`; result = union (dict-deduped, `_result` at gate.py:76-77) of `tool_calls_ok(tool_errors).missing`, `pre_gate(all_cites, ..., soft=abstain).missing`, `citation_check(answer, {c.id for c in all_cites}).missing`.

- **`trust_banner` rung comparison** (gate.py:159-176):
  ```
  rung  = DRIFT_LADDER.index(drift_state)
  floor = DRIFT_LADDER.index(distrust_at)
  banner shown iff rung >= floor   (ValueError on either -> no banner)
  ```

- **Stage-2 abstain softening** (loop.py:378-385): when `pre_gate`/`citation_check` (stage 1) pass, `self_judge`'s `missing` is filtered — if `abstain`, only entries starting `"contradiction:"` survive (a self-judge "insufficient" verdict is dropped); otherwise all of `judged` survive unfiltered.

- **Empty-answer vs gate-fail retry budgets are separate counters** (loop.py:340-341,386-417): `retries` (gate-fail path, capped by `cfg.gate_max_retries`) and `empty_retries` (blank-turn path, intended cap `cfg.empty_answer_max_retries` — see Gotchas) never share state; an empty terminal turn is detected before the `cfg.gate_enabled` check even runs (loop.py:365-371).

## Config & schemas

This subsystem owns no YAML/JSON files on disk. It defines/consumes these in-memory schemas:

- **`Event` wire shape** (`event_wire`, loop.py:90-96): `{"type": <one of EVENT_TYPES>, "ts": <ISO-8601 UTC>, **data}`. `data` must not itself carry `type`/`ts` keys (asserted, loop.py:94-95) — this is the schema streamed live (F4) and persisted to `events.jsonl` (out of scope). `EVENT_TYPES` = `{"user_msg","assistant_msg","think","tool_call","tool_result","gate","artifact"}` (loop.py:57-59).

- **`GateResult`** (gate.py:70-73): `{ok: bool, missing: tuple[str, ...]}`. Deduped order-preserving via `_result` (gate.py:76-77, `dict.fromkeys`).

- **`Outcome`** (loop.py:79-87): `{answer: str|None, events: tuple[Event,...], stopped: None|"step_cap"|"tool_call_cap", cites: tuple[Cite,...]}`.

- **Tool-call parameter schemas** (JSON Schema, advertised to the LLM alongside registry `TOOL_SPECS`):
  - `load_skill` (loop.py:141-147): `{name: str}` required.
  - `bash` (loop.py:152-159): `{command: str}` required.
  - `present` (loop.py:165-173): `{path: str required, title: str optional}`.

- **Owned-fallback tool-call JSON** (`parse_tool_calls`/`_tool_call_json`, loop.py:180-201,569-580): a single JSON object `{"name": str, "arguments": dict}`, either the whole trimmed turn or inside a lone ` ```json ` fence — never scanned out of mixed prose (R1 hardening, #-tagged fix in the docstring).

- **Self-judge verdict JSON** (produced by the LLM per `JUDGE_SYSTEM`, loop.py:126-136; parsed loop.py:496-501): `{"pass": bool, "missing": [str], "contradictions": [str]}`. Fail-open on unparseable/missing-`pass` output (`GateResult(True)`, loop.py:497) — the deterministic stage-1 gate is the hard guarantee, not this one.

- **OpenAI-compatible chat message shapes this module writes** (R1, ADR-0004): system/user/assistant/tool dicts appended to `messages`; the assistant turn carrying tool calls is built by `_assistant_turn` (loop.py:559-566) — `{"role":"assistant","content":..., "tool_calls":[{"id","type":"function","function":{"name","arguments": json.dumps(...)}}]}`. Every following `tool` message matches one of these by `id`.

- **Compacted-history digest note** (`_digest_note`, loop.py:243-253): `{"role": "system", "content": "Investigation so far (N earlier turns compacted): " + truncated-prose + " cites: <ids>"}`.

## Gotchas

- **`cfg.empty_answer_max_retries` is referenced but does not exist on `Config`** (loop.py:392: `if empty_retries < cfg.empty_answer_max_retries`). `copilot/config.py`'s `Config` dataclass (fields at config.py:47-91) has no such field. Reproduced directly:
  ```
  $ python3 -c "from copilot.agent import investigate; from copilot.adapter import StubAdapter; \
    from copilot.config import Config; from copilot.llm import ScriptedLLM, final; from copilot.window import WindowContext; \
    investigate('x', WindowContext(0,1), llm=ScriptedLLM([final('')]), adapter=StubAdapter(), cfg=Config())"
  AttributeError: 'Config' object has no attribute 'empty_answer_max_retries'
  ```
  This fires in production any time the model returns a non-empty-tool-calls, empty-content terminal turn with the default `cfg.gate_enabled=True` (config.py:61) — i.e. any real "model said nothing" glitch. Per CLAUDE.md's codependency rule, this is a produced signal (the empty-answer retry path) with no ticket that finished wiring its config field — a missing ticket, not just a bug.

- **The three tests exercising that path are dead code, and `python3 -m copilot.agent.test_agent` hides it.** `test_empty_answer_retry_uses_a_separate_budget_from_gate_retries` (test_agent.py:285), `..._message_differs_from_gate_fail_guidance` (test_agent.py:302), and `..._exhausts_its_own_cap` (test_agent.py:314) all call `_cfg(empty_answer_max_retries=...)` (`dataclasses.replace(Config(), ...)`, test_agent.py:29-30), which raises `TypeError: unexpected keyword argument` immediately — before even reaching the `AttributeError` above. None of the three are in the `__main__` block's manual call list (test_agent.py:812-853), so `python3 -m copilot.agent.test_agent` prints "self-check OK" without ever running them. Run under pytest (`pytest copilot/agent/test_agent.py`) to see them fail/error for real.

- **`self_judge` fails OPEN on any unparseable verdict** (loop.py:497: `if not isinstance(obj, dict) or obj.get("pass", True): return GateResult(True)`). A judge-model response that isn't valid JSON, or omits `"pass"`, always passes. Deliberate (comment loop.py:488-491: stage-1 is the hard guarantee) — but it means stage 2 catches nothing when the backend is flaky/quota-limited, silently.

- **`dispatch_cap` (loop.py:338) is far looser than `tool_call_cap`.** At defaults, `step_cap * tool_call_cap = 100 * 100 = 10000` total dispatches are allowed before the hard backstop trips, vs. 100 *productive* calls. A model that mostly issues empty/errored reads can run thousands of free dispatches per investigation before `dispatched >= dispatch_cap` stops it (loop.py:444-445) — a cost/latency footgun against a misbehaving or adversarial model, not just a correctness one.

- **`tool_errors` is cleared only on a gate retry, not on an empty-answer retry** (loop.py:408: `tool_errors.clear()` inside the `retries < cfg.gate_max_retries` branch only). The empty-answer branch (loop.py:390-399) never clears it, so stale tool errors from before a blank turn still ride into the next `tool_calls_ok` check after that retry.

- **`WINDOWED_SOURCES` (gate.py:25) fully exempts KB/topology citations from the window check.** A `runbook-*`/`incident-*`/`topo:*` cite is never checked against `window.start/end` at all (not "checked and always passes" — literally skipped, gate.py:104-105), by design (their `ts` is historical or absent) — but it means a `pre_gate` call with a bogus/absent `window` cannot be caught by this path for those sources.

- **Ask-back bypasses the gate unconditionally** (loop.py:357-359: `if not evidence and _is_ask_back(text)`). Any zero-tool-call terminal turn matching `ASK_BACK_RE` or ending in `?` returns immediately with no citation/evidence check — a model could phrase an unsupported claim as a question to dodge the gate; the code accepts this tradeoff explicitly (comment loop.py:352-356, citing a 100-run audit that a trailing-`?`-only check missed ~6 in 10 genuine clarifying turns).

- **`ENTITY_RE` (gate.py:32-33) is a role-prefix whitelist, not a real device registry** — the docstring (gate.py:29-31) itself warns it must not over-match (a protocol token like `as65001` or `ge0` would become an unfulfillable "required entity") and is not intersected against the adapter's actual topology node set.
