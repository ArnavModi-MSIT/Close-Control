"""
Measured accuracy for the tool-using investigation agent -- CLI entrypoint.

Every other LLM-facing layer in this project has a real accuracy number:
the matcher is scored against ground truth (evaluate.py), the single-shot
agent's policy grounding is scored via the RAG ablation (run_rag_ablation.py,
2 cases per exception type x however many escalated types). investigator/
had proven the MECHANISM worked (real tool calls, correct verdicts on a
handful of individual cases) but never a measured number across a sample --
this closes that gap, using the exact same 2-per-type stratified sample
size as run_rag_ablation.py, for direct comparability between the two
LLM-facing layers.

This script makes ZERO LLM calls -- it only reads and scores whatever is
already in data/investigation_log.jsonl. Run run_investigator.py yourself
to add more cases (each takes minutes on CPU-only Ollama), then re-run this
any time -- it's instant.

Like run_rag_ablation.py, this does NOT read ground_truth.csv. The
"correct" policy_id is the deterministic agent/policy_kb.py mapping for the
MATCHER's authoritative exception_type -- consistent with this project's
core rule that the matcher's classification, not the LLM's opinion, is what
governs policy lookup (see agent/gate.py). Scoring against that standard
(not ground truth) is exactly what run_rag_ablation.py already does, and
what agent/gate.py itself checks at decision time.

    python evaluate_investigator.py
    python evaluate_investigator.py --n-per-type 3
"""

import os
import json
import argparse

import pandas as pd

from run_matcher import run
from run_investigator import stratified_sample

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LOG_PATH = os.path.join(DATA_DIR, "investigation_log.jsonl")
DEFAULT_N_PER_TYPE = 2

# Same marker seed_review_queue.py uses to recognize a total-infra-failure
# placeholder (Ollama unreachable, no real investigation happened) rather
# than a genuine result. Duplicated as a literal, not imported, because
# seed_review_queue.py defines it as a module-level constant with no
# __all__/public-API contract -- copying a one-line string literal here is
# lower-risk than creating a cross-import dependency between two unrelated
# entrypoints for a single constant.
_TOTAL_FAILURE_MARKER = "[INVESTIGATION FAILED TO PRODUCE A FINAL ANSWER"


def load_good_investigations(log_path: str) -> dict:
    """Keyed by transaction_id, latest entry wins (same resolution rule as
    seed_review_queue.py), total-infra-failure placeholders excluded."""
    if not os.path.exists(log_path):
        return {}
    latest = {}
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            if (e.get("root_cause") or "").startswith(_TOTAL_FAILURE_MARKER):
                continue
            latest[e["transaction_id"]] = e
    return latest


def score_cases(sample: pd.DataFrame, investigations: dict) -> tuple[pd.DataFrame, list]:
    """Joins the target stratified sample against whatever real investigation
    results exist. Returns (scored_rows_df, missing_transaction_ids)."""
    from agent.gate import apply_gate
    from agent.policy_kb import get_policy, POLICY_KB
    from investigator.schema import InvestigationResult
    from investigator.loop import tool_evidence_ids

    known_policy_ids = {p["policy_id"] for p in POLICY_KB.values()}

    rows, missing = [], []
    for _, row in sample.iterrows():
        txn_id = row["transaction_id"]
        entry = investigations.get(txn_id)
        if entry is None:
            missing.append(txn_id)
            continue

        row_dict = row.to_dict()
        try:
            authoritative_policy_id = get_policy(row_dict["final_exception_type"])["policy_id"]
        except KeyError:
            authoritative_policy_id = None

        result = InvestigationResult(**{k: v for k, v in entry.items()
                                         if k in InvestigationResult.model_fields})
        gate_result = apply_gate(result, row_dict, tool_evidence_ids(result))

        rows.append({
            "transaction_id": txn_id,
            "matcher_exception_type": row_dict["final_exception_type"],
            "investigator_exception_type": result.exception_type,
            "reclassified": gate_result["reclassified"],
            "authoritative_policy_id": authoritative_policy_id,
            "cited_policy_id": result.policy_id,
            "policy_id_hallucinated": result.policy_id not in known_policy_ids,
            "policy_id_consistent": gate_result["policy_id_consistent"],
            "confidence": result.confidence,
            "sufficient_evidence": result.sufficient_evidence,
            "gate_decision": gate_result["final_decision"],
            "tool_rounds_used": entry.get("tool_rounds_used"),
            "tool_calls_made": len(entry.get("investigation_log") or []),
            "has_drafted_communication": bool(entry.get("drafted_communication")),
            # None for any entry logged before elapsed_seconds existed on
            # investigator/schema.py's InvestigationResult -- not coalesced
            # to 0, which would silently understate latency in a mixed batch
            "elapsed_seconds": entry.get("elapsed_seconds") or None,
            # Entries written before this field existed have no "model" key --
            # labeled rather than silently coalesced into the modern default,
            # since assuming a value here is exactly the kind of thing that
            # would make a mixed-model sample look clean when it isn't.
            "model": entry.get("model", "unknown (pre-provenance-tracking)"),
        })
    return pd.DataFrame(rows), missing


