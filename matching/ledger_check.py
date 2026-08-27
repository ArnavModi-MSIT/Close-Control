"""Payment-level discrepancy detection: ledger's independent expectation
vs. gateway's actual/observed outcome. This is a direct join via
transaction_id (no fuzzy matching needed) -- the classification itself is
rule-based and evidence-driven: it never reads the dataset's hidden
failure_mode label, only observable fields (fee, tax, adjustment, status,
signature, duplicate count).
"""

import pandas as pd

from . import config


def check_ledger_vs_gateway(gateway: pd.DataFrame, ledger: pd.DataFrame) -> pd.DataFrame:
    successful = gateway[gateway["attempt_status"] == "success"]

    # duplicate detection: >1 successful gateway row for the same transaction_id
    dup_counts = successful.groupby("transaction_id_ref").size()
    duplicate_txn_ids = set(dup_counts[dup_counts > 1].index)

    # Pre-grouped once, instead of re-scanning the full `successful` table
    # for every ledger row below -- the original `successful[successful[...
    # ] == txn_id]` inside the loop was an O(ledger_rows * gateway_rows)
    # full-table scan per row. Unnoticeable at demo scale (2,000 rows,
    # ~4M comparisons) but became the actual bottleneck at a 100,000
    # -payment scale check (~10B comparisons, didn't finish in 5+ minutes) --
    # confirmed via profiling, not blocking.py or settlement_builder.py,
    # both of which stay small since settlement/block count barely grows
    # with payment volume. This dict lookup is O(1) per row instead.
    grouped_by_txn = {txn_id: grp for txn_id, grp in successful.groupby("transaction_id_ref")}
    _empty = successful.iloc[0:0]

    rows = []
    for _, led in ledger.iterrows():
        txn_id = led["transaction_id"]
        matches = grouped_by_txn.get(txn_id, _empty)

        result = {
            "transaction_id": txn_id,
            "order_id": led["order_id"],
            "merchant_id": led["merchant_id"],
            "ledger_expected_net_rupees": led["expected_net_settlement_rupees"],
            "gateway_records_found": len(matches),
            "observed_net_rupees": None,
            "net_delta_rupees": None,
            "exception_type": None,
            "risk_class": "none",
            "auto_resolve_eligible": True,
        }

        if matches.empty:
            result["exception_type"] = "no_gateway_record_found"
            result["risk_class"] = "high"
            result["auto_resolve_eligible"] = False
            rows.append(result)
            continue

        # matches.iloc[0] is "first in generation order", not first by any
        # timestamp/id -- relies on data_generation/payments.py building
        # original payments before appending duplicate children. Only safe
        # because (a) duplicate_payment_detected is escalated below
        # regardless of which row got picked, and (b) duplicate generation
        # doesn't corrupt the amount, so observed_net_rupees is materially
        # the same either way. Do not rely on this ordering for anything
        # that isn't already escalated.
        primary = matches.iloc[0]
        result["observed_net_rupees"] = round(float(primary["settlement_amount_rupees"]), 2)
        net_delta = round(result["observed_net_rupees"] - led["expected_net_settlement_rupees"], 2)
        result["net_delta_rupees"] = net_delta

        # duplicate payment -- extra money the ledger never expected
        if txn_id in duplicate_txn_ids:
            result["exception_type"] = "duplicate_payment_detected"
            result["risk_class"] = "high"
            result["auto_resolve_eligible"] = False
            rows.append(result)
            continue

        # signature invalid -- never trust the amount reconciliation, always escalate
        if not primary["signature_valid"]:
            result["exception_type"] = "signature_verification_failed"
            result["risk_class"] = "high"
            result["auto_resolve_eligible"] = False
            rows.append(result)
            continue

        # held for risk review -- no settlement, nothing to reconcile yet
        if primary["status"] == "on_hold":
            result["exception_type"] = "held_for_risk_review"
            result["risk_class"] = "high"
            result["auto_resolve_eligible"] = False
            rows.append(result)
            continue

        # deemed success -- gateway itself isn't confident yet
        if primary["status"] == "deemed_success":
            result["exception_type"] = "deemed_success_ambiguous"
            result["risk_class"] = "medium"
            result["auto_resolve_eligible"] = False
            rows.append(result)
            continue

        # amount reconciles cleanly
        if abs(net_delta) <= config.EXACT_MATCH_TOLERANCE_RUPEES:
            rows.append(result)  # exception_type stays None -- clean
            continue

        # explicit refund/adjustment on record -- explains a lower net amount.
        # Invariant this dataset relies on: a negative adjustment ALWAYS means
        # a refund, never a chargeback/reversal/other negative-adjustment
        # scenario -- true by construction of this generator (see
        # data_generation/hard_negatives.py, sources/gateway.py), but if the
        # data model ever grows other negative-adjustment causes, this
        # classification would become too broad and needs a real distinguishing
        # signal, not just the sign (flagged by an external review).
        if primary["adjustment_rupees"] < -config.EXACT_MATCH_TOLERANCE_RUPEES:
            result["exception_type"] = "partial_refund"
            result["risk_class"] = "medium"
            result["auto_resolve_eligible"] = False  # refunds need evidence review, not blind auto-resolve
            rows.append(result)
            continue

        # fee delta roughly explains the net delta -> fee variance, not a mystery.
        # Semantic relationship being tested (not just a sign coincidence):
        #   observed_net = expected_net - (actual_fee+tax - expected_fee+tax)
        #   => net_delta  = -(fee_delta)
        #   => fee_delta + net_delta ~= 0 whenever fee/tax variance is the
        #      TRUE explanation for the net delta, not something else that
        #      happens to be close in magnitude (flagged by an external
        #      review as worth stating explicitly rather than leaving the
        #      reader to infer it from the sign check alone).
        fee_delta = round(float(primary["fee_rupees"]) + float(primary["tax_rupees"])
                           - (led["expected_fee_rupees"] + led["expected_tax_rupees"]), 2)
        if abs(fee_delta + net_delta) <= 0.5:  # fee/tax difference accounts for the net delta
            result["exception_type"] = "fee_variance"
            result["risk_class"] = "low"
            result["auto_resolve_eligible"] = True
            rows.append(result)
            continue

        # net delta unexplained by fee, tax, or recorded adjustment
        result["exception_type"] = "unexplained_shortage"
        result["risk_class"] = "high"
        result["auto_resolve_eligible"] = False
        rows.append(result)

    return pd.DataFrame(rows)
