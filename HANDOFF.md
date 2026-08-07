# Agent Handoff — Air-Gapped Predictive NOC Copilot (network + data foundation)

You are taking over an in-progress project. This file orients you. **Read the linked docs — do not re-derive what they already record.**

## What this repo is
A reproducible, air-gapped **Containerlab SD-WAN-over-MPLS** lab that produces realistic, labeled NOC telemetry for an ISRO BAH 2026 entry. Scope here = the network simulation + telemetry + data foundation (Objectives/Phases 1–2). The AI/ML/RAG modelling is a separate team's job; this repo is their **data source + clean API**.

## Read first, in this order
1. **`CLAUDE.md`** — the operating rules (authoritative, overrides defaults). Working principles, agent-model strategy, the standing 5-step workflow, and the commit-attribution policy. Follow it exactly.
2. **`PLAN.md`** — the full design: target architecture, phases, decisions, reuse list, verification checklist.
3. **`docs/01_PROJECT_OVERVIEW.md` → `05_TECHNICAL_GLOSSARY.md`** — the 5-doc set written for AI/ML readers with zero networking background: overview, analogies, code/data-API guide, copy-paste cheatsheet, glossary.
4. **`docs/SPEC-NOTES.md`** — addressing scheme + generator production rules + the per-site netem design.
5. **`docs/PHASE0ENVIRONMENT.md`** — kernel prerequisites (run before deploying on a fresh host).
6. **Component READMEs**: `generator/`, `controller/`, `trafficgen/`, `faults/`, `synthetic/`, `airgap/` (and `dataapi/schema/`).

## How it's built (component map)
- **`generator/`** — `generate.py` + Jinja2 templates render the entire lab (`clab.yml` + all per-node FRR/snmpd/qos/wireguard config + the telemetry node-mappings) from `topology-spec.yaml`. Reads only `ce_asn_base` / `wg_overlay_subnet` / `wg_port` from the spec's addressing/underlay/overlay blocks — the rest of those blocks are reference-only comments, everything else is derived from node indices in `generate.py` itself (`generator/generate.py:22-30`). Idempotent; `--check` guards addressing.
- **`frr-node/`** — the node image (FRR 10.5.1 + snmpd + pmacctd + tc + wireguard-go + rsyslog). Tag `frr-node:0.1`.
- **`telemetry/`** — `docker-compose.yml`, 11 services on docker net `clab`: Telegraf (SNMP + scrape controller) → VictoriaMetrics; Grafana (11-panel NOC dashboard); pmacctd → nfacctd (IPFIX flows); FRR rsyslog → promtail → Loki; `ldp-metrics` + `env-metrics` sidecars; Kafka (KRaft). **Universal join key = `device`.**
- **`controller/`** — simulated SD-WAN controller. Tunnel RTT/loss/jitter is **modelled**, read back from the site's netem qdisc config, not independently measured (see escalation 1 below); Prometheus on :9362.
- **`trafficgen/`** — diurnal per-VRF traffic (nc backend) so counters/flows move.
- **`faults/`** — `injectors.py` (netem/flap/BGP/kill/rekey/drift, each reversible) + `orchestrator.py` (single scenarios + `--campaign` mode) writing the ground-truth **labels timeline** (joinable on device+time). 21 scenarios.
- **`dataapi/`** — FastAPI (localhost): `/metrics /events /flows /labels /topology /datasets`; `export.py` joins everything → canonical 59-column Parquet (multi-label) (schema in `dataapi/schema/`). `ragcorpus/` seeds the RAG team.
- **`synthetic/`** — `calibrate.py` (profile from real captures) + `generate.py` (ML-scale labeled time-series in the same canonical schema; `--scale`/`--days`/`--seed`).
- **`airgap/`** — `pull-and-save.sh` / `load-offline.sh` / `verify-airgap.sh` (zero runtime egress).
- **`streaming/`** — `bridge.py` (Kafka producer → `noc.metrics` / `noc.events` / `noc.faults` / `noc.topology`, keyed by `device`) + `consume.py` (two consumer groups: `noc-predictive` from earliest, `noc-copilot` from latest).

## Current state
A 105-finding repair pass just landed (commits through `ed06dd8a`) fixing bugs across the generator, controller/faults, dataapi, synthetic, and telemetry/airgap subsystems. Full detail: the repair notes covering every changed behaviour. Topology and counts, re-derived from code:

