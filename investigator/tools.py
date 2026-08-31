"""Tool implementations the investigation agent can call.

Every tool here is deterministic Python over data already produced by
Layer 1/2 (data_generation/, matching/) -- no tool invents a number or
calls out to anything beyond this repo's own files. This is the same
"AI proposes, deterministic code disposes" boundary as the rest of the
project, just applied one level deeper: the LLM decides WHICH tool to
call and WHEN, but every tool's actual computation is plain, auditable
Python, not the model doing arithmetic in its head.
"""

import datetime as dt

import pandas as pd

# Imported directly rather than re-declared here so the investigator can
# never disagree with the matcher about what "the recovery reconciles the
# delta" means -- same single-source-of-truth discipline as ingestion/
# importing AMOUNT_BLOCK_TOLERANCE_PCT instead of hand-copying 1.5.
from matching.config import EXACT_MATCH_TOLERANCE_RUPEES

from . import config


class ToolContext:
    """Loaded once per investigation run, shared across all tool calls
    for that run -- not reloaded per call. Built from the exact same
    matcher report + sources every other script in this project uses."""

    def __init__(self, report: pd.DataFrame, gateway: pd.DataFrame, bank: pd.DataFrame,
                 settlement_matches: pd.DataFrame | None = None,
                 loan_book: pd.DataFrame | None = None):
        self.report = report
        self.bank = bank
        # The raw, non-deduplicated gateway frame, kept alongside
        # gateway_primary below -- cash_position/engine.py's
        # build_cash_position() needs the full frame (it does its own
        # dedup internally via _primary_gateway_dates()), which
        # gateway_primary's already-deduped, already-reindexed shape can't
        # substitute for. Added for qa_agent/'s get_cash_position_summary()
        # tool, which reuses this same ToolContext rather than duplicating
        # its derivation logic.
        self.gateway = gateway
        # The fourth source (Razorpay Capital's recovery ledger), consulted
        # by get_loan_recovery_schedule(). Optional for the same reason
        # settlement_matches is: ToolContext stays constructible in isolation
        # for tests, and a dataset generated before this source existed still
        # works -- the tool reports loan_book_available: False rather than
        # failing, which is a true and useful answer, not a silent skip.
        self.loan_book = loan_book
        successful = gateway[gateway["attempt_status"] == "success"]
        primary = successful.drop_duplicates("transaction_id_ref", keep="first").set_index("transaction_id_ref")
        self.captured_date_by_txn = primary["captured_at"].dt.date.to_dict()
        self.merchant_by_txn = primary["merchant_id"].to_dict()
        # Full deduplicated gateway row per transaction -- get_transaction_details()
        # uses this for fields the report doesn't carry (payment_method, gross
        # amount, expected fee/tax, refund_id/refund_reason, signature_valid).
        self.gateway_primary = primary

        # settlement_matches (from run_matcher.run()) -- get_settlement_details()
        # uses this for the settlement-level match facts (which bank posting(s),
        # confidence, amount delta) that the per-transaction report row doesn't
        # carry on its own. Indexed once here, not per call.
        self.settlement_matches_by_id = (
            settlement_matches.set_index("settlement_id") if settlement_matches is not None else None
        )

        # Which bank_txn_ids are ALREADY consumed by some settlement's match --
        # search_bank_statement() uses this so it can never present an
        # already-claimed posting as if it were unclaimed evidence for the
        # CURRENT case (found via external review: the tool's own docstring
        # claimed "unclaimed" but nothing enforced it -- it searched the
        # entire bank dataframe by date+amount alone). settlement_matches is
        # optional only so ToolContext stays constructible in isolation
        # (e.g. tests); every real caller (run_investigator.py, run_demo.py's
        # --live-case) passes it.
        self.claimed_bank_txn_ids: set = set()
        if settlement_matches is not None:
            for ids in settlement_matches["matched_bank_txn_ids"]:
                self.claimed_bank_txn_ids.update(ids)


