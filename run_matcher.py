"""
Deterministic multi-pass reconciliation matcher -- CLI entrypoint.

Runs blocking -> settlement<->bank matching -> ledger-vs-gateway
discrepancy detection -> combined report. Never reads ground_truth.csv
(see evaluate.py for scoring against it).

    python run_matcher.py
"""

import os

from matching.loaders import load_sources
from matching.settlement_builder import build_settlement_candidates
from matching.blocking import build_blocks
from matching.engine import run_matching
from matching.ledger_check import check_ledger_vs_gateway
from matching.report import build_report

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def run(data_dir: str = DATA_DIR):
    gateway, bank, ledger = load_sources(data_dir)

    settlements = build_settlement_candidates(gateway)
    blocks = build_blocks(settlements, bank)
    settlement_matches = run_matching(settlements, blocks, bank)

    ledger_check = check_ledger_vs_gateway(gateway, ledger)
    report = build_report(ledger_check, settlement_matches, gateway, ledger)

    return report, settlement_matches, ledger_check


def print_summary(report, settlement_matches):
    n = len(report)
    clean = report["is_clean"].sum()
    print(f"Processed {n} ledger transactions")
    print(f"No exception detected (NOT the same as 'settlement matched' -- see below): "
          f"{clean} ({clean/n:.1%})")
    print(f"Exceptions found: {n - clean} ({(n-clean)/n:.1%})")
    print()

    print("Settlement matching (this is the actual bank-matching success rate):")
    print(settlement_matches["match_status"].value_counts())
    print()
    print("Settlement match pass breakdown (matched only):")
    print(settlement_matches[settlement_matches["match_status"] != "unmatched"]["match_pass"].value_counts())
    print()

    print("Exception type breakdown:")
    print(report["final_exception_type"].value_counts(dropna=True))
    print()

    auto = report[report["final_exception_type"].notna()]["auto_resolve_eligible"]
    if len(auto):
        print(f"Of {len(auto)} exceptions: {auto.sum()} auto-resolve-eligible, {(~auto).sum()} escalate")
    print()

    print("Risk class breakdown (exceptions only):")
    print(report[report["final_exception_type"].notna()]["risk_class"].value_counts())


if __name__ == "__main__":
    import time
    t0 = time.perf_counter()
    report, settlement_matches, ledger_check = run()
    elapsed = time.perf_counter() - t0
    print_summary(report, settlement_matches)
    print()
    print(f"Throughput: {len(report)} transactions in {elapsed:.2f}s "
          f"(~{len(report)/elapsed:,.0f} txn/s), fully deterministic, zero LLM calls.")
