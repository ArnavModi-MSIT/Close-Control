"""
Naive baseline -- quantifies why the multi-pass matcher's tolerance/split/
ambiguity logic actually matters, instead of just asserting it.

This baseline is deliberately dumber than matching/engine.py: exact
account + exact settle-date (no blocking window) + exact amount (no
shortage/overage tolerance) + no split-settlement handling + no tie
detection (picks the first candidate found, the way an early, naive
reconciliation script typically would). It reuses matching/loaders.py,
matching/settlement_builder.py, matching/ledger_check.py, and
matching/report.py completely unmodified -- only the settlement<->bank
matching PASS itself is replaced, so the comparison isolates exactly the
thing being measured (does the sophistication in engine.py earn its keep)
rather than re-testing ledger-vs-gateway detection twice.

Never reads ground_truth.csv for anything except final scoring, same rule
as evaluate.py.

    python run_baseline_naive.py
"""

import os
import time

import pandas as pd

from matching.loaders import load_sources
from matching.settlement_builder import build_settlement_candidates
from matching.blocking import bank_account_for_merchant
from matching.ledger_check import check_ledger_vs_gateway
from matching.report import build_report
from run_matcher import run as run_full_matcher
# Delegates to evaluate.py's own load_ground_truth() rather than a second,
# independently-hand-copied pd.read_csv(".../ground_truth.csv") -- this is
# not a second reader, it's the SAME sanctioned reader, reused, so
# test_ground_truth_isolation.py's static scan only has one real read call
# to allowlist, not two that could silently drift apart.
from evaluate import load_ground_truth

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
NAIVE_AMOUNT_TOLERANCE_RUPEES = 0.02  # same paisa-rounding-only definition of "exact" the real engine uses


def naive_match(settlements: pd.DataFrame, bank: pd.DataFrame) -> pd.DataFrame:
    consumed = set()
    results = []
    ordered = settlements.sort_values(["settle_date", "settlement_id"])
    bank_by_account = {acct: grp for acct, grp in bank.groupby("bank_account_id")}

    for _, s in ordered.iterrows():
        sid = s["settlement_id"]
        expected = s["expected_total_rupees"]
        acct = bank_account_for_merchant(s["merchant_id"])
        result = {
            "settlement_id": sid,
            "merchant_id": s["merchant_id"],
            "member_count": s["member_count"],
            "expected_total_rupees": expected,
            "match_status": "unmatched",
            "match_pass": None,
            "missing_bank_reference": False,
            "bank_overage": False,
            "had_ambiguous_candidates": False,
        }

        candidates = bank_by_account.get(acct)
        if candidates is not None and not candidates.empty:
            # naive: only the settlement's own settle_date, no window at all
            same_day = candidates[
                (candidates["credit_date"] == s["settle_date"])
                & (~candidates["bank_txn_id"].isin(consumed))
            ]
            exact = same_day[(same_day["credit_amount_rupees"] - expected).abs() <= NAIVE_AMOUNT_TOLERANCE_RUPEES]
            if not exact.empty:
                # naive: no tie/ambiguity detection -- first candidate found wins,
                # exactly the behavior a genuinely naive script would have
                row = exact.iloc[0]
                consumed.add(row["bank_txn_id"])
                result.update({
                    "match_status": "matched",
                    "match_pass": "naive_exact_same_day",
                    "missing_bank_reference": bool(pd.isna(row["utr"])),
                })
        results.append(result)

    return pd.DataFrame(results)


def score(report: pd.DataFrame, gt: pd.DataFrame) -> dict:
    """Deliberately lighter than evaluate.py's full settlement-aware scoring
    (this script's job is a fair contrast, not a second production
    evaluator) -- same core ideas: predicted vs. true action over the full
    population, and whether hard-negative pairs got wrongly merged."""
    gt_primary = gt.drop_duplicates("transaction_id", keep="first")
    merged = report.merge(
        gt_primary[["transaction_id", "failure_mode", "expected_resolution", "is_clean_match"]],
        on="transaction_id", how="left",
    )

    predicted_clean = merged["final_exception_type"].isna()
    true_clean = merged["is_clean_match"]
    accuracy = (predicted_clean == true_clean).mean()

    merged["predicted_action"] = merged["final_exception_type"].apply(
        lambda x: "escalate" if pd.notna(x) else "auto_resolve")
    merged.loc[merged["final_exception_type"].notna() & merged["auto_resolve_eligible"], "predicted_action"] = "auto_resolve"
    false_auto = ((merged["predicted_action"] == "auto_resolve") & (merged["expected_resolution"] == "escalate")).sum()

    # Same correctness standard evaluate.py §6 uses: a hard-negative pair is
    # handled correctly if it resolves clean 1:1 (genuinely distinct
    # payments, matched correctly) OR is safely escalated as ambiguous --
    # NOT "resolved clean" alone. A naive matcher with no tie-detection can
    # "resolve clean" on a hard negative by accident (no ambiguity check to
    # catch it) while the full system correctly escalates the same case --
    # scoring only "resolved clean" would make that correct escalation look
    # like a miss, which is backwards.
    hn = merged[merged["failure_mode"] == "hard_negative"]
    hn_resolved_clean = hn["final_exception_type"].isna()
    hn_escalated_ambiguous = hn["final_exception_type"] == "ambiguous_bank_match"
    hn_correct_outcome = int((hn_resolved_clean | hn_escalated_ambiguous).sum())

    return {
        "clean_vs_exception_accuracy_pct": round(accuracy * 100, 2),
        "false_auto_resolve_count": int(false_auto),
        "false_auto_resolve_rate_pct": round(false_auto / len(merged) * 100, 2),
        "hard_negative_total": len(hn),
        "hard_negative_correct_outcome": hn_correct_outcome,
        "transactions_processed": len(merged),
    }


