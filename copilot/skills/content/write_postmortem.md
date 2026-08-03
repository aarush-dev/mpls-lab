---
name: write_postmortem
description: Structure a finding — what/when, blast radius, root cause, evidence ids, fix.
---
When the investigation is done the quality gate audits the answer for sufficiency. Structure
it so the evidence is obvious.

1. **What & when** — the fault type and the bounded window (`t_start`/`t_impact`/`t_end` from
   `/labels`; cite it).
2. **Blast radius** — which devices/sites, from `walk_topology_graph` or the event fan-out.
   Name them.
3. **Root cause** — the epicenter device + mechanism (link down / process kill / netem /
   policy), tied to a runbook via `search_runbooks`.
4. **Evidence** — cite every id you relied on (metric rows, event lines, incident matches).
   The gate counts cites; an uncited claim is a bluff.
5. **Fix / next step** — what self-recovers in the lab vs what needs action.

Terse. No claim without a cite behind it.
