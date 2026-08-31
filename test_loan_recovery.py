"""
Standalone proof that Razorpay Capital loan-recovery detection fires, is
correctly separated from a refund, and -- most importantly -- refuses to
explain away a shortfall the recovery does not actually cover.

The curated dataset DOES contain 18 real loan recoveries (unlike
test_chargeback.py's situation), so the happy path is already exercised by
the live data and scored by evaluate.py. What the real data cannot show is
the adversarial half: a recovery that only partly explains a gap, a refund
of identical magnitude with no recovery behind it, and a merchant who
borrows but has no recovery on THIS settlement. Those are the cases where
a naive "there's a loan, so the shortfall is fine" implementation quietly
auto-resolves genuinely missing money.

So this constructs minimal synthetic rows and runs them through the REAL,
unmodified check_ledger_vs_gateway() -- the same approach test_ambiguity.py
and test_chargeback.py use.

What this proves, and equally what it does NOT: it proves the detection
path, its precedence over the refund branch, the residual guard, and the
backward-compatible no-loan-book behaviour. It does NOT produce a recovery
rate -- for that, see evaluate.py's per-type table against the real 18.

    python test_loan_recovery.py
"""

import sys

import pandas as pd

from matching.ledger_check import check_ledger_vs_gateway
from agent.policy_kb import get_policy

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_passed = 0
_failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f" -- {detail}" if detail else ""))


def _gateway_row(txn_id: str, *, adjustment_rupees: float = 0.0,
                  merchant_id: str = "merch_test",
                  gross: float = 1000.0, fee: float = 10.0, tax: float = 1.8) -> dict:
    """One successful gateway record, in the exact shape
    check_ledger_vs_gateway reads (loaders.py has already converted
    _paise -> _rupees by that point)."""
    net = gross - fee - tax + adjustment_rupees
    return {
        "transaction_id_ref": txn_id, "attempt_status": "success", "status": "processed",
        "signature_valid": True, "settlement_amount_rupees": round(net, 2),
        "payment_amount_rupees": gross, "fee_rupees": fee, "tax_rupees": tax,
        "adjustment_rupees": adjustment_rupees, "merchant_id": merchant_id,
        "refund_id": None, "refund_reason": None,
        "chargeback_id": None, "chargeback_reason": None,
    }


def _ledger_row(txn_id: str, expected_net: float, merchant_id: str = "merch_test") -> dict:
    return {"transaction_id": txn_id, "order_id": f"order_{txn_id}",
            "merchant_id": merchant_id, "expected_net_settlement_rupees": expected_net,
            "expected_fee_rupees": 10.0, "expected_tax_rupees": 1.8}


def _recovery_row(txn_id: str, amount: float, merchant_id: str = "merch_test") -> dict:
    return {"recovery_id": f"rcv_{txn_id}", "loan_id": "loan_test_001",
            "merchant_id": merchant_id, "transaction_id": txn_id,
            "loan_principal_rupees": 250000.0, "recovery_rate_pct": 0.20,
            "recovery_amount_rupees": amount, "recovery_date": "2026-07-10",
            "recovery_method": "settlement_deduction", "status": "applied"}


def run(gateway_rows: list, ledger_rows: list, loan_rows: list = None) -> pd.DataFrame:
    loan_book = pd.DataFrame(loan_rows) if loan_rows else None
    return check_ledger_vs_gateway(pd.DataFrame(gateway_rows),
                                    pd.DataFrame(ledger_rows), loan_book)