def pct(series) -> str:
    return f"{series.mean():.1%}" if len(series) else "n/a"


def print_report(scored: pd.DataFrame, missing: list, target_n: int) -> None:
    print()
    print("=" * 70)
    print("INVESTIGATOR ACCURACY REPORT")
    print("=" * 70)
    print(f"Target stratified sample: {target_n} cases. "
          f"Scored: {len(scored)}. Missing: {len(missing)}.")
    if missing:
        print(f"\nStill need a real investigation run for:")
        for txn_id in missing:
            print(f"  python run_investigator.py --transaction-id {txn_id}")
    if scored.empty:
        print("\nNo scoreable cases yet -- run the commands above, then re-run this script.")
        return

    models_used = scored["model"].value_counts()
    if len(models_used) > 1:
        print()
        print("*** WARNING: this sample spans more than one model -- the numbers below are")
        print("*** a mixed-model average, not a clean single-model measurement. Every other")
        print("*** LLM-facing report in this project (RAG ablation) holds the model fixed and")
        print("*** varies exactly one thing. Re-run the cases below under one INVESTIGATOR_MODEL")
        print("*** (see .env) before treating this as a rigorous number:")
        print(models_used.to_string())
    else:
        print(f"\nAll {len(scored)} cases ran under a single model: {models_used.index[0]}")

    print()
    print(f"{'metric':<40}{'value':>15}")
    print("-" * 55)
    print(f"{'policy_id citation correct':<40}{pct(scored['policy_id_consistent']):>15}")
    print(f"{'policy_id hallucinated (not real)':<40}{pct(scored['policy_id_hallucinated']):>15}")
    reclassified_label = "reclassified matcher's exception type"
    print(f"{reclassified_label:<40}{pct(scored['reclassified']):>15}")
    print(f"{'mean confidence':<40}{scored['confidence'].mean():>15.2f}")
    print(f"{'sufficient_evidence rate':<40}{pct(scored['sufficient_evidence']):>15}")
    print(f"{'gate: auto_resolve rate':<40}{pct(scored['gate_decision'] == 'auto_resolve'):>15}")
    print(f"{'gate: escalate rate':<40}{pct(scored['gate_decision'] == 'escalate'):>15}")
    print(f"{'mean tool rounds used':<40}{scored['tool_rounds_used'].mean():>15.2f}")
    print(f"{'mean tool calls made':<40}{scored['tool_calls_made'].mean():>15.2f}")
    print(f"{'cases with zero tool calls':<40}{pct(scored['tool_calls_made'] == 0):>15}")
    print(f"{'cases with a drafted communication':<40}{pct(scored['has_drafted_communication']):>15}")
    timed = scored["elapsed_seconds"].dropna()
    if len(timed):
        print(f"{'mean latency (of ' + str(len(timed)) + ' timed cases)':<40}{timed.mean():>14.1f}s")
        print(f"{'p95 latency':<40}{timed.quantile(0.95):>14.1f}s")
    else:
        print(f"{'latency':<40}{'n/a (pre-timing entries)':>15}")
    print()
    print("Note: 'correct' policy_id is the deterministic agent/policy_kb.py mapping for the")
    print("MATCHER's exception_type -- not ground truth, not the investigator's own opinion of")
    print("what type it is. This is the same standard agent/gate.py itself applies, and the")
    print("same standard run_rag_ablation.py uses for the single-shot agent -- the two numbers")
    print("are directly comparable.")
    if (scored["gate_decision"] == "auto_resolve").any():
        print()
        print("Cases the gate actually auto-resolved (all 7 conditions held simultaneously):")
        print(scored[scored["gate_decision"] == "auto_resolve"]
              [["transaction_id", "matcher_exception_type", "confidence"]].to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n-per-type", type=int, default=DEFAULT_N_PER_TYPE)
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--log-path", default=LOG_PATH)
    args = parser.parse_args()

    report, settlement_matches, ledger_check = run(args.data_dir)
    escalated = report[report["final_exception_type"].notna() & (~report["auto_resolve_eligible"])]
    sample = stratified_sample(escalated, args.n_per_type)

    investigations = load_good_investigations(args.log_path)
    scored, missing = score_cases(sample, investigations)

    print_report(scored, missing, len(sample))

    if not scored.empty:
        out_csv = os.path.join(args.data_dir, "investigator_eval_detail.csv")
        scored.to_csv(out_csv, index=False)
        print(f"\nPer-case detail written to: {out_csv}")


if __name__ == "__main__":
    main()
