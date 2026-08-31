"""Cash position: deterministic aggregation on top of matching/'s already-
computed reconciliation report. No ML/LLM, no new reconciliation logic, never
reads ground_truth.csv, never touches agent/ -- "AI proposes, deterministic
code disposes" applies here exactly as it does in matching/ and agent/gate.py.
"""

import datetime as dt

import pandas as pd

from data_generation.utils import is_business_day, add_business_days
from . import config

BUCKET_CONFIRMED = "confirmed"
BUCKET_IN_TRANSIT = "in_transit"
BUCKET_AT_RISK = "at_risk"
BUCKET_NOT_YET_CAPTURED = "not_yet_captured"


def _primary_gateway_dates(gateway: pd.DataFrame) -> pd.DataFrame:
    """One row per transaction_id_ref, using the same drop_duplicates rule
    matching/report.py itself uses (keep="first") -- keeps cash position's
    notion of "the" gateway row per transaction consistent with the matcher's.
    settle_date is NaT for held_for_risk_review (settled_at never populates,
    verified: eligible_for_settlement is False iff failure_mode ==
    "held_for_risk_review" -- data_generation/payments.py, the only place
    it's set -- so this is the only path to a NaT settle_date, not one of
    several).

    "first" (drop_duplicates(keep="first")) means first-in-row-order, not
    first-in-time -- matches matching/report.py's own convention
    deliberately (same rule, same grain). Only 9 of ~1,500+ successful
    transactions in the curated dataset ever have more than one successful
    gateway row for the same transaction_id_ref, so which one counts as
    "primary" here is a low-stakes edge case in practice, not a source of
    real drift today."""
    successful = gateway[gateway["attempt_status"] == "success"]
    primary = successful.drop_duplicates("transaction_id_ref", keep="first").set_index("transaction_id_ref")
    return pd.DataFrame({
        "captured_date": primary["captured_at"].dt.date,
        "settle_date": primary["settled_at"].dt.date,
    })


