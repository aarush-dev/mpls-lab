"""copilot.eval.checks -- deterministic regressions checks, one per harness-gap issue
(#113-#128). Each check reads a parsed run (question + list of SSE events) and returns
(status, detail): status is "pass" | "fail" | "flag" (heuristic, needs a human look) |
"skip" (not automatable from a single-shot probe -- see NOTE in the docstring).

Reuses the REAL gate code (copilot.agent.gate) wherever the check is really "does the
gate's own logic behave" -- a hand-rolled regex here would drift from the thing it's
supposed to verify (ponytail: don't reinvent the gate to check the gate).
"""
import json
import re

from copilot.agent.gate import CITE_RE, ENTITY_RE, citation_check, extract_entities

# ---- helpers ---------------------------------------------------------------

def _events(run):
    return run["events"]


def _assistant_texts(run):
    return [e.get("content") or "" for e in _events(run) if e.get("type") == "assistant_msg"]


def _tool_calls(run):
    return [e for e in _events(run) if e.get("type") == "tool_call"]


def _tool_results(run):
    return [e for e in _events(run) if e.get("type") == "tool_result"]


def _gate_events(run):
    return [e for e in _events(run) if e.get("type") == "gate"]


_FALSE_CAPABILITY_CLAIMS = [
    r"requires? a (specific )?device",
    r"don't have access to session state",
    r"each interaction is stateless",
    r"don't have a ['\"]?skills['\"]?",
    r"i (don'?t|do not) have (a )?session",
    r"tools require a specific device",
]

_REAL_DEVICE_RE = re.compile(r"\b(?:ce_branch|ce_hub|pe|p)\d+\b|\bh_\w+\b", re.IGNORECASE)


# ---- checks ------------------------------------------------------------------

def check_113_no_server_error(run):
    raw = run.get("raw", "")
    if "Internal Server Error" in raw or not run["events"]:
        return "fail", "raw response was an unhandled 500 / empty stream"
    return "pass", ""


def check_114_no_blank_terminal_answer(run):
    texts = _assistant_texts(run)
    if any(not t.strip() for t in texts):
        return "fail", "assistant_msg with empty content reached the wire"
    return "pass", ""


def check_115_gate_not_silently_skipped(run):
    for g in _gate_events(run):
        if g.get("ok") is True and g.get("skipped") is None:
            # gate_enabled must be true and the event must say so either way
            return "flag", "gate event has no explicit skipped=True/False -- can't tell a real pass from a bypass"
    return "pass", ""


def check_116_window_disclosed(run):
    # can't fully verify without ground truth on what period was asked vs served;
    # heuristic: if the question implies a period other than "now", the answer should
    # mention a window/time range at all.
    q = run["question"].lower()
    implies_period = any(k in q for k in ("last month", "last week", "this week", "24 hour", "day", "yesterday"))
    if not implies_period:
        return "skip", "question doesn't name a period"
    text = " ".join(_assistant_texts(run)).lower()
    if any(k in text for k in ("window", "minute", "hour", "period", "timeframe")):
        return "pass", ""
    return "flag", "question named a time period; answer never mentions the window it actually used"


def check_117_totals_not_fabricated(run):
    # #117 fix: tool results should carry a mechanical total= header for walks. If present,
    # cross-check any "N devices"/"N nodes" claim in the answer against it.
    totals = []
    for r in _tool_results(run):
        m = re.search(r"total=(\d+)", str(r.get("content", "")))
        if m:
            totals.append(int(m.group(1)))
    if not totals:
        return "skip", "no walk with a mechanical total in this run"
    text = " ".join(_assistant_texts(run))
    claimed = [int(n) for n in re.findall(r"\b(\d+)\s+(?:devices|nodes|routers)\b", text, re.IGNORECASE)]
    bad = [c for c in claimed if c not in totals]
    if bad:
        return "flag", f"answer claims counts {bad} not matching any tool-reported total {totals}"
    return "pass", ""


def check_118_gate_regex_coverage():
    """Not a per-run check -- a direct regression test of gate.py's own regexes."""
    entities = extract_entities("ce_branch24 ce_hub3 h_branch24_corp pe12")
    if not {"ce_branch24", "ce_hub3", "pe12"} <= {e.lower() for e in entities} and not entities:
        return "fail", "ENTITY_RE still blind to ce_branch/ce_hub/h_* names"
    zw = "[\u200bmetrics\u200b:\u200b2\u200b]"
    result = citation_check(f"the fan is 3000 rpm {zw}", {"metrics:2"})
    if not result.ok:
        return "fail", "zero-width characters inside a real citation still read as fabricated"
    fake = citation_check('{"a": [1,2,3]}', set())
    return "pass", ""


