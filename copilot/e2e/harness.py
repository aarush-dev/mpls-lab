"""copilot.e2e.harness -- E1 (#42): the real end-to-end integration + verification harness.

NOT new product code -- it stands up the real stack and drives it, the first time a chat
runs against a REAL model + REAL dataapi + REAL seeded KB with zero doubles (F1..I5 each ran
on a stub, mock, or single smoke; this proves nothing still does):

  - LLM       : the config-selected OpenAIClient (R1) -- nim profile -> NVIDIA-hosted gpt-oss-20b
                (COPILOT_LLM_* env; gpt-oss is a reasoning model so COPILOT_LLM_REASONING_EFFORT
                sets the tier). NOT air-gapped -- the interim nim path (ADR-0004).
  - adapter   : the real HttpAdapter (A1/#40) over the live dataapi -- real telemetry rows.
  - retriever : the real nim embedder (nv-embedqa) over a freshly-seeded LanceDB of ragcorpus/
                (S1/S2). Fresh temp dir per run -> no append-dup (store.py ceiling).
  - skills    : the real S3 diagnostic playbooks (copilot/skills/content).

`setup()` asserts the wiring is live (profiles resolve, dataapi reachable, KB seeded, one model
smoke) -- the acceptance's self-check, cheap, no full loop. `run_live()` drives a scripted set
of real questions end to end, captures every canonical trace event, and writes REPORT.md +
per-question trace JSON. The question set exercises all five read/KB tools + topology + the R1
ask-back risk, and rides every #40 landmine against the REAL backend (ts typing on /events &
/flows, PromQL synth on /metrics, pattern/offset on /events, approximate /flows windowing).

Manual E2E is first-class (spec Testing): a human reads REPORT.md and judges answer quality --
a small model's real answer quality can't be unit-asserted.

Self-check (no LLM, no tokens):   python3 -m copilot.e2e.harness
Live run (burns NIM tokens):      COPILOT_E2E_LIVE=1 python3 -m copilot.e2e.harness
"""
import dataclasses
import json
import os
import time

from copilot.adapter import HttpAdapter
from copilot.agent import investigate
from copilot.config import load
from copilot.llm import OpenAIClient, make_client
from copilot.retrieval import LanceRetriever, make_embedder
from copilot.retrieval.seed import RAGCORPUS_DIR, seed_from_dir
from copilot.skills import load_skills
from copilot.window import WindowContext

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.join(os.path.dirname(HERE), "skills", "content")
TRACE_DIR = os.path.join(HERE, "traces")
REPORT = os.path.join(HERE, "REPORT.md")
WINDOW_S = 900   # look back 15 min -- wide enough that a 2h-up lab has live rows in window.

# Scripted real questions. `tools` = the read/KB tools each is meant to drive (documents intent;
# the model chooses). `landmine` names the #40 shape hazard it rides against the real backend.
QUESTIONS = [
    dict(slug="metrics-health", device="pe6",
         q="Is PE router pe6 healthy right now? Check its live metrics and cite them.",
         tools=["query_metrics"], landmine="PromQL synth on /metrics; per-series latest sample"),
    dict(slug="logs-errors", device="pe8",
         q="Search pe8's logs for any BGP or OSPF error or adjacency-change messages in the window.",
         tools=["search_logs"], landmine="/events pattern+offset done adapter-side; ISO ts->epoch"),
    dict(slug="flows-volume", device="ce_branch16",
         q="What traffic flows are moving through ce_branch16 right now, and how big are they?",
         tools=["flows"], landmine="/flows approximate log-time windowing; stamp_updated ts->epoch"),
    dict(slug="topology-blast", device="pe6",
         q="If pe6 is degrading, which downstream devices are in its blast radius and what is "
           "their live status?",
         tools=["walk_topology_graph"], landmine="BFS + /metrics enrich over real topology"),
    dict(slug="kb-runbook", device=None,
         q="Find the runbook for handling a BGP adjacency that has gone down.",
         tools=["search_runbooks"], landmine="real nv-embedqa retrieval over seeded runbooks"),
    dict(slug="kb-incident", device="ce_branch1",
         q="Have we seen tunnel degradation before? Show similar past incidents near ce_branch1.",
         tools=["search_incidents"], landmine="retrieval + topology-hop proximity filter"),
    dict(slug="ask-back-vague", device=None,
         q="Is the network ok?",
         tools=[], landmine="R1 ask-back: a vague no-device question must ask one question, not fabricate"),
]


