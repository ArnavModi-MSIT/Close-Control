"""Gateway record generation: payment/settlement records, including retry attempts."""

import random
import datetime as dt

import pandas as pd

from ..payments import compute_net_contribution
from ..utils import rand_id, unix_ts


def build_gateway_records(payments: pd.DataFrame):
    gateway_rows = []
    net_contributions = {}

    for _, p in payments.iterrows():
        fm = p["failure_mode"]
        fee, tax, adjustment, net, refund_id, refund_reason = compute_net_contribution(p)
        net_contributions[p["payment_index"]] = net

        status = "processed"
        if fm == "deemed_success_ambiguous":
            status = "deemed_success"
        elif fm == "held_for_risk_review":
            status = "on_hold"

        settled_at = None
        if p["eligible_for_settlement"]:
            settled_at = unix_ts(dt.datetime.combine(p["settle_day"], dt.time(16, 30)))

        gateway_rows.append({
            "razorpay_payment_id": p["razorpay_payment_id"],
            "order_id": p["order_id"],
            "transaction_id_ref": p["transaction_id"],
            "settlement_id": p["settlement_id"] if p["eligible_for_settlement"] else None,
            "merchant_id": p["merchant_id"],
            "payment_method": p["payment_method"],
            "payment_amount_paise": p["gross_paise"],
            "fee_paise": fee,
            "tax_paise": tax,
            "adjustment_paise": adjustment,
            "settlement_amount_paise": net,
            "status": status,
            "captured_at": unix_ts(p["captured_at"]),
            "settled_at": settled_at,
            "signature_valid": fm != "signature_verification_failed",
            "attempt_number": 1,          # corrected below for duplicate_retry
            "attempt_status": "success",
            "successful_attempt_id": None,
            "refund_id": refund_id,
            "refund_reason": refund_reason,
            "duplicate_of_payment_id": p["duplicate_of_payment_id"],
            "payment_index_internal": p["payment_index"],  # dropped before writing; join aid only
        })

        # duplicate_retry -> coherent attempt sequence: failed 1..N, success is N+1
        if fm == "duplicate_retry" and not p["is_duplicate_child"]:
            n_retries = random.choice([1, 2])
            gateway_rows[-1]["attempt_number"] = n_retries + 1
            for attempt_no in range(1, n_retries + 1):
                seconds_before = (n_retries + 1 - attempt_no) * random.randint(30, 300)
                retry_time = p["captured_at"] - dt.timedelta(seconds=seconds_before)
                gateway_rows.append({
                    "razorpay_payment_id": rand_id("pay", 14),
                    "order_id": p["order_id"],
                    "transaction_id_ref": p["transaction_id"],
                    "settlement_id": None,
                    "merchant_id": p["merchant_id"],
                    "payment_method": p["payment_method"],
                    "payment_amount_paise": p["gross_paise"],
                    "fee_paise": 0, "tax_paise": 0, "adjustment_paise": 0,
                    "settlement_amount_paise": 0,
                    "status": "failed",
                    "captured_at": unix_ts(retry_time),
                    "settled_at": None,
                    "signature_valid": True,
                    "attempt_number": attempt_no,
                    "attempt_status": "failed",
                    "successful_attempt_id": p["razorpay_payment_id"],
                    "refund_id": None, "refund_reason": None,
                    "duplicate_of_payment_id": None,
                    "payment_index_internal": p["payment_index"],
                })

    return pd.DataFrame(gateway_rows), net_contributions
