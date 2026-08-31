"""Tool implementations for the Settlement Q&A agent.

Per-transaction tools (get_transaction_details, get_settlement_details,
calculate_settlement_variance, lookup_related_transactions,
search_bank_statement, get_loan_recovery_schedule, compute_delta) are
investigator/tools.py's, imported directly rather than duplicated -- same
ToolContext, same deterministic-Python-over-real-data contract, same
"AI proposes, deterministic code disposes" boundary one level deeper
(the model decides WHICH tool to call and WHEN, every tool's own
computation is plain Python).

This module adds the PORTFOLIO-level tools a Q&A agent needs that a
per-case investigator never did: "how many cases are escalated," "show me
cases over Rs.50,000," "what's the cash position," "what are the biggest
root causes." All four are pure aggregation over data investigator/'s
ToolContext already loads (report, gateway) or over matching/root_cause.py
and cash_position/engine.py's own existing, already-verified computations
-- nothing here computes a new number by a new method, it only exposes
numbers this project already trusts through a Q&A-shaped interface.
"""

import pandas as pd

from investigator.tools import (  # noqa: F401 -- re-exported for qa_agent/tool_schema.py's TOOLS dict
    ToolContext,
    get_transaction_details,
    get_settlement_details,
    calculate_settlement_variance,
    lookup_related_transactions,
    search_bank_statement,
    get_loan_recovery_schedule,
    compute_delta,
)

from . import config


def get_portfolio_summary(ctx: ToolContext, **_ignored) -> dict:
    """Headline counts across the whole dataset -- clean, matcher-level
    auto-resolved, and escalated, plus the escalated population's total
    amount at risk and its breakdown by exception type. The Q&A-shaped
    equivalent of evaluate.py's own match-rate headline, computed the same
    way (over ctx.report, never ground_truth.csv).

    **_ignored: this tool takes no real arguments (the schema declares
    empty parameters), but a smaller local model (observed live with
    qwen3:1.7b) sometimes hallucinates keyword arguments for a
    zero-parameter tool anyway, and previously got a hard TypeError on
    every attempt -- burning tool rounds until the model gave up and
    fabricated an answer instead (caught by grounding.py's check, but
    the real data was never even fetched). Since there is nothing real
    for a kwarg to misconfigure here, silently discarding whatever the
    model invents is safe and lets the call succeed on the first try."""
    report = ctx.report
    total = len(report)
    clean = int(report["is_clean"].sum())
    auto_mask = report["final_exception_type"].notna() & report["auto_resolve_eligible"]
    escalated_mask = report["final_exception_type"].notna() & ~report["auto_resolve_eligible"]
    auto = int(auto_mask.sum())
    escalated = int(escalated_mask.sum())
    amount_at_risk = float(report.loc[escalated_mask, "ledger_expected_net_rupees"].fillna(0).sum())
    by_type = report.loc[escalated_mask, "final_exception_type"].value_counts().to_dict()

    return {
        "total_transactions": total,
        "clean_count": clean,
        "matcher_auto_resolved_count": auto,
        "escalated_count": escalated,
        "automation_rate_pct": round((clean + auto) / total * 100, 1) if total else 0.0,
        "escalated_amount_at_risk_rupees": round(amount_at_risk, 2),
        "escalated_by_exception_type": by_type,
    }