def main() -> None:
    clean_net = 1000.0 - 10.0 - 1.8  # 988.20

    print("\nScenario 1: a contracted recovery is classified as loan_recovery_deduction,")
    print("            NOT as a partial_refund -- even though it is a negative adjustment.")
    df = run([_gateway_row("trn-l1", adjustment_rupees=-200.0)],
              [_ledger_row("trn-l1", clean_net)],
              [_recovery_row("trn-l1", 200.0)])
    row = df.iloc[0]
    check("classified loan_recovery_deduction",
          row["exception_type"] == "loan_recovery_deduction", f"got {row['exception_type']}")
    check("NOT misclassified as partial_refund", row["exception_type"] != "partial_refund")
    check("risk_class is low", row["risk_class"] == "low", f"got {row['risk_class']}")
    check("auto_resolve_eligible is True", bool(row["auto_resolve_eligible"]))
    check("loan_id carried onto the result row", row["loan_id"] == "loan_test_001")
    check("recovery amount carried onto the result row",
          row["loan_recovery_amount_rupees"] == 200.0)

    print("\nScenario 2: an IDENTICAL-magnitude refund, with no recovery record behind it,")
    print("            is still partial_refund -- sign alone cannot separate the two, so")
    print("            the loan book is the real distinguishing signal (same role")
    print("            chargeback_id plays for disputes).")
    df = run([_gateway_row("trn-l2", adjustment_rupees=-200.0)],
              [_ledger_row("trn-l2", clean_net)],
              [_recovery_row("trn-other", 200.0)])  # a recovery, but for a DIFFERENT txn
    row = df.iloc[0]
    check("classified partial_refund", row["exception_type"] == "partial_refund",
          f"got {row['exception_type']}")
    check("NOT auto-resolved", not bool(row["auto_resolve_eligible"]))

    print("\nScenario 3 (the important one): a recovery that explains only PART of the")
    print("            shortfall must NOT auto-resolve the remainder. Recovery is Rs.200")
    print("            but Rs.500 actually went missing.")
    df = run([_gateway_row("trn-l3", adjustment_rupees=-500.0)],
              [_ledger_row("trn-l3", clean_net)],
              [_recovery_row("trn-l3", 200.0)])
    row = df.iloc[0]
    check("NOT classified loan_recovery_deduction",
          row["exception_type"] != "loan_recovery_deduction", f"got {row['exception_type']}")
    check("NOT auto-resolved -- the Rs.300 residual is genuinely unexplained",
          not bool(row["auto_resolve_eligible"]))
    check("falls through to a real exception type", pd.notna(row["exception_type"]))

    print("\nScenario 4: a merchant who HAS an advance, but no recovery booked against")
    print("            this settlement, is not explained by the advance's mere existence.")
    df = run([_gateway_row("trn-l4", adjustment_rupees=0.0, gross=1000.0)],
              [_ledger_row("trn-l4", clean_net + 300.0)],  # 300 short, no adjustment on record
              [_recovery_row("trn-elsewhere", 300.0)])
    row = df.iloc[0]
    check("NOT classified loan_recovery_deduction",
          row["exception_type"] != "loan_recovery_deduction", f"got {row['exception_type']}")
    check("escalates as unexplained_shortage",
          row["exception_type"] == "unexplained_shortage", f"got {row['exception_type']}")

    print("\nScenario 5: no loan book at all (a dataset generated before this source")
    print("            existed) behaves exactly as it did before -- backward compatible.")
    df = run([_gateway_row("trn-l5", adjustment_rupees=-200.0)],
              [_ledger_row("trn-l5", clean_net)])  # loan_book omitted entirely
    row = df.iloc[0]
    check("still classified partial_refund with no loan book",
          row["exception_type"] == "partial_refund", f"got {row['exception_type']}")
    check("loan columns present but empty", pd.isna(row["loan_id"]))

    print("\nScenario 6: a clean transaction is unaffected by the presence of a loan book.")
    df = run([_gateway_row("trn-l6", adjustment_rupees=0.0)],
              [_ledger_row("trn-l6", clean_net)],
              [_recovery_row("trn-l6", 200.0)])
    row = df.iloc[0]
    check("stays clean (no exception raised)", pd.isna(row["exception_type"]),
          f"got {row['exception_type']}")

    print("\nScenario 7: POLICY-013 exists, is auto-resolvable, and cites the real")
    print("            regulation the resolution actually depends on.")
    policy = get_policy("loan_recovery_deduction")
    check("POLICY-013 is the policy id", policy["policy_id"] == "POLICY-013")
    check("marked auto_resolvable", policy["auto_resolvable"] is True)
    check("risk_class low, matching the matcher", policy["risk_class"] == "low")
    check("cites RBI's Digital Lending guidelines",
          "Digital Lending" in policy["resolution_action"])
    check("states the partial-recovery rule explicitly",
          "part of the gap" in policy["resolution_action"])

    print(f"\n{'=' * 62}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'=' * 62}")
    if _failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
