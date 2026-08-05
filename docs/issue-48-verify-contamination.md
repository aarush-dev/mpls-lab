# Issue #48 — `verify_full_generation.py` contamination: how to handle

**Context:** the #48 case-freshness commit (`48d3c35b`) held `verify_full_generation.py`
out because a review-agent edit got mixed with uncommitted work. Question: how to land it safely.

## Verdict — nothing substantive to separate; the "contamination" is cosmetic

The working-tree `verify_full_generation.py` vs the already-committed `verify_full_memsafe.py`
differ **only** by:

- comment wording,
- `;`-joined vs multi-line formatting,
- two **unused** imports (`os`, `pyarrow as pa`),
- a redundant `# accumulators` comment + one blank line.

**Zero behavioral delta.** Same columns, same streaming batches, same checks, same accumulators.
Both compile. The script is **standalone** — nothing imports it (only `verify_full_memsafe.py`
mentions it in a comment). So landing or discarding it cannot break anything.

## Why provenance doesn't matter here

Git has no intermediate state to recover — no stash, no reflog entry, no editor backup. But no
recovery is needed: the agent edit added **no value** over a file already committed and trusted
(`verify_full_memsafe.py`, which is already the streaming/OOM-safe version). What actually happened:
the agent rewrote the original *pandas* `verify_full_generation.py` into ~the same streaming thing
`verify_full_memsafe.py` already was. Result = three near-identical verifiers. The real defect is
**duplication**, not a tangled edit.

## How to handle — collapse to one (YAGNI)

### Recommended — canonical name holds the streaming logic, drop the dup
```bash
# 1. drop the 2 unused imports the agent left (os, pyarrow as pa) from the import line:
#    import sys, numpy as np, pyarrow.dataset as ds, pyarrow.parquet as pq
# 2. commit standalone — NOT folded into the #48 commit
git add verify_full_generation.py
git commit -m "chore: stream verify_full_generation (dir input, OOM-safe)"
# 3. remove the now-redundant sibling
git rm verify_full_memsafe.py
git commit -m "chore: drop verify_full_memsafe — merged into verify_full_generation"
```

### Zero-touch alternative — if you distrust the edit at all
Throw it away; you lose nothing, because `verify_full_memsafe.py` already provides streaming.
```bash
git checkout HEAD -- verify_full_generation.py   # back to pandas original
# keep using verify_full_memsafe.py for full tranches
```

Either path keeps the #48 commit (`48d3c35b`, already landed) clean and untouched.

## Call

Recommended path — one verifier under the expected name, minus the two dead imports. Only real
eyeball needed = confirm you are fine deleting `verify_full_memsafe.py`.
