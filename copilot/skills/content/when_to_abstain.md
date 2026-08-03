---
name: when_to_abstain
description: Say "insufficient evidence" instead of guessing when reads are thin or contradictory.
---
A weak model's failure mode is a confident wrong answer. Abstain instead when:

1. **No evidence yet and the ask is vague** — ask ONE clarifying question BEFORE any tool call
   (ask-back bypasses the gate, ADR-0005). Once you have read evidence, a trailing question
   won't rescue a thin answer.
2. **Reads came back empty, or a `modelled` fault has no clean metric crossing** — many faults
   (`gray_failure`, `policy_drift`, the core cuts) are `impact_method=modelled`: the
   label/event is the only signal. With neither, say the metric is silent and the label is
   needed — do not invent a crossing.
3. **Evidence contradicts** — two reads disagree (a "down" claim with the neighbor still
   Established). State the contradiction; the gate flags it anyway.

Better a scoped "I can't confirm X without Y" than a fabricated root cause.
