"""copilot.api -- Convergence (F4). FastAPI chat service; streamed timestamped
step-trace (ADR-0010) driving the F3 agent loop. Owner lane builds here.

F4: POST /chat streams the canonical ADR-0009 events (`event_wire` -- one schema
for stream + store) as SSE, each stamped ISO-UTC `ts`. LLM client + tool adapter
are injected deps (tests/R1 override them). Run: uvicorn copilot.api.app:app.
"""
from copilot.api.app import app  # so `uvicorn copilot.api:app` works

__all__ = ["app"]
