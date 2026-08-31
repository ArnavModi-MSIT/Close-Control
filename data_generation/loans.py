"""Razorpay Capital loan recoveries -- a working-capital advance being
repaid by deducting a contracted percentage of the merchant's own
settlements.

This is not a separate payment flow bolted onto the side. Razorpay Capital
really does collect this way: merchants "pay them as a percentage of your
settlements... repay automatically through settlements," and Razorpay's
terms reserve the right to "recover any amounts from the Transaction Amount
to be settled to you... by way of deduction." So a loan repayment shows up
inside the settlement pipeline as a smaller-than-expected bank credit --
which is precisely the shape this project already reconciles.

WHY THIS EXISTS AS A DATASET SCENARIO
Before this module, matching/ledger_check.py had no way to tell a
contracted recovery apart from money genuinely going missing: the deduction
fell through every explanation branch and landed on `unexplained_shortage`
-- high risk, never auto-resolvable, escalated to a human. That is a false
positive on the single most severe classification the matcher has. The
fourth source (data/loan_recovery_schedule.csv) is what turns it into an
explained, auto-resolvable variance, and it is a genuine cross-system join,
not another column on an existing row.

INJECTION PATTERN
Identical to chargebacks.py, for identical reasons: a SEPARATE
transaction-id space (trn-loan###), built AFTER the main payment generation
has drawn all of its randomness, merged only into gateway_df / bank_df /
ledger_df / gt_df, never back into `payments`, and deliberately NOT added
to config.FAILURE_MODES (which would reshuffle every existing payment's
drawn mode on the shared sequential RNG stream and invalidate the
investigator benchmark, the audit log, the seeded review queue, and every
published headline number at once).

RECONCILIATION SHAPE
  gateway  -- settled, carrying the recovery as a negative adjustment
  bank     -- shows the REDUCED credit, so settlement matching still ties
              out exactly; a recovery is a value exception, not a matching
              failure (same principle as a chargeback)
  ledger   -- still expects the ORIGINAL net, because the settlement was
              booked before Capital's recovery was applied to it. That gap
              is the exception.
  loan book -- the fourth source, which explains the gap.

Note the deduction is a negative adjustment, exactly like a refund, so sign
alone cannot separate the two -- the presence of a matching recovery record
is the real distinguishing signal, the same role chargeback_id plays for
disputes. matching/ledger_check.py therefore checks this BEFORE its
partial_refund branch, and only accepts the explanation when the recovery
amount actually reconciles the observed delta (see test_loan_recovery.py
Scenario 3, where a recovery that does NOT explain the shortage correctly
falls through to unexplained_shortage instead of being waved through).
"""

import random
import datetime as dt

import pandas as pd

from . import config
from .utils import (add_business_days, rand_id, rand_utr, gross_amount,
                     pick_method, random_datetime, unix_ts, compute_fee_tax)

# The columns of the fourth source file. This is an operational feed from
# Razorpay Capital's own recovery ledger -- it carries no failure label and
# no ground truth, exactly like gateway.json / bank_statement.csv /
# internal_settlement_ledger.csv.
LOAN_BOOK_COLUMNS = [
    "recovery_id", "loan_id", "merchant_id", "transaction_id",
    "loan_principal_rupees", "recovery_rate_pct", "recovery_amount_rupees",
    "recovery_date", "recovery_method", "status",
]


def _build_loans(n_loans: int) -> list:
    """A handful of merchants carry an active advance. Kept deliberately
    small -- the point is that SOME merchants have a Capital facility and
    most don't, so the matcher has to actually look the merchant up rather
    than assuming every shortage is a recovery."""
    loans = []
    merchants = random.sample(config.MERCHANTS, k=min(n_loans, len(config.MERCHANTS)))
    for merchant_id, _name in merchants:
        principal = random.randint(*config.LOAN_PRINCIPAL_RANGE_RUPEES)
        loans.append({
            "loan_id": rand_id("loan", 10),
            "merchant_id": merchant_id,
            "principal_rupees": float(principal),
            "recovery_rate_pct": round(random.uniform(*config.LOAN_RECOVERY_RATE_RANGE), 4),
        })
    return loans


