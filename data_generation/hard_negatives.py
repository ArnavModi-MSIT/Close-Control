"""Hard-negative distractor pairs and amount-collision ambiguity labeling."""

import random
import datetime as dt

import pandas as pd

from . import config
from .utils import add_business_days, rand_id, rand_utr, gross_amount, pick_method, random_datetime, unix_ts, compute_fee_tax


def add_hard_negatives(payments: pd.DataFrame, n_pairs: int = 20):
    """Inject genuinely distinct payments that are deliberately confusable:
    same merchant, same amount, timestamps minutes apart. Both are real,
    separate, correctly-matching transactions -- a matcher that merges
    them is wrong."""
    extra_gw, extra_bank, extra_ledger, extra_gt = [], [], [], []
    base_idx = int(payments["payment_index"].max()) + 1

    for i in range(n_pairs):
        merchant_id, merchant_name = random.choice(config.MERCHANTS)
        amount = gross_amount()
        method = pick_method()
        day = config.BATCH_START + dt.timedelta(days=random.randint(0, config.BATCH_DAYS - 5))
        t1 = random_datetime(day)
        t2 = t1 + dt.timedelta(minutes=random.randint(2, 15))

        for j, captured_at in enumerate([t1, t2]):
            idx = base_idx + i * 2 + j
            fee, tax = compute_fee_tax(amount, method)
            net = amount - fee - tax
            settle_day = add_business_days(captured_at.date(), 2)
            settlement_id = rand_id("setl", 12)
            posting_id = rand_id("post", 10)
            utr = rand_utr()
            txn_id = f"trn-hn{i:03d}-{j}"
            order_id = rand_id("order", 14)
            pay_id = rand_id("pay", 14)

            extra_gw.append({
                "razorpay_payment_id": pay_id, "order_id": order_id, "transaction_id_ref": txn_id,
                "settlement_id": settlement_id, "merchant_id": merchant_id, "payment_method": method,
                "payment_amount_paise": amount, "fee_paise": fee, "tax_paise": tax, "adjustment_paise": 0,
                "settlement_amount_paise": net, "status": "processed",
                "captured_at": unix_ts(captured_at), "settled_at": unix_ts(dt.datetime.combine(settle_day, dt.time(16, 30))),
                "signature_valid": True, "attempt_number": 1, "attempt_status": "success",
                "successful_attempt_id": None, "refund_id": None, "refund_reason": None,
                "duplicate_of_payment_id": None, "payment_index_internal": idx,
            })
            extra_bank.append({
                "bank_txn_id": rand_id("bnk", 12), "settlement_posting_id": posting_id, "utr": utr,
                "credit_amount_rupees": round(net / 100.0, 2),
                "credit_date": settle_day.isoformat(), "value_date": settle_day.isoformat(),
                "narration": f"NEFT-{merchant_name[:10].upper().replace(' ', '')}-SETL",
                "bank_account_id": f"acct_{merchant_id}", "transaction_type": "credit",
            })
            extra_ledger.append({
                "ledger_id": rand_id("led", 10), "transaction_id": txn_id, "order_id": order_id,
                "merchant_id": merchant_id, "gross_amount_rupees": round(amount / 100.0, 2),
                "expected_fee_rupees": round(fee / 100.0, 2), "expected_tax_rupees": round(tax / 100.0, 2),
                "expected_adjustment_rupees": 0.0, "expected_net_settlement_rupees": round(net / 100.0, 2),
                "expected_settlement_date": settle_day.isoformat(), "status": "expected",
            })
            extra_gt.append({
                "payment_index": idx, "transaction_id": txn_id, "order_id": order_id,
                "razorpay_payment_id": pay_id, "settlement_id": settlement_id,
                "settlement_posting_id": posting_id, "utr": utr, "merchant_id": merchant_id,
                "failure_mode": "hard_negative", "ambiguity_flag": False, "ambiguity_reason": None,
                "payment_bank_relationship": "1:1", "settlement_bank_relationship": "1:1",
                "is_clean_match": True, "expected_auto_resolvable": True, "risk_class": "none",
                "expected_resolution": "auto_resolve",
                "original_payment_id": None, "duplicate_of_event_id": None,
                "note": f"hard_negative_pair_{i}: distinct from its pair partner despite similarity",
            })

    return (pd.DataFrame(extra_gw), pd.DataFrame(extra_bank),
            pd.DataFrame(extra_ledger), pd.DataFrame(extra_gt))


def label_amount_collisions(payments: pd.DataFrame) -> dict:
    """Within each settlement group, flag payments whose gross amount is
    within ~₹1 of another member -- a naive amount-only matcher could
    confuse them. Kept SEPARATE from failure_mode (never overwrites it)."""
    collisions = {}
    eligible = payments.dropna(subset=["settlement_id"])
    for settlement_id, group in eligible.groupby("settlement_id"):
        amounts = group[["payment_index", "gross_paise"]].sort_values("gross_paise")
        vals = amounts["gross_paise"].values
        idxs = amounts["payment_index"].values
        for i in range(len(vals) - 1):
            if abs(int(vals[i]) - int(vals[i + 1])) <= 100:
                collisions[idxs[i]] = True
                collisions[idxs[i + 1]] = True
    return collisions
