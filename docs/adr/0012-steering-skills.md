# ADR-0012 — Steering via diagnostic skills

**Status:** accepted

## Decision

The (weak) model is steered by **diagnostic skills** — Claude-Code-style progressive-disclosure files:

- A `skills/` dir of markdown, each with frontmatter `{name, description}` + a body.
- Only `name + description` of every skill sits in the base prompt (cheap). The **body loads on
  demand** when the situation matches.
- **Agent auto-selects** by description; a **human can manually invoke** a named skill in a chat
  (Claude-Code `/skill` style). The Prediction Record's `fault_type` is context the agent uses to
  pick — no rigid mapping.
- Skills = fault-family playbooks, methodology ("how to write a postmortem", "when to abstain",
  "how to query narrowly"), and tool recipes ("how to blast-radius").

## Context

A small model won't invent diagnostic discipline; it needs explicit procedure. This is distinct from
runbooks.

## Skill vs Runbook

| | Runbook (KB, ADR-0006) | Diagnostic skill |
|---|---|---|
| What | knowledge *about* a fault | *method* the agent follows |
| Role | **evidence**, retrieved + cited | **instructions**, loaded into context |
| Store | LanceDB | `skills/` markdown, progressive disclosure |

## Alternatives rejected

- **Single static system-prompt playbook** — rejected; progressive disclosure keeps the base prompt
  small and loads the relevant procedure.
- **Deterministic `fault_type → skill` auto-load only** — softened; agent selects + human invokes.

## Nuances

- Loader = **code** (ticket). Skill **content = seeded separately** (ticket, like the corpus).
- Directly serves context management (ADR-0015): "how to query narrowly" is a skill.

## Consequences

- The diagnostic discipline the small model can't be trusted to invent is encoded and loadable.