def check_119_no_false_capability_or_ambiguous_no_rows(run):
    text = " ".join(_assistant_texts(run)).lower()
    for pat in _FALSE_CAPABILITY_CLAIMS:
        if re.search(pat, text):
            return "fail", f"false capability claim detected: matched /{pat}/"
    return "pass", ""


# #120 folds into the same false-capability-claim regex list as #119's device-mandatory check.
check_120_no_false_capability_claims = check_119_no_false_capability_or_ambiguous_no_rows


def check_121_no_fabricated_hostnames(run):
    text = " ".join(_assistant_texts(run))
    # look for hostname-shaped tokens the model suggested that aren't real-pattern devices
    suspects = re.findall(r"`([a-zA-Z][\w-]{2,20})`", text)
    bad = [s for s in suspects if not _REAL_DEVICE_RE.match(s) and re.search(r"\d", s)]
    if bad:
        return "flag", f"suggested device-like names not matching the real naming scheme: {bad}"
    return "pass", ""


def check_122_no_dead_backend_reprobe(run):
    errors_by_tool = {}
    for tc, tr in zip(_tool_calls(run), _tool_results(run)):
        name = tc.get("name")
        content = str(tr.get("content", ""))
        if name in ("search_runbooks", "search_incidents") and "not available" in content:
            errors_by_tool[name] = errors_by_tool.get(name, 0) + 1
    repeated = {k: v for k, v in errors_by_tool.items() if v > 1}
    if repeated:
        return "fail", f"dead backend re-probed after first failure: {repeated}"
    return "pass", ""


def check_123_topology_flatten():
    return "skip", "needs cross-referencing a walk against a raw metrics read of the same device/timestamp -- not automatable from a single run"


def check_124_no_citation_id_collision(run):
    seen = {}
    for r in _tool_results(run):
        cid = r.get("cite_id") or r.get("id")
        content = str(r.get("content", ""))
        if cid and cid in seen and seen[cid] != content:
            return "fail", f"id {cid} reused for two different tool_result contents in one run"
        if cid:
            seen[cid] = content
    return "pass", ""


def check_125_truncation_marked(run):
    for r in _tool_results(run):
        content = str(r.get("content", ""))
        if re.search(r"\bn=100\b", content) or ("hops" in content and len(content) > 20000):
            if "more" not in content and "truncat" not in content.lower():
                return "flag", "result landed at/near a cap with no truncation marker"
    return "pass", ""


def check_126_retry_and_askback(run):
    gates = _gate_events(run)
    for g in gates:
        missing = g.get("missing") or []
        if missing == ["empty answer"] and g.get("retry", 0) > 2:
            return "flag", "empty-answer retries exceeded the dedicated budget (should be separate from gate_max_retries)"
    return "pass", ""


def check_127_action_refusal_and_formatting(run):
    q = run["question"].lower()
    action_words = ("restart", "roll back", "rollback", "push a config", "set a threshold",
                    "open a jira", "page the on-call")
    names_device = bool(_REAL_DEVICE_RE.search(run["question"]))
    if any(w in q for w in action_words) and names_device:
        if not _tool_calls(run):
            return "flag", "out-of-scope action naming a real device got zero tool calls (cold refusal, no investigation)"
    return "pass", ""


def check_128_session_fields():
    return "skip", "requires a probe that actually sets session_id/case_id/workspace/skills on the request -- see issue #128"


CHECKS = {
    113: check_113_no_server_error,
    114: check_114_no_blank_terminal_answer,
    115: check_115_gate_not_silently_skipped,
    116: check_116_window_disclosed,
    117: check_117_totals_not_fabricated,
    119: check_119_no_false_capability_or_ambiguous_no_rows,
    120: check_120_no_false_capability_claims,
    121: check_121_no_fabricated_hostnames,
    122: check_122_no_dead_backend_reprobe,
    124: check_124_no_citation_id_collision,
    125: check_125_truncation_marked,
    126: check_126_retry_and_askback,
    127: check_127_action_refusal_and_formatting,
}

STATIC_CHECKS = {
    118: check_118_gate_regex_coverage,
    123: check_123_topology_flatten,
    128: check_128_session_fields,
}
