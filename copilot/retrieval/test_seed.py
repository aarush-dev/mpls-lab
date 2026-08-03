"""Assert self-check for the S1/S2 corpus seeder.

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Seam under test: seed_from_dir(dir, retriever) -> int, the ragcorpus/ -> KB path.
Run:  python3 -m copilot.retrieval.test_seed
"""
import os
import tempfile

from copilot.retrieval import HashEmbedder, LanceRetriever, seed_from_dir
from copilot.retrieval.seed import RAGCORPUS_DIR


def _fixture_dir():
    d = tempfile.mkdtemp()
    for fname, text in [("runbook-x.md", "runbook x body"),
                        ("incident-y.md", "incident y body"),
                        ("incident-template.md", "fill me in")]:
        with open(os.path.join(d, fname), "w") as f:
            f.write(text)
    return d


def test_seed_from_dir_skips_template_and_sets_source():
    r = LanceRetriever(HashEmbedder(), tempfile.mkdtemp())
    n = seed_from_dir(_fixture_dir(), r)
    assert n == 2, f"expected 2 docs (template skipped), got {n}"

    runbook_hits = r.search("runbook x body", k=5, source="runbook")
    assert runbook_hits and runbook_hits[0].doc.id == "runbook-x", \
        "runbook-x.md ingested with source=runbook and round-trips on search"

    incident_hits = r.search("incident y body", k=5, source="incident")
    assert incident_hits and incident_hits[0].doc.id == "incident-y", \
        "incident-y.md ingested with source=incident and round-trips on search"


def test_seed_real_ragcorpus():
    # Expected = every runbook-/incident-*.md in ragcorpus/ EXCEPT the -template
    # skeleton. Derived from the dir (not a hardcoded count) so S2 adding runbooks
    # can't silently drop a file -- every stem must round-trip back on search.
    import glob
    expected = {
        os.path.splitext(os.path.basename(p))[0]
        for p in glob.glob(os.path.join(RAGCORPUS_DIR, "runbook-*.md")) +
                 glob.glob(os.path.join(RAGCORPUS_DIR, "incident-*.md"))
        if not os.path.splitext(os.path.basename(p))[0].endswith("-template")
    }
    r = LanceRetriever(HashEmbedder(), tempfile.mkdtemp())
    n = seed_from_dir(RAGCORPUS_DIR, r)
    assert n == len(expected), f"seeded {n}, expected {len(expected)} files"

    ids = {h.doc.id for h in r.search("runbook incident bgp tunnel ospf mpls", k=n, source=None)}
    assert ids == expected, f"ingested ids != files on disk: missing {expected - ids}"
    assert any(i.startswith("runbook-") for i in ids), "no runbook-*.md ingested"
    assert any(i.startswith("incident-") for i in ids), "no incident-*.md ingested"
    assert "incident-template" not in ids, "incident-template.md must be skipped, not seeded"


def _run():
    test_seed_from_dir_skips_template_and_sets_source()
    test_seed_real_ragcorpus()
    print("copilot.retrieval.seed self-check OK")


if __name__ == "__main__":
    _run()
