"""
Standalone proof that chargeback detection actually fires and is correctly
separated from a refund.

The curated dataset deliberately contains ZERO chargebacks (see
data_generation/sources/gateway.py's chargeback_id comment: adding a
chargeback FAILURE_MODE would reshuffle every payment's randomly-drawn mode
and invalidate the already-verified benchmark numbers, the 603-entry audit
log, and the investigator's logged runs). So the mechanism exists in
matching/ledger_check.py but is never exercised by the real data.

Rather than regenerate the dataset, this constructs minimal synthetic rows
and runs them through the REAL, unmodified check_ledger_vs_gateway() --
exactly the same approach test_ambiguity.py uses to prove the matcher's
ambiguity logic, which the main dataset also never triggers.

What this proves, and equally what it does NOT: it proves the detection
path, priority, and risk classification are real and correct. It does NOT
produce a chargeback rate or any measured statistic -- there is no
chargeback volume in this dataset to measure, and nothing in this project
claims one.

    python test_chargeback.py
"""

import sys

import pandas as pd

from matching.ledger_check import check_ledger_vs_gateway

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
                  chargeback_id=None, chargeback_reason=None,
                  refund_id=None, refund_reason=None,
                  gross: float = 1000.0, fee: float = 10.0, tax: float = 1.8) -> dict:
    """One successful gateway record, in the exact shape check_ledger_vs_gateway
    reads (loaders.py has already converted _paise -> _rupees by that point)."""
    net = gross - fee - tax + adjustment_rupees
    return {
        "transaction_id_ref": txn_id, "attempt_status": "success", "status": "processed",
        "signature_valid": True, "settlement_amount_rupees": round(net, 2),
        "payment_amount_rupees": gross, "fee_rupees": fee, "tax_rupees": tax,
        "adjustment_rupees": adjustment_rupees,
        "refund_id": refund_id, "refund_reason": refund_reason,
        "chargeback_id": chargeback_id, "chargeback_reason": chargeback_reason,
    }


def _ledger_row(txn_id: str, expected_net: float) -> dict:
    return {"transaction_id": txn_id, "order_id": f"order_{txn_id}",
            "merchant_id": "merch_test", "expected_net_settlement_rupees": expected_net}


def run(gateway_rows: list, ledger_rows: list) -> pd.DataFrame:
    return check_ledger_vs_gateway(pd.DataFrame(gateway_rows), pd.DataFrame(ledger_rows))


def main() -> None:
    print("=" * 70)
    print("CHARGEBACK DETECTION -- synthetic proof (dataset contains none)")
    print("=" * 70)
    print()

    # The expected net the ledger booked when the payment originally settled.
    CLEAN_NET = 1000.0 - 10.0 - 1.8

    print("Scenario 1: chargeback debit -- funds clawed back after settlement")
    row = run(
        [_gateway_row("trn-cb-1", adjustment_rupees=-1000.0,
                       chargeback_id="cb_9KQm2LxT", chargeback_reason="unauthorized_transaction")],
        [_ledger_row("trn-cb-1", CLEAN_NET)],
    ).iloc[0]
    print(f"  exception_type = {row.exception_type}  (expected: chargeback_received)")
    print(f"  risk_class     = {row.risk_class}      auto_resolve_eligible = {row.auto_resolve_eligible}")
    check("a chargeback is classified as chargeback_received, not partial_refund",
          row.exception_type == "chargeback_received", str(row.exception_type))
    check("chargeback is high risk", row.risk_class == "high", str(row.risk_class))
    check("chargeback is never auto-resolve eligible",
          not row.auto_resolve_eligible, str(row.auto_resolve_eligible))
    print()

    print("Scenario 2: refund of the SAME amount -- must stay partial_refund")
    print("  (both are negative adjustments; sign alone cannot separate them)")
    row = run(
        [_gateway_row("trn-rf-1", adjustment_rupees=-1000.0,
                       refund_id="rfnd_C8NYu3ZKQL", refund_reason="order_cancelled")],
        [_ledger_row("trn-rf-1", CLEAN_NET)],
    ).iloc[0]
    print(f"  exception_type = {row.exception_type}  (expected: partial_refund)")
    check("an identical-magnitude refund is still partial_refund, not a chargeback",
          row.exception_type == "partial_refund", str(row.exception_type))
    check("refund is medium risk, distinct from a chargeback's high",
          row.risk_class == "medium", str(row.risk_class))
    print()

    print("Scenario 3: dispute raised, debit not yet posted (net_delta ~ 0)")
    print("  (the case that would silently read as 'clean' without this check)")
    row = run(
        [_gateway_row("trn-cb-2", adjustment_rupees=0.0,
                       chargeback_id="cb_PENDING01", chargeback_reason="goods_not_received")],
        [_ledger_row("trn-cb-2", CLEAN_NET)],
    ).iloc[0]
    print(f"  net_delta      = {row.net_delta_rupees}")
    print(f"  exception_type = {row.exception_type}  (expected: chargeback_received)")
    check("a live dispute is flagged even when the money hasn't moved yet",
          row.exception_type == "chargeback_received", str(row.exception_type))
    print()

    print("Scenario 4: no chargeback field at all -- older/unmigrated dataset")
    print("  (proves .get() tolerance: the real curated dataset has no such column)")
    legacy = _gateway_row("trn-cl-1")
    del legacy["chargeback_id"]
    del legacy["chargeback_reason"]
    row = run([legacy], [_ledger_row("trn-cl-1", CLEAN_NET)]).iloc[0]
    print(f"  exception_type = {row.exception_type}  (expected: None -- clean)")
    check("a gateway row with no chargeback columns still reconciles clean",
          row.exception_type is None, str(row.exception_type))
    print()

    print("Scenario 5: the policy the agent must cite actually exists")
    from agent.policy_kb import get_policy
    p = get_policy("chargeback_received")
    print(f"  policy_id = {p['policy_id']}  auto_resolvable = {p['auto_resolvable']}  "
          f"risk = {p['risk_class']}")
    check("chargeback_received maps to a real policy", p["policy_id"] == "POLICY-012", p["policy_id"])
    check("that policy does NOT permit auto-resolution", p["auto_resolvable"] is False)
    print()

    print("=" * 70)
    print(f"{_passed} passed, {_failed} failed")
    print("=" * 70)
    print("NOTE: this proves the detection MECHANISM only. The curated dataset")
    print("contains zero chargebacks by design, so there is no chargeback rate")
    print("or volume to report -- and nothing in this project claims one.")
    if _failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
