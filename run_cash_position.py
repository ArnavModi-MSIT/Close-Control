"""
Cash position snapshot & forward forecast -- CLI entrypoint.

Classifies every reconciled transaction (via matching/'s report) into
confirmed / in-transit / at-risk cash as of a chosen snapshot date, and
writes a day-by-day forward forecast CSV. Pure deterministic aggregation on
top of matching/'s already-computed report -- no ML/LLM, no new
reconciliation logic, never reads ground_truth.csv, never touches agent/.

    python run_cash_position.py
    python run_cash_position.py --as-of 2026-07-20
    python run_cash_position.py --as-of 2026-07-25 --horizon-days 14
"""

import os
import argparse
import datetime as dt

from run_matcher import run
from matching.loaders import load_sources
from cash_position import config as cp_config
from cash_position.engine import build_cash_position

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--as-of", type=dt.date.fromisoformat, default=cp_config.DEFAULT_AS_OF,
                         help=f"Snapshot date, YYYY-MM-DD (default: {cp_config.DEFAULT_AS_OF})")
    parser.add_argument("--horizon-days", type=int, default=cp_config.FORECAST_HORIZON_BUSINESS_DAYS,
                         help=f"Forecast horizon in business days (default: {cp_config.FORECAST_HORIZON_BUSINESS_DAYS})")
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--out-csv", default=None)
    return parser.parse_args()


def print_summary(snapshot, as_of):
    print("=" * 70)
    print(f"CASH POSITION -- SETTLEMENT-DERIVED SNAPSHOT -- as of {as_of}")
    print("=" * 70)
    # "Settlement-derived" is the operative qualifier, not decoration --
    # following an external review noting that a bare "CASH POSITION
    # SNAPSHOT" headline invites reading this as the company's actual bank
    # cash balance. It's a classification of transactions the matcher
    # already reconciled (confirmed/in-transit/at-risk), not a separately
    # sourced treasury/GL cash figure.
    total_captured = (snapshot["confirmed_count"] + snapshot["in_transit_count"]
                       + snapshot["held_count"] + snapshot["at_risk_due_count"])
    print(f"Built on matching/'s reconciliation report (fresh matcher run, never reads "
          f"ground_truth.csv). {total_captured} transactions captured by this date "
          f"({snapshot['not_yet_captured_count']} not yet captured as of this snapshot -- excluded).")
    print()

    print("=" * 70)
    print("1. CONFIRMED / RECONCILED CASH")
    print("=" * 70)
    print("Bank-confirmed, settle date on/before as-of, clean or auto-resolvable only.")
    print(f"Transactions: {snapshot['confirmed_count']}")
    print(f"  clean (no exception): {snapshot['confirmed_clean_count']}")
    print(f"  auto-resolvable minor variance: {snapshot['confirmed_auto_resolved_count']}")
    print(f"Confirmed cash: Rs {snapshot['confirmed_rupees']:,.2f}")
    print()

    print("=" * 70)
    print("2. IN-TRANSIT / FORECASTED CASH")
    print("=" * 70)
    print("Captured, not yet due to settle as of this snapshot.")
    print(f"Transactions: {snapshot['in_transit_count']}")
    print(f"Forecasted inflow (ledger-expected, no bank confirmation yet): "
          f"Rs {snapshot['in_transit_rupees']:,.2f}")
    print("See data/cash_position_forecast.csv for the day-by-day breakdown.")
    print()

    print("=" * 70)
    print("3. AT-RISK / UNCONFIRMED CASH")
    print("=" * 70)
    print("Past-due and not cleanly confirmed by the matcher, OR held for risk review")
    print("(no settle date, ever). Never folded into confirmed cash -- escalate, don't guess.")
    print(f"Held for risk review: {snapshot['held_count']} txns, "
          f"Rs {snapshot['held_rupees']:,.2f} nominal")
    print(f"Other past-due, unconfirmed: {snapshot['at_risk_due_count']} txns")
    print(f"  Nominal expected (ledger): Rs {snapshot['at_risk_due_nominal_rupees']:,.2f}")
    print(f"  Known delta (where computable): Rs {snapshot['at_risk_due_known_delta_rupees']:,.2f}")
    print()
    print("Breakdown by exception type:")
    print(snapshot["at_risk_by_exception_type"])
    print()

    at_risk_total_rupees = snapshot["held_rupees"] + snapshot["at_risk_due_nominal_rupees"]
    at_risk_total_count = snapshot["held_count"] + snapshot["at_risk_due_count"]

    print("=" * 70)
    print("4. PROJECTED CASH POSITION")
    print("=" * 70)
    print("Confirmed (bank-verified) + in-transit (forecasted, not yet due).")
    print("Refunds/fees/adjustments are already netted per-transaction into the")
    print("ledger's own expected amount -- not a separate subtracted line here.")
    print(f"  Confirmed (bank-verified):             Rs {snapshot['confirmed_rupees']:>15,.2f}  "
          f"({snapshot['confirmed_count']} txns)")
    print(f"+ In-transit (forecasted, not yet due):  Rs {snapshot['in_transit_rupees']:>15,.2f}  "
          f"({snapshot['in_transit_count']} txns)")
    print("-" * 70)
    print(f"= Projected cash position:               Rs {snapshot['projected_cash_position_rupees']:>15,.2f}")
    print()
    print(f"Excluded from this projection -- at-risk / unconfirmed (section 3): "
          f"Rs {at_risk_total_rupees:,.2f} across {at_risk_total_count} txns.")
    print("Not counted until resolved: folding unconfirmed exception money into a")
    print("\"projected\" total would be exactly the kind of guess this system is")
    print("built never to make.")


def main():
    args = parse_args()
    report, settlement_matches, ledger_check = run(args.data_dir)
    gateway, bank, ledger = load_sources(args.data_dir)

    result = build_cash_position(report, gateway, args.as_of, args.horizon_days)
    print_summary(result["snapshot"], args.as_of)

    out_csv = args.out_csv or os.path.join(args.data_dir, "cash_position_forecast.csv")
    result["forecast"].to_csv(out_csv, index=False)
    print(f"\nWrote day-by-day forecast: {out_csv} ({len(result['forecast'])} rows)")


if __name__ == "__main__":
    main()
