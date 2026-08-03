---
name: query_narrowly
description: Scope every read by device + pattern before widening; the loop owns the time window.
---
The read tools (`query_metrics`, `search_logs`, `flows`) are already clamped to the
investigation window — you do NOT pass start/end (ADR-0002/0015). Narrow the ROWS instead.

1. Start at the single suspect `device` (the Prediction Record's device, or the epicenter
   named in an event). One device, one metric `pattern`.
2. Read that, cite the ids, decide. Widen (drop the device, broaden the pattern) only when
   the suspect clears.
3. `limit`/`offset` page results — they do not dump. A 200-row read you can't cite is noise
   the quality gate ignores.
4. For "who else is hit", don't scan the fabric: `walk_topology_graph device=<epicenter>`
   returns only nodes within N hops, already status-enriched.

Narrow reads keep context small (ADR-0015) and keep every cite defensible.
