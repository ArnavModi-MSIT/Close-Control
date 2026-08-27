"""Bank statement generation: one row per settlement posting (batched, not per-payment)."""

import random

import pandas as pd

from ..utils import add_business_days, rand_id, rand_utr


def build_bank_records(payments: pd.DataFrame, net_contributions: dict, split_flags: dict, missing_utr_groups: set):
    """One bank row per settlement posting. Amount = sum of member
    payments' net contributions. Split groups post in two tranches."""
    bank_rows = []
    utr_assignment = {}
    posting_id_assignment = {}
    settlement_posting_count = {}

    eligible = payments[payments["eligible_for_settlement"]].dropna(subset=["settlement_id"])
    for settlement_id, group in eligible.groupby("settlement_id"):
        merchant_name = group.iloc[0]["merchant_name"]
        settle_day = group.iloc[0]["settle_day"]
        utr_missing = settlement_id in missing_utr_groups
        is_split = split_flags.get(settlement_id, False) and len(group) > 1

        def emit_posting(member_idxs, credit_day):
            total = sum(net_contributions[idx] for idx in member_idxs)
            utr = None if utr_missing else rand_utr()
            posting_id = rand_id("post", 10)
            bank_rows.append({
                "bank_txn_id": rand_id("bnk", 12),
                "settlement_posting_id": posting_id,
                "utr": utr,
                "credit_amount_rupees": round(total / 100.0, 2),
                "credit_date": credit_day.isoformat(),
                "value_date": credit_day.isoformat(),
                # narration deliberately does not leak the exact txn count
                "narration": f"NEFT-{merchant_name[:10].upper().replace(' ', '')}-SETL",
                "bank_account_id": f"acct_{group.iloc[0]['merchant_id']}",
                "transaction_type": "credit",
            })
            for idx in member_idxs:
                utr_assignment[idx] = utr
                posting_id_assignment[idx] = posting_id

        if is_split:
            member_indices = list(group["payment_index"])
            random.shuffle(member_indices)
            cut = max(1, len(member_indices) // 2)
            tranche_a, tranche_b = member_indices[:cut], member_indices[cut:]
            n_postings = 0
            if tranche_a:
                emit_posting(tranche_a, settle_day)
                n_postings += 1
            if tranche_b:
                # business-day offset for the second tranche (not a raw calendar-day delta)
                emit_posting(tranche_b, add_business_days(settle_day, 1))
                n_postings += 1
            settlement_posting_count[settlement_id] = n_postings
        else:
            emit_posting(list(group["payment_index"]), settle_day)
            settlement_posting_count[settlement_id] = 1

    return pd.DataFrame(bank_rows), utr_assignment, posting_id_assignment, settlement_posting_count
