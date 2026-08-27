"""Combine settlement-match results and ledger-check results into a single
per-payment report, with a priority order for which exception "wins" when
more than one signal fires."""

import datetime as dt

import pandas as pd

# highest-priority exception wins when multiple signals are present
EXCEPTION_PRIORITY = [
    "duplicate_payment_detected",
    "signature_verification_failed",
    "held_for_risk_review",
    "deemed_success_ambiguous",
    "settlement_bank_posting_not_found",
    "missing_bank_reference",
    "bank_overage",
    "partial_refund",
    "unexplained_shortage",
    "ambiguous_bank_match",
    "fee_variance",
    "timing_lag_beyond_t2",
]

RISK_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


def build_report(ledger_check: pd.DataFrame, settlement_matches: pd.DataFrame, gateway: pd.DataFrame,
                  ledger: pd.DataFrame) -> pd.DataFrame:
    successful = gateway[gateway["attempt_status"] == "success"]
    # keep="first" is "first in generation order" (same caveat as
    # ledger_check.py's primary row selection) -- for a duplicate_payment
    # case this only decides which duplicate's settlement_id/match_status
    # get displayed, never the exception outcome itself:
    # duplicate_payment_detected is EXCEPTION_PRIORITY's highest-priority
    # entry (below), so the case is escalated regardless of which row
    # "primary" points to.
    primary_by_txn = successful.drop_duplicates("transaction_id_ref", keep="first").set_index("transaction_id_ref")
    settlement_by_id = settlement_matches.set_index("settlement_id")
    ledger_by_txn = ledger.set_index("transaction_id")

    rows = []
    for _, lc in ledger_check.iterrows():
        txn_id = lc["transaction_id"]
        signals = []
        risk = "none"
        auto_resolve = True

        if pd.notna(lc["exception_type"]):
            signals.append(lc["exception_type"])
            risk = lc["risk_class"]
            auto_resolve = lc["auto_resolve_eligible"]

        settlement_id = None
        if txn_id in primary_by_txn.index:
            settlement_id = primary_by_txn.loc[txn_id, "settlement_id"]

        settlement_result = None
        if pd.notna(settlement_id) and settlement_id in settlement_by_id.index:
            settlement_result = settlement_by_id.loc[settlement_id]

            if settlement_result["match_status"] == "unmatched":
                signals.append("settlement_bank_posting_not_found")
            elif settlement_result["match_status"] == "ambiguous":
                signals.append("ambiguous_bank_match")
            elif settlement_result["missing_bank_reference"]:
                signals.append("missing_bank_reference")
            elif settlement_result.get("bank_overage"):
                signals.append("bank_overage")
            elif settlement_result.get("had_ambiguous_candidates"):
                signals.append("ambiguous_bank_match")

            # timing lag: did the settlement land later than the ledger's own T+2 expectation?
            if txn_id in ledger_by_txn.index:
                expected_date = ledger_by_txn.loc[txn_id, "expected_settlement_date"]
                if isinstance(expected_date, str):
                    expected_date = dt.date.fromisoformat(expected_date)
                gw_row = primary_by_txn.loc[txn_id]
                if pd.notna(gw_row["settled_at"]):
                    settled_val = gw_row["settled_at"]
                    actual_settle_date = pd.Timestamp(settled_val).date()
                    if actual_settle_date > expected_date:
                        signals.append("timing_lag_beyond_t2")

        # pick the highest-priority signal present
        final_exception = None
        for candidate in EXCEPTION_PRIORITY:
            if candidate in signals:
                final_exception = candidate
                break

        # risk class: take the worse of the ledger-check risk and a default
        # for settlement-only signals not already covered by ledger_check
        settlement_only_risk = {
            "settlement_bank_posting_not_found": "high",
            "missing_bank_reference": "medium",
            "bank_overage": "high",
            "ambiguous_bank_match": "medium",
            "timing_lag_beyond_t2": "low",
        }
        if final_exception in settlement_only_risk and RISK_RANK[settlement_only_risk[final_exception]] > RISK_RANK[risk]:
            risk = settlement_only_risk[final_exception]
        if final_exception in ("settlement_bank_posting_not_found", "missing_bank_reference",
                                 "bank_overage", "ambiguous_bank_match"):
            auto_resolve = False
        elif final_exception == "timing_lag_beyond_t2" and lc["exception_type"] is None:
            auto_resolve = True

        rows.append({
            "transaction_id": txn_id,
            "order_id": lc["order_id"],
            "merchant_id": lc["merchant_id"],
            "settlement_id": settlement_id,
            "match_status": settlement_result["match_status"] if settlement_result is not None else (
                "no_settlement" if pd.isna(settlement_id) else "unknown"),
            "match_pass": settlement_result["match_pass"] if settlement_result is not None else None,
            "final_exception_type": final_exception,
            "all_signals": signals,
            "risk_class": risk,
            "auto_resolve_eligible": bool(auto_resolve) if final_exception else True,
            "is_clean": final_exception is None,
            "ledger_expected_net_rupees": lc["ledger_expected_net_rupees"],
            "observed_net_rupees": lc["observed_net_rupees"],
            "net_delta_rupees": lc["net_delta_rupees"],
        })

    return pd.DataFrame(rows)
