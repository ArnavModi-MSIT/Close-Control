"""Load and normalize the 3 source files. Ground truth is never loaded here."""

import pandas as pd


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
