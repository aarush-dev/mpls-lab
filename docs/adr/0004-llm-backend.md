# ADR-0004 — LLM backend

**Status:** accepted

## Decision

The LLM backend is **one OpenAI-compatible HTTP client**, config = `{base_url, model, api_key}`,
selected by a **master-config profile**:

- `gemma` (current) → on-prem gemma-4 at `10.0.0.5:8888` (OpenAI-compatible, keyless). Default profile.
- `nim` (interim) → NVIDIA NIM / hosted, `gpt-oss-20b` (or a hosted nemotron via `.env` model override).
- `unsloth-local` (final) → a local API URL served on the air-gapped network.

Model/provider swap = **one config line**. Native OpenAI function-calling when the endpoint supports
it, an owned JSON/ReAct parser as fallback (ADR-0005).

`gemma` carries its **own** base-url env (`COPILOT_LLM_BASE_URL_GEMMA`), not `nim`'s
`COPILOT_LLM_BASE_URL` — a hosted-NIM `.env` sets the latter to the nvidia URL, which would otherwise
hijack the gemma profile. The served model id is server-mangled (`mtp-gemma-4-26B-A4B-it-Q8_0`) and
has already changed once; it is pinned in the profile and overridable via `COPILOT_LLM_MODEL_GEMMA`.

## Context

Both the interim (NIM) and final (unsloth) runtimes expose OpenAI-compatible HTTP, so the loop never
sees the difference. Engine choice (llama.cpp vs vLLM vs NIM) is irrelevant above the HTTP line. The
final host gives "just a local API link" — hardware is not the copilot's concern.

## Alternatives rejected

- **CPU-only llama.cpp / GGUF as the assumed runtime** — moot; both real runtimes are HTTP APIs.
- **Fail-closed `deployment_mode` gating** (forbid NIM in an `airgapped` mode) — rejected by user as
  over-engineering; the master config already handles backend selection.

## Nuances

- **NIM breaks the air-gap** — every prompt (telemetry, logs, evidence) leaves to a third party.
  Acceptable as a dev scaffold; the air-gap claim only holds on `unsloth-local`.
  `# ponytail:` NIM egress isn't air-gapped; upgrade path = point the profile at unsloth-local. No
  enforcement code built.
- **API key** never in the repo or chat — `.env` (gitignored) / env var, read at implement-time.

## Consequences

- Backend is a swappable dependency; the loop is model-agnostic.
- Any eval/demo that proves the air-gap property must run on `unsloth-local`, not NIM (ADR-0017).
