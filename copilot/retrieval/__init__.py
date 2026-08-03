"""copilot.retrieval -- Lane-Investigation (I2a/I2b). Retriever spine over embedded
LanceDB; KB runbooks/incidents (ADR-0006).

I2a: the `Retriever` seam (`Doc`/`Hit` shapes, `add`/`search`) backed by embedded
LanceDB (`LanceRetriever`) + profile-swapped embedder (`make_embedder`, ADR-0004
pattern). `search_runbooks`/`search_incidents` + the topology-hop filter land in I2b.
Owner lane builds here; do not edit from the other lane.
"""
from copilot.retrieval.contract import Doc, Embedder, Hit, Retriever
from copilot.retrieval.embedder import (
    HashEmbedder, LocalEmbedder, NimEmbedder, make_embedder,
)
from copilot.retrieval.seed import seed_from_dir
from copilot.retrieval.store import LanceRetriever

__all__ = [
    "Doc", "Hit", "Embedder", "Retriever", "LanceRetriever",
    "make_embedder", "NimEmbedder", "LocalEmbedder", "HashEmbedder",
    "seed_from_dir",
]
