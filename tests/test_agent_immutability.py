"""
Agent-immutability proof: the AI layer (agent/client.py's resolve_exception(),
agent/gate.py's apply_gate(), investigator/loop.py's investigate()) never
mutates the matcher's own report_row or report DataFrame it's handed --
only ever reads from them and returns a NEW proposal/decision object.

This project already states the equivalent claim as a working principle
(CLAUDE.md §9: "The AI's original proposal is immutable"), but that
principle was written about `seed_review_queue.py` never overwriting an
already-SEEDED case -- a database-level guarantee. It never had an
automated proof for the narrower, upstream claim: that the matcher's
report_row dict / DataFrame itself, passed BY REFERENCE into every one of
these functions, is never mutated in place during a normal exception
-resolution or investigation call. Python dicts and pandas DataFrames are
both mutable and passed by reference, so this is a real thing that could
silently break (a future edit that does `report_row["final_exception_type"]
= ...` instead of building a new object, or a tool that assigns into
ctx.report in place) without any existing test catching it.

Proven with a deep-copy-before/byte-identical-after check, applied here
to this project's own three real entry points where a report_row/report
DataFrame crosses into AI-layer code.

    python tests/test_agent_immutability.py
"""

import os as _os
import sys as _sys
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)
_SCRIPTS_DIR = _os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPTS_DIR)

import copy
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

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


def _sample_report_row() -> dict:
    return {
        "transaction_id": "trn-immut-1", "merchant_id": "mer-1", "settlement_id": "setl-1",
        "final_exception_type": "missing_bank_reference", "all_signals": ["missing_bank_reference"],
        "risk_class": "medium", "match_status": "matched", "match_pass": "exact",
        "ledger_expected_net_rupees": 976.4, "observed_net_rupees": 976.4, "net_delta_rupees": 0.0,
        "auto_resolve_eligible": False, "is_clean": False,
    }


def main() -> None:
    print("\nSection 1: agent/client.py's resolve_exception() never mutates report_row")
    from agent.client import resolve_exception

    row = _sample_report_row()
    snapshot = copy.deepcopy(row)
    # LLM_PROVIDER defaults to "mock" ($0, no network, deterministic) --
    # exactly what run_agent.py's default demo mode uses, so this exercises
    # the real default path, not a specially-constructed fake.
    resolution = resolve_exception(row, data_dir="data")
    check("report_row is byte-identical after resolve_exception() (mock provider)",
          row == snapshot, f"before={snapshot}\nafter={row}")
    check("resolve_exception() genuinely returned a real proposal, not a no-op stub "
          "(confirms this test exercised real work, not a trivial pass-through)",
          resolution.exception_type is not None and resolution.policy_id != "", str(resolution))
    print()

    print("Section 2: agent/gate.py's apply_gate() never mutates report_row, even on a reclassification")
    from agent.gate import apply_gate
    from agent.schema import ExceptionResolution

    row2 = _sample_report_row()
    snapshot2 = copy.deepcopy(row2)
    # Deliberately a reclassifying, high-confidence resolution -- the exact
    # scenario CLAUDE.md's own gate.py docstring warns about ("a
    # sufficiently confident... reclassification could smuggle a high-risk
    # case into a policy bucket that permits automation" if the matcher's
    # own row were ever trusted to be mutable/overridable). If apply_gate()
    # ever mutated report_row in place to reflect the agent's opinion, this
    # is exactly the call that would reveal it.
    resolution2 = ExceptionResolution(
        exception_type="clean", policy_id="POLICY-001", root_cause="agent's own (wrong) opinion",
        evidence_used=[], recommended_action="none", confidence=1.0, sufficient_evidence=True,
    )
    gate_result = apply_gate(resolution2, row2)
    check("report_row is byte-identical after apply_gate(), even for a reclassifying resolution",
          row2 == snapshot2, f"before={snapshot2}\nafter={row2}")
    check("the gate's own OUTPUT correctly records the reclassification attempt "
          "(confirms the test exercised the reclassification path for real)",
          gate_result["reclassified"] is True, str(gate_result))
    check("...but the matcher's own field inside report_row was never touched to match it",
          row2["final_exception_type"] == "missing_bank_reference", row2["final_exception_type"])
    print()

    print("Section 3: investigator/loop.py's investigate() never mutates report_row OR ctx.report")
    from investigator.loop import investigate
    from investigator.tools import ToolContext

    gateway = pd.DataFrame([{
        "transaction_id_ref": "trn-immut-1", "attempt_status": "success",
        "merchant_id": "mer-1", "order_id": "ord-1", "payment_method": "upi",
        "captured_at": pd.Timestamp("2026-07-01"), "payment_amount_rupees": 1000.0,
        "fee_rupees": 20.0, "tax_rupees": 3.6, "settlement_id": None, "status": "captured",
        "signature_valid": True, "refund_id": None, "refund_reason": None,
        "adjustment_rupees": 0.0,
    }])
    bank = pd.DataFrame([{
        "bank_txn_id": "bank-immut-1", "utr": None, "credit_amount_rupees": 976.4,
        "credit_date": "2026-07-02", "bank_account_id": "acc-1", "narration": "ordinary narration",
    }])
    report_df = pd.DataFrame([_sample_report_row()])
    ctx = ToolContext(report=report_df, gateway=gateway, bank=bank)
    report_df_snapshot = report_df.copy(deep=True)

    row3 = _sample_report_row()
    snapshot3 = copy.deepcopy(row3)

    class _RealToolCallingClient:
        """Actually calls a real tool (search_bank_statement) mid-investigation
        -- not a fake that skips straight to a canned final answer -- so this
        genuinely exercises whatever a tool might do to ctx.report, not just
        the loop's own bookkeeping around a call it never makes."""
        def __init__(self):
            self._round = 0

        def chat_with_tools(self, messages, tools):
            self._round += 1
            if self._round == 1:
                return {"role": "assistant", "tool_calls": [
                    {"function": {"name": "search_bank_statement",
                                  "arguments": {"transaction_id": "trn-immut-1"}}}
                ]}
            return {"role": "assistant", "tool_calls": None}

        def final_answer(self, messages):
            return {
                "exception_type": "missing_bank_reference", "policy_id": "POLICY-004",
                "root_cause": "test", "evidence_used": ["TOOL-1"], "recommended_action": "test",
                "confidence": 0.9, "sufficient_evidence": True,
            }

    investigate(row3, policy_block="[test policy]", evidence_block="[test evidence]",
                ctx=ctx, client=_RealToolCallingClient(), data_dir="data")

    check("report_row (the plain dict passed into investigate()) is byte-identical after",
          row3 == snapshot3, f"before={snapshot3}\nafter={row3}")
    check("ctx.report (the DataFrame every tool reads from) is byte-identical after a real tool call",
          ctx.report.equals(report_df_snapshot), "ctx.report changed during investigation")

    print(f"\n{'=' * 62}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'=' * 62}")
    if _failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
