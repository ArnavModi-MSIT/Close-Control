"""Load and normalize the source files. Ground truth is never loaded here."""

import os

import pandas as pd

# The fourth source: Razorpay Capital's own recovery ledger. Deliberately
# loaded by its own function rather than added to load_sources()'s return
# tuple -- that signature is unpacked in a dozen places (run_matcher,
# evaluate, cash_position, investigator/tools, review_backend), and widening
# it would churn all of them for a source only ledger_check.py consults.
LOAN_BOOK_FILENAME = "loan_recovery_schedule.csv"


def load_sources(data_dir: str):
    gateway = pd.read_json(f"{data_dir}/gateway.json")
    bank = pd.read_csv(f"{data_dir}/bank_statement.csv")
    ledger = pd.read_csv(f"{data_dir}/internal_settlement_ledger.csv")

    # normalize gateway amounts from paise (int) to rupees (float) -- the
    # deliberate unit mismatch between sources gets resolved right here,
    # once, rather than scattered through matching logic.
    for col in ["payment_amount_paise", "fee_paise", "tax_paise", "adjustment_paise", "settlement_amount_paise"]:
        gateway[col.replace("_paise", "_rupees")] = gateway[col] / 100.0

    bank["credit_date"] = pd.to_datetime(bank["credit_date"]).dt.date
    bank["value_date"] = pd.to_datetime(bank["value_date"]).dt.date
    ledger["expected_settlement_date"] = pd.to_datetime(ledger["expected_settlement_date"]).dt.date

    return gateway, bank, ledger


def load_loan_book(data_dir: str) -> pd.DataFrame:
    """Razorpay Capital advances being repaid by settlement deduction.

    Returns an EMPTY frame (correct columns, zero rows) when the file is
    absent, so a dataset generated before this source existed still loads
    and reconciles exactly as it did before -- the same tolerance
    ledger_check.py's chargeback branch has for a missing chargeback_id
    column. A missing loan book means "no merchant has an advance," which
    is a valid state, not an error.
    """
    path = os.path.join(data_dir, LOAN_BOOK_FILENAME)
    if not os.path.exists(path):
        return pd.DataFrame(columns=["recovery_id", "loan_id", "merchant_id", "transaction_id",
                                       "loan_principal_rupees", "recovery_rate_pct",
                                       "recovery_amount_rupees", "recovery_date",
                                       "recovery_method", "status"])
    loans = pd.read_csv(path)
    if len(loans):
        loans["recovery_date"] = pd.to_datetime(loans["recovery_date"]).dt.date
    return loans
