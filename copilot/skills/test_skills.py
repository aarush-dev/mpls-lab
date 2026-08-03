"""Assert-based tests / self-check for the diagnostic skills loader (I5, ADR-0012).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Seam under test: load_skills(dir) -> {name: Skill} and catalog(skills) -> prompt block.
Run:  python3 -m copilot.skills.test_skills
"""
import os
import tempfile

from copilot.skills import Skill, catalog, load_skills

RUNBOOK_SKILL = """---
name: bgp_flap
description: How to investigate a flapping BGP session.
---
1. Pull the session logs for the device.
2. Correlate with interface resets.
"""

NARROW_SKILL = """---
name: query_narrowly
description: Always scope a read by device + time window before widening.
---
Start with the single suspect device; widen only when it clears.
"""

NO_META = "just a body, no frontmatter\n"
HALF_META = "---\nname: only_name\n---\nbody\n"  # missing description -> skipped


def _skills_dir():
    d = tempfile.mkdtemp()
    for fname, text in [("bgp.md", RUNBOOK_SKILL), ("narrow.md", NARROW_SKILL),
                        ("plain.md", NO_META), ("half.md", HALF_META)]:
        with open(os.path.join(d, fname), "w") as f:
            f.write(text)
    return d


def test_load_skills_parses_frontmatter_and_body():
    skills = load_skills(_skills_dir())
    # two well-formed skills load; the no-meta + half-meta files are skipped (a
    # half-written skill must not steer the weak model).
    assert set(skills) == {"bgp_flap", "query_narrowly"}
    s = skills["bgp_flap"]
    assert isinstance(s, Skill)
    assert s.description == "How to investigate a flapping BGP session."
    # body = markdown AFTER the frontmatter fence, no meta leakage.
    assert s.body.startswith("1. Pull the session logs")
    assert "description:" not in s.body


def test_load_skills_empty_dir():
    assert load_skills(tempfile.mkdtemp()) == {}


def test_catalog_lists_descriptions_not_bodies():
    skills = load_skills(_skills_dir())
    cat = catalog(skills)
    # progressive disclosure: names + descriptions sit in the prompt, bodies do NOT.
    assert "bgp_flap" in cat and "How to investigate a flapping BGP session." in cat
    assert "query_narrowly" in cat
    assert "Pull the session logs" not in cat, "body must not leak into the base prompt"


def test_catalog_empty_is_blank():
    assert catalog({}) == ""


def _run():
    test_load_skills_parses_frontmatter_and_body()
    test_load_skills_empty_dir()
    test_catalog_lists_descriptions_not_bodies()
    test_catalog_empty_is_blank()
    print("copilot.skills self-check OK")


if __name__ == "__main__":
    _run()
