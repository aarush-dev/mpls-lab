"""copilot.e2e.ask -- ask ONE real question end to end (E1/#42 stack), print the trace + answer.

Reuses harness.setup() -> the real nim gpt-oss client + live dataapi + freshly-seeded KB + skills.
For a human to drive the copilot by hand without the full 7-question REPORT run.

Usage:  COPILOT_LLM_API_KEY in copilot/.env (or env), lab + dataapi up, then:
    python3 -m copilot.e2e.ask "Is PE router pe6 healthy right now?"
    python3 -m copilot.e2e.ask                      # uses a default question
"""
import sys
import time

from copilot.agent import investigate
from copilot.e2e.harness import WINDOW_S, setup
from copilot.window import WindowContext

DEFAULT_Q = "Is PE router pe6 healthy right now? Check its live metrics and cite them."


def main():
    q = " ".join(sys.argv[1:]).strip() or DEFAULT_Q
    cfg, client, adapter, retriever, skills, _ = setup()
    now = int(time.time())
    print(f"\nQ: {q}\n" + "-" * 60)
    t0 = time.time()
    out = investigate(q, WindowContext(now - WINDOW_S, now), llm=client, adapter=adapter,
                      cfg=cfg, retriever=retriever, skills=skills)
    for e in out.events:
        d = e.data
        if e.type == "tool_call":
            print(f"  -> {d['name']}({d.get('arguments')})")
        elif e.type == "tool_result":
            print(f"     {d['name']}: {d.get('n')} row(s)")
        elif e.type == "gate":
            print(f"     [gate blocked, retry] {'; '.join(d.get('missing') or [])[:120]}")
    print("-" * 60)
    print(f"ANSWER ({time.time() - t0:.0f}s, stopped={out.stopped}):\n\n{out.answer}\n")


if __name__ == "__main__":
    main()
