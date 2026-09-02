"""
Deterministic multi-pass reconciliation matcher -- CLI entrypoint.

Runs blocking -> settlement<->bank matching -> ledger-vs-gateway
discrepancy detection -> combined report. Never reads ground_truth.csv
(see evaluate.py for scoring against it).

Every real caller of run() -- agent/, investigator/, review_backend/,
cash_position/, every test -- goes through this one function, which is
exactly why the invariant checks below live here and not only inside
evaluate.py's own diagnostic report. This project's core rule ("AI
proposes, deterministic code disposes") is usually read as a rule about
the LLM layer -- but a wrong classification the MATCHER itself silently
produced would be just as dangerous downstream (it becomes a case's
`final_exception_type`, is treated as ground truth by agent/gate.py, and
an LLM never gets a chance to question it, since escalation is keyed off
exactly this field). matching/diagnostics.py's `verify_consumption_invariants()`
and `settlement_conservation_summary()` already independently recompute
whether the matcher's own claimed matches actually hold up -- they existed
before this change, but only ran when evaluate.py happened to call them.
Wiring them in here means the matcher's own output is re-verified on
EVERY real run, not just when someone explicitly asks for a diagnostic
report -- the same "trust no proposer, including the ones that cannot
lie" standard this project already applies to the LLM layer, extended
one level earlier, to the layer that hands the LLM its facts in the
first place.

    python run_matcher.py
"""

import os

from matching.loaders import load_sources, load_loan_book
from matching.settlement_builder import build_settlement_candidates
from matching.blocking import build_blocks
from matching.engine import run_matching
from matching.ledger_check import check_ledger_vs_gateway
from matching.report import build_report
from matching.diagnostics import verify_consumption_invariants, settlement_conservation_summary

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


class MatcherInvariantError(RuntimeError):
    """The matcher's own output failed a check that has nothing to do with
    the LLM layer at all -- see this module's own docstring. Should never
    actually fire against real, non-corrupted input data; if it does, the
    matcher's own report cannot be trusted and nothing downstream (a
    classification, an auto-resolve, a cash-position figure) should run
    against it either."""


def run(data_dir: str = DATA_DIR):
    gateway, bank, ledger = load_sources(data_dir)
    loan_book = load_loan_book(data_dir)

    settlements = build_settlement_candidates(gateway)
    blocks = build_blocks(settlements, bank)
    settlement_matches = run_matching(settlements, blocks, bank)

    # verify_consumption_invariants() already raises AssertionError on its
    # own violation (a bank row claimed by more than one settlement, or a
    # matched id that doesn't exist in the real bank statement) -- calling
    # it is enough. settlement_conservation_summary() does NOT raise on its
    # own (evaluate.py's own diagnostic report wants a full summary even
    # when something's wrong, not a crash mid-report), so its finding is
    # checked and raised HERE instead -- at the one call site every real
    # consumer of this module goes through, not just evaluate.py's.
    verify_consumption_invariants(settlement_matches, bank)
    conservation = settlement_conservation_summary(settlement_matches)
    mismatched = conservation["exact_or_split_pass_with_real_delta"]
    if mismatched:
        raise MatcherInvariantError(
            f"matching invariant violated: settlement(s) matched via an 'exact'/'split' pass "
            f"(which claims a precise reconciliation) but have a real, non-tolerance delta "
            f"between matched and expected total: {mismatched[:5]}"
        )

    ledger_check = check_ledger_vs_gateway(gateway, ledger, loan_book)
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
    from audit_manifest import write_manifest, summary_line
    t0 = time.perf_counter()
    report, settlement_matches, ledger_check = run()
    elapsed = time.perf_counter() - t0
    print_summary(report, settlement_matches)
    print()
    print(f"Throughput: {len(report)} transactions in {elapsed:.2f}s "
          f"(~{len(report)/elapsed:,.0f} txn/s), fully deterministic, zero LLM calls.")

    # Pin this run to the exact input bytes and threshold values that
    # produced it -- see audit_manifest.py. Cheap (three file hashes) and
    # written on every run, so any published number can be traced back.
    manifest_results = {
        "transactions": int(len(report)),
        "clean": int(report["is_clean"].sum()),
        "settlements": int(len(settlement_matches)),
        "settlements_matched": int((settlement_matches["match_status"] != "unmatched").sum()),
        "escalated": int((report["final_exception_type"].notna()
                          & (~report["auto_resolve_eligible"])).sum()),
        "elapsed_seconds": round(elapsed, 3),
    }
    path, manifest = write_manifest(DATA_DIR, results=manifest_results)
    print()
    print(summary_line(manifest))
    print(f"Run manifest: {path}")