def get_transaction_details(ctx: ToolContext, transaction_id: str) -> dict:
    """Authoritative fields for the case itself, beyond the compact initial
    evidence block -- payment method, gross amount, expected fee/tax, and
    (when the exception is refund/signature related) the refund_id/
    refund_reason or signature_valid flag straight from the gateway record,
    not summarized or interpreted. Added following an external review that
    noted the investigator had no way to pull the case's own authoritative
    detail beyond what it was handed at the start -- it could look up
    OTHER transactions (lookup_related_transactions) and bank candidates
    (search_bank_statement), but never re-check its own case's raw record."""
    if transaction_id not in ctx.gateway_primary.index:
        return {"error": f"no gateway record for {transaction_id}"}
    gw = ctx.gateway_primary.loc[transaction_id]
    report_rows = ctx.report[ctx.report["transaction_id"] == transaction_id]
    report_row = report_rows.iloc[0] if len(report_rows) else None

    return {
        "transaction_id": transaction_id,
        "merchant_id": gw["merchant_id"],
        "order_id": gw["order_id"],
        "payment_method": gw["payment_method"],
        "captured_at": gw["captured_at"].isoformat() if pd.notna(gw["captured_at"]) else None,
        "gross_amount_rupees": round(gw["payment_amount_rupees"], 2),
        "expected_fee_rupees": round(gw["fee_rupees"], 2),
        "expected_tax_rupees": round(gw["tax_rupees"], 2),
        "settlement_id": gw["settlement_id"] if pd.notna(gw["settlement_id"]) else None,
        "gateway_status": gw["status"],
        "signature_valid": bool(gw["signature_valid"]),
        "refund_id": gw["refund_id"] if pd.notna(gw["refund_id"]) else None,
        "refund_reason": gw["refund_reason"] if pd.notna(gw["refund_reason"]) else None,
        # Guarded the same way as the three optional fields above, though not
        # currently reachable via any real caller: ctx.report is always the
        # in-process DataFrame from run_matcher.run() (never CSV-reloaded),
        # so a clean transaction's final_exception_type is genuinely Python
        # None here, not the float NaN it becomes after a CSV round-trip
        # (verified directly: json.dumps() on this dict already succeeds
        # today). Added anyway, defense-in-depth, matching json_safe()'s own
        # stated purpose (protect against ANY tool leaking a stray NaN, not
        # just the one field that already caused a real incident) -- found
        # via external review.
        "matcher_exception_type": (
            report_row["final_exception_type"] if report_row is not None and pd.notna(report_row["final_exception_type"])
            else None
        ),
        "matcher_risk_class": (
            report_row["risk_class"] if report_row is not None and pd.notna(report_row["risk_class"])
            else None
        ),
    }