def main():
    gt = load_ground_truth()

    print("=" * 70)
    print("BASELINE A: NAIVE RECONCILIATION")
    print("(exact account + exact settle-date, no window; exact amount, no")
    print(" shortage/overage tolerance; no split handling; no tie detection)")
    print("=" * 70)
    gateway, bank, ledger = load_sources(DATA_DIR)
    settlements = build_settlement_candidates(gateway)
    ledger_check = check_ledger_vs_gateway(gateway, ledger)

    t0 = time.perf_counter()
    naive_matches = naive_match(settlements, bank)
    naive_report = build_report(ledger_check, naive_matches, gateway, ledger)
    naive_elapsed = time.perf_counter() - t0
    naive_scores = score(naive_report, gt)
    naive_matched = (naive_matches["match_status"] == "matched").sum()

    for k, v in naive_scores.items():
        print(f"  {k}: {v}")
    print(f"  settlements matched: {naive_matched}/{len(naive_matches)} "
          f"({naive_matched/len(naive_matches):.1%})")
    print(f"  elapsed: {naive_elapsed:.2f}s")
    print()

    print("=" * 70)
    print("BASELINE D: FULL SYSTEM (matching/engine.py, unmodified)")
    print("=" * 70)
    t0 = time.perf_counter()
    full_report, full_settlement_matches, _ = run_full_matcher(DATA_DIR)
    full_elapsed = time.perf_counter() - t0
    full_scores = score(full_report, gt)
    full_matched = (full_settlement_matches["match_status"] != "unmatched").sum()

    for k, v in full_scores.items():
        print(f"  {k}: {v}")
    print(f"  settlements matched (incl. w/ exception): {full_matched}/{len(full_settlement_matches)} "
          f"({full_matched/len(full_settlement_matches):.1%})")
    print(f"  elapsed: {full_elapsed:.2f}s")
    print()

    print("=" * 70)
    print("COMPARISON")
    print("=" * 70)
    print(f"{'metric':<42}{'naive':>14}{'full system':>16}")
    print(f"{'clean/exception accuracy':<42}{naive_scores['clean_vs_exception_accuracy_pct']:>13}%{full_scores['clean_vs_exception_accuracy_pct']:>15}%")
    print(f"{'false auto-resolve rate':<42}{naive_scores['false_auto_resolve_rate_pct']:>13}%{full_scores['false_auto_resolve_rate_pct']:>15}%")
    print(f"{'settlements matched':<42}{naive_matched:>13}/{len(naive_matches)}{full_matched:>10}/{len(full_settlement_matches)}")
    print()
    print("The naive baseline's settlement match count directly quantifies what the")
    print("blocking window and split-settlement passes in matching/engine.py actually")
    print("buy -- every settlement it misses that the full system catches is a real")
    print("transaction that would have sat unexplained under exact-date, exact-amount,")
    print("1:1-only matching.")
    print()
    print("Deliberately NOT credited here: the shortage/overage-tolerance passes.")
    print("They contribute nothing to this gap because they never fire on the curated")
    print("dataset -- every bank posting equals its settlement total exactly, so the")
    print("only passes that ever run are exact, split, and the ambiguity escalations")
    print("(verify with evaluate.py's match-pass distribution). Those two passes are")
    print("real and proven, just by test_ambiguity.py's scenarios 8 and 9 rather than")
    print("by this dataset -- claiming them here would overstate what's measured.")

    # Guard against this whole comparison going quietly vacuous. If a future
    # data-generation change made the naive matcher just as good, every claim
    # above would silently become meaningless while still printing happily.
    # Idea borrowed from a Snowflake cost-attribution project that gates its
    # build on "naive must strictly underreport" for exactly this reason --
    # a demo dataset that stops being adversarial is a real, silent failure.
    if naive_matched >= full_matched:
        print()
        print("=" * 70)
        print("ASSERTION FAILED: the naive baseline matched "
              f"{naive_matched}/{len(naive_matches)} settlements, which is NOT worse")
        print(f"than the full system's {full_matched}/{len(full_settlement_matches)}.")
        print("Either the matcher regressed, or the dataset stopped exercising the")
        print("blocking/split logic -- in which case every 'why the multi-pass matcher")
        print("matters' claim in this project is currently unsupported by its own data.")
        print("=" * 70)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