- **148 containers** = 70 FRR (24 P + 12 PE + 34 CE: 24 branch / 6 hub / 4 dc) + 78 hosts (`generator/generate.py:846-848`, `topology-spec.yaml:14-27`).
- **Core:** 6 POPs × 4 P routers, multi-area OSPF (intra-POP mesh areas 1–6 cost 10, inter-POP ring+chords in area 0 cost 100, ABR–ABR link forced into area 0). 12 PE routers, 2 per POP, dual-homed.
- **iBGP:** route reflection — pe1+pe2 as RRs, pe3–pe12 clients = 21 sessions (full mesh would be 66).
- **VPNv4 RD** is per-PE (`<pe_loopback>:<vrf>`), not a shared `65000:<vrf>`.
- **Overlay:** 168 spoke-hub WireGuard tunnels + 3 hub-hub, 28 spokes across 6 hubs.
- **VRFs:** CORP / VOICE / GUEST. **ASNs:** branch 65101–65124, hub 65201–65206, dc 65301–65304.
- **Faults:** 21 scenarios. 4 lost their probes and are now `impact_method: modelled` (not `vm_threshold`): `hub_spoke_congest`, `bgp_cascade`, `brownout`. New `impact_method: probe_unavailable` for empty-probe windows.
- **Telemetry:** 11 compose services, 70 SNMP agents (all FRR nodes), 11 NOC Overview metric panels verified against actual emitters, plus one Router Logs Loki panel in active plugin Grafana. Router Logs filters by `device` and polls every 5 seconds; local FRR file logging remains disabled.
- **Data schema:** 59 Parquet columns, FROZEN (was 49 pre-closing-pass; 40 before multi-label/dtype; 21 pre-device-health). +10 closing-pass columns: `topology_id`,`stream`,`is_hard_negative`,`is_root`,`cascade_parent_id`,`cascade_depth`,`cascade_motif_id`,`affected_entity_count`,`injection_seed`. Row key now `(stream, topology_id, device, entity, ts)`. Companion tables: `*_events`/`*_topology_edges`/`*_paths` (`synthetic/{events,topology_paths}.py`).
- **Dataset closing pass (G1–G11):** landed — topology diversity (12 topos, 10 train + 2 held-out, `synthetic/topologies.py`), Stream F/N split, hard negatives, depth-2-3 graph-adjacent cascades, events/edges/paths tables, `injection_seed`, G9 controller 10s scrape. `verify_readiness.py` all-pass on the short multi-topology run. **Gated: G10 realism-gap AUC=0.9999 (won't transfer) — the 24-min calibration is the bottleneck; G5 ≥7h capture + recalibration is the real prerequisite before the full-scale run. See `docs/SPEC-NOTES.md`.**
- **Host:** 19 cores / 108 GB RAM / 1007 GB disk.

## What is verified vs. not
- Verified this pass (by reading/re-deriving from code): the counts above, the fault `impact_method` changes, the dataset schema column count, the airgap compose `pull_policy: never` keys, Grafana panel-to-metric mapping.
- **Verified against a live deploy on 2026-07-26.** Full 148-container lab + telemetry stack deployed; `env-metrics` sidecar exercised (three bugs found and fixed: OSPF LSA nesting under `areas`, queue iface hardcoded to `eth1`, `tc` `(dropped` token never parsed). Real dataset exported (since re-joined onto the 49-column schema by `dataapi/reschema.py`) and `profile.json` recalibrated from that capture.
- **Re-verified against a live deploy on 2026-08-03 (X1, #39).** The lab was found up but UNWIRED — a host/docker restart had kept the 148 containers but destroyed the containerlab veths (only `eth0`/`lo` survived), so the whole control plane was down (OSPF/LDP/BGP/WG all zero) while `/metrics` and `/flows` still looked alive (controller models tunnels; trafficgen drives flows). `sim-up.sh` skipped redeploy because it only checked container *existence*, not wiring — now fixed to probe `p1:eth1` and destroy+redeploy if unwired. Ran `containerlab destroy --cleanup && deploy`; after convergence: OSPF 6 Full on `p1`, LDP 6 operational, BGP-VPNv4 11 established on `pe1` (correct cmd `show bgp ipv4 vpn summary`), WireGuard 6 peers on `ce_branch1`. Telemetry re-scrapes (VM 168 tunnel series, age 0s; 70 SNMP nodes). One scenario end-to-end (`ldp_session_flap` on `pe1`) wrote label row 70 and its LDP events reached `/events`.
- **`/events` was empty on redeploy — root-caused to a stale `frr-node:0.1` image.** The committed `frr-node/rsyslog.conf` was already fixed (no `module(load="omfwd")`, see [[frr-syslog-omfwd-fix]]) but the running image still had the buggy baked conf, so rsyslogd died at boot on every node. **Rebuilt `frr-node:0.1`** (now bakes the fixed conf) so the fix is durable across redeploys — no more per-node hot-patching. **dataapi `ts` types (recorded for #40's HTTP adapter, which must normalize them):** `/metrics` PromQL value = **epoch int seconds**; `/events` `ts` = **ISO-8601 `…Z`** (`2026-08-03T10:03:23Z`); `/flows` `ts` = **naive space-string** (`2026-08-03 09:55:31`, no `T`/`Z`); `/labels` `t_start`/`t_impact`/`t_end` = **ISO-8601 `…Z`**, `lead_time` float seconds; `/topology` carries **no `ts`**.
- **Streaming layer added (`streaming/`).** Kafka broker in the telemetry compose (KRaft, `noc-kafka`, 172.20.20.60:9092 in-lab / 127.0.0.1:29092 host); host-side producer `bridge.py` → 4 topics; two consumer groups in `consume.py`. Verified against the broker + the committed 49,844-row capture in replay mode (lab down): 4 topics with intended partitions/retention, 49,844 metric + 14 fault records in 7.5 s, 4,000 predictive windows of which 363 label-joined across 4 fault types, 49,858 records consumed by the copilot group with a rendered brief. Live paths (`noc.events` from Loki, `noc.topology` from topology-meta) are NOT verified — they need a deployed lab. See `streaming/README.md`.
- Three reference datasets are committed and documented in **`DATASETS.md`** (real 49,844 rows / 391 fault rows; synthetic train 2,589,120 rows seed 42 / 159,021 fault rows / 719 episodes; synthetic holdout 2,589,120 rows `--seed 7` / 156,054 fault rows / 720 episodes, 0 `scenario_id` overlap — both synthetic files are a full day and carry all 21 fault types). Everything else under `dataapi/datasets/` is gitignored and stale (21-column pre-device-health, fails `check.py`); `synthetic/output/` was emptied of every file lacking `seed` + `calibrated_from` metadata.
- **Six audited defects fixed (this pass).** `lead_time_s` was a clamped constant (CV 0.03, 9 distinct values, 98.2% of precursor rows on four `time_to_impact_s` values) — leads now come from `faults/leadpriors.py`, CV 0.83/1.03, and `t_impact` is the per-VRF SLA crossing inside the ramp (`impact_method: ramp_derived`). Three error counters are deliberately 0 in both paths. `vrf` and `flow_bytes`/`flow_packets` are populated. Labels are multi-label lists with `n_concurrent` up to 3, which needed the campaign lock re-keyed off the bare device. `severity` is an ordinal float, `ts` a timestamp. Gate: `python3 synthetic/verify_fixes.py <train> <holdout>` — 24 checks, all PASS. Detail + reasoning in `docs/SPEC-NOTES.md`.

## How to run / verify
- **Autostart (recommended):** `sim-up.sh` runs on WSL/host boot via the systemd unit `noc-lab.service` (committed at repo root). It brings up the whole lab and, because a restart leaves the containers UNWIRED (`docker exec …-p1 ip -br link show eth1` — veths gone), destroy+redeploys when it detects that. Install once: `sudo cp noc-lab.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now noc-lab.service`. Check: `systemctl status noc-lab.service`, `journalctl -u noc-lab.service`. Manual run any time: `./sim-up.sh`.
- Regenerate: `cd generator && python3 generate.py` (then `--check`). Deploy: `cd topology && sudo containerlab deploy -t clab.yml`. Bring up stack: `cd telemetry && docker compose up -d`.
- Redeploy after a topology/image change: stop stack (`docker compose stop`), `containerlab destroy` + `deploy`, then `docker compose down && up -d` (re-resolves the clab network).
- Verification commands (control plane, WG, telemetry, faults, data API, air-gap) are in **`docs/04_USABILITY_CHEATSHEET.md`** and PLAN.md's Verification section. Use them — don't guess.

## How to work here (from CLAUDE.md — non-negotiable)
- Apply **YAGNI + `/ponytail:ponytail full`** (and `/caveman` for prose). No redundant code; shortest working diff.
- **opus** for code/reasoning/agents, **sonnet** for menial; **parallelise** (workflows with sonnet agents, or fan out parallel agents with disjoint file ownership).
- After every substantial change run: **plan → code (agents) → verify (real evidence) → document (update `docs/` + component READMEs) → commit + push**.
- **Commits:** author = `Aarush Mahajan <aarushmahajan.dev@gmail.com>`; **never** add `Co-Authored-By: Claude` or `Claude-Session` trailers.

## Known-open items (outstanding escalations from the repair pass — not fixed, recorded here)
1. **Tunnel RTT is modelled, not measured.** Genuine measurement needs the WireGuard endpoint repointed at a CE loopback over the L3VPN (`generator/generate.py:456-458`).
2. `bgp_cascade` cannot use `vm_threshold` until a device-scoped BGP metric exists in the telemetry pillar.
3. `t_impact` should be null on `probe_unavailable`, but `dataapi/export.py:206` would raise `TypeError` on that — needs an export change first.
4. Generator should emit `p_pe_ifaces` directly rather than the orchestrator inferring P-PE links from link ordering.
5. Telegraf's SNMP agent list and the generator have no single source of truth.
6. The airgap image list is triplicated across `airgap/pull-and-save.sh`, `airgap/load-offline.sh`, `airgap/verify-airgap.sh` — cannot be derived from one place. (`apache/kafka:3.9.1` was added to all three by hand; the next image will need the same three edits.)
7. Synthetic `flow_bytes`/`flow_packets` are MODELLED from `trafficgen.VRF_FLOW` × the diurnal curve, not calibrated from the real flow rows. Calibrating them needs new `profile.json` keys and a `calibrate.py` re-run against a live capture.
8. New dependencies `jsonschema` (`dataapi/requirements.txt:10`) and `kafka-python` (`streaming/requirements.txt`) must be added to the offline wheel bundle.
9. **The copilot has no inject-time signal.** `noc.faults` records are written at revert, so the copilot learns about an incident only after it ends (`streaming/consume.py:partition_faults` works around this with recency). Fix: publish the orchestrator's existing `campaign_inject` JSON to `noc.events` at inject time.
10. **Interface error counters are structurally dead in containers.** `if_in_errors`, `if_in_discards`, `if_out_errors` are constant 0 — veth pairs produce no CRC/input errors — so the synthetic path now emits 0 to match and `check.py` asserts it. OIDs are wired correctly and will populate on real hardware; the literature's top-ranked failure signal is reserved for deployment, not available here.
11. **The live path's `ramp_derived` t_impact is untested against a lab.** `faults/orchestrator.draw_ramp_seconds` + `injectors.NetemImpair.ramp(total_seconds=)` are exercised only by `python3 faults/orchestrator.py --selftest`. At the default `--duration 90` the 0.7x cap truncates almost every drawn lead, so a real campaign needs a much longer `--duration` to exercise the priors.
12. **The real capture's labels were re-joined, not re-measured** (`dataapi/reschema.py`), which is why its fault rows went 327 -> 391. Its `impact_methods` contain no `ramp_derived` because the capture predates it.

## Copilot subsystem — state
`copilot/` is a second, separate build (GitHub issues, two lanes — see `docs/copilot-build-plan.md`).
**Built:** F0–F4 (config/module skeleton, LLM-client seam, tool-adapter seam, agent loop, FastAPI
`/chat` endpoint) + **I1** (#9, `search_logs` + `flows`) + **I2a** (#10, retrieval spine) + **I2b**
(#11, `search_runbooks`/`search_incidents` + topology-hop filter) + **I3** (#12,
`walk_topology_graph`).
I1: `copilot/tools/` registry (`TOOLS`/`TOOL_SPECS`/dispatch) wires
`query_metrics`/`search_logs`/`flows` on the F2 adapter contract; `copilot/agent/loop.py`
dispatches through it; `dataapi` `/flows` gained `start`/`end` window params as a prereq.
I2a: `copilot/retrieval/` — `LanceRetriever` (add/search over **embedded LanceDB**, provenance on
every `Hit`) + `make_embedder(cfg)` profile-swapped (`nim`|`unsloth-local`, lazy) + `HashEmbedder`
test double (ADR-0006). New dep `lancedb` → `copilot/requirements.txt`.
I2b: `RETRIEVAL_TOOLS` (`search_runbooks`/`search_incidents`) over the I2a Retriever; provenance-
scoped `search(query,k,source,nodes)` **prefilters** in LanceDB; the incident hop-filter uses
`adapter.hops_within(focus,n)` (adapter owns the `/topology` shape, ADR-0006/0007). `/chat`
threads an optional `retriever` (`COPILOT_KB_URI` env, else absent).
I3: `walk_topology_graph` — deterministic BFS on real `/topology` edges (`bfs_hops`, now shared
with `hops_within`) + per-node `/metrics` live status, the join owned by
`adapter.walk_topology(focus,n,window)`. Curated KG is additive-only behind `kg_enabled`
(`get_kg(cfg)`, `COPILOT_KG_URI` env, else `None`) — correctness identical with it off. Lines are
cited `[topo:<node>]`; unknown focus → guidance, not a fabricated node; status `sanitize()`d
(ADR-0016). All self-checks green: config/llm/adapter/agent/tools/api/retrieval, plus dataapi's
`python3 test_flows_window.py`.
**The adapter now calls `dataapi` for real (A1/#40 landed).** `get_adapter` returns `HttpAdapter`
(`copilot/api/app.py`), verified live: `query_metrics`/`search_logs`/`flows`/`walk_topology_graph`
return real rows with real provenance, ts normalised to epoch int, a 502/refusal → tool observation
(not a raise, not a false "unknown device"). `POST /chat` still `503`s but now on the **LLM** only
(`"LLM backend not wired yet (R1)"`), not the adapter. F1/F2 shipped `ScriptedLLM` + `StubAdapter`
**as their stated scope**. The **adapter** stub had **no replacing ticket** in the original 34
(#4–#37) — that gap was **#40 (A1)**, now closed (`StubAdapter` stays as the test double). The
**LLM** stub was **not** in the same boat: `ScriptedLLM` was always owned by
**R1 (#16)** (`copilot/api/app.py:46` — `R1 ships the real HTTP one`). So the finished-state-`503`
was caused by the missing **adapter** ticket alone. I1–I5 are real logic that has only ever seen
canned rows; the one exception is I2a: `LanceRetriever` runs against a genuine embedded LanceDB (its
embedder is the `HashEmbedder` double). I4a's gate is pure functions with no doubles at all.

**R2a (#17) landed.** Session Store (`copilot/memory/session.py` `SessionStore`):
`sessions/<id>/{events.jsonl, meta.json}`, append + read-back, resumable across process restart.
`events.jsonl` reuses F4's exact `event_wire` schema (relocated to `copilot/agent/loop.py` so both
`api` and `memory` import it — one vocabulary, ADR-0009). Three loop changes rode with it: `Event`
now self-stamps an **emit-time** ISO-UTC `ts` at construction (was F4 send-time); `investigate(…,
history=)` threads a resumed session's prior turns between the system prompt and the new question
(multi-turn loop entry — resume was unreachable before); a `gate` event is now emitted on **pass**
too (`ok=True`), not only on fail. `POST /chat` gained `session_id` — set it to resume/persist, omit
for a stateless one-off. Self-check: `python3 -m copilot.memory.test_session` (+ new resume/session
cases in `agent`/`api` suites). Unblocks **R2b (#18)**, which reuses the emit-time schema + gate-on-pass.

**R2b (#18) landed.** Event Ledger (`copilot/memory/ledger.py` `Ledger`): append-only SQLite —
the system's timeline (Prediction Records + journal + gate outcomes), idempotent by record id
(`INSERT OR IGNORE` on the PK — a re-append is a no-op, a written row never updates), queryable
`by_device` / `by_time` (inclusive ISO-UTC range). One row = one `event_wire` dict (same schema as
R2a), with `ts`/`type`/`device` lifted into columns. Connection-per-call (dodges sqlite3's
same-thread guard under FastAPI's threadpool). Per-conversation single-writer lock landed in R6a
(#24) on the SessionStore, not the ledger (idempotent by PK, no lock needed).
Gate outcomes (pass and fail) route in through **F4's event pipeline** (`api/app.py`, not the I4b
emit site) — recorded only on a session request, keyed `f"{sid}:{ts}"`. Prediction Records land
here from their producer (PA-emulator, #20/#23). `COPILOT_LEDGER_PATH` (default `ledger.db`).
Self-check: `python3 -m copilot.memory.test_ledger` (+ a pass/fail gate-outcome case in the `api`
suite). Unblocks **R4a (#20)** / **B4**.

**B0 (#28) landed — Milestone B starts.** Workspace cage (`copilot/workspace/policy.py`): per-session
`scratchpad/`+`artifacts/` under `sessions/<id>/` (`for_session`, `Workspace.make`, idempotent) + the
one path check the workspace tools gate writes/exec on — `Workspace.writable(path)` returns the
`realpath` iff inside `scratchpad/`, else `PathPolicyError`. `realpath` defeats `..`, absolute-outside,
symlink-escape, prefix-sibling (all in the self-check). Reads unrestricted (ADR-0011/0013:
read-only-outside == only writes gated); `artifacts/` is created but NOT writable via the policy
(B3b writes it a separate way). **B2 (#30)** uses `scratchpad` as exec `cwd`. Self-check:
`python3 copilot/workspace/policy.py`.

**B1 (#29) landed.** `copilot/workspace/tools.py` — `WorkspaceTools(ws)` = read/write/edit over the B0
cage with little-coder's invariants (ADR-0011): read-before-edit (per-session `_read` set; `edit`
refuses a path not read this session), write=new-only (`write` refuses an existing file and returns
the `edit(...)` call to make instead), edit=exact-match (verbatim `old_string`; a non-unique match
needs `replace_all` or more context). Reads unrestricted; `write`/`edit` gated by `Workspace.writable`
so copy-in-to-modify works (read external → write copy in scratchpad → edit copy). Errors return as a
guidance string, never raised (ADR-0015, like `tools/registry.py`). One instance per session; **B3a
(#31)** hands one to the loop as the workspace tool family (its consumer). Self-check:
`python3 -m copilot.workspace.tools`.

**B2 (#30) landed.** `copilot/workspace/executor.py` — `Executor(ws).run(command, timeout=None)` = the
constrained subprocess (ADR-0013, **no container**). Three tool-layer constraints on the child:
**no-net** = run under `unshare -n` (fresh netns, no interfaces up → any `connect()` fails at the
kernel — verified real, not an env trick); **cwd** = the B0 `scratchpad/`; **timeout** = wall-clock cap
(default `cfg.exec_timeout_s`=30, clamped to `exec_max_timeout_s`=300), on expiry the whole process
**group** is SIGKILLed (`start_new_session` + `killpg`, so a forked grandchild can't outlive it).
**Fails closed**: if the netns can't be built (unshare missing/unprivileged), `run()` refuses
(`ExecResult.refused`, rc 126) rather than exec with the host network. Output capped at
`exec_output_cap`=65536 B. Whitelisted libs (pandas/matplotlib) are the available env, not a kernel
import filter (ADR-0013 rejected an interpreter sandbox). **B3a (#31)** wires the `bash` tool onto
`run`. Real-sandbox tests (`test_executor.py`) prove no-net + timeout bite; skip where unshare is
unavailable. Self-check: `python3 -m copilot.workspace.executor`.

**B3a (#31) landed.** The `bash` tool wired into the agent loop (`copilot/agent/loop.py`): a
per-session `Executor` (B2) is threaded into `investigate(..., executor=)` and advertised as
`BASH_SPEC` **only when wired** (like `load_skill` — no executor ⇒ no bash, byte-identical to F3).
A `bash` call routes by-name to `_run_bash` → `executor.run(command)` (no-net, timeout, cwd=B0
scratchpad); the observation (`exit=N` + stdout + stderr) rides back as the `tool_result` the model
reads next. Output is **action, not cited evidence** → no `Cite`, never gate-blocks. `api/app.py`
builds the executor per request from `for_session(sessions.root, sid)` when a `session_id` is
present (the scratchpad lives under the session dir); a one-off chat (no sid) gets no bash. Seam
tests: loop-level (`test_agent.py`, real executor) + HTTP-level (`test_api.py`, `/chat` with a
session) prove code runs + no-net/timeout still bite through the loop; skip where unshare is absent.

**B3b (#32) landed (backend).** The `present` tool wired into the loop (`copilot/agent/loop.py`):
a per-session `Workspace` is threaded into `investigate(..., workspace=)` and `PRESENT_SPEC` is
advertised **only when wired** (like bash — no session ⇒ no present). A `present(path, title?)`
call routes by-name to `_present` → `snapshot(ws, path)` (`copilot/workspace/present.py`), which
**copies the scratchpad file into the append-only `artifacts/` at present-time** (name
`NNNN-<basename>`, never reused) and returns the payload for an `artifact` event emitted right
after the `tool_result`. Snapshot **freezes the bytes now** — a later overwrite of the source
can't change the shown artifact (ADR-0009). Payload: `kind` chart (image ext → base64
`content_b64`) or code (text `content`), `mime`, `path`, capped inline (>512 KB → reference-only).
Source confined to scratchpad (reuses `Workspace.writable`); a missing/outside file → guidance
(ADR-0015), no artifact event. `api/app.py` builds the `Workspace` once and shares it with the
Executor. Seam tests: loop + HTTP (`/chat`) prove the artifact event streams with an inline render
payload and round-trips into `events.jsonl`; snapshot-on-present unit self-check
(`python3 -m copilot.workspace.present`). **Render is a separate team** — the UI (ADR-0010
Amended, out of scope here) consumes the `artifact` event; this ticket only produces it.

**R4a (#20) landed.** PA-emulator core (`copilot/emulator/emulate.py`): the ONLY copilot↔prediction
seam is the Prediction Record (PA.md §3.3, ADR-0003). `emulate_record(label)` turns a ground-truth
`/labels` fault into a full-fidelity §3.3 record — every block (risk/forecast/localization/anomaly/
decision) a deterministic readout of the label; `oracle` exact, `light`/`heavy` perturb (TTI/abstain/
drift) keyed by a scenario_id hash (no clock/RNG → reproducible). `prediction(cfg, labels, now=…)`
is the `emulate_pa`-routed seam (on→emulator, off→`real_pa(now)` callable that raises until built;
the window_end_ts crosses the seam, the caller is unchanged). `fetch_labels(base_url)` reads the
ground-truth `/labels` timeline (injectable transport) so produce→persist runs off real rows, not
a literal; the *periodic* firing that loops it is R4b. `persist(ledger, record)` lands a record in
the R2b Ledger (idempotent by alert_id). **Two consumer hooks** (tested end-to-end producer→
consumer): `is_abstain(record)`→`run_gate(…, abstain=True)` softens the pre-gate's *sufficiency*
checks (thin-evidence floor + entity-support) while keeping *integrity* (in-window/on-topic/
citation) — "anomalous, no confident call" is a valid answer (ADR-0008 §Nuances); stage-2 self_judge
still runs under abstain but only a *contradiction* blocks (a self-inconsistent answer is never
licensed). `fault_type(record)`→`investigate(…, fault_type=…)` adds a soft skill-selection steer
(`skills.fault_type_hint`, no rigid mapping, ADR-0012). **Two shape conflicts
resolved** (PA.md §3.3.1, amended §3.5): `health.drift_state`+`codebook_novelty` fold INSIDE the
record (ADR-0003 wins the §3.5 separate-endpoint split — one air-gapped seam), unblocking T1/#41;
`n_concurrent` (int≥1) invented for ADR-0014/0009/#25. Runtime callers are downstream: periodic
firing that writes records every `predict_interval_s` = R4b/#21 (ADR-0014); forensic chat threading
a frozen record into `investigate()` = R5. Self-check: `python3 -m copilot.emulator.test_emulate`
(+ new gate/loop cases in `agent` suites). Unblocks **R4b (#21)**, **R5a (#22)**.

**Plan repaired 2026-08-03, audited same day.** Repair created **#38** (codependency rule + graph
repair), **#39** (redeploy lab + verify dataapi live), **#40** (real HTTP tool adapter); #16–#27
rewritten with `Modifies` + `Consumes stub` sections + corrected `blocked_by`; #9–#15 closed. The
audit then: narrowed **#16 (R1)** back to a config-swap tested against a fake OpenAI server + one
smoke call (**not** gated on the live lab; `blocked_by #40` removed); created **#42 (E1)** as the
real end-to-end gate (real model + real dataapi + seeded KB); created **#41 (T1)** the trust gate
(spec story 14 — consumes #21's drift scalar). Single builder, so lanes are moot; 9 of 10 R tickets
touch I/F-lane files anyway. Count: **39 tickets** (#4–#42); the original set was 34 (#4–#37), not
"31". Order is `#39/#19 → #40 → #16 → (S1–S3) → #42`; **#42 closing is the first moment `/chat`
answers a real question end to end** (not #16). Full detail + both graphs:
`docs/copilot-build-plan.md`.

**Adapter landmines — all resolved in A1 (`copilot/adapter/http.py`):** `Evidence.ts` epoch-int vs
`/events` ISO / `/flows` `stamp_updated` string → normalised to int at the adapter (`_iso_to_epoch`),
so the gate's numeric compare never `TypeError`s. `/metrics` PromQL → selector synthesised from
`Filters`, per-series latest sample → one `Evidence`. `/events` `pattern`/`offset` done adapter-side
(fetch-then-filter). `/flows` window is `docker logs` print-time = approximate (known ceiling,
recorded). Transport faults → `AdapterError` → tool observation (`registry.dispatch`). See
`docs/SPEC-NOTES.md §Copilot A1`. Self-check: `python3 -m copilot.adapter.test_http`.

**E1 (#42) landed — `/chat` answers end to end for real.** `copilot/e2e/harness.py` runs the
whole stack with **zero doubles**: real gpt-oss-20b (NVIDIA-hosted nim profile,
`COPILOT_LLM_REASONING_EFFORT=high`) + live dataapi (A1) + real nv-embedqa KB seeded from
`ragcorpus/` (S1/S2) + S3 skills. It drives 7 scripted questions, writes `copilot/e2e/REPORT.md`
+ `traces/`. Latest pass: 3 cited answers, 1 correct ask-back, rest safely gated/capped; all
read/KB tools returned real rows, no crash. Two env-gated client fixes made the real backend work
(`reasoning_effort`, embedder `input_type`/`truncate` for NVIDIA-hosted embeddings) + a prompt fix
teaching the exact `[metrics:0]` citation token. **Regressions filed** (#42 mandate — file, don't
patch): #43 (range/unicode citations), #44 (embedder query/passage asymmetry), #45 (harmony leak),
#46 (all-None-node retrieval crash — **fixed** here: `store.py` pins the pyarrow schema).

**Forensic chain R5a→R6b landed.** R5a (#22) trigger poll-loop + episode dedup + restart cursor;
R5b (#23) case creation — freeze window to `cases/<id>/window/`, `prediction.json`, `ReplayAdapter`
(2nd F2 ToolAdapter, disk only), `case.md`; R6a (#24, `copilot/forensic/chat.py`) multi-chat per
case + follow-ups pinned to the FROZEN window — n chats coexist addressably (each resumes only its
own history), reads ride the ReplayAdapter (freeze guard rejects `end > T_snapshot`), the creation
run persists as chat `initial`, `/chat` routes a `case_id` to a follow-up (untrusted id sanitised,
unknown → 404). SessionStore `append` now takes a per-conversation `flock` (ADR-0009). Self-check:
`python3 -m copilot.forensic.test_chat`.
R6b (#25, `copilot/forensic/synthesis.py`) concurrent-fault fan-out — `n_concurrent > 1` spawns one
chat per fault (`fault-0` = the reused primary run) + a `master` synthesis chat. The master merges
findings (no telemetry of its own), inheriting the sub-chats' cites attributed per sub-chat
(`fault-1:metrics:0`) and passing them to the gate as first-class `prior_cites` (`run_gate` gained
the param; single-fault path byte-identical, integrity — in-window/on-topic — still enforced).
`Outcome` gained `cites` so a synthesis can inherit them. Count-driven over the frozen single-device
case (the emulator collapses concurrency to one record + a count); a real emitter of n per-device
records is **missing ticket #49**. Self-check: `python3 -m copilot.forensic.test_synthesis`.

T1 (#67, `grafana ui/plugin/src/data/`) — copilot chat data-layer seam. `DataClient.chat(request,
onEvent) → Promise<CopilotTurn>` POSTs the real copilot `/chat` (separate service, `copilotBaseUrl`
default :8100, `copilotTimeoutMs` 180s) and streams its SSE `event_wire` trace via `fetch` +
`ReadableStream` reader (not EventSource — GET-only). Pure `parseSseFrames` + `mapEventsToTurn` in
`data/copilotChat.ts`; trace model (`ChatEvent` union, `CopilotTurn`) in `types.ts`. **Mock mode +
`MockDataClient` + `telemetrySynth` + `CopilotChat` deleted** — the data client always talks to real
backends, no path can serve a canned answer. `config.mode`/`showDemoBadge` gone. Self-check:
`yarn typecheck && yarn test:ci`. Contract docs (`grafana ui/{API_CONTRACT,INTEGRATION_GUIDE}.md`)
updated.

T2 (#68, `grafana ui/plugin/src/hooks/`, `pages/CopilotPage.tsx`) — the Copilot tab answers for
real. `hooks/useCopilotChat.ts` drives sending: one `Turn` per exchange (question + streamed
`events` + folded `turn` + `sending`/`done`/`error`), a `localStorage`-persisted `sessionId`,
History mode sending `start`/`end` (epoch s) and Live omitting both. `CopilotPage` rebuilt as a thin
render — user/assistant bubbles, "investigating…" spinner, error + Retry. First in-browser proof the
mock is gone. Self-check: `yarn typecheck && yarn test:ci` (108 tests).

T3 (#69, `components/CopilotTrace.tsx`) — Claude-style collapsed trace + citation chips. Each
streamed event renders inline as a collapsed, expandable card (think / tool_call+its tool_result /
gate); the answer renders with `[source:offset]` citation chips. A chip's native hover previews the
cited evidence row; clicking it expands + scrolls to + highlights that row inside its tool_result
card (via the turn's `citeMap` from T1). Honest to the sync backend loop — cards burst in after
"investigating…", not typed live. Self-check: `yarn typecheck && yarn test:ci` (112 tests).

T4/T6 (#70/#71, commit 39820fdb) — multi-turn session (shared id, `localStorage` thread persist,
New chat, Stop) + backend `workspace` flag with UI toggle and inline/download artifact rendering.

T5 (#72, `hooks/CopilotChatContext.tsx`, `components/{CopilotChat,CopilotPanel}.tsx`) — global
collapsible side panel. `useCopilotChat` lifted into `CopilotChatProvider` (mounted once in
`App.tsx`) so the /copilot tab and the panel are ONE conversation off the same hook instance; the
chat surface extracted to `CopilotChat` and reused by both. A top-bar "Copilot" button (`AppShell`)
toggles a `@grafana/ui` `Drawer` that mounts above every route. Self-check: `yarn typecheck &&
yarn test:ci` (122 tests).

**Not done:** the emulator emitting n distinct
per-fault records (#49); C1; Milestone B. Seeder does not set `node` on incidents, so
`search_incidents`-by-device narrows to empty (S1 follow-up, noted in #46). Default `/chat` KB still
needs a seeded `COPILOT_KB_URI`.

## Git
- Remote: `github.com/aarush-dev/mpls-lab` (public). `main` and `sidd` are level. Generated artifacts (`topology/`, `dataapi/datasets/`, `airgap/images/`, WG keys, `refs/`) are gitignored — reproduce via the generators. Exception: the three reference Parquets in `DATASETS.md` are force-added and tracked.
