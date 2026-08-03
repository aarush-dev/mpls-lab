"""copilot.retrieval.seed -- markdown corpus -> KB (S1/S2, the seeder store.py promised).

Reads ragcorpus/*.md (runbook-*.md, incident-*.md) as whole-file Docs and adds them
to a Retriever. ponytail: whole-file docs, no chunking -- chunk only when a file
exceeds the embedder context, which nothing in ragcorpus/ does today (ADR-0006
picks exact search at small N, same call applies to chunking). topology-map.md is
NOT ingested here -- it's served by the topology tool, not KB search.

Usage:  python3 -m copilot.retrieval.seed        # seeds ragcorpus/ into COPILOT_KB_URI
"""
import glob
import os

from copilot.config import load
from copilot.retrieval.contract import Doc
from copilot.retrieval.embedder import make_embedder
from copilot.retrieval.store import LanceRetriever

HERE = os.path.dirname(os.path.abspath(__file__))
RAGCORPUS_DIR = os.path.join(HERE, "..", "..", "ragcorpus")


def seed_from_dir(directory: str, retriever) -> int:
    """Glob runbook-*.md + incident-*.md in `directory`, add as Docs, return count."""
    paths = sorted(glob.glob(os.path.join(directory, "runbook-*.md")) +
                    glob.glob(os.path.join(directory, "incident-*.md")))
    docs = []
    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem.endswith("-template"):
            continue  # incident-template.md is a fill-in-the-blank skeleton, not content
        with open(path) as f:
            text = f.read()
        source = "runbook" if stem.startswith("runbook-") else "incident"
        docs.append(Doc(id=stem, text=text, source=source))
    retriever.add(docs)
    return len(docs)


if __name__ == "__main__":
    cfg = load()
    retriever = LanceRetriever(make_embedder(cfg), os.environ["COPILOT_KB_URI"])
    n = seed_from_dir(RAGCORPUS_DIR, retriever)
    print(f"seeded {n} docs from {RAGCORPUS_DIR}")