def get_settlement_details(ctx: ToolContext, settlement_id: str) -> dict:
    """Settlement-level match facts -- which bank posting(s) it matched to
    (if any), the matcher's own confidence, every member transaction, and
    the amount delta. Added following an external review that noted N:1/1:N
    cases are hard to genuinely investigate from a single transaction's own
    evidence block alone -- the settlement IS the unit the bank actually
    pays out, so settlement-level context often matters more than any one
    member payment's own fields."""
    members = ctx.report[ctx.report["settlement_id"] == settlement_id]
    if len(members) == 0 and (ctx.settlement_matches_by_id is None
                               or settlement_id not in ctx.settlement_matches_by_id.index):
        return {"error": f"no settlement found for settlement_id={settlement_id!r}"}

    result = {
        "settlement_id": settlement_id,
        "member_transaction_ids": members["transaction_id"].tolist(),
        "member_count": len(members),
        "member_exception_types": members["final_exception_type"].value_counts(dropna=True).to_dict(),
        "members_clean_count": int(members["is_clean"].sum()),
    }
    if ctx.settlement_matches_by_id is not None and settlement_id in ctx.settlement_matches_by_id.index:
        sm = ctx.settlement_matches_by_id.loc[settlement_id]
        result.update({
            "match_status": sm["match_status"],
            "match_pass": sm["match_pass"],
            "expected_total_rupees": sm["expected_total_rupees"],
            "matched_bank_txn_ids": sm["matched_bank_txn_ids"],
            # A bank posting with no UTR at all is real, correct data --
            # it's exactly what makes a missing_bank_reference case what it
            # is -- but pandas represents that missing value as a raw float
            # NaN, not None, and NaN is not valid JSON (found live: a real
            # 500 on GET /api/cases/trn-000070, whose matched settlement's
            # one posting genuinely has no UTR; Starlette's JSONResponse
            # sets allow_nan=False and correctly refuses to emit non-
            # compliant JSON once this list reached serialization, several
            # layers downstream of where it was first produced -- by then
            # already written into investigation_log.jsonl AND persisted
            # into Postgres). Sanitized at the exact point this project
            # already sanitizes every OTHER optional field in this same
            # function (pd.notna(...) else None) -- same principle, just
            # applied per-element since this one field is a list, not a
            # scalar.
            "matched_utrs": [None if pd.isna(u) else u for u in sm["matched_utrs"]],
            "matched_total_rupees": sm["matched_total_rupees"],
            "amount_delta_rupees": sm["amount_delta_rupees"],
            "match_confidence": sm["confidence"],
        })
    else:
        result["match_status"] = "no_bank_side_match_record"
    return result


def calculate_settlement_variance(ctx: ToolContext, transaction_id: str) -> dict:
    """A single, meaningful financial breakdown for this case instead of
    generic subtraction: expected vs. observed net, AND the fee/tax
    components that could explain the gap, all in one deterministic call.
    Added following an external review noting that requiring compute_delta
    for every numeric step is safe but produces less meaningful evidence
    than a domain-shaped calculation a real reconciliation analyst would
    actually reach for. Still just arithmetic over already-known fields --
    invents nothing, same boundary as compute_delta."""
    if transaction_id not in ctx.gateway_primary.index:
        return {"error": f"no gateway record for {transaction_id}"}
    gw = ctx.gateway_primary.loc[transaction_id]
    report_rows = ctx.report[ctx.report["transaction_id"] == transaction_id]
    if len(report_rows) == 0:
        return {"error": f"no matcher report row for {transaction_id}"}
    r = report_rows.iloc[0]

    expected_net = r["ledger_expected_net_rupees"]
    observed_net = r["observed_net_rupees"]
    net_delta = r["net_delta_rupees"]
    gross = round(gw["payment_amount_rupees"], 2)
    expected_fee = round(gw["fee_rupees"], 2)
    expected_tax = round(gw["tax_rupees"], 2)
    adjustment = gw["adjustment_rupees"]

    return {
        "transaction_id": transaction_id,
        "gross_amount_rupees": gross,
        "expected_fee_rupees": expected_fee,
        "expected_tax_rupees": expected_tax,
        "ledger_expected_net_rupees": expected_net,
        "observed_net_rupees": observed_net,
        "net_delta_rupees": net_delta,
        "gross_minus_fee_minus_tax_rupees": round(gross - expected_fee - expected_tax, 2),
        "refund_amount_rupees": round(-adjustment, 2) if pd.notna(adjustment) and adjustment < 0 else 0.0,
    }


