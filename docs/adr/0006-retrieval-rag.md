# ADR-0006 — Retrieval / RAG

**Status:** accepted

## Decision

- A **`Retriever` interface** (`add(docs)`, `search(query, k) -> [(doc, score, provenance)]`) backed
  by **embedded LanceDB** (no server, single file, air-gap-clean, scales to millions; exact search at
  small N, index when large).
- **Embedder** follows the same profile pattern as the LLM (ADR-0004): local on the 3080 Ti box
  (`bge-large`/`gte-large` class) for final, NIM embedding endpoint interim.
- Retrieval is **iterative** — the agent re-queries based on what it finds (free; it's the loop
  calling `search_*` repeatedly).
- Every retrieved item carries **provenance** (source, time range, node) — required by the gate.

## Context

`search_incidents` / `search_runbooks` need an embedder + a store. Corpus is tiny today (2 runbooks,
an incident *template* only) but grows after seeding.

## Alternatives rejected

- **numpy cosine + `.npz`** as an interim store — rejected by user: no retrieval-quality downside at
  few docs, and corpus will grow, so build the scalable path directly.
- **Server vector DBs** (Milvus/Weaviate/Qdrant-server) — rejected: extra service, ops, sandbox hole.
- **Reranking / query-decomposition** — deferred, **not permanent YAGNI**. The user's stated trigger
  to revisit is directional — *"the docs will increase"* (corpus grows after seeding) — not a fixed
  size; a few-thousand-chunk threshold is only an illustrative order of magnitude.

## Nuances

- **Tool adapter over dataapi — the endpoints are not a trusted-final contract.** The user is
  explicitly *"not so sure about the endpoints as of right now"*. So the copilot must **not**
  hard-couple to exact `/metrics`/`/events`/`/topology` shapes — one thin adapter wraps them; if an
  endpoint changes, only the adapter moves. This is a hard reason for the seam, not just good
  practice. (Adapter also enforces filters + caps — ADR-0015.)
- The `search_incidents` corpus is **near-empty** — retrieval *code* ships against a fixture corpus;
  **corpus seeding is its own ticket** (ADR-0017).
- Whether the Event Ledger feeds the KB (a case verdict becoming a future hit) is ADR-0009's
  `ledger_to_kb` flag.

## Consequences

- Scale-ready + air-gap-clean + zero server. The numpy-vs-DB argument is absorbed by the interface.