def add_loan_recoveries(payments: pd.DataFrame, n: int = None):
    """Returns (extra_gw, extra_bank, extra_ledger, extra_gt, loan_book) --
    the first four to concat onto the already-generated sources (same
    contract as add_hard_negatives() / add_chargebacks()), plus the fourth
    source file itself."""
    n = config.LOAN_RECOVERY_COUNT if n is None else n
    extra_gw, extra_bank, extra_ledger, extra_gt, loan_rows = [], [], [], [], []

    # Start this id space after BOTH previously-appended spaces so no
    # payment_index ever collides (hard negatives take 2 per pair,
    # chargebacks take 1 each).
    base_idx = (int(payments["payment_index"].max()) + 1
                 + (config.HARD_NEGATIVE_PAIRS * 2) + config.CHARGEBACK_COUNT)

    loans = _build_loans(n_loans=3)

    for i in range(n):
        loan = loans[i % len(loans)]
        merchant_id = loan["merchant_id"]
        merchant_name = dict(config.MERCHANTS)[merchant_id]

        amount = gross_amount()
        method = pick_method()
        # Same headroom before the batch end as hard_negatives.py and
        # chargebacks.py, so the settle date stays inside the dataset range.
        day = config.BATCH_START + dt.timedelta(days=random.randint(0, config.BATCH_DAYS - 5))
        captured_at = random_datetime(day)

        fee, tax = compute_fee_tax(amount, method)
        net_original = amount - fee - tax
        # Recovery is a contracted percentage of THIS settlement, in paise,
        # so the deduction is an exact integer the reconciliation can tie to.
        recovery = round(net_original * loan["recovery_rate_pct"])
        net_after = net_original - recovery

        settle_day = add_business_days(captured_at.date(), 2)
        settlement_id = rand_id("setl", 12)
        posting_id = rand_id("post", 10)
        utr = rand_utr()
        txn_id = f"trn-loan{i:03d}"
        order_id = rand_id("order", 14)
        pay_id = rand_id("pay", 14)
        idx = base_idx + i

        extra_gw.append({
            "razorpay_payment_id": pay_id, "order_id": order_id, "transaction_id_ref": txn_id,
            "settlement_id": settlement_id, "merchant_id": merchant_id, "payment_method": method,
            "payment_amount_paise": amount, "fee_paise": fee, "tax_paise": tax,
            # Negative adjustment -- indistinguishable from a refund by sign,
            # which is exactly why the loan book has to be consulted.
            "adjustment_paise": -recovery, "settlement_amount_paise": net_after,
            "status": "processed",
            "captured_at": unix_ts(captured_at),
            "settled_at": unix_ts(dt.datetime.combine(settle_day, dt.time(16, 30))),
            "signature_valid": True, "attempt_number": 1, "attempt_status": "success",
            "successful_attempt_id": None, "refund_id": None, "refund_reason": None,
            "chargeback_id": None, "chargeback_reason": None,
            "duplicate_of_payment_id": None, "payment_index_internal": idx,
        })
        # Bank shows what actually landed after Capital's deduction, so the
        # settlement<->bank match still ties out exactly.
        extra_bank.append({
            "bank_txn_id": rand_id("bnk", 12), "settlement_posting_id": posting_id, "utr": utr,
            "credit_amount_rupees": round(net_after / 100.0, 2),
            "credit_date": settle_day.isoformat(), "value_date": settle_day.isoformat(),
            "narration": f"NEFT-{merchant_name[:10].upper().replace(' ', '')}-SETL-CAPRECOV",
            "bank_account_id": f"acct_{merchant_id}", "transaction_type": "credit",
        })
        # The settlement ledger booked before Capital applied the recovery,
        # so it still expects the pre-recovery net.
        extra_ledger.append({
            "ledger_id": rand_id("led", 10), "transaction_id": txn_id, "order_id": order_id,
            "merchant_id": merchant_id, "gross_amount_rupees": round(amount / 100.0, 2),
            "expected_fee_rupees": round(fee / 100.0, 2), "expected_tax_rupees": round(tax / 100.0, 2),
            "expected_adjustment_rupees": 0.0,
            "expected_net_settlement_rupees": round(net_original / 100.0, 2),
            "expected_settlement_date": settle_day.isoformat(), "status": "expected",
        })
        loan_rows.append({
            "recovery_id": rand_id("rcv", 12),
            "loan_id": loan["loan_id"],
            "merchant_id": merchant_id,
            "transaction_id": txn_id,
            "loan_principal_rupees": loan["principal_rupees"],
            "recovery_rate_pct": loan["recovery_rate_pct"],
            "recovery_amount_rupees": round(recovery / 100.0, 2),
            "recovery_date": settle_day.isoformat(),
            "recovery_method": "settlement_deduction",
            "status": "applied",
        })
        extra_gt.append({
            "payment_index": idx, "transaction_id": txn_id, "order_id": order_id,
            "razorpay_payment_id": pay_id, "settlement_id": settlement_id,
            "settlement_posting_id": posting_id, "utr": utr, "merchant_id": merchant_id,
            # Named to match matching/ledger_check.py's emitted exception_type
            # exactly, so evaluate.py's per-type precision/recall needs no
            # rename shim (same convention as chargebacks.py).
            "failure_mode": "loan_recovery_deduction", "ambiguity_flag": False, "ambiguity_reason": None,
            "payment_bank_relationship": "1:1", "settlement_bank_relationship": "1:1",
            "is_clean_match": False, "expected_auto_resolvable": True, "risk_class": "low",
            "expected_resolution": "auto_resolve",
            "original_payment_id": None, "duplicate_of_event_id": None,
            "note": f"razorpay_capital_recovery: Rs.{recovery/100.0:,.2f} "
                    f"({loan['recovery_rate_pct']:.1%} of settlement) deducted against "
                    f"advance {loan['loan_id']}",
        })

    return (pd.DataFrame(extra_gw), pd.DataFrame(extra_bank),
            pd.DataFrame(extra_ledger), pd.DataFrame(extra_gt),
            pd.DataFrame(loan_rows, columns=LOAN_BOOK_COLUMNS))
