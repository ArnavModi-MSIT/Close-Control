"""
Synthetic Settlement Reconciliation Dataset Generator -- CLI entrypoint.

All logic lives in data_generation/; this file just orchestrates the
pipeline and writes output. See data_generation/*.py for the actual
generation, validation, and data_generation/sources/ for per-source builders.

    python scripts/generate_data.py
    RNG_SEED_OVERRIDE=1337 python scripts/generate_data.py --out-dir data_seed_1337
"""

import os as _os
import sys as _sys
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)
_SCRIPTS_DIR = _os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPTS_DIR)

import argparse
import datetime as dt

import pandas as pd

from data_generation import config
from data_generation.payments import build_payments
from data_generation.settlements import assign_settlement_groups, decide_group_properties
from data_generation.sources.gateway import build_gateway_records
from data_generation.sources.bank import build_bank_records
from data_generation.sources.ledger import build_ledger_records
from data_generation.hard_negatives import add_hard_negatives, label_amount_collisions
from data_generation.chargebacks import add_chargebacks
from data_generation.loans import add_loan_recoveries
from data_generation.ground_truth import build_ground_truth
from data_generation.validation import validate_dataset
from ingestion.warehouse import run_ingestion
from ingestion import config as ingestion_config

import os
DEFAULT_OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def main(out_dir: str = DEFAULT_OUT_DIR):
    OUT_DIR = out_dir
    os.makedirs(OUT_DIR, exist_ok=True)
    payments = build_payments(config.N_PAYMENTS)
    payments = assign_settlement_groups(payments)
    split_flags, missing_utr_groups = decide_group_properties(payments)

    gateway_df, net_contributions = build_gateway_records(payments)
    bank_df, utr_assignment, posting_id_assignment, settlement_posting_count = build_bank_records(
        payments, net_contributions, split_flags, missing_utr_groups)
    ledger_df = build_ledger_records(payments)

    collisions = label_amount_collisions(payments)
    gt_df = build_ground_truth(payments, utr_assignment, posting_id_assignment, collisions, settlement_posting_count)

    extra_gw, extra_bank, extra_ledger, extra_gt = add_hard_negatives(payments, n_pairs=config.HARD_NEGATIVE_PAIRS)
    gateway_df = pd.concat([gateway_df, extra_gw], ignore_index=True)
    bank_df = pd.concat([bank_df, extra_bank], ignore_index=True)
    ledger_df = pd.concat([ledger_df, extra_ledger], ignore_index=True)
    gt_df = pd.concat([gt_df, extra_gt], ignore_index=True)

    # Chargebacks -- appended AFTER hard negatives so neither the 2,000 base
    # payments nor the hard-negative pairs shift a single random draw (see
    # data_generation/chargebacks.py's docstring for why that matters).
    cb_gw, cb_bank, cb_ledger, cb_gt = add_chargebacks(payments, n=config.CHARGEBACK_COUNT)
    gateway_df = pd.concat([gateway_df, cb_gw], ignore_index=True)
    bank_df = pd.concat([bank_df, cb_bank], ignore_index=True)
    ledger_df = pd.concat([ledger_df, cb_ledger], ignore_index=True)
    gt_df = pd.concat([gt_df, cb_gt], ignore_index=True)

    # Razorpay Capital loan recoveries -- appended after chargebacks, same
    # separate-id-space pattern and the same RNG-stability reason (see
    # data_generation/loans.py). Produces a FOURTH source file: Capital's own
    # recovery ledger, which is what lets matching/ledger_check.py tell a
    # contracted deduction apart from money genuinely going missing.
    ln_gw, ln_bank, ln_ledger, ln_gt, loan_book_df = add_loan_recoveries(
        payments, n=config.LOAN_RECOVERY_COUNT)
    gateway_df = pd.concat([gateway_df, ln_gw], ignore_index=True)
    bank_df = pd.concat([bank_df, ln_bank], ignore_index=True)
    ledger_df = pd.concat([ledger_df, ln_ledger], ignore_index=True)
    gt_df = pd.concat([gt_df, ln_gt], ignore_index=True)

    # Multi-partner bank ingestion round-trip: canonical bank rows -> each
    # partner's own raw export format -> back to canonical, plus a few
    # orphan bank credits with no settlement at all. Value-preserving by
    # design (see ingestion/warehouse.py's identity assertion) -- this
    # changes what the raw input looks like, not the reconciliation truth.
    real_bank_row_count = len(bank_df)
    bank_df, ingestion_example, ingestion_metrics = run_ingestion(bank_df, gateway_df, OUT_DIR)
    orphan_credit_count = len(bank_df) - real_bank_row_count

    stats = validate_dataset(payments, gateway_df, bank_df, ledger_df, gt_df,
                              hard_negative_pairs=config.HARD_NEGATIVE_PAIRS,
                              loan_book_df=loan_book_df)

    # drop internal join-aid column before writing
    gateway_df = gateway_df.drop(columns=["payment_index_internal"])

    gateway_df.to_json(f"{OUT_DIR}/gateway.json", orient="records", indent=2)
    bank_df.to_csv(f"{OUT_DIR}/bank_statement.csv", index=False)
    ledger_df.to_csv(f"{OUT_DIR}/internal_settlement_ledger.csv", index=False)
    loan_book_df.to_csv(f"{OUT_DIR}/loan_recovery_schedule.csv", index=False)
    gt_df.to_csv(f"{OUT_DIR}/ground_truth.csv", index=False)

    batch_end = config.BATCH_START + dt.timedelta(days=config.BATCH_DAYS - 1)
    meta = {
        "dataset_version": config.DATASET_VERSION,
        "seed": config.RNG_SEED,
        "logical_payments": config.N_PAYMENTS,
        "hard_negative_pairs": config.HARD_NEGATIVE_PAIRS,
        "capture_start": config.BATCH_START.isoformat(),
        "capture_end": batch_end.isoformat(),
        "exception_rate_target": config.EXCEPTION_RATE,
        "exception_rate_actual": round(1 - gt_df["is_clean_match"].mean(), 4),
        "gateway_rows": len(gateway_df),
        "bank_rows": len(bank_df),
        "ledger_rows": len(ledger_df),
        "loan_recovery_rows": len(loan_book_df),
        "loan_accounts": int(loan_book_df["loan_id"].nunique()) if len(loan_book_df) else 0,
        "ground_truth_rows": len(gt_df),
        "split_settlement_groups": sum(split_flags.values()),
        "missing_utr_groups": len(missing_utr_groups),
        "bank_partners": list(ingestion_config.PARTNER_DISPLAY_NAMES.values()),
        "orphan_credit_count": orphan_credit_count,
        "ingestion_metrics": ingestion_metrics,
        **stats,
    }
    pd.Series(meta).to_json(f"{OUT_DIR}/dataset_metadata.json", indent=2)

    print(f"Payments (incl. hard negatives + duplicates): {len(gt_df)}")
    print(f"Gateway records:                              {len(gateway_df)}")
    print(f"Bank records (settlement postings):            {len(bank_df)}")
    print(f"Ledger records:                                {len(ledger_df)}")
    print(f"Loan recovery records (Razorpay Capital):      {len(loan_book_df)} "
          f"across {loan_book_df['loan_id'].nunique() if len(loan_book_df) else 0} advances")
    print(f"Unique settlements:                            {stats['unique_settlements']}")
    print(f"Unique bank postings:                          {stats['unique_bank_postings']}")
    print(f"Payments with N:1 payment->bank relationship:  {stats['n1_payment_count']}")
    print(f"Payments in 1:N settlement->bank groups:       {stats['one_n_settlement_payment_count']}")
    print()
    print("payment_bank_relationship distribution:")
    print(gt_df["payment_bank_relationship"].value_counts())
    print()
    print("settlement_bank_relationship distribution:")
    print(gt_df["settlement_bank_relationship"].value_counts())
    print()
    print("Failure mode distribution:")
    print(gt_df["failure_mode"].value_counts())
    print()
    print(f"Ambiguity-flagged rows (separate from failure_mode now): {gt_df['ambiguity_flag'].sum()}")
    print(f"Clean match rate: {gt_df['is_clean_match'].mean():.2%}")
    print()
    print("VALIDATION: all invariants passed (numeric dtypes, PK uniqueness,")
    print("settlement amount consistency, relationship coverage, no leakage).")
    print()
    print("Bank partners:                                 " +
          ", ".join(ingestion_config.PARTNER_DISPLAY_NAMES.values()))
    print(f"Orphan bank credits (no settlement at all):    {orphan_credit_count}")
    print(f"Ingestion round-trip: {ingestion_metrics['raw_rows']} raw rows -> "
          f"{ingestion_metrics['normalized_rows']} normalized rows "
          f"({ingestion_metrics['orphan_rows']} orphan) across "
          f"{len(ingestion_metrics['partners_processed'])} partners -- "
          f"identity check: round_trip_ok={ingestion_metrics['identity_check']['round_trip_ok']}, "
          f"{ingestion_metrics['rows_round_tripped']} real rows verified byte-identical "
          f"across {len(ingestion_metrics['identity_check']['identity_fields_checked'])} fields.")
    if ingestion_example:
        print(f"Example raw->normalized round-trip ({ingestion_example['partner']}):")
        print(f"  raw:        {ingestion_example['raw_row']}")
        print(f"  normalized: {ingestion_example['normalized_row']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                         help="Write the generated dataset here instead of data/ -- use this for "
                              "a seed-robustness check (RNG_SEED_OVERRIDE + a different --out-dir) "
                              "so the curated demo dataset is never touched.")
    args = parser.parse_args()
    main(args.out_dir)