def setup(cfg=None):
    """Stand up + assert the real stack. Returns (cfg, client, adapter, retriever, skills, kb_dir).

    Asserts (the acceptance self-check): the nim profile resolves to a real OpenAIClient, the
    live dataapi answers, the KB seeds > 0 docs over the real embedder, and one model smoke
    returns a completion. A dead endpoint / bad key fails HERE, loudly, before any question runs.
    """
    cfg = cfg or load()
    assert cfg.llm_profile in ("nim", "gemma"), \
        f"E1 runs a hosted OpenAI-compatible profile (nim|gemma), got {cfg.llm_profile!r}"
    assert cfg.llm_profile == "gemma" or cfg.llm_api_key, \
        "COPILOT_LLM_API_KEY unset -- put it in copilot/.env (gitignored)"

    client = make_client(cfg)
    assert isinstance(client, OpenAIClient), "hosted profile must resolve to the real HTTP client"

    dataapi = os.environ.get("COPILOT_DATAAPI_URL", cfg.dataapi_url)
    _assert_reachable(dataapi)
    adapter = HttpAdapter(dataapi)

    kb_dir = _fresh_dir()
    retriever = LanceRetriever(make_embedder(cfg), kb_dir)
    n = seed_from_dir(RAGCORPUS_DIR, retriever)
    assert n > 0, f"KB seeded 0 docs from {RAGCORPUS_DIR}"
    assert retriever.search("bgp adjacency", k=1), "real retrieval returned nothing after seeding"

    skills = load_skills(SKILLS_DIR)
    assert skills, f"no diagnostic skills loaded from {SKILLS_DIR} (S3)"

    reply = client.chat([{"role": "user", "content": "Reply with the single word: ok"}])
    assert reply.content, f"model smoke returned no completion (content={reply.content!r})"

    return cfg, client, adapter, retriever, skills, kb_dir


def run_live(cfg=None):
    """Drive every scripted question end to end over the real stack; write traces + REPORT.md.
    Returns the list of per-question result dicts (also the report's source of truth)."""
    cfg, client, adapter, retriever, skills, kb_dir = setup(cfg)
    os.makedirs(TRACE_DIR, exist_ok=True)
    now = int(time.time())
    results = []
    for item in QUESTIONS:
        window = WindowContext(now - WINDOW_S, now)
        t0 = time.time()
        # a real backend fault on ONE question (a crash out of investigate) is a finding to
        # RECORD, not something that kills the whole manual-E2E pass -- catch, mark, continue.
        try:
            out = investigate(item["q"], window, llm=client, adapter=adapter, cfg=cfg,
                              retriever=retriever, skills=skills)
            crashed = None
        except Exception as e:
            out, crashed = None, f"{type(e).__name__}: {e}"
        secs = round(time.time() - t0, 1)
        res = _summarize(item, out, secs, crashed)
        results.append(res)
        with open(os.path.join(TRACE_DIR, item["slug"] + ".json"), "w") as f:
            json.dump({"question": item["q"], "elapsed_s": secs, "crashed": crashed,
                       "stopped": out.stopped if out else None,
                       "answer": out.answer if out else None,
                       "events": [{"type": e.type, **e.data} for e in out.events] if out else []},
                      f, indent=2)
        print(f"[{item['slug']}] {secs}s tools={res['tool_calls']} -> {res['verdict']}")
    _write_report(results)
    print(f"\nwrote {REPORT} ({len(results)} questions)")
    return results


