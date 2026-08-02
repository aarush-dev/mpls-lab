"""copilot.retrieval.contract -- the retrieval seam: shapes + protocols (ADR-0006).

A `Retriever` is add(docs) + search(query, k) -> [(doc, score, provenance)]; an
`Embedder` is just encode(texts). Both are Protocols so the LanceDB store and the
profile-swapped embedders satisfy them structurally (config-only swap, like the
LLM/tool-adapter seams). Provenance (source, node, ts) rides on every Doc and is
required by the quality gate (I4a).
"""
from dataclasses import dataclass
from typing import Iterable, Protocol, runtime_checkable


@dataclass(frozen=True)
class Doc:
    """A KB item (runbook / past-incident chunk) + its provenance. `node` is the
    topology node it concerns (feeds I2b's hop-proximity filter); `ts` gives the
    time-range provenance. Both nullable for corpus items with neither."""
    id: str
    text: str
    source: str              # runbook | incident | ...
    node: str | None = None
    ts: int | None = None    # epoch seconds


@dataclass(frozen=True)
class Hit:
    """One ranked result = (doc, score, provenance). Provenance rides on `doc`."""
    doc: Doc
    score: float             # cosine similarity, -1..1 (higher = more relevant)


@runtime_checkable
class Embedder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class Retriever(Protocol):
    def add(self, docs: Iterable[Doc]) -> None: ...
    def search(self, query: str, k: int = 5) -> list[Hit]: ...
