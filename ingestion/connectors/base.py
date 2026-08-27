"""Shared constants for the partner connectors -- both suryaan.py and
northbridge.py convert to/from exactly this canonical column set, matching
data/bank_statement.csv's existing 9-column schema unchanged."""

CANONICAL_COLUMNS = [
    "bank_txn_id",
    "settlement_posting_id",
    "utr",
    "credit_amount_rupees",
    "credit_date",
    "value_date",
    "narration",
    "bank_account_id",
    "transaction_type",
]
