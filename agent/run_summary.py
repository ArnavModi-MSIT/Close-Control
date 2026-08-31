"""Whole-RUN narrative summary -- an LLM narrates the deterministic
root-cause clustering (matching/root_cause.py) and the matcher's own
headline counts, in plain English. It never computes anything itself,
only narrates facts already computed deterministically. Idea adapted
from Microsoft Copilot for Finance's "generative AI report summary...
insights and suggestions" -- explicitly lower-stakes than this project's
other AI-adjacent work (a "nice to read" convenience, not a capability
gap this project was missing), scoped accordingly: mock mode (default,
$0, a deterministic template) is the primary, always-available path;
Ollama (local, free, live) is the only optional live path. Groq/Anthropic
aren't wired up here -- Ollama already covers "a live demo that can't
fail on venue wifi" (this project's own stated reason for preferring it
elsewhere, see agent/providers/ollama.py's docstring) without adding a
second provider surface for what was explicitly agreed to be polish, not
core, before this was built.

STAYS ON THE "PROPOSES" SIDE, same as every other AI call in this
project. This function's OUTPUT IS NEVER READ by anything downstream --
no gate, no auto-resolve path, no case classification depends on this
text in any way. It exists purely for a human to read.

GROUND TRUTH DISCIPLINE: this module lives under agent/, so the same rule
applies as everywhere else in this package -- it must NEVER read
ground_truth.csv or depend on evaluate.py's ground-truth-scored accuracy
figures ("Ground truth is sacred... only evaluate.py touches it," see
CLAUDE.md's working principles). Every number this module narrates is
computed directly from the matcher's own `report` DataFrame -- clean
count, exception count -- never from a score against the answer key.
"""

import os

import requests

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_MOCK_LABEL = "[MOCK PROVIDER -- deterministic template, not a real LLM response] "

GENERAL_INSTRUCTIONS = """You are summarizing the results of one run of a deterministic \
payment-settlement reconciliation pipeline for a finance-ops reader who wants the \
top-line picture in a few sentences, not a table. You are NOT deciding anything and \
NOT proposing a resolution for any individual case -- every number below was already \
computed by plain, deterministic Python before you ever saw it. Your only job is to \
narrate it accurately and concisely.

CRITICAL RULES:
- Never invent a number. Every figure you cite must come from the facts given below.
- 3-5 sentences. Plain English, no bullet points, no markdown.
- Lead with the headline (how much was resolved automatically), then the shape of what's
  left (how concentrated the escalated backlog is), then the single most useful fact for
  someone deciding where to spend review time first.

FACTS FOR THIS RUN:
"""


def _facts_block(report, root_cause_summary: dict) -> str:
    total = len(report)
    clean = int(report["final_exception_type"].isna().sum())
    exceptions = total - clean
    automated = int((report["final_exception_type"].isna() | report["auto_resolve_eligible"]).sum())
    s = root_cause_summary
    return (
        f"- Total ledger transactions processed: {total}\n"
        f"- Clean (no exception at all): {clean}\n"
        f"- Exceptions found: {exceptions}\n"
        f"- Resolved with zero ML/LLM involvement (clean + matcher-level auto-resolve): "
        f"{automated} ({automated / total * 100:.1f}%)\n"
        f"- Escalated cases requiring human review: {s['escalated_cases']}\n"
        f"- Those {s['escalated_cases']} cases collapse to {s['root_cause_clusters']} distinct root causes "
        f"({s['amplification_factor']}x amplification)\n"
        f"- {s['multi_case_clusters']} clusters that fan out to more than one case account for "
        f"{s['cases_in_multi_case_clusters']} of the {s['escalated_cases']} escalated cases "
        f"({s['pct_cases_in_multi_case_clusters']}% of the queue)\n"
        f"- The single largest root cause: {s['largest_cluster_case_count']} cases, "
        f"type '{s['largest_cluster_exception_type']}'\n"
        f"- Total amount at risk across all root-cause clusters: "
        f"Rs.{s['total_amount_at_risk_rupees']:,.2f}\n"
    )


def _mock_narrative(report, root_cause_summary: dict) -> str:
    """Deterministic, template-composed -- same transparency convention as
    agent/providers/mock.py's own [MOCK PROVIDER] prefix, extended to this
    new kind of output rather than inventing a new one."""
    total = len(report)
    clean = int(report["final_exception_type"].isna().sum())
    automated = int((report["final_exception_type"].isna() | report["auto_resolve_eligible"]).sum())
    s = root_cause_summary
    if s["root_cause_clusters"] == 0:
        return (f"{_MOCK_LABEL}All {total} transactions reconciled cleanly this run -- "
                f"{clean} required no exception handling at all, and nothing was escalated.")
    return (
        f"{_MOCK_LABEL}This run processed {total} transactions and resolved "
        f"{automated} ({automated / total * 100:.1f}%) with zero ML/LLM involvement. "
        f"The remaining {s['escalated_cases']} escalated cases collapse to just "
        f"{s['root_cause_clusters']} distinct root causes ({s['amplification_factor']}x "
        f"amplification) -- {s['multi_case_clusters']} clusters covering "
        f"{s['pct_cases_in_multi_case_clusters']}% of the queue. "
        f"The single largest cause is '{s['largest_cluster_exception_type']}' at "
        f"{s['largest_cluster_case_count']} cases, so resolving that one underlying issue "
        f"would clear the biggest chunk of the backlog at once. "
        f"Rs.{s['total_amount_at_risk_rupees']:,.2f} is at risk across the full escalated set."
    )


def _ollama_narrative(report, root_cause_summary: dict, model: str, host: str) -> str:
    """Plain free-text completion -- deliberately NOT format='json' like
    agent/providers/ollama.py's structured resolve() calls, since this
    output is prose for a human, not a Pydantic-validated schema."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": GENERAL_INSTRUCTIONS + _facts_block(report, root_cause_summary)},
            {"role": "user", "content": "Write the summary now."},
        ],
        "stream": False,
        "options": {"temperature": 0.3},
    }
    resp = requests.post(f"{host.rstrip('/')}/api/chat", json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()


def build_run_summary(report, root_cause_summary: dict, provider: str | None = None,
                       model: str = "llama3.1:8b", host: str = "http://127.0.0.1:11434") -> str:
    """provider: 'mock' (default) or 'ollama'. Falls back to the mock
    template (never raises) if 'ollama' is requested but the call fails --
    a run summary is a convenience, not something worth breaking a demo
    over if Ollama isn't running (same "don't start it, report if it's
    down" contract as everywhere else Ollama is optional in this project)."""
    provider = provider or os.environ.get("RUN_SUMMARY_PROVIDER", "mock")
    if provider == "ollama":
        try:
            return _ollama_narrative(report, root_cause_summary, model, host)
        except Exception as e:  # noqa: BLE001 -- deliberately broad, see docstring
            print(f"[WARN] Ollama run-summary call failed ({type(e).__name__}: {e}), "
                  f"falling back to the mock template.")
    return _mock_narrative(report, root_cause_summary)
