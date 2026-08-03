"""Assert self-check: the seeded diagnostic skills all load and disclose (S3, ADR-0012).

Ground truth = the committed content dir + the real loader (copilot.skills.load_skills), NOT a
hardcoded manifest. The real risk this guards: a `.md` with half-written frontmatter is
SILENTLY skipped by the loader (loader.py: `if not name or not desc: continue`), so it would
vanish from the catalog the base prompt shows -- an unsteered fault family nobody notices.

Prior art: dataapi/check_dataset.py + ragcorpus/check_corpus.py (assert + __main__, no framework).
Run:  python3 -m copilot.skills.content.test_content
"""
import glob
import os

from copilot.skills import catalog, load_skills

HERE = os.path.dirname(os.path.abspath(__file__))

# The methodology skills the ticket (#36) names explicitly + the fault-family investigate set.
_METHODOLOGY = {"query_narrowly", "write_postmortem", "when_to_abstain"}


def test_every_committed_file_loads():
    # nothing silently skipped: one Skill per committed *.md (bad frontmatter -> dropped).
    files = [p for p in glob.glob(os.path.join(HERE, "*.md"))]
    skills = load_skills(HERE)
    assert len(skills) == len(files), (
        f"{len(files)} .md files but only {len(skills)} loaded -- a skill has broken "
        f"frontmatter and was silently dropped: {sorted(os.path.basename(f) for f in files)}")


def test_names_unique_and_descriptions_nonempty():
    skills = load_skills(HERE)
    for s in skills.values():
        assert s.description.strip(), f"{s.name}: empty description (won't disclose)"
        assert s.body.strip(), f"{s.name}: empty body (nothing to load)"


def test_catalog_lists_every_skill_not_bodies():
    skills = load_skills(HERE)
    cat = catalog(skills)
    for s in skills.values():
        assert s.name in cat and s.description in cat, f"{s.name} missing from catalog"
        # progressive disclosure: the body (method steps) stays out of the base prompt.
        first_body_line = s.body.strip().splitlines()[0]
        assert first_body_line not in cat, f"{s.name}: body leaked into catalog"


def test_starter_set_present():
    skills = load_skills(HERE)
    assert _METHODOLOGY <= set(skills), f"missing methodology skills: {_METHODOLOGY - set(skills)}"
    investigate = {n for n in skills if n.startswith("investigate_")}
    assert len(investigate) >= 6, f"expected >=6 fault-family investigate skills, got {len(investigate)}"


def _run():
    test_every_committed_file_loads()
    test_names_unique_and_descriptions_nonempty()
    test_catalog_lists_every_skill_not_bodies()
    test_starter_set_present()
    n = len(load_skills(HERE))
    print(f"copilot.skills.content self-check OK: {n} skills seeded + loadable")


if __name__ == "__main__":
    _run()
