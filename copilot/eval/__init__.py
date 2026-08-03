"""copilot.eval -- deferred replay-eval seed content (S4, ADR-0017).

The eval HARNESS is deferred until the system is finalized (ADR-0017 §Eval); this package is
only its non-blocking SEED (ADR-0017 §Nuances: content-seeding runs parallel). It carries the
labelled starter scenario set a future harness scores investigator answers against.
"""
from copilot.eval.scenarios import build_scenarios, load_seed, SEED_PATH

__all__ = ["build_scenarios", "load_seed", "SEED_PATH"]
