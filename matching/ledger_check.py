"""Payment-level discrepancy detection: ledger's independent expectation
vs. gateway's actual/observed outcome. This is a direct join via
transaction_id (no fuzzy matching needed) -- the classification itself is
rule-based and evidence-driven: it never reads the dataset's hidden
failure_mode label, only observable fields (fee, tax, adjustment, status,
signature, duplicate count).
"""

import pandas as pd

from . import config


def check_ledger_vs_gateway(gateway: pd.DataFrame, ledger: pd.DataFrame,
                             loan_book: pd.DataFrame = None) -> pd.DataFrame:
    """loan_book is the OPTIONAL fourth source (Razorpay Capital's recovery
    ledger, see matching/loaders.py's load_loan_book). Defaults to None so
    every existing caller and every dataset generated before that source
    existed keeps working unchanged -- no loan book simply means no merchant
    has an advance, which is a valid state rather than an error."""
    successful = gateway[gateway["attempt_status"] == "success"]

    # Indexed once by transaction_id, same O(1)-lookup reasoning as
    # grouped_by_txn below. A recovery is 1:1 with the settlement it was
    # deducted from, so a plain dict is the right shape here.
    recoveries_by_txn = {}
    if loan_book is not None and len(loan_book):
        recoveries_by_txn = {r["transaction_id"]: r for _, r in loan_book.iterrows()}

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
            # Populated only when a Razorpay Capital recovery actually
            # explains this transaction's delta (see the loan-recovery
            # branch below) -- kept on every row so the column exists
            # uniformly for evaluate.py / diagnostics consumers.
            "loan_id": None,
            "loan_recovery_amount_rupees": None,
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

        # chargeback -- checked BEFORE both the clean-amount test and the
        # refund test below, deliberately, for two reasons:
        #   1. A disputed transaction is an exception even when the money
        #      hasn't moved yet (dispute raised, debit not yet posted ->
        #      net_delta still ~0). Falling through to "clean" would hide a
        #      live dispute entirely.
        #   2. A chargeback and a refund are BOTH negative adjustments, so
        #      sign alone cannot tell them apart -- chargeback_id is the real
        #      distinguishing signal, which is exactly what the refund
        #      comment below has always said this would need.
        # Detected via .get() so this works on a dataset generated before the
        # chargeback fields existed (missing column -> no chargeback), rather
        # than requiring a regeneration to stay loadable.
        chargeback_id = primary.get("chargeback_id")
        if chargeback_id is not None and not pd.isna(chargeback_id):
            result["exception_type"] = "chargeback_received"
            result["risk_class"] = "high"
            result["auto_resolve_eligible"] = False  # funds clawed back -- never auto-resolve
            rows.append(result)
            continue

        # amount reconciles cleanly
        if abs(net_delta) <= config.EXACT_MATCH_TOLERANCE_RUPEES:
            rows.append(result)  # exception_type stays None -- clean
            continue

        # Razorpay Capital loan recovery -- checked BEFORE the refund branch
        # below, deliberately. A recovery is booked as a negative adjustment
        # exactly like a refund is, so sign alone cannot separate them; the
        # presence of a matching record in Capital's recovery ledger is the
        # real distinguishing signal, the same role chargeback_id plays for
        # disputes above.
        #
        # The recovery is only ACCEPTED as the explanation when it actually
        # reconciles the observed delta. observed_net = expected_net -
        # recovery, so net_delta = -recovery, so net_delta + recovery ~= 0
        # whenever the recovery is the true and COMPLETE explanation. A
        # merchant with a real ₹500 recovery and a ₹3,000 shortage falls
        # through to unexplained_shortage below, which is the correct
        # outcome -- the residual genuinely is unexplained, and a partially
        # -explaining record must never launder the rest of the gap into an
        # auto-resolve (see test_loan_recovery.py Scenario 3).
        recovery = recoveries_by_txn.get(txn_id)
        if recovery is not None:
            recovery_amount = round(float(recovery["recovery_amount_rupees"]), 2)
            if abs(net_delta + recovery_amount) <= config.EXACT_MATCH_TOLERANCE_RUPEES:
                result["exception_type"] = "loan_recovery_deduction"
                result["risk_class"] = "low"
                # Contracted, scheduled, and fully reconciled -- the money is
                # not missing, it was collected under an agreement the
                # merchant signed. Same class of explained variance as
                # fee_variance, and auto-resolved at the MATCHER level, so
                # this never reaches the LLM at all.
                result["auto_resolve_eligible"] = True
                result["loan_id"] = recovery["loan_id"]
                result["loan_recovery_amount_rupees"] = recovery_amount
                rows.append(result)
                continue

        # explicit refund/adjustment on record -- explains a lower net amount.
        # A negative adjustment here is a refund specifically: the chargeback
        # case (the other real negative-adjustment cause) was already
        # separated out above by its own chargeback_id signal, so this is no
        # longer the sign-alone heuristic an external review flagged.
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
        # fee/tax difference accounts for the net delta -- see config.py's
        # FEE_VARIANCE_RECONCILIATION_TOLERANCE_RUPEES for why this equals
        # EXACT_MATCH_TOLERANCE_RUPEES rather than a separate, looser value
        # (was a bare 0.5 literal, found via external review).
        if abs(fee_delta + net_delta) <= config.FEE_VARIANCE_RECONCILIATION_TOLERANCE_RUPEES:
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
