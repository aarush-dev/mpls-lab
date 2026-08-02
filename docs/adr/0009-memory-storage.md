# ADR-0009 — Memory & storage (five domains)

**Status:** accepted

## Decision

Organize by **logical domain** (purpose + owner + lifecycle), not by database:

| Domain | Purpose | Store | Lifecycle |
|---|---|---|---|
| **Live Observability** | raw network truth, now | Loki / VictoriaMetrics / nfacctd — **queried live, never copied** | external; **≥7d retention floor** |
| **Knowledge Base** | what the agent looks up | LanceDB + markdown in git | grows, curated |
| **Event Ledger** | the system's timeline | SQLite | append-only, immutable once written |
| **Case Archive** | reproducible postmortems | `cases/<id>/` files | write-once, sealed |
| **Session Store** | conversation working memory | `sessions/<id>/` | persisted, resumable |

### Session & case layout

```
sessions/<id>/          cases/<id>/
  events.jsonl            case.md          # report + verdict — THE case's identity;
  scratchpad/                              #   human-readable markdown, CLAUDE.md-style (git-diffable)
  artifacts/             prediction.json
  meta.json              window/          # FROZEN COPY of observability for the period
                         chats/<sid>/     # investigation sessions (same shape); MANY per case
```

- **One `events.jsonl` per session, every event timestamped (ISO-8601 UTC), all events user-visible**
  (`tool_call`/`tool_result`/`gate` shown so the user watches the agent work). **Canonical event
  type enum** (used by BOTH the persisted log AND the live stream — one vocabulary, no second set):
  `user_msg | assistant_msg | think | tool_call | tool_result | gate | artifact`.
- **The ADR-0010 live trace IS this same event stream, surfaced live** — not a separate vocabulary.
  Map its stage words onto the enum: `think`→`think`, `observation`→`tool_result`,
  `answer`→`assistant_msg`. A streamed event must round-trip into `events.jsonl` unchanged. Any
  ticket producing the stream (F4) MUST emit these canonical types, not invent `answer`/`observation`
  as new persisted types.
- **`scratchpad/`** — persistent working dir (Milestone B), never auto-wiped.
- **`artifacts/`** — append-only **snapshot of each presented file at present-time** (fixes overwrite
  corruption); referenced by `artifact` events.
- **Case identity = `case.md` (report + verdict)**, never a chat. A case carries **many chats**.
  `case.md` is deliberately **human-readable markdown on disk, mirroring the CLAUDE.md convention**
  (browsable, git-diffable) — not a structured JSON blob. The user's explicit ask.
- On a Prediction Record with `n_concurrent>1`: **n investigation chats (one per fault) + a master
  chat that synthesizes** (ADR-0014).

## Alternatives rejected

- **Organize by storage tech** (SQLite/LanceDB/files) — rejected by user as unorganized.
- **Per-case workspace** — rejected; workspace is per-session, a case references its sessions.
- **Wipe temp / ephemeral workspace** — rejected; persistent scratchpad (Claude-Code style).
- **Separate trace vs chat files** — rejected; one event stream, everything visible.
- **Chat → case promotion** — rejected; cases are born only from the Forensic trigger.

## Nuances

- Case **copies** the concerned observability window into `window/` at open time, so it survives live
  expiry — the "buffer of whatever period is concerned."
- **`ledger_to_kb` flag** — a finalized case verdict feeding the Knowledge Base (a past conclusion
  becoming a future `search_incidents` hit). **Implemented, config-toggled, default ON** (user:
  "will probably do this... but there should be a flag to turn it on or off"). Echo risk noted (the
  corpus could reinforce an earlier wrong call) — the reason it stays toggleable.
- Concurrency: conversations exist in parallel; **per-conversation single-writer lock** (one chatter
  at a time); everyone can **read** any conversation. (User said "only one person can chat at a
  time" — read here as *per-conversation*, since they also said conversations "exist in parallel". A
  global single-writer is the other reading; per-conversation chosen as the less restrictive.)

## Consequences

- Three physical stores (SQLite / LanceDB / files) + live TSDB, no new server. Air-gap-clean.
- Cases are self-contained and replayable months later.
