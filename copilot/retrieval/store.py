"""copilot.retrieval.store -- the Retriever over embedded LanceDB (ADR-0006).

No server, single on-disk dataset, air-gap-clean; exact (brute-force) search at the
small N we have today, index when the corpus grows (ADR-0006 rejects the numpy/npz
interim -- build the scalable path directly). The embedder is injected, so the store
is independent of which profile produced the vectors.
"""
from typing import Iterable

import lancedb
import pyarrow as pa

from copilot.retrieval.contract import Doc, Embedder, Hit


def _schema(dim: int) -> pa.Schema:
    """Explicit table schema so an all-None provenance column is typed (string/int64), not the
    Null type lancedb infers from an all-None first batch -- a Null column breaks the node/source
    prefilter. `dim` = embedding width (nv-embedqa 1024, HashEmbedder 64), from the first vector."""
    return pa.schema([
        ("id", pa.string()), ("text", pa.string()), ("source", pa.string()),
        ("node", pa.string()), ("ts", pa.int64()),
        ("vector", pa.list_(pa.float32(), dim)),
    ])


class LanceRetriever:
    def __init__(self, embedder: Embedder, uri: str, table: str = "kb"):
        self._embedder = embedder
        self._db = lancedb.connect(uri)
        self._name = table

    def add(self, docs: Iterable[Doc]) -> None:
        docs = list(docs)
        if not docs:
            return
        vecs = self._embedder.encode([d.text for d in docs])
        rows = [{"id": d.id, "text": d.text, "source": d.source,
                 "node": d.node, "ts": d.ts, "vector": v}
                for d, v in zip(docs, vecs)]
        # ponytail: append-only, no upsert-on-id -- re-seeding would duplicate docs.
        # Swap the append path to merge_insert("id") when the S1/S2 seeder lands
        # (out of I2a scope).
        # Schema is PINNED (was inferred): a real seeded corpus has node=None on every
        # doc (the S1/S2 seeder sets no node), so inference typed the `node` column as
        # Null -> search()'s `node IN (...)` prefilter crashed ("could not convert to
        # literal of type 'Null'"). Pinning node=string/ts=int64 keeps the filter valid
        # on an all-None column. Vector dim is taken from the first row (embedder-agnostic).
        if self._name in self._db.table_names():
            self._db.open_table(self._name).add(rows)
        else:
            self._db.create_table(self._name, data=rows, schema=_schema(len(rows[0]["vector"])))

    def search(self, query: str, k: int = 5, source: str | None = None,
               nodes: set[str] | None = None) -> list[Hit]:
        if self._name not in self._db.table_names():
            return []                                    # nothing added yet
        vec = self._embedder.encode([query])[0]
        q = self._db.open_table(self._name).search(vec).metric("cosine")
        # prefilter=True filters BEFORE the ANN scan -> true top-k WITHIN the scope (a
        # post-filter would trim an all-out-of-scope top-k to nothing, so a nearby
        # incident at global rank 6 would vanish). `source` is a fixed tool constant and
        # `nodes` are topology ids from the adapter, never raw user text -> f-string safe.
        preds = []
        if source is not None:
            preds.append(f"source = '{source}'")
        if nodes is not None:
            preds.append("node IN (%s)" % ", ".join(f"'{n}'" for n in nodes))
        if preds:
            q = q.where(" AND ".join(preds), prefilter=True)
        rows = q.limit(k).to_list()
        return [Hit(doc=Doc(id=r["id"], text=r["text"], source=r["source"],
                            node=r["node"], ts=r["ts"]),
                    score=1.0 - r["_distance"]) for r in rows]  # cosine dist [0,2] -> sim [-1,1]
