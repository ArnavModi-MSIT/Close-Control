"""Canonical payment generation, including duplicate-payment children."""

import random
import datetime as dt

import numpy as np
import pandas as pd

from . import config
from .utils import add_business_days, rand_id, gross_amount, pick_method, random_datetime, compute_fee_tax


def build_payments(n: int) -> pd.DataFrame:
    rows = []
    exception_flags = np.random.random(n) < config.EXCEPTION_RATE
    modes, mode_weights = zip(*config.FAILURE_MODES.items())

    for i in range(n):
        merchant_id, merchant_name = random.choice(config.MERCHANTS)
        day_offset = random.randint(0, config.BATCH_DAYS - 1)
        captured_day = config.BATCH_START + dt.timedelta(days=day_offset)
        captured_at = random_datetime(captured_day)

        failure_mode = random.choices(modes, weights=mode_weights, k=1)[0] if exception_flags[i] else "clean"
        method = pick_method()
        gross = gross_amount()

        instant = (random.random() < 0.05) and failure_mode != "held_for_risk_review"
        beyond_t2 = failure_mode == "timing_lag_beyond_t2"
        lag_days = 0 if instant else (random.choice([3, 4, 5]) if beyond_t2 else 2)
        settle_day = captured_day if instant else add_business_days(captured_day, lag_days)

        rows.append({
            "payment_index": i,
            "merchant_id": merchant_id,
            "merchant_name": merchant_name,
            "transaction_id": f"trn-{i:06d}",
            "order_id": rand_id("order", 14),
            "razorpay_payment_id": rand_id("pay", 14),
            "payment_method": method,
            "gross_paise": gross,
            "captured_at": captured_at,
            "settle_day": settle_day,
            "failure_mode": failure_mode,
            "eligible_for_settlement": failure_mode != "held_for_risk_review",
            "is_duplicate_child": False,
            "duplicate_of_payment_id": None,
            "original_payment_index": None,
        })

    payments = pd.DataFrame(rows)

    # append duplicate-payment children: same order_id/transaction_id/merchant,
    # own payment_index, own gross/fee (computed normally, not corrupted),
    # flows through settlement/bank like any other payment, excluded from ledger.
    next_idx = n
    dup_rows = []
    for _, orig in payments[payments["failure_mode"] == "duplicate_payment"].iterrows():
        dup_time = orig["captured_at"] + dt.timedelta(minutes=random.randint(2, 20))
        dup_day = dup_time.date()
        dup_settle_day = add_business_days(dup_day, 2)
        dup_rows.append({
            "payment_index": next_idx,
            "merchant_id": orig["merchant_id"],
            "merchant_name": orig["merchant_name"],
            "transaction_id": orig["transaction_id"],   # SAME transaction_id -- this is the point
            "order_id": orig["order_id"],                # SAME order_id -- true duplicate charge
            "razorpay_payment_id": rand_id("pay", 14),
            "payment_method": orig["payment_method"],
            "gross_paise": orig["gross_paise"],
            "captured_at": dup_time,
            "settle_day": dup_settle_day,
            "failure_mode": "duplicate_payment",
            "eligible_for_settlement": True,
            "is_duplicate_child": True,
            "duplicate_of_payment_id": orig["razorpay_payment_id"],
            "original_payment_index": orig["payment_index"],
        })
        next_idx += 1

    if dup_rows:
        payments = pd.concat([payments, pd.DataFrame(dup_rows)], ignore_index=True)

    return payments


def compute_net_contribution(row):
    """Actual (observed) gateway net -- may be corrupted per failure mode.
    This is deliberately NOT what the ledger expects (see sources/ledger.py)."""
    fm = row["failure_mode"]
    fee, tax = compute_fee_tax(row["gross_paise"], row["payment_method"], wrong_fee=(fm == "fee_variance"))
    adjustment = 0
    refund_id, refund_reason = None, None
    if fm == "partial_refund":
        adjustment = -round(row["gross_paise"] * random.uniform(0.1, 0.4))
        refund_id = rand_id("rfnd", 10)
        refund_reason = random.choice(["customer_request", "order_cancelled", "product_defect"])

    net = row["gross_paise"] - fee - tax + adjustment

    if fm == "unexplained_shortage":
        shortage = round(row["gross_paise"] * random.uniform(0.02, 0.08))
        net -= shortage

    return fee, tax, adjustment, net, refund_id, refund_reason
