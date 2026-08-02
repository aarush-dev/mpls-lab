"""copilot -- the Predictive NOC Copilot subsystem (LLM-facing half of the pipeline).

Two systems on one agent core: Forensic (auto-fires on a PA alert, freezes, reports)
and Query (human asks, cited answers). See CONTEXT.md for vocabulary, docs/adr/ for
decisions, docs/copilot-build-plan.md for the ticket map.

Package layout mirrors the two build lanes (disjoint file ownership):
  Lane-Investigation: adapter tools retrieval agent skills
  Lane-Runtime:       llm memory window emulator forensic
  Convergence:        api demo
"""
from copilot.config import Config, load

__all__ = ["Config", "load"]