def classify_positions(report: pd.DataFrame, gateway: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
    """Join matching/report.py's per-transaction reconciliation verdict with
    capture/settle dates and classify into confirmed / in_transit / at_risk /
    not_yet_captured as of `as_of`. One row per ledger transaction (report's
    own grain)."""
    dates = _primary_gateway_dates(gateway)
    df = report.merge(dates, left_on="transaction_id", right_index=True, how="left")

    df["is_held"] = df["settle_date"].isna()
    captured = df["captured_date"].notna() & (df["captured_date"] <= as_of)

    df["cash_bucket"] = BUCKET_NOT_YET_CAPTURED
    df.loc[captured & df["is_held"], "cash_bucket"] = BUCKET_AT_RISK

    not_held_captured = captured & ~df["is_held"]
    due = not_held_captured & (df["settle_date"] <= as_of)
    not_due = not_held_captured & (df["settle_date"] > as_of)

    # confirmed = clean OR the matcher's own auto_resolve_eligible policy says
    # this exception is safe -- derived from report.py's flag rather than a
    # separately maintained exception-type list, so it can never drift out of
    # sync with the matcher's actual policy (a hand-maintained list was tried
    # and verified to silently miss partial_refund and deemed_success_ambiguous,
    # both auto_resolve_eligible=False -- would have wrongly counted as confirmed)
    confirmed = df["is_clean"] | (df["final_exception_type"].notna() & df["auto_resolve_eligible"])
    df.loc[due & confirmed, "cash_bucket"] = BUCKET_CONFIRMED
    df.loc[due & ~confirmed, "cash_bucket"] = BUCKET_AT_RISK
    df.loc[not_due, "cash_bucket"] = BUCKET_IN_TRANSIT

    # cash_amount_rupees: bank-confirmed actual for confirmed rows; the
    # ledger's own independent, no-hindsight expectation everywhere else.
    # Never observed_net_rupees for held/at-risk/in-transit rows -- that field
    # is a theoretical gateway computation, not a real bank observation (see
    # matching/ledger_check.py: computed before the on_hold check fires, so it
    # exists and looks plausible for held rows even though no settlement or
    # bank posting ever occurs for them).
    df["cash_amount_rupees"] = df["ledger_expected_net_rupees"]
    df.loc[df["cash_bucket"] == BUCKET_CONFIRMED, "cash_amount_rupees"] = df["observed_net_rupees"]

    return df


def summarize_snapshot(detail: pd.DataFrame) -> dict:
    """Aggregate classify_positions() output into headline totals/counts."""
    confirmed = detail[detail["cash_bucket"] == BUCKET_CONFIRMED]
    in_transit = detail[detail["cash_bucket"] == BUCKET_IN_TRANSIT]
    at_risk = detail[detail["cash_bucket"] == BUCKET_AT_RISK]

    held = at_risk[at_risk["is_held"]]
    at_risk_due = at_risk[~at_risk["is_held"]]

    return {
        "confirmed_count": len(confirmed),
        "confirmed_clean_count": int(confirmed["is_clean"].sum()),
        "confirmed_auto_resolved_count": int((~confirmed["is_clean"]).sum()),
        "confirmed_rupees": float(confirmed["cash_amount_rupees"].sum()),

        "in_transit_count": len(in_transit),
        "in_transit_rupees": float(in_transit["cash_amount_rupees"].sum()),

        "held_count": len(held),
        "held_rupees": float(held["cash_amount_rupees"].sum()),

        "at_risk_due_count": len(at_risk_due),
        "at_risk_due_nominal_rupees": float(at_risk_due["cash_amount_rupees"].sum()),
        "at_risk_due_known_delta_rupees": float(at_risk_due["net_delta_rupees"].dropna().sum()),
        # .to_dict() (not the raw Series value_counts() returns) -- pandas
        # converts numpy int64 counts to plain Python int in the process, so
        # this dict is JSON-safe on its own, matching orphan_rows's own
        # to_dict(orient="records") treatment elsewhere in this module.
        # Verified empirically: no current caller was actually broken by the
        # raw-Series version (review_backend/main.py never touches this key,
        # run_cash_position.py only prints individual snapshot fields,
        # export_dashboard_data.py was already calling .to_dict() at its own
        # call site) -- fixed at the source anyway so a future caller can't
        # rediscover this the hard way.
        "at_risk_by_exception_type": at_risk_due["final_exception_type"].value_counts().to_dict(),

        "not_yet_captured_count": int((detail["cash_bucket"] == BUCKET_NOT_YET_CAPTURED).sum()),

        # single headline figure: confirmed + in-transit only. at_risk/held
        # are deliberately excluded -- folding unconfirmed exception money
        # into one "projected" number would be exactly the kind of guess
        # the matcher and gate are built never to make.
        "projected_cash_position_rupees": (
            float(confirmed["cash_amount_rupees"].sum()) + float(in_transit["cash_amount_rupees"].sum())
        ),
    }


def _forecast_horizon_coverage(detail: pd.DataFrame, as_of: dt.date, horizon_business_days: int) -> dict:
    """Facts about whether the forecast horizon actually covers every
    in-transit row -- shared by build_daily_forecast() (which only prints a
    warning) and build_cash_position() (which surfaces this as structured
    fields a dashboard/evaluation harness can actually check, instead of
    only a stdout line no API caller ever sees)."""
    in_transit = detail[detail["cash_bucket"] == BUCKET_IN_TRANSIT]
    horizon_end = add_business_days(as_of, horizon_business_days)
    beyond_horizon = in_transit[in_transit["settle_date"] > horizon_end]
    return {
        "horizon_end": horizon_end,
        "transactions_beyond_horizon_count": len(beyond_horizon),
        "rupees_beyond_horizon": float(beyond_horizon["cash_amount_rupees"].sum()),
        "forecast_complete": len(beyond_horizon) == 0,
    }


def build_daily_forecast(detail: pd.DataFrame, as_of: dt.date,
                          horizon_business_days: int = config.FORECAST_HORIZON_BUSINESS_DAYS) -> pd.DataFrame:
    """Day-by-day forward forecast, one row per calendar day from as_of+1
    through the Nth business day out (weekends/holidays included with zero
    inflow, using is_business_day/add_business_days from data_generation.utils,
    so a dashboard gets a continuous timeline). Only in_transit rows
    contribute -- confirmed cash is already in hand, at-risk/held cash has no
    reliable forward date to forecast against. Never silently truncates: warns
    loudly if any in-transit row's settle_date falls beyond the horizon (see
    build_cash_position() for the same fact as structured output)."""
    in_transit = detail[detail["cash_bucket"] == BUCKET_IN_TRANSIT]

    coverage = _forecast_horizon_coverage(detail, as_of, horizon_business_days)
    horizon_end = coverage["horizon_end"]
    n_days = (horizon_end - as_of).days

    if coverage["transactions_beyond_horizon_count"]:
        print(f"WARNING: {coverage['transactions_beyond_horizon_count']} in-transit transaction(s) settle beyond the "
              f"{horizon_business_days}-business-day forecast horizon ({horizon_end}) and are "
              f"excluded from the forecast CSV. Consider a larger --horizon-days.")

    daily_amounts = in_transit.groupby("settle_date")["cash_amount_rupees"].sum()
    daily_counts = in_transit.groupby("settle_date").size()

    rows = []
    cumulative = 0.0
    business_day_counter = 0
    for offset in range(1, n_days + 1):
        day = as_of + dt.timedelta(days=offset)
        if is_business_day(day):
            business_day_counter += 1
        amount = float(daily_amounts.get(day, 0.0))
        count = int(daily_counts.get(day, 0))
        cumulative += amount
        rows.append({
            "forecast_date": day.isoformat(),
            "business_days_from_as_of": business_day_counter,
            "is_business_day": is_business_day(day),
            "transaction_count": count,
            "forecasted_net_rupees": round(amount, 2),
            "cumulative_forecasted_net_rupees": round(cumulative, 2),
        })
    return pd.DataFrame(rows)


def build_cash_position(report: pd.DataFrame, gateway: pd.DataFrame, as_of: dt.date,
                         horizon_business_days: int = config.FORECAST_HORIZON_BUSINESS_DAYS) -> dict:
    """Convenience wrapper, same role as run_matcher.run()'s tuple return.

    "detail" and "forecast" are deliberately raw DataFrames, not JSON-safe
    dicts -- by design, for internal/CLI callers that want them as-is
    (run_cash_position.py writes "forecast" straight to CSV via
    .to_csv()). "snapshot" (see summarize_snapshot()) is the one sub-dict
    meant as a clean, JSON-safe leaf summary. A future API caller that
    wants "detail"/"forecast" as JSON must convert explicitly
    (.to_dict(orient="records"), with date columns cast to .isoformat()
    first) -- exactly what export_dashboard_data.py already does today."""
    detail = classify_positions(report, gateway, as_of)
    coverage = _forecast_horizon_coverage(detail, as_of, horizon_business_days)
    return {
        "detail": detail,
        "snapshot": summarize_snapshot(detail),
        "forecast": build_daily_forecast(detail, as_of, horizon_business_days),
        # Structured, not just build_daily_forecast()'s stdout warning -- an
        # API caller/dashboard can check forecast_complete directly instead
        # of the forecast silently looking "done" when it's actually partial.
        "forecast_horizon_end": coverage["horizon_end"].isoformat(),
        "forecast_complete": coverage["forecast_complete"],
        "transactions_beyond_horizon_count": coverage["transactions_beyond_horizon_count"],
        "rupees_beyond_horizon": round(coverage["rupees_beyond_horizon"], 2),
    }
