# Copilot — Window, Eval, Skills, Workspace

## Purpose
Four unrelated support subsystems bundled under one doc by ownership boundary, not by function:

- **window** — two things share the name. (1) `copilot/window/build.py`: assembles the PA model's
  per-entity `(L=168, C=28)` telemetry tensor from live `dataapi` exports, for the real-PA client
  (`copilot/emulator/real_pa.py`). (2) `copilot/window/__init__.py`: `WindowContext`, the
  investigation-time-range struct the agent loop threads through every read tool call (live /
  query / forensic / salvage resolutions). Despite the shared package, these do not call each other.
- **eval** — `copilot/eval/scenarios.py`: a frozen, non-blocking seed of labelled ground-truth
  scenarios for a *future* replay-eval harness (harness itself is deferred, not built).
- **skills** — `copilot/skills/loader.py` + `copilot/skills/content/*.md`: progressive-disclosure
  diagnostic-method files the investigator agent loads by name mid-investigation.
- **workspace** — `copilot/workspace/{policy,executor,tools,present}.py`: the scoped coding-agent
  cage (B0–B3b) — per-session scratchpad, path-confined read/write/edit, no-net sandboxed bash
  exec, and snapshot-on-present for chart/code artifacts.

Pipeline position: `window.build` feeds `emulator/real_pa.py::RealPA.predict`, upstream of the
Prediction Record. `WindowContext` and `workspace.*` are wired into `agent/loop.py` and
`api/app.py`, in the request path of every `/chat` call. `skills` is loaded once per process and
injected into the loop's system prompt. `eval` is not wired into any runtime path — it is a
committed data file plus its own generator/self-check.

