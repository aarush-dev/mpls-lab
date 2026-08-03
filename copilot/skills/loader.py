"""copilot.skills.loader -- diagnostic skills loader (I5, ADR-0012).

A diagnostic skill is the METHOD the agent follows (how to investigate), distinct from a
runbook's cited evidence (ADR-0012). Progressive disclosure, Claude-Code style: only every
skill's {name, description} sits in the base prompt (cheap, always); the BODY loads on
demand -- the agent auto-selects by description via the loop's `load_skill` tool, or a human
manually invokes one by name. Loader = code (this file); skill CONTENT is seeded separately
(S3), so a bare/empty skills dir is normal and just means no steering yet.

Skill files are TRUSTED operator content (committed to the repo, not adapter-fetched), so
their bodies go into the system prompt un-`sanitize()`d -- unlike KB/tool text, which is
untrusted and framed (ADR-0016). The trust boundary is authorship, not the loader.

ponytail: a dict + markdown frontmatter, no plugin registry -- skills are static files read
once at startup. yaml is already installed (config.py), so no hand-rolled parser.
"""
import glob
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str                # the full method; loaded into context only on match/invoke


def load_skills(directory: str) -> dict[str, Skill]:
    """Parse every `*.md` in `directory` (frontmatter `{name, description}` + markdown body)
    into {name: Skill}. A file missing name or description is skipped -- a half-written skill
    must not steer the weak model. Missing dir -> empty (skills are seeded later, S3)."""
    skills: dict[str, Skill] = {}
    for path in sorted(glob.glob(os.path.join(directory, "*.md"))):
        with open(path) as f:
            meta, body = _split_frontmatter(f.read())
        name, desc = meta.get("name"), meta.get("description")
        if not name or not desc:
            continue
        skills[str(name)] = Skill(str(name), str(desc), body.strip())
    return skills


def catalog(skills: dict[str, Skill]) -> str:
    """The name+description block for the base prompt (progressive disclosure: NO bodies).
    Empty in, empty out -> the loop appends nothing when no skills are seeded."""
    if not skills:
        return ""
    lines = ["Diagnostic skills available -- call load_skill with a name to load its method:"]
    lines += [f"- {s.name}: {s.description}" for s in skills.values()]
    return "\n".join(lines)


def fault_type_hint(fault_type: str | None) -> str:
    """A soft steer for skill selection (ADR-0012, R4a): tells the model the Prediction Record's
    `fault_type` so it can pick the matching diagnostic skill by description. Deliberately NOT a
    rigid `fault_type -> skill` map (ADR-0012 rejects that) -- it's context, the agent still
    chooses. Empty in -> empty out, so the base prompt is unchanged when no prediction is wired."""
    if not fault_type:
        return ""
    return (f"The current prediction flags a {fault_type!r} fault -- prefer the diagnostic skill "
            "whose description matches it, if one applies.")


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return ({frontmatter}, body). Frontmatter = the first `---`-fenced YAML block at the
    top of the file; no fence -> ({}, text) so the file falls out (no name/description)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)                          # closing fence
    if end < 0:
        return {}, text
    import yaml
    meta = yaml.safe_load(text[3:end]) or {}
    return (meta if isinstance(meta, dict) else {}), text[end + 4:]
