"""
RAG ablation study -- CLI entrypoint.

Measures what retrieving the matched policy actually buys the Exception
Resolution Agent, by running the SAME sampled cases through the SAME live
LLM provider twice: once with the retrieved policy in the prompt (RAG-ON,
the normal pipeline -- see agent/client.py), once without it (RAG-OFF, the
agent has to guess the policy_id/cause/action from general knowledge alone).

The headline metrics are checked against deterministic ground truth (the
matcher-authoritative policy_id from agent/policy_kb.py), not LLM
self-report: policy_id citation accuracy, whether the cited policy_id is
even a real one, and the resulting gate decision (agent/gate.py requires
policy_id_consistent to auto-resolve, so a citation miss forces escalation
regardless of how confident the agent sounds).

Requires a real LLM_PROVIDER (groq/ollama/anthropic) in .env -- the mock
provider ignores system_prompt entirely (it pulls exception_type back out
of the evidence block and does its own deterministic policy-KB lookup), so
RAG-ON and RAG-OFF would be byte-identical under mock and the ablation
would prove nothing.

    python run_rag_ablation.py
    python run_rag_ablation.py --n-per-type 3
"""

import os
import argparse

import pandas as pd

from run_matcher import run
from agent import config
from agent.client import resolve_exception, get_active_provider
from agent.gate import apply_gate
from agent.policy_kb import POLICY_KB, get_policy

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DEFAULT_N_PER_TYPE = 2
KNOWN_POLICY_IDS = {p["policy_id"] for p in POLICY_KB.values()}


def stratified_sample(escalated: pd.DataFrame, n_per_type: int) -> pd.DataFrame:
    parts = [group.head(n_per_type) for _, group in escalated.groupby("final_exception_type")]
    return pd.concat(parts, ignore_index=True) if parts else escalated.iloc[0:0]


def run_condition(cases: pd.DataFrame, use_policy_retrieval: bool, label: str) -> pd.DataFrame:
    rows = []
    for i, (_, row) in enumerate(cases.iterrows()):
        row_dict = row.to_dict()
        resolution = resolve_exception(row_dict, use_policy_retrieval=use_policy_retrieval)
        gate_result = apply_gate(resolution, row_dict)
        try:
            authoritative_policy_id = get_policy(row_dict["final_exception_type"])["policy_id"]
        except KeyError:
            authoritative_policy_id = None

        rows.append({
            "transaction_id": row_dict["transaction_id"],
            "exception_type": row_dict["final_exception_type"],
            "authoritative_policy_id": authoritative_policy_id,
            "cited_policy_id": resolution.policy_id,
            "policy_id_hallucinated": resolution.policy_id not in KNOWN_POLICY_IDS,
            "policy_id_consistent": gate_result["policy_id_consistent"],
            "confidence": resolution.confidence,
            "sufficient_evidence": resolution.sufficient_evidence,
            "gate_decision": gate_result["final_decision"],
            "root_cause": resolution.root_cause,
        })
        print(f"  [{label}] {i + 1}/{len(cases)}")
    return pd.DataFrame(rows)


def pct(series) -> str:
    return f"{series.mean():.1%}"


def print_report(rag_on: pd.DataFrame, rag_off: pd.DataFrame):
    print()
    print("=" * 70)
    print("RAG ABLATION REPORT")
    print("=" * 70)
    print(f"{len(rag_on)} cases, each run under both conditions with the same provider.")
    print()
    print(f"{'metric':<38}{'RAG-ON':>15}{'RAG-OFF':>15}")
    print("-" * 68)
    print(f"{'policy_id citation correct':<38}{pct(rag_on['policy_id_consistent']):>15}"
          f"{pct(rag_off['policy_id_consistent']):>15}")
    print(f"{'policy_id hallucinated (not real)':<38}{pct(rag_on['policy_id_hallucinated']):>15}"
          f"{pct(rag_off['policy_id_hallucinated']):>15}")
    print(f"{'mean confidence':<38}{rag_on['confidence'].mean():>15.2f}"
          f"{rag_off['confidence'].mean():>15.2f}")
    print(f"{'sufficient_evidence rate':<38}{pct(rag_on['sufficient_evidence']):>15}"
          f"{pct(rag_off['sufficient_evidence']):>15}")
    print(f"{'gate: auto_resolve rate':<38}{pct(rag_on['gate_decision'] == 'auto_resolve'):>15}"
          f"{pct(rag_off['gate_decision'] == 'auto_resolve'):>15}")
    print()
    print("Note: gate.py requires policy_id_consistent==True to ever auto-resolve, "
          "regardless of confidence -- so a citation miss under RAG-OFF forces escalation "
          "even for cases the agent sounds equally confident about.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n-per-type", type=int, default=DEFAULT_N_PER_TYPE)
    parser.add_argument("--data-dir", default=DATA_DIR)
    args = parser.parse_args()

    if config.LLM_PROVIDER == "mock":
        print("ERROR: RAG ablation requires a real LLM provider. The mock provider ignores "
              "system_prompt entirely (deterministic policy-KB lookup keyed off the evidence "
              "block, not the prompt), so RAG-ON and RAG-OFF would be identical -- the "
              "ablation would prove nothing.")
        print("Set LLM_PROVIDER=ollama or LLM_PROVIDER=groq in .env and retry.")
        raise SystemExit(1)

    report, settlement_matches, ledger_check = run(args.data_dir)
    escalated = report[report["final_exception_type"].notna() & (~report["auto_resolve_eligible"])]
    cases = stratified_sample(escalated, args.n_per_type)

    provider = get_active_provider()
    print(f"Provider: {provider.name} ({provider.model})")
    print(f"Cases: {len(cases)} ({args.n_per_type} per exception type x "
          f"{cases['final_exception_type'].nunique()} types)")
    print(f"Each case runs twice (RAG-ON, RAG-OFF) -- {len(cases) * 2} total LLM calls.")
    print()

    print("Running RAG-ON (policy retrieved)...")
    rag_on = run_condition(cases, use_policy_retrieval=True, label="RAG-ON")
    print("Running RAG-OFF (no policy retrieved)...")
    rag_off = run_condition(cases, use_policy_retrieval=False, label="RAG-OFF")

    print_report(rag_on, rag_off)

    detail = rag_on.merge(
        rag_off, on=["transaction_id", "exception_type", "authoritative_policy_id"],
        suffixes=("_rag_on", "_rag_off"),
    )
    out_csv = os.path.join(args.data_dir, "rag_ablation_detail.csv")
    detail.to_csv(out_csv, index=False)
    print(f"\nPer-case detail written to: {out_csv}")


if __name__ == "__main__":
    main()
