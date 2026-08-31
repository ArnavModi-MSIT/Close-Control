"""Chargeback transactions -- the issuer disputes a settled payment and
claws the funds back from the merchant.

Injected the same way hard_negatives.py injects its distractor pairs: as a
SEPARATE transaction-id space (trn-cb###), built AFTER the main payment
generation has already drawn all of its randomness, and merged only into
gateway_df / bank_df / ledger_df / gt_df -- never back into `payments`.

Why that matters, specifically: a chargeback is NOT added to
config.FAILURE_MODES. Doing that would change the `modes`/`mode_weights`
tuples that payments.py feeds to `random.choices()`, which would reshuffle
every existing payment's drawn failure mode on the shared sequential RNG
stream -- silently invalidating the investigator benchmark (real GPU hours
of logged runs keyed by transaction_id), the audit log, the seeded review
queue, and every published headline number at once. Appending after that
draw is complete leaves all 2,040 existing transactions byte-identical
while still putting genuine chargebacks in the dataset.

Reconciliation shape (deliberately mirrors how a real settlement file
carries a dispute debit): the payment settled normally, then the clawback
reduced what actually reached the merchant. So the gateway record carries
chargeback_id + a large negative adjustment, the bank posting shows the
REDUCED amount (it matches the settlement exactly -- the dispute is not a
matching failure), and the internal ledger still expects the ORIGINAL net
because it booked the settlement before the dispute existed. That gap is
the exception, and matching/ledger_check.py classifies it as
`chargeback_received` off chargeback_id -- not as a partial_refund, which
is the other negative-adjustment cause and is indistinguishable by sign
alone (see test_chargeback.py Scenario 2).
"""

import random
import datetime as dt

import pandas as pd

from . import config
from .utils import add_business_days, rand_id, rand_utr, gross_amount, pick_method, random_datetime, unix_ts, compute_fee_tax

# Realistic dispute reasons, matching the categories NPCI's URCS actually
# routes (see agent/policy_kb.py's POLICY-012 grounding).
CHARGEBACK_REASONS = [
    "unauthorized_transaction",
    "goods_not_received",
    "duplicate_processing",
    "service_not_rendered",
    "credit_not_processed",
]


def add_chargebacks(payments: pd.DataFrame, n: int = None):
    """Returns (extra_gw, extra_bank, extra_ledger, extra_gt) to concat onto
    the already-generated sources -- same contract as add_hard_negatives()."""
    n = config.CHARGEBACK_COUNT if n is None else n
    extra_gw, extra_bank, extra_ledger, extra_gt = [], [], [], []
    base_idx = int(payments["payment_index"].max()) + 1 + (config.HARD_NEGATIVE_PAIRS * 2)

    for i in range(n):
        merchant_id, merchant_name = random.choice(config.MERCHANTS)
        amount = gross_amount()
        method = pick_method()
        # Leave headroom before the batch end so the settle date stays inside
        # the dataset's own date range, same as hard_negatives.py does.
        day = config.BATCH_START + dt.timedelta(days=random.randint(0, config.BATCH_DAYS - 5))
        captured_at = random_datetime(day)

        fee, tax = compute_fee_tax(amount, method)
        net_original = amount - fee - tax
        # The clawback takes back nearly all of the settled amount. Kept just
        # short of the full net on purpose: a settlement of exactly 0 would
        # give matching/blocking.py a zero-amount candidate window, which is
        # a degenerate case this dataset has no reason to introduce.
        clawback = -round(net_original * 0.95)
        net_after = net_original + clawback

        settle_day = add_business_days(captured_at.date(), 2)
        settlement_id = rand_id("setl", 12)
        posting_id = rand_id("post", 10)
        utr = rand_utr()
        txn_id = f"trn-cb{i:03d}"
        order_id = rand_id("order", 14)
        pay_id = rand_id("pay", 14)
        reason = random.choice(CHARGEBACK_REASONS)
        idx = base_idx + i

        extra_gw.append({
            "razorpay_payment_id": pay_id, "order_id": order_id, "transaction_id_ref": txn_id,
            "settlement_id": settlement_id, "merchant_id": merchant_id, "payment_method": method,
            "payment_amount_paise": amount, "fee_paise": fee, "tax_paise": tax,
            "adjustment_paise": clawback, "settlement_amount_paise": net_after,
            "status": "processed",
            "captured_at": unix_ts(captured_at),
            "settled_at": unix_ts(dt.datetime.combine(settle_day, dt.time(16, 30))),
            "signature_valid": True, "attempt_number": 1, "attempt_status": "success",
            "successful_attempt_id": None, "refund_id": None, "refund_reason": None,
            "chargeback_id": rand_id("cb", 12), "chargeback_reason": reason,
            "duplicate_of_payment_id": None, "payment_index_internal": idx,
        })
        # Bank shows what actually landed AFTER the clawback -- so settlement
        # matching still ties out exactly; the dispute is a value exception,
        # not a matching failure.
        extra_bank.append({
            "bank_txn_id": rand_id("bnk", 12), "settlement_posting_id": posting_id, "utr": utr,
            "credit_amount_rupees": round(net_after / 100.0, 2),
            "credit_date": settle_day.isoformat(), "value_date": settle_day.isoformat(),
            "narration": f"NEFT-{merchant_name[:10].upper().replace(' ', '')}-SETL-CBADJ",
            "bank_account_id": f"acct_{merchant_id}", "transaction_type": "credit",
        })
        # The internal ledger booked the settlement BEFORE the dispute was
        # raised, so it still expects the original net -- that mismatch is
        # exactly what the reconciliation is supposed to surface.
        extra_ledger.append({
            "ledger_id": rand_id("led", 10), "transaction_id": txn_id, "order_id": order_id,
            "merchant_id": merchant_id, "gross_amount_rupees": round(amount / 100.0, 2),
            "expected_fee_rupees": round(fee / 100.0, 2), "expected_tax_rupees": round(tax / 100.0, 2),
            "expected_adjustment_rupees": 0.0,
            "expected_net_settlement_rupees": round(net_original / 100.0, 2),
            "expected_settlement_date": settle_day.isoformat(), "status": "expected",
        })
        extra_gt.append({
            "payment_index": idx, "transaction_id": txn_id, "order_id": order_id,
            "razorpay_payment_id": pay_id, "settlement_id": settlement_id,
            "settlement_posting_id": posting_id, "utr": utr, "merchant_id": merchant_id,
            # Named to match matching/ledger_check.py's emitted exception_type
            # exactly, so evaluate.py's per-type precision/recall needs no
            # rename shim (unlike duplicate_payment -> duplicate_payment_detected).
            "failure_mode": "chargeback_received", "ambiguity_flag": False, "ambiguity_reason": None,
            "payment_bank_relationship": "1:1", "settlement_bank_relationship": "1:1",
            "is_clean_match": False, "expected_auto_resolvable": False, "risk_class": "high",
            "expected_resolution": "escalate",
            "original_payment_id": None, "duplicate_of_event_id": None,
            "note": f"chargeback_{reason}: issuer clawed back "
                    f"Rs.{abs(clawback)/100.0:,.2f} of a settled payment",
        })

    return (pd.DataFrame(extra_gw), pd.DataFrame(extra_bank),
            pd.DataFrame(extra_ledger), pd.DataFrame(extra_gt))
