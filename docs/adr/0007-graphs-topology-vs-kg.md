# ADR-0007 — Graphs: topology vs knowledge graph

**Status:** accepted

## Decision

Two distinct "graphs", not conflated:

- **Topology graph** — from `GET /topology`, derived from the **real** `topology-spec.yaml`.
  Trustworthy. **Blast-radius / downstream = deterministic BFS on real edges.**
- **Knowledge graph (KG)** — curated, attaches incidents/runbooks to devices. Low-confidence.
  **Feature-flagged (`kg_enabled`, default-on, off-able); never on the critical path.** Correctness
  must hold with it off. The user's framing: a **backup option / "to show"** — a fallback signal to
  lean on if embeddings fall short, and a demo asset, but never load-bearing.

`walk_topology_graph` = **structure from `/topology` + live-state enrichment from `/metrics`** per
hop (the adapter owns the join). Incident relevance = **embeddings (ADR-0006) + topology-hop proximity
filter** — no curated KG required.

## Context

The user distrusted "the graph system" and nearly dropped it for the phantom "ReGain". The distrust
is specifically of the **curated KG node-attachment**, not of graphs. Separating the two dissolves it.
`/topology` (`sources.py:200`) returns **static wiring only** — no stats; connection stats live in
`/metrics`, keyed by `device`+entity. So "smart blast-radius" is a two-endpoint join, not one query.

## Alternatives rejected

- **Curated KG on the critical path** — rejected; too error-prone (attaching the right incident to the
  right node). Kept as a flagged additive signal only.
- **KG holding live stats** — impossible to keep fresh; stats come from `/metrics` at query time.

## Nuances

- Blast-radius = BFS on ~148 nodes + a batched PromQL query per frontier. Cheap.
- The KG could later be auto-populated from case verdicts (ADR-0009 `ledger_to_kb`), but that's off by
  default.

## Consequences

- Structure from real topology (trustworthy), freshness from `/metrics`, relevance from embeddings.
  The unreliable middle layer (curated KG) is off the critical path.
