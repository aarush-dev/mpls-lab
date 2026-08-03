"""copilot.skills -- Lane-Investigation (I5). Progressive-disclosure diagnostic skills loader (ADR-0012).

Steers the weak model with diagnostic METHOD (how to investigate), distinct from a runbook's
cited evidence. Only {name, description} of each skill sits in the base prompt; the body loads
on match (the agent's `load_skill` tool) or on manual invoke. Owner lane builds here.
"""
from copilot.skills.loader import Skill, catalog, load_skills

__all__ = ["Skill", "catalog", "load_skills"]
