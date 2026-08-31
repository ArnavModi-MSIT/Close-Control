"""
Dashboard data export -- CLI entrypoint.

Runs the full pipeline (matcher, evaluate, mock agent, cash position) fresh
and dumps one consolidated JSON of headline metrics for the showcase
dashboard. Never hand-copy numbers into the dashboard -- always regenerate
this file so what's shown is provably real, not stale.

RAG ablation numbers are NOT regenerated here (that requires a live LLM
provider and ~15 minutes) -- pass --rag-ablation-csv to fold in an existing
data/rag_ablation_detail.csv from a prior run_rag_ablation.py run.

    python scripts/export_dashboard_data.py
    python scripts/export_dashboard_data.py --rag-ablation-csv data/rag_ablation_detail.csv
"""

import os as _os
import sys as _sys
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)
_SCRIPTS_DIR = _os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPTS_DIR)

import os
import json
import argparse
import datetime as dt

import numpy as np
import pandas as pd


def _json_default(o):
    """pandas/numpy aggregations (.sum(), .value_counts(), etc.) return
    numpy scalar types (int64/float64/bool_), which json.dump doesn't know
    how to serialize -- convert via .item() rather than hunting down every
    individual cast site across this file's four data sections."""
    if isinstance(o, (np.integer, np.floating, np.bool_)):
        return o.item()
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")

from run_matcher import run
from matching.loaders import load_sources
from matching.root_cause import cluster_escalated_cases, summarize, per_exception_type_amplification
from evaluate import evaluate
from cash_position import config as cp_config
from cash_position.engine import build_cash_position
from agent.client import resolve_exception
from agent.gate import apply_gate
from agent import config as agent_config

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def matcher_section(report, settlement_matches):
    n = len(report)
    clean = int(report["is_clean"].sum())
    exc = report[report["final_exception_type"].notna()]
    return {
        "n_ledger_transactions": n,
        "clean_count": clean,
        "clean_pct": round(clean / n * 100, 1),
        "exception_count": len(exc),
        "exception_pct": round(len(exc) / n * 100, 1),
        "settlements_total": int(len(settlement_matches)),
        "settlements_matched": int((settlement_matches["match_status"] == "matched").sum()),
        "settlements_ambiguous": int((settlement_matches["match_status"] == "ambiguous").sum()),
        "exception_type_breakdown": exc["final_exception_type"].value_counts().to_dict(),
        "auto_resolve_eligible": int(exc["auto_resolve_eligible"].sum()),
        "escalated": int((~exc["auto_resolve_eligible"]).sum()),
        "risk_class_breakdown": exc["risk_class"].value_counts().to_dict(),
    }


def root_cause_section(report):
    """Collapses the escalated queue into its underlying causes. Cheap
    (~70ms, no new dependency) and deterministic -- see
    matching/root_cause.py for why this is a join, not an embedding model."""
    escalated = report[report["final_exception_type"].notna()
                        & (~report["auto_resolve_eligible"])]
    clusters = cluster_escalated_cases(report)
    payload = summarize(clusters, len(escalated))
    payload["per_exception_type"] = per_exception_type_amplification(
        report, clusters).to_dict(orient="records")
    payload["top_clusters"] = clusters.head(5)[
        ["cluster_id", "final_exception_type", "case_count", "risk_class",
         "amount_at_risk_rupees"]].to_dict(orient="records")
    return payload


def evaluate_section():
    """Reuses evaluate.py's own evaluate() function -- same computation
    that produces the numbers already verified against ground truth
    throughout this project, not a re-derived copy that could drift."""
    return evaluate()


def agent_mock_section(report):
    escalated = report[report["final_exception_type"].notna() & (~report["auto_resolve_eligible"])]
    agent_config.LLM_PROVIDER = "mock"
    results = []
    for _, row in escalated.iterrows():
        row_dict = row.to_dict()
        resolution = resolve_exception(row_dict)
        gate_result = apply_gate(resolution, row_dict)
        results.append({
            "confidence": resolution.confidence,
            "sufficient_evidence": resolution.sufficient_evidence,
            "gate_decision": gate_result["final_decision"],
        })
    df = pd.DataFrame(results)
    return {
        "total_processed": len(df),
        "escalate_count": int((df["gate_decision"] == "escalate").sum()),
        "auto_resolve_count": int((df["gate_decision"] == "auto_resolve").sum()),
        "mean_confidence": round(float(df["confidence"].mean()), 2),
        "sufficient_evidence_rate_pct": round(float(df["sufficient_evidence"].mean()) * 100, 1),
    }