def _summarize(item, out, secs, crashed=None):
    if crashed:
        return dict(slug=item["slug"], question=item["q"], device=item["device"],
                    intended=item["tools"], landmine=item["landmine"], elapsed_s=secs,
                    tool_calls=[], gate_fails=0, stopped=None,
                    answer=f"CRASHED: {crashed}", verdict="crashed")
    calls = [e.data["name"] for e in out.of_type("tool_call")]
    gates = out.of_type("gate")
    cited = "[" in (out.answer or "") and out.stopped is None
    if out.answer and out.answer.startswith("cannot answer yet"):
        verdict = "gated (what's-missing)"       # acceptance-valid: cited answer OR what's-missing
    elif out.stopped:
        verdict = f"stopped:{out.stopped}"
    elif out.answer and out.answer.rstrip().endswith("?") and not calls:
        verdict = "ask-back"
    elif cited:
        verdict = "cited answer"
    else:
        verdict = "answer (uncited)"
    return dict(slug=item["slug"], question=item["q"], device=item["device"],
                intended=item["tools"], landmine=item["landmine"], elapsed_s=secs,
                tool_calls=calls, gate_fails=len(gates), stopped=out.stopped,
                answer=out.answer, verdict=verdict)


def _write_report(results):
    lines = ["# E1 end-to-end run -- manual E2E record (#42)", "",
             f"Model `{os.environ.get('COPILOT_LLM_MODEL_NIM', 'gpt-oss-20b')}` "
             f"(effort={os.environ.get('COPILOT_LLM_REASONING_EFFORT', 'default')}) over "
             f"`{os.environ.get('COPILOT_LLM_BASE_URL', 'nim')}`; live dataapi + real seeded KB.",
             "", "Verdict legend: **cited answer** = answered with valid `[source:id]` citations; "
             "**gated** = gate withheld the answer and returned what's-missing (both are "
             "acceptance-valid); **ask-back** = asked one clarifying question.", "",
             "| # | question | intended tools | tools called | verdict | gate fails | s |",
             "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        lines.append(f"| {i} | {r['question'][:60]} | {','.join(r['intended']) or '-'} | "
                     f"{','.join(r['tool_calls']) or '-'} | {r['verdict']} | {r['gate_fails']} | "
                     f"{r['elapsed_s']} |")
    lines += ["", "## Per-question detail", ""]
    for i, r in enumerate(results, 1):
        lines += [f"### {i}. {r['slug']}", f"- **Q:** {r['question']}",
                  f"- **#40 landmine ridden:** {r['landmine']}",
                  f"- **tools called:** {', '.join(r['tool_calls']) or 'none'}",
                  f"- **verdict:** {r['verdict']}  (gate fails: {r['gate_fails']}, "
                  f"stopped: {r['stopped']})",
                  f"- **answer:**", "", "  > " + (r["answer"] or "").replace("\n", "\n  > "),
                  "", f"- trace: `copilot/e2e/traces/{r['slug']}.json`", ""]
    with open(REPORT, "w") as f:
        f.write("\n".join(lines) + "\n")


def _assert_reachable(base_url):
    import httpx
    r = httpx.get(base_url.rstrip("/") + "/topology", timeout=10)
    r.raise_for_status()
    assert r.json().get("nodes"), f"dataapi at {base_url} returned no topology nodes"


def _fresh_dir():
    # fresh KB per run (no append-dup, store.py ceiling); atexit cleanup so repeated runs /
    # the self-check don't leak temp dirs. ponytail: one registration point, both callers covered.
    import atexit
    import shutil
    import tempfile
    d = tempfile.mkdtemp(prefix="e1kb_")
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d


def _selfcheck():
    """Assert-based self-check (the #42 acceptance's harness self-check). Runs setup() -- profiles
    resolve, dataapi reachable, KB seeds over the real embedder, one model smoke -- WITHOUT the
    full question loop (so it's cheap). Needs the .env key + a live dataapi; skips if absent."""
    cfg = load()
    if cfg.llm_profile not in ("nim", "gemma") or (cfg.llm_profile == "nim" and not cfg.llm_api_key):
        print("e2e self-check SKIPPED (no hosted llm profile / key in copilot/.env)")
        return
    try:
        _assert_reachable(os.environ.get("COPILOT_DATAAPI_URL", cfg.dataapi_url))
    except Exception as e:
        print(f"e2e self-check SKIPPED (dataapi unreachable: {e})")
        return
    setup(cfg)
    print("copilot.e2e self-check OK (nim client resolves, dataapi live, KB seeds, model smokes)")


if __name__ == "__main__":
    if os.environ.get("COPILOT_E2E_LIVE"):
        run_live()
    else:
        _selfcheck()