def lookup_related_transactions(ctx: ToolContext, transaction_id: str,
                                 days: int = config.RELATED_TRANSACTION_WINDOW_DAYS) -> dict:
    """Other transactions from the SAME merchant captured within `days` of
    this one. A human analyst's first instinct on a flagged case is
    usually "did this happen to other payments too, or just this one" --
    this is that check, done directly against real data instead of asked
    about in the abstract."""
    if transaction_id not in ctx.captured_date_by_txn:
        return {"error": f"no gateway record for {transaction_id}"}

    merchant_id = ctx.merchant_by_txn[transaction_id]
    ref_date = ctx.captured_date_by_txn[transaction_id]
    window = dt.timedelta(days=days)

    related_ids = [
        txn for txn, d in ctx.captured_date_by_txn.items()
        if ctx.merchant_by_txn.get(txn) == merchant_id
        and abs((d - ref_date).days) <= window.days
        and txn != transaction_id
    ]
    related_report = ctx.report[ctx.report["transaction_id"].isin(related_ids)]

    return {
        "merchant_id": merchant_id,
        "window_days": days,
        "related_transaction_count": len(related_report),
        "exception_type_breakdown": related_report["final_exception_type"].value_counts(dropna=True).to_dict(),
        "clean_count": int(related_report["is_clean"].sum()),
        "sample_transaction_ids": related_report["transaction_id"].head(10).tolist(),
    }


def search_bank_statement(ctx: ToolContext, transaction_id: str, window_days: int = 5,
                           amount_tolerance_rupees: float = 5.0) -> dict:
    """Actively search for a plausible unclaimed bank posting near this
    transaction's own captured date and expected amount, instead of just
    being told "no UTR found." This is the difference between an analyst
    who looked and one who didn't.

    Deliberately does NOT take raw date/amount strings from the model --
    an earlier version did, and the model (with no real date anchor
    anywhere in the evidence block) hallucinated a plausible-looking but
    completely wrong window (2023 dates against a dataset that only spans
    2026-07). Same fix pattern as lookup_related_transactions: derive the
    window from data we already have, don't make the model guess it."""
    if transaction_id not in ctx.captured_date_by_txn:
        return {"error": f"no gateway record for {transaction_id}"}

    ref_date = ctx.captured_date_by_txn[transaction_id]
    d_from = ref_date - dt.timedelta(days=window_days)
    d_to = ref_date + dt.timedelta(days=window_days)

    expected_rows = ctx.report.loc[ctx.report["transaction_id"] == transaction_id, "ledger_expected_net_rupees"]
    expected_amount = float(expected_rows.iloc[0]) if len(expected_rows) else None

    bank = ctx.bank.copy()
    bank["credit_date"] = pd.to_datetime(bank["credit_date"]).dt.date
    mask = (bank["credit_date"] >= d_from) & (bank["credit_date"] <= d_to)
    if expected_amount is not None:
        mask &= (bank["credit_amount_rupees"] - expected_amount).abs() <= amount_tolerance_rupees
    matches = bank[mask].copy()

    # candidate_status distinguishes a genuinely unclaimed posting from one
    # already consumed by a DIFFERENT settlement's match -- without this, a
    # date+amount coincidence could be read as "unclaimed evidence" for the
    # current case when it's really someone else's already-resolved posting.
    matches["candidate_status"] = matches["bank_txn_id"].apply(
        lambda t: "already_matched_elsewhere" if t in ctx.claimed_bank_txn_ids else "unclaimed")
    unclaimed = matches[matches["candidate_status"] == "unclaimed"]

    return {
        "searched_date_range": [d_from.isoformat(), d_to.isoformat()],
        "searched_expected_amount_rupees": expected_amount,
        "candidate_count": len(matches),
        "unclaimed_candidate_count": len(unclaimed),
        "candidates": matches[["bank_txn_id", "utr", "credit_amount_rupees", "credit_date",
                                "bank_account_id", "narration", "candidate_status"]]
        .head(10).assign(credit_date=lambda d: d["credit_date"].astype(str))
        .to_dict(orient="records"),
    }


def compute_delta(ctx: ToolContext, a: float, b: float) -> dict:
    """The only arithmetic tool available. The model calls this instead
    of computing a difference itself -- same principle as the rest of
    the project (agent/gate.py's docstring: "the LLM never touches a
    number"), just enforced one level deeper: even mid-investigation,
    subtraction happens in Python, not in the model's head."""
    return {"a_minus_b": round(a - b, 2)}


