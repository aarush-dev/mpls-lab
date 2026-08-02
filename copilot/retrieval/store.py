"""copilot.retrieval.store -- the Retriever over embedded LanceDB (ADR-0006).

No server, single on-disk dataset, air-gap-clean; exact (brute-force) search at the
small N we have today, index when the corpus grows (ADR-0006 rejects the numpy/npz
interim -- build the scalable path directly). The embedder is injected, so the store
is independent of which profile produced the vectors.
"""
from typing import Iterable

import lancedb

from copilot.retrieval.contract import Doc, Embedder, Hit


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
        # (out of I2a scope). Schema is inferred from the first batch (nullable
        # node/ts round-trip fine on lancedb 0.36; pin a pyarrow schema only if that
        # regresses).
        if self._name in self._db.table_names():
            self._db.open_table(self._name).add(rows)
        else:
            self._db.create_table(self._name, data=rows)

    def search(self, query: str, k: int = 5) -> list[Hit]:
        if self._name not in self._db.table_names():
            return []                                    # nothing added yet
        vec = self._embedder.encode([query])[0]
        rows = (self._db.open_table(self._name)
                .search(vec).metric("cosine").limit(k).to_list())
        return [Hit(doc=Doc(id=r["id"], text=r["text"], source=r["source"],
                            node=r["node"], ts=r["ts"]),
                    score=1.0 - r["_distance"]) for r in rows]  # cosine dist [0,2] -> sim [-1,1]