def cash_position_section(report, gateway):
    result = build_cash_position(report, gateway, cp_config.DEFAULT_AS_OF)
    s = result["snapshot"]
    at_risk_total_count = s["held_count"] + s["at_risk_due_count"]
    at_risk_total_rupees = s["held_rupees"] + s["at_risk_due_nominal_rupees"]
    return {
        "as_of": cp_config.DEFAULT_AS_OF.isoformat(),
        "confirmed_rupees": round(s["confirmed_rupees"], 2),
        "confirmed_count": s["confirmed_count"],
        "in_transit_rupees": round(s["in_transit_rupees"], 2),
        "in_transit_count": s["in_transit_count"],
        "at_risk_rupees": round(at_risk_total_rupees, 2),
        "at_risk_count": at_risk_total_count,
        "held_rupees": round(s["held_rupees"], 2),
        "held_count": s["held_count"],
        "projected_cash_position_rupees": round(s["projected_cash_position_rupees"], 2),
        # already a plain dict -- cash_position/engine.py's summarize_snapshot()
        # converts this at the source now, no .to_dict() needed here anymore.
        "at_risk_by_exception_type": s["at_risk_by_exception_type"],
        "forecast_daily": result["forecast"].to_dict(orient="records"),
    }


def rag_ablation_section(csv_path):
    if not csv_path or not os.path.exists(csv_path):
        return None
    d = pd.read_csv(csv_path)
    return {
        "n_cases": len(d),
        "policy_citation_correct_on_pct": round(float(d["policy_id_consistent_rag_on"].mean()) * 100, 1),
        "policy_citation_correct_off_pct": round(float(d["policy_id_consistent_rag_off"].mean()) * 100, 1),
        "confidence_on": round(float(d["confidence_rag_on"].mean()), 2),
        "confidence_off": round(float(d["confidence_rag_off"].mean()), 2),
        "sufficient_evidence_on_pct": round(float(d["sufficient_evidence_rag_on"].mean()) * 100, 1),
        "sufficient_evidence_off_pct": round(float(d["sufficient_evidence_rag_off"].mean()) * 100, 1),
        "auto_resolve_rate_on_pct": round(float((d["gate_decision_rag_on"] == "auto_resolve").mean()) * 100, 1),
        "auto_resolve_rate_off_pct": round(float((d["gate_decision_rag_off"] == "auto_resolve").mean()) * 100, 1),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--rag-ablation-csv", default=os.path.join(DATA_DIR, "rag_ablation_detail.csv"))
    parser.add_argument("--out", default=os.path.join(DATA_DIR, "dashboard_data.json"))
    args = parser.parse_args()

    report, settlement_matches, ledger_check = run(args.data_dir)
    gateway, bank, ledger = load_sources(args.data_dir)

    with open(os.path.join(args.data_dir, "dataset_metadata.json")) as f:
        dataset_metadata = json.load(f)

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dataset": dataset_metadata,
        "matcher": matcher_section(report, settlement_matches),
        "root_cause": root_cause_section(report),
        # evaluate() always uses its own module-level DATA_DIR (re-runs the
        # matcher internally too) -- fine for this script's scope, but means
        # --data-dir doesn't affect this section specifically.
        "evaluate": evaluate_section(),
        "agent_mock": agent_mock_section(report),
        "cash_position": cash_position_section(report, gateway),
        "rag_ablation": rag_ablation_section(args.rag_ablation_csv),
    }

    with open(args.out, "w") as f:
        json.dump(payload, f, indent=2, default=_json_default)
    print(f"Wrote {args.out}")
    print(json.dumps({k: ("..." if isinstance(v, (dict, list)) else v) for k, v in payload.items()}, indent=2))


if __name__ == "__main__":
    main()