## Entry points
No FastAPI routes or CLI commands live in these paths directly (the HTTP surface is
`copilot/api/app.py`, outside this doc's ownership). Every module instead exposes a `__main__`
self-check, run directly:

```
python3 -m copilot.window.build          # PA-A5: fabricate a 2-entity export_df, assert (L,C) shape
python3 -m copilot.window.vocab          # print CHANNELS + entity/site type code maps
python3 -m copilot.window.test_window    # R3: WindowContext resolution self-check
python3 -m copilot.eval.scenarios        # runs copilot.eval.test_scenarios._run()
python3 -m copilot.eval.scenarios --write  # regenerate scenarios.jsonl from faults/labels/labels.jsonl
python3 -m copilot.skills.test_skills    # loader self-check (synthetic fixture skills)
python3 -m copilot.skills.content.test_content  # asserts every content/*.md loads + discloses
python3 -m copilot.workspace.policy      # B0: path-containment self-check
python3 -m copilot.workspace.executor    # B2: no-net/timeout/cwd self-check (skips if unshare unavailable)
python3 -m copilot.workspace.present     # B3b: snapshot-on-present self-check
python3 -m copilot.workspace.tools       # B1: read/write/edit invariants self-check
```

pytest-collectable tests (documented for how-to-run only, per instructions):
`copilot/window/test_window.py`, `copilot/eval/test_scenarios.py`, `copilot/skills/test_skills.py`,
`copilot/skills/content/test_content.py`, `copilot/workspace/test_workspace.py`,
`copilot/workspace/test_executor.py` — run via `python3 -m pytest copilot/workspace/test_executor.py`
(explicit in its own docstring; needs `unshare -n` to exercise the real boundary, else skips).

Indirect entry points (consumers, outside this doc's ownership, cited for context):
- `copilot/emulator/real_pa.py:69` calls `build_windows(now, self.channels(), etc_of=entity_type_code, stc_of=site_type_code)`.
- `copilot/api/app.py:110` `get_skills()` reads `COPILOT_SKILLS_DIR` env and calls `load_skills(d)`.
- `copilot/api/app.py:283-284` builds `for_session(sessions.root, sid)` + `Executor(ws, ...)` per chat request when `req.workspace` is set.
- `copilot/agent/loop.py:317-324` calls `catalog(skills)` / `fault_type_hint` / `_load_skill`; `loop.py:544` calls `snapshot(ws, path, ...)`.

## Modules

### window
- `copilot/window/build.py` — live telemetry → per-entity `(L, C)` window tensor for PA scoring.
  - `build_windows(now_iso, channels, *, L=168, step=30, etc_of=None, stc_of=None, export_df=None) -> dict[str, dict]` — `copilot/window/build.py:49`
  - `_export_df(start, end, step)` — lazy import of `dataapi.export.export_df`, sys.path shim — `copilot/window/build.py:35`
  - `_epoch(now_iso)` — ISO8601 (incl. bare `Z`) → epoch seconds — `copilot/window/build.py:28`
  - `_selfcheck()` — `copilot/window/build.py:88`
- `copilot/window/vocab.py` — the model's authoritative channel order + metadata-embedding code tables.
  - `CHANNELS: list[str]` (28 entries) — `copilot/window/vocab.py:17`
  - `entity_type_code(entity_type) -> int` — `copilot/window/vocab.py:36`
  - `site_type_code(site_type) -> int` — `copilot/window/vocab.py:40`
- `copilot/window/__init__.py` — `WindowContext`, the investigation time-range struct (R3, ADR-0002).
  - `WindowContext` (frozen dataclass: `start, end, frozen=False`) — `copilot/window/__init__.py:21`
  - `.live(cfg, now)` — `copilot/window/__init__.py:28`
  - `.query(start, end, cfg, now)` — `copilot/window/__init__.py:32`
  - `.forensic(t_snapshot, cfg)` — `copilot/window/__init__.py:41`
  - `.salvage(buildup_start, now, cfg)` — `copilot/window/__init__.py:45`
  - `.t_snapshot` property — `copilot/window/__init__.py:52`

### eval
- `copilot/eval/scenarios.py` — projects `faults/labels/labels.jsonl` ground truth into the frozen eval seed.
  - `FAMILIES: frozenset[str]` (5 coarse families) — `copilot/eval/scenarios.py:29`
  - `_root_cause(label) -> dict` — normalizes `target` (dict or bare device string) to `{device, ...}` — `copilot/eval/scenarios.py:37`
  - `_scenario(label) -> dict` — one label row → one scenario record — `copilot/eval/scenarios.py:55`
  - `build_scenarios(labels) -> list[dict]` — pure curation: first (min `scenario_id`) row per distinct `type` — `copilot/eval/scenarios.py:68`
  - `load_seed(path=SEED_PATH) -> list[dict]` — `copilot/eval/scenarios.py:86`
  - `write_seed(labels_path=LABELS_PATH, out_path=SEED_PATH) -> list[dict]` — `copilot/eval/scenarios.py:91`
- `copilot/eval/__init__.py` — re-exports `build_scenarios, load_seed, SEED_PATH` — `copilot/eval/__init__.py:7`
- `copilot/eval/scenarios.jsonl` — the committed 21-row seed (schema in Config & schemas below).

### skills
- `copilot/skills/loader.py` — parses `*.md` skill files into `{name: Skill}`; builds the base-prompt catalog string.
  - `Skill` (frozen dataclass: `name, description, body`) — `copilot/skills/loader.py:23`
  - `load_skills(directory) -> dict[str, Skill]` — `copilot/skills/loader.py:29`
  - `catalog(skills) -> str` — name+description block, bodies excluded — `copilot/skills/loader.py:44`
  - `fault_type_hint(fault_type) -> str` — soft steer string for skill selection — `copilot/skills/loader.py:54`
  - `_split_frontmatter(text) -> tuple[dict, str]` — YAML frontmatter parse via `yaml.safe_load` — `copilot/skills/loader.py:65`
- `copilot/skills/__init__.py` — re-exports `Skill, catalog, fault_type_hint, load_skills` — `copilot/skills/__init__.py:7`
- `copilot/skills/content/*.md` — 10 committed skill files (3 methodology + 6 fault-family "investigate_*" + `write_postmortem`); content documented under Config & schemas.

### workspace
- `copilot/workspace/policy.py` — B0: path-containment policy + per-session dir layout.
  - `PathPolicyError(Exception)` — `copilot/workspace/policy.py:20`
  - `Workspace` (`session_dir`, `scratchpad`, `artifacts`) — `copilot/workspace/policy.py:25`
  - `Workspace.make()` — idempotent mkdir of both dirs — `copilot/workspace/policy.py:35`
  - `Workspace.writable(path) -> str` — realpath-containment check against `scratchpad/` — `copilot/workspace/policy.py:42`
  - `artifact_path(sessions_root, sid, name) -> str` — sanitizes untrusted `(sid, name)`, containment-checks against `artifacts/`, 404-safe — `copilot/workspace/policy.py:55`
  - `for_session(sessions_root, sid) -> Workspace` — `copilot/workspace/policy.py:73`
- `copilot/workspace/executor.py` — B2: constrained subprocess runner (no-net, timeout, cwd cage).
  - `_nonet_ok() -> bool` — probes `unshare -n true` once — `copilot/workspace/executor.py:43`
  - `ExecResult` (dataclass: `returncode, stdout, stderr, duration_s, timed_out=False, refused=False`) — `copilot/workspace/executor.py:52`
  - `Executor(ws, timeout_s=30, max_timeout_s=300, output_cap=65536)` — `copilot/workspace/executor.py:65`
  - `.run(command, timeout=None) -> ExecResult` — `copilot/workspace/executor.py:78`
  - `._cap(s) -> str` — char-truncation with marker — `copilot/workspace/executor.py:109`
- `copilot/workspace/tools.py` — B1: read/write/edit over the B0 cage, "little-coder" invariants.
  - `WorkspaceTools(ws)` — per-session, holds `_read: set[str]` — `copilot/workspace/tools.py:32`
  - `.read(path) -> str` — unrestricted path, records realpath as read — `copilot/workspace/tools.py:41`
  - `.write(path, content) -> str` — new-file only, refuses existing — `copilot/workspace/tools.py:54`
  - `.edit(path, old_string, new_string, replace_all=False) -> str` — exact-match replace, read-before-edit gated — `copilot/workspace/tools.py:70`
  - `._resolve(path) -> str` — `copilot/workspace/tools.py:101`
- `copilot/workspace/present.py` — B3b: snapshot-on-present into `artifacts/` + the `artifact` event payload.
  - `_IMAGE_EXT: set[str]` (7 extensions) — `copilot/workspace/present.py:32`
  - `_INLINE_CAP = 512 * 1024` bytes — `copilot/workspace/present.py:33`
  - `Artifact` (frozen dataclass: `name, path, source, kind, mime, size, title=None, content=None, content_b64=None`) — `copilot/workspace/present.py:37`
  - `.event() -> dict` — the wire payload for the loop's `artifact` event — `copilot/workspace/present.py:52`
  - `snapshot(ws, path, *, title=None) -> Artifact` — `copilot/workspace/present.py:66`
  - `_reserve(artifacts_dir, base) -> tuple[str, str]` — `O_CREAT|O_EXCL` append-only naming — `copilot/workspace/present.py:96`
- `copilot/workspace/__init__.py` — re-exports the public surface of all four modules — `copilot/workspace/__init__.py:8`

## Parameters

| name | default | env-var/CLI-flag | units | what it controls | source (file:line) |
|---|---|---|---|---|---|
| `L` | `168` | `build_windows(L=...)` kwarg | timesteps | window length (rows) of the PA model tensor | `copilot/window/build.py:49` |
| `step` | `30` | `build_windows(step=...)` kwarg | seconds | grid spacing between window timesteps | `copilot/window/build.py:49` |
| `CHANNELS` | 28-name list | — (vendored copy of `/v1/health.channels`) | — | model's channel order; columns of the window tensor | `copilot/window/vocab.py:17` |
| `_ENTITY_TYPE_CODE` | `{device:0, tunnel:1, interface:2}` | — | code | entity-type metadata-embedding code; unknown → `0` | `copilot/window/vocab.py:32` |
| `_SITE_TYPE_CODE` | `{branch:0, dc:1, hub:2, core:3, pe:4}` | — | code | site-type metadata-embedding code; unknown → `0` | `copilot/window/vocab.py:33` |
| `cfg.window_x_min` (X) | `10` | `Config.window_x_min` (`copilot/config.py:71`) | minutes | rolling/forensic window length; `WindowContext.live`/`.forensic` use `start = end - X*60` | `copilot/window/__init__.py:28,42` |
| `cfg.window_x_max` (X_max) | `60` | `Config.window_x_max` (`copilot/config.py:72`) | minutes | salvage lookback cap; `WindowContext.salvage` clamps `start >= now - X_max*60` | `copilot/window/__init__.py:49` |
| `FAMILIES` | 5-member frozenset | — | — | coarse fault families the eval seed must cover (asserted in self-check) | `copilot/eval/scenarios.py:29` |
| `SEED_PATH` | `<eval dir>/scenarios.jsonl` | — | — | committed eval-seed file the harness reads | `copilot/eval/scenarios.py:33` |
| `LABELS_PATH` | `<eval dir>/../../faults/labels/labels.jsonl` | — | — | ground-truth source `write_seed` regenerates from | `copilot/eval/scenarios.py:34` |
| `_METHODOLOGY` | `{query_narrowly, write_postmortem, when_to_abstain}` | — | — | skill names required present by `test_content.py` | `copilot/skills/content/test_content.py:19` |
| `COPILOT_SKILLS_DIR` | unset → skills disabled | env var | — | directory `load_skills()` reads; unset → `get_skills()` returns `None` (no catalog, no `load_skill` tool) | `copilot/api/app.py:116` |
| `timeout_s` | `30` | `Executor(timeout_s=...)`; app wires `cfg.exec_timeout_s` (`copilot/config.py:93`, default `30`) | seconds | default per-`run()` wall-clock cap | `copilot/workspace/executor.py:70` |
| `max_timeout_s` | `300` | `Executor(max_timeout_s=...)`; app wires `cfg.exec_max_timeout_s` (`copilot/config.py:94`, default `300`) | seconds | ceiling a per-call `timeout` is clamped to | `copilot/workspace/executor.py:70` |
| `output_cap` | `65536` | `Executor(output_cap=...)`; app wires `cfg.exec_output_cap` (`copilot/config.py:95`, default `65536`) | chars | stdout/stderr truncation cap per run | `copilot/workspace/executor.py:70` |
| `_NO_NET` | `("unshare", "-n")` | — | argv prefix | sandbox command every exec is wrapped in | `copilot/workspace/executor.py:40` |
| `_IMAGE_EXT` | `{.png,.jpg,.jpeg,.gif,.svg,.webp,.bmp}` | — | — | extensions classified `kind="chart"`; else `kind="code"` | `copilot/workspace/present.py:32` |
| `_INLINE_CAP` | `524288` (512 KiB) | — | bytes | file-size cutoff for inline `content`/`content_b64`; over → reference-only | `copilot/workspace/present.py:33` |

## Data flow

**window.build.build_windows** (real-PA scoring path):
`copilot/emulator/real_pa.py:69` → `build_windows(now, channels, etc_of, stc_of)` →
`_export_df(start, end, step)` (`copilot/window/build.py:35`), which lazily imports
`dataapi.export.export_df` (sys.path-injected sibling package) → returns a long labeled
DataFrame (`ts, device, entity, entity_type, site_type, vrf, <channel columns...>`) covering
`[now-(L-1)*step, now]` at `step`-second cadence → grouped by `(device, entity, entity_type)` →
each group reindexed onto an `L`-row UTC grid ending at `now` → columns selected in `CHANNELS`
order, missing channels filled `NaN` → serialized to JSON with `NaN → null` → output
`{entity: {window: L×C list-of-lists, entity_type, vrf, device, etc, stc}}`. In tests,
`export_df=` is injected so no live `dataapi`/VM dependency is needed
(`copilot/window/build.py:88-119` self-check).

**window.vocab**: static, sourced once from the PA model's training manifest
(`bah-data:/prepared/manifest.json` `vocabs`, per docstring `copilot/window/vocab.py:9-12`) — not
re-derived at runtime except the optional `/v1/health.channels` cross-check done by the caller
(`copilot/emulator/real_pa.py:59-64`), outside this doc's ownership.

**window.WindowContext**: `cfg` (a `Config` instance, `window_x_min`/`window_x_max`) and `now`
(wall-clock epoch, or `t_snapshot` for forensic) come from the calling request in `agent/loop.py`
/ `api/app.py` (outside ownership) → one of the four classmethods resolves `(start, end, frozen)`
→ consumed by `copilot/adapter/contract.py` `Filters.validate` (outside ownership) to clamp every
read-tool call's time bounds, and by `agent/loop.py:302-303` to state the resolved bounds in the
system prompt every turn.

**eval.scenarios**: input = `faults/labels/labels.jsonl` (one JSON object per fault episode,
produced by `faults/orchestrator.py`, outside ownership) → `build_scenarios` picks the
lexicographically-first `scenario_id` per distinct `type` → `_scenario` projects each picked
label into `{scenario_id, fault_type, family, root_cause, tti_s, severity, signature}` → written
to `copilot/eval/scenarios.jsonl` by `write_seed` (`--write` CLI flag). `family` is computed by
calling `copilot.emulator.emulate.family(fault_type)` (`copilot/eval/scenarios.py:26`, outside
ownership) — the *one* source of truth for the fine→coarse mapping, not duplicated here.
`load_seed()` is the read side any future harness calls; nothing in the current repo consumes it
at runtime (ADR-0017 defers the harness itself).

**skills.loader**: input = every `*.md` under a directory (committed `copilot/skills/content/`,
or a test tmpdir) → `load_skills` splits YAML frontmatter (`---\n...\n---`) from markdown body per
file, skips a file missing `name`/`description` → `{name: Skill}` → `catalog()` renders only
`name`+`description` into the system prompt base text (`agent/loop.py:317`); a skill's `body` is
withheld until either the model calls `load_skill` (`agent/loop.py:503-509`, `_load_skill`) or a
human manually `invoke`s it by name (`agent/loop.py:319-323`), at which point the full body is
spliced into the system prompt for that turn only.

**workspace**: `for_session(sessions_root, sid)` (`api/app.py:283`) creates/reuses
`sessions/<sid>/{scratchpad,artifacts}/` → `WorkspaceTools` gates model `read`/`write`/`edit` tool
calls against `Workspace.writable` (writes only; reads unrestricted) → `Executor.run` executes
model `bash` tool calls inside `unshare -n`, cwd=scratchpad, output/time capped → `snapshot()`
(triggered by the model's `present` tool call, `agent/loop.py:544`) copies a scratchpad file's
bytes into `artifacts/` under an append-only `NNNN-<basename>` name and returns an `Artifact`,
whose `.event()` becomes the `artifact` SSE/event-log entry the UI (or `GET
/sessions/{sid}/artifacts/{name}`, `api/app.py`, outside ownership) renders from.

## Calculations

**Window start (live/forensic):** `start = end - X * 60` where `X = cfg.window_x_min` (minutes →
seconds). — `copilot/window/__init__.py:29,42`

**Window start (salvage, anchored + clamped):**
`start = max(buildup_start, now - X_max * 60)` where `X_max = cfg.window_x_max` (minutes →
seconds), `buildup_start` = the fault's precursor onset time passed in by the caller. This keeps
the earliest precursor visible while bounding total window length on a long episode. —
`copilot/window/__init__.py:49`

**Export-window bounds for `build_windows`:**
`end = epoch(now_iso)`; `start = end - (L-1) * step`, giving exactly `L` step-aligned samples
inclusive of both ends when reindexed onto `pd.date_range(end=end, periods=L, freq=f"{step}s")`.
— `copilot/window/build.py:56-63`

**Window tensor shape:** `(L, C)` per entity, `L=168` timesteps × `C=len(channels)=28` columns —
`copilot/window/build.py:72` (`np.full((L, len(channels)), np.nan, ...)`). At `step=30s`, `L=168`
covers `167*30s = 5010s ≈ 83.5 minutes` of history (168 samples, 167 intervals).

**Observed-mask (implicit):** a cell is "observed" by the PA model iff `np.isfinite(v)`; this
module's only contribution is emitting `NaN → JSON null` (`copilot/window/build.py:14,79`) so the
model can reconstruct that mask downstream — no masking math happens in this package itself.

**Eval seed curation (`build_scenarios`):** per distinct `label["type"]`, keep the row with
`min(scenario_id)` (lexicographic string min) — `copilot/eval/scenarios.py:72-76`. Deterministic:
independent of input row order (asserted by `test_build_is_deterministic_and_one_per_type`,
`copilot/eval/test_scenarios.py:17`).

**`root_cause` normalization:** if `label["target"]` is a dict, `root_cause = {"device":
label["device"], **label["target"]}` — target's own `device` key (if present) overrides the
top-level one; verified 0 rows differ today (`copilot/eval/scenarios.py:44-52`). If `target` is a
bare string, `root_cause = {"device": target}`.

**`tti_s` (expected time-to-impact):** copied verbatim from `label["lead_time"]`
(`copilot/eval/scenarios.py:62`) — not recomputed; per the module docstring `lead_time = t_impact
- t_start` is computed upstream by the label producer (`faults/orchestrator.py`, outside
ownership), not here.

**Executor per-call timeout clamp:**
`t = timeout_s if timeout is None else max(1, min(int(timeout), max_timeout_s))` —
`copilot/workspace/executor.py:85`. Floors at 1s, ceilings at `max_timeout_s` regardless of what
the model requests.

**Output truncation:** `s[:output_cap] + f"\n[truncated at {output_cap} chars]"` when
`len(s) > output_cap`, applied independently to stdout and stderr — `copilot/workspace/executor.py:109-114`.

**Artifact append-only naming:** starting index `n = len(os.listdir(artifacts_dir))`, then the
first `f"{n:04d}-{base}"` for which `O_CREAT|O_EXCL` succeeds (retrying `n += 1` on collision) —
`copilot/workspace/present.py:102-110`. The listdir count is only a hint; `O_EXCL` is what
actually guarantees no two snapshots of the same basename collide, even after an earlier snapshot
was deleted (verified by `_selfcheck`, `copilot/workspace/present.py:145-151`).

**Artifact kind:** `"chart"` if `os.path.splitext(base)[1].lower() in _IMAGE_EXT` else `"code"` —
`copilot/workspace/present.py:84-85`.

**Artifact inline-vs-reference:** inline iff `size <= _INLINE_CAP` (`524288` bytes); chart →
base64-encode raw bytes into `content_b64`, code → UTF-8 decode (`errors="replace"`) into
`content`; over cap → both `None` (reference-only, served via a separate GET) —
`copilot/workspace/present.py:87-93`.

## Config & schemas

**`copilot/eval/scenarios.jsonl`** (JSON Lines, one object per fault type, 21 rows verified by
`wc -l`) — written by `write_seed`, read by `load_seed`, schema (all fields required):

| field | type | source |
|---|---|---|
| `scenario_id` | string | `label["scenario_id"]` (min per type) |
| `fault_type` | string | `label["type"]` |
| `family` | string, one of `FAMILIES` | `copilot.emulator.emulate.family(fault_type)` |
| `root_cause` | object, always has `device` key | see Calculations §root_cause normalization |
| `tti_s` | number > 0 | `label["lead_time"]` |
| `severity` | string | `label["severity"]` |
| `signature` | string | `label["signature"]` |

Example row (`copilot/eval/scenarios.jsonl:1`):
```json
{"scenario_id": "asymmetric_loss-ce_branch13-6d2c5da9", "fault_type": "asymmetric_loss",
 "family": "link-fault", "root_cause": {"device": "ce_branch13", "interface": "eth1"},
 "tti_s": 2.0, "severity": "high", "signature": "one-directional loss; loss% up with latency near-normal (asymmetric)"}
```
`root_cause` shape varies by fault type (e.g. `core_partition` carries `seam`, `half`, `n_links`,
`links` in addition to `device` — `copilot/eval/scenarios.jsonl:8`); the only guaranteed key is
`device` (`copilot/eval/test_scenarios.py:57`).

**Skill file schema** (`copilot/skills/content/*.md`, 10 committed files) — YAML frontmatter
fenced by `---` lines, then a markdown body:
```
---
name: <string, required, unique>
description: <string, required, shown in base-prompt catalog>
---
<markdown body: numbered investigation steps>
```
A file missing `name` or `description` is silently dropped by the loader
(`copilot/skills/loader.py:37-39`) — this is a footgun, see Gotchas. Committed set: 3 methodology
skills (`query_narrowly`, `when_to_abstain`, `write_postmortem`) + 6 `investigate_*` fault-family
skills (`bgp_adjacency`, `mpls_ldp`, `ospf_core`, `policy_drift`, `rr_bgp_cascade`,
`tunnel_degradation`) — `copilot/skills/content/test_content.py:19,51` requires `>=6`
`investigate_*` skills plus the 3 methodology names present.

**`Skill.event()`/catalog wire shape**: `catalog()` output is plain text, not JSON — one line
per skill: `"- {name}: {description}"`, prefixed by a fixed header line
(`copilot/skills/loader.py:49-51`).

**Workspace on-disk layout** (per session, under `sessions/<sid>/`):
```
sessions/<sid>/
  scratchpad/     # writable + exec cwd; model read/write/edit + bash land here
  artifacts/      # append-only; only snapshot() writes here; served read-only over HTTP
```
Both created idempotently by `Workspace.make()` (`copilot/workspace/policy.py:35-39`).

**`artifact` event payload** (`Artifact.event()`, `copilot/workspace/present.py:52-63`), a dict —
no `type`/`ts` keys (the loop's `Event` wrapper owns those):

| field | always present | type | notes |
|---|---|---|---|
| `name` | yes | string | e.g. `"0000-chart.png"` |
| `kind` | yes | `"chart"` \| `"code"` | |
| `mime` | yes | string | via `mimetypes.guess_type`, fallback `application/octet-stream` |
| `source` | yes | string | basename of the scratchpad file presented |
| `size` | yes | int | bytes |
| `path` | yes | string | `"artifacts/<name>"`, relative |
| `title` | only if given | string | from the model's `present` tool call |
| `content` | only if `kind="code"` and `size<=_INLINE_CAP` | string | UTF-8 text |
| `content_b64` | only if `kind="chart"` and `size<=_INLINE_CAP` | string | base64 |

**`ExecResult`** (`copilot/workspace/executor.py:52-62`, not serialized to JSON directly — rendered
to a text observation by `agent/loop.py:514-529` `_run_bash`): `returncode: int`, `stdout: str`
(capped), `stderr: str` (capped), `duration_s: float`, `timed_out: bool`, `refused: bool`.

## Gotchas
- **Two unrelated "window" concepts share a package name.** `window.build.build_windows` (PA
  model tensor assembly) and `window.WindowContext` (`window/__init__.py`, investigation time
  range) do not call each other and serve entirely different consumers
  (`emulator/real_pa.py` vs. `agent/loop.py`/`adapter/contract.py`). Reading `copilot/window/`
  expecting one coherent module is a trap — `copilot/window/__init__.py:1` vs `copilot/window/build.py:1`.
- **`CHANNELS` is a vendored copy, not the live source of truth.** It must be kept in lockstep
  with the real PA service's `/v1/health.channels`; `real_pa.py` only falls back to the vendored
  list if health is unreachable, and a PA-A8 test (outside this doc) asserts they agree
  (`copilot/window/vocab.py:7,12`). A model retrain that reorders/adds channels silently
  desyncs this file until someone updates it.
- **A skill with broken/missing YAML frontmatter is silently dropped**, not erroring — the
  investigate skill just vanishes from the catalog and the fault family goes unsteered with no
  warning (`copilot/skills/loader.py:37-39`; guarded only by
  `copilot/skills/content/test_content.py:22-28` counting files vs. loaded skills).
- **Skill bodies are un-`sanitize()`'d** — trusted because they're committed repo content, unlike
  adapter/KB text which is untrusted and framed (ADR-0016, per `copilot/skills/loader.py:10-12`).
  Any future mechanism that lets a non-committer write into the skills dir breaks this trust
  assumption silently.
- **`Executor` fails closed, not open, when `unshare -n` is unavailable** — every `run()` call
  returns `refused=True` with a `126` returncode rather than falling back to host networking
  (`copilot/workspace/executor.py:82-84`). A dev box without unprivileged-userns support (or
  missing `unshare`) makes every bash-tool call silently a no-op-with-error, not a security hole —
  but this can look like "the model refuses to run anything" if the operator doesn't know why.
- **`cwd=scratchpad` is not a filesystem jail** — `bash -c` can still write to an absolute path
  outside scratchpad (e.g. `echo x > /etc/foo`); only the *file tools* (`WorkspaceTools`) are
  path-confined via `Workspace.writable`. Raw exec trades filesystem containment for the no-net +
  timeout boundary — documented as deliberate in `copilot/workspace/executor.py:8-12,86-90`.
  Anyone assuming bash-exec output can't touch host files outside the session dir is wrong.
  Same caveat for the "trusted lib" convention (pandas/matplotlib) — nothing enforces it at
  runtime, it's prompt text only (`copilot/workspace/executor.py:23-25`).
  - **`communicate()` buffers all output in memory** — no streaming truncation; a runaway
    printer can balloon RAM before the char-cap fires post-hoc (`copilot/workspace/executor.py:93-94`).
- **`write()` refuses to overwrite; `edit()` requires prior `read()` in the *same*
  `WorkspaceTools` instance.** The read-set is per-instance/per-session and does not persist
  across a new session object even against the same on-disk directory
  (`copilot/workspace/tools.py:39`, verified by the "fresh session does not inherit the read set"
  self-check at `copilot/workspace/tools.py:159-161`). A caller that constructs a new
  `WorkspaceTools` mid-conversation loses edit access to files it already wrote earlier.
- **`snapshot()`'s freeze is real but scratchpad edits after aren't reflected** — by design
  (ADR-0009): once presented, the artifact is frozen even if the model later overwrites the same
  scratchpad file (`copilot/workspace/present.py:8,69-70`). If the model intends to show an
  *updated* chart, it must `present` again — that creates a *new* `NNNN-` artifact, it does not
  update the old one in place.
- **`eval` has no runtime consumer today.** `copilot/eval/scenarios.py` and `scenarios.jsonl`
  are pure seed content for a harness that does not exist yet (ADR-0017 §Eval, deferred); nothing
  in `copilot/agent`, `copilot/api`, or elsewhere imports `load_seed` outside its own test. Do not
  assume eval scoring is wired into `/chat` or any pipeline — it is not.
- **`family()` for the eval seed is computed by `copilot.emulator.emulate.family`, not
  reimplemented** — `copilot/eval/scenarios.py:26,60` explicitly imports it as "ONE source of
  truth"; if that mapping changes, `scenarios.jsonl` must be regenerated with `--write` or it goes
  stale (guarded by `test_seed_matches_a_fresh_build`, `copilot/eval/test_scenarios.py:36-39`).