def search_cases(ctx: ToolContext, exception_type: str = None, min_amount_rupees: float = None,
                  max_amount_rupees: float = None, merchant_id: str = None, limit: int = None) -> dict:
    """Filtered search over escalated cases -- by exception type, amount
    range, or merchant. Always sorted by amount at risk descending (the
    same ordering the review queue itself defaults to), capped at
    SEARCH_CASES_MAX_RESULTS so a broad query can't dump hundreds of rows
    into the model's context -- total_matches and truncated tell the model
    (and a human reading the tool trace) whether it's looking at
    everything or a sample, rather than silently showing a partial list as
    if it were complete."""
    limit = limit or config.SEARCH_CASES_MAX_RESULTS
    report = ctx.report
    mask = report["final_exception_type"].notna() & ~report["auto_resolve_eligible"]
    if exception_type:
        mask &= report["final_exception_type"] == exception_type
    if merchant_id:
        mask &= report["merchant_id"] == merchant_id
    amount = report["ledger_expected_net_rupees"].fillna(0)
    if min_amount_rupees is not None:
        mask &= amount >= min_amount_rupees
    if max_amount_rupees is not None:
        mask &= amount <= max_amount_rupees

    matched = report.loc[mask].copy()
    matched["_sort_amount"] = matched["ledger_expected_net_rupees"].fillna(0)
    matched = matched.sort_values("_sort_amount", ascending=False)
    total_matches = len(matched)
    sample = matched.head(limit)

    return {
        "total_matches": total_matches,
        "returned_count": len(sample),
        "truncated": total_matches > len(sample),
        "cases": [
            {
                "transaction_id": r["transaction_id"],
                "exception_type": r["final_exception_type"],
                "merchant_id": r["merchant_id"],
                "amount_at_risk_rupees": round(float(r["ledger_expected_net_rupees"]), 2)
                                         if pd.notna(r["ledger_expected_net_rupees"]) else 0.0,
                "risk_class": r["risk_class"],
            }
            for _, r in sample.iterrows()
        ],
    }


def get_root_cause_summary(ctx: ToolContext, **_ignored) -> dict:
    """Deterministic root-cause clustering (matching/root_cause.py),
    exposed as a Q&A tool -- the same computation review_backend/main.py's
    GET /api/root-cause-clusters and ui/showcase.html's "617 -> 130" card
    already surface, reused rather than re-derived.

    **_ignored: see get_portfolio_summary()'s docstring -- same
    zero-real-arguments tool, same defensive tolerance for a hallucinated
    kwarg from a smaller model."""
    from matching.root_cause import cluster_escalated_cases, summarize

    clusters = cluster_escalated_cases(ctx.report)
    escalated_count = int(
        (ctx.report["final_exception_type"].notna() & ~ctx.report["auto_resolve_eligible"]).sum()
    )
    summary = summarize(clusters, escalated_count)

    top = clusters.sort_values("case_count", ascending=False).head(5)
    return {
        **summary,
        "top_clusters": [
            {
                "cluster_key": r["cluster_key"],
                "final_exception_type": r["final_exception_type"],
                "case_count": int(r["case_count"]),
                "risk_class": r["risk_class"],
                "amount_at_risk_rupees": round(float(r["amount_at_risk_rupees"]), 2),
            }
            for _, r in top.iterrows()
        ],
    }


def get_cash_position_summary(ctx: ToolContext, **_ignored) -> dict:
    """cash_position/engine.py's own snapshot (confirmed / in-transit /
    at-risk / projected), exposed as a Q&A tool -- same DEFAULT_AS_OF every
    other money figure in this app uses, never re-derived.

    **_ignored: see get_portfolio_summary()'s docstring -- same
    zero-real-arguments tool, same defensive tolerance for a hallucinated
    kwarg from a smaller model. This is the exact tool that was observed
    live failing 3 rounds in a row on invented kwargs
    (cash_position_summary=..., confirmed=0/in_transit=0/..., then
    confirmed=true/in_transit=true/...) before the model gave up and
    fabricated numbers instead."""
    from cash_position.engine import build_cash_position
    from cash_position.config import DEFAULT_AS_OF

    result = build_cash_position(ctx.report, ctx.gateway, DEFAULT_AS_OF)
    return {"as_of": DEFAULT_AS_OF.isoformat(), **result["snapshot"]}


TOOLS = {
    "get_transaction_details": get_transaction_details,
    "get_settlement_details": get_settlement_details,
    "calculate_settlement_variance": calculate_settlement_variance,
    "lookup_related_transactions": lookup_related_transactions,
    "search_bank_statement": search_bank_statement,
    "get_loan_recovery_schedule": get_loan_recovery_schedule,
    "compute_delta": compute_delta,
    "get_portfolio_summary": get_portfolio_summary,
    "search_cases": search_cases,
    "get_root_cause_summary": get_root_cause_summary,
    "get_cash_position_summary": get_cash_position_summary,
}
