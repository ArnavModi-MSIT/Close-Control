"""Ground truth construction -- evaluation only, never fed to the matcher/agent."""

import pandas as pd

from . import config


def build_ground_truth(payments: pd.DataFrame, utr_assignment: dict, posting_id_assignment: dict,
                        collisions: dict, settlement_posting_count: dict):
    rows = []
    group_sizes = payments.dropna(subset=["settlement_id"]).groupby("settlement_id").size().to_dict()
    idx_to_dup_payment_id = {}  # original_payment_index -> duplicate's razorpay_payment_id
    for _, p in payments[payments["is_duplicate_child"]].iterrows():
        idx_to_dup_payment_id[p["original_payment_index"]] = p["razorpay_payment_id"]

    for _, p in payments.iterrows():
        fm = p["failure_mode"]
        settlement_id = p["settlement_id"] if pd.notna(p["settlement_id"]) else None
        utr = utr_assignment.get(p["payment_index"])
        posting_id = posting_id_assignment.get(p["payment_index"])
        group_size = group_sizes.get(settlement_id, 0) if settlement_id else 0
        postings = settlement_posting_count.get(settlement_id, 1) if settlement_id else 0

        if not p["eligible_for_settlement"]:
            payment_bank_relationship = "unmatched"
            settlement_bank_relationship = "unmatched"
        else:
            # from THIS payment's own side, it always maps to exactly one posting
            payment_bank_relationship = "N:1" if group_size > 1 else "1:1"
            settlement_bank_relationship = "1:N" if postings > 1 else ("N:1" if group_size > 1 else "1:1")

        is_collision = collisions.get(p["payment_index"], False)
        expected_resolution = "auto_resolve" if (fm in config.AUTO_RESOLVABLE_MODES and not is_collision) else "escalate"
        if fm == "clean" and not is_collision:
            expected_resolution = "auto_resolve"

        duplicate_of_event_id = None
        original_payment_id = None
        if fm == "duplicate_payment":
            if p["is_duplicate_child"]:
                duplicate_of_event_id = p["razorpay_payment_id"]  # this row IS the duplicate
                original_payment_id = p["duplicate_of_payment_id"]
            else:
                duplicate_of_event_id = idx_to_dup_payment_id.get(p["payment_index"])
                original_payment_id = p["razorpay_payment_id"]

        rows.append({
            "payment_index": p["payment_index"],
            "transaction_id": p["transaction_id"],
            "order_id": p["order_id"],
            "razorpay_payment_id": p["razorpay_payment_id"],
            "settlement_id": settlement_id,
            "settlement_posting_id": posting_id,
            "utr": utr,
            "merchant_id": p["merchant_id"],
            "failure_mode": fm,                      # never overwritten by ambiguity
            "ambiguity_flag": is_collision,
            "ambiguity_reason": "amount_collision" if is_collision else None,
            "payment_bank_relationship": payment_bank_relationship,
            "settlement_bank_relationship": settlement_bank_relationship,
            "is_clean_match": fm == "clean" and not is_collision,
            "expected_auto_resolvable": fm in config.AUTO_RESOLVABLE_MODES and not is_collision,
            "risk_class": config.RISK_CLASS.get(fm, "medium"),
            "expected_resolution": expected_resolution,
            "original_payment_id": original_payment_id,
            "duplicate_of_event_id": duplicate_of_event_id,
        })
    return pd.DataFrame(rows)