def get_loan_recovery_schedule(ctx: ToolContext, transaction_id: str) -> dict:
    """Look this transaction up in Razorpay Capital's recovery ledger --
    the fourth source. A settlement can credit less than the ledger
    expected because a contracted working-capital advance took its agreed
    cut, which is a collection rather than a loss.

    Deliberately answers THREE separate questions, because conflating them
    is exactly how a shortfall gets waved through incorrectly:
      1. does this merchant carry an active advance at all?
      2. is there a recovery booked against THIS transaction?
      3. does that recovery amount actually account for the full observed
         delta, or only part of it?

    (3) is computed here in Python, never left to the model -- a recovery
    that explains only part of a gap leaves a genuinely unexplained
    residual, and `reconciles_delta: false` is the signal that the case
    must still escalate. Mirrors matching/ledger_check.py's own rule
    exactly, so the investigation cannot reach a verdict the deterministic
    matcher would disagree with.
    """
    if ctx.loan_book is None or len(ctx.loan_book) == 0:
        return {"transaction_id": transaction_id, "loan_book_available": False,
                "note": "No Razorpay Capital recovery ledger is loaded for this dataset."}

    merchant_id = ctx.merchant_by_txn.get(transaction_id)
    merchant_loans = ctx.loan_book[ctx.loan_book["merchant_id"] == merchant_id]
    row = ctx.loan_book[ctx.loan_book["transaction_id"] == transaction_id]

    out = {
        "transaction_id": transaction_id,
        "loan_book_available": True,
        "merchant_id": merchant_id,
        "merchant_has_active_advance": bool(len(merchant_loans)),
        "merchant_recovery_count": int(len(merchant_loans)),
        "recovery_found_for_this_transaction": bool(len(row)),
    }
    if not len(row):
        out["note"] = ("No recovery is booked against this transaction. A shortfall here "
                        "is NOT explained by loan recovery, even if this merchant has an "
                        "advance -- do not treat the advance itself as the explanation.")
        return out

    rec = row.iloc[0]
    recovery_amount = round(float(rec["recovery_amount_rupees"]), 2)
    out.update({
        "recovery_id": rec["recovery_id"],
        "loan_id": rec["loan_id"],
        "loan_principal_rupees": float(rec["loan_principal_rupees"]),
        "recovery_rate_pct": float(rec["recovery_rate_pct"]),
        "recovery_amount_rupees": recovery_amount,
        "recovery_date": str(rec["recovery_date"]),
        "recovery_method": rec["recovery_method"],
        "status": rec["status"],
    })

    # Does the recovery actually reconcile the gap? Same arithmetic and same
    # tolerance matching/ledger_check.py applies.
    rep = ctx.report[ctx.report["transaction_id"] == transaction_id]
    if len(rep) and pd.notna(rep.iloc[0].get("net_delta_rupees")):
        net_delta = round(float(rep.iloc[0]["net_delta_rupees"]), 2)
        residual = round(net_delta + recovery_amount, 2)
        reconciles = abs(residual) <= EXACT_MATCH_TOLERANCE_RUPEES
        out.update({
            "observed_net_delta_rupees": net_delta,
            "residual_after_recovery_rupees": residual,
            "reconciles_delta": bool(reconciles),
            "note": ("The recovery fully accounts for the observed shortfall -- this is a "
                      "contracted collection, not missing money."
                      if reconciles else
                      f"The recovery does NOT fully account for the shortfall: "
                      f"Rs.{abs(residual):,.2f} remains unexplained after it. This case "
                      f"must still escalate; a partially-explaining record never launders "
                      f"the rest of the gap into an auto-resolve."),
        })
    return out


TOOLS = {
    "get_transaction_details": get_transaction_details,
    "get_settlement_details": get_settlement_details,
    "calculate_settlement_variance": calculate_settlement_variance,
    "lookup_related_transactions": lookup_related_transactions,
    "search_bank_statement": search_bank_statement,
    "get_loan_recovery_schedule": get_loan_recovery_schedule,
    "compute_delta": compute_delta,
}
