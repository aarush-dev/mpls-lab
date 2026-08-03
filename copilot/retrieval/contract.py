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
    # kind distinguishes asymmetric retrieval models (nv-embedqa): a passage and a query for
    # the same text must embed with different input_type to land in the right relative space.
    # add(docs)->passage, search(query)->query. Symmetric embedders (Hash/Local) ignore it.
    def encode(self, texts: list[str], kind: str = "passage") -> list[list[float]]: ...


@runtime_checkable
class Retriever(Protocol):
    def add(self, docs: Iterable[Doc]) -> None: ...
    # source + nodes scope the search by provenance (I2b: runbook|incident; a topology-hop
    # node allow-set). Both prefilter, so the top-k is taken WITHIN the scope; None = no scope.
    def search(self, query: str, k: int = 5, source: str | None = None,
               nodes: set[str] | None = None) -> list[Hit]: ...
