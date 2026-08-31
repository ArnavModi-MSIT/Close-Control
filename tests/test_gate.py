"""
Standalone unit tests for agent/gate.py -- the single most safety-critical
function in this codebase (it's the only thing standing between an LLM's
proposal and an actual auto-resolve decision). Covers every branch that can
flip final_decision or agent_status, in isolation, with synthetic inputs --
independent of whatever the current dataset happens to exercise.

No pytest dependency required -- functions are named test_* so pytest CAN
discover them if installed (`pytest -q test_gate.py`), but this file also
runs standalone:

    python tests/test_gate.py
"""

import os as _os
import sys as _sys
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)
_SCRIPTS_DIR = _os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPTS_DIR)

from agent.gate import apply_gate
from agent.schema import ExceptionResolution
from agent import config


def resolution(**overrides):
    """Build an ExceptionResolution with sane defaults, overridden per test."""
    defaults = dict(
        exception_type="deemed_success_ambiguous",
        policy_id="POLICY-006",
        root_cause="test fixture -- not a real LLM response",
        evidence_used=["EVIDENCE-8", "EVIDENCE-9"],
        recommended_action="test fixture",
        confidence=0.90,
        sufficient_evidence=True,
    )
    defaults.update(overrides)
    return ExceptionResolution(**defaults)


def report_row(**overrides):
    defaults = dict(
        transaction_id="trn-test",
        final_exception_type="deemed_success_ambiguous",
        net_delta_rupees=500.0,
        ledger_expected_net_rupees=500.0,
    )
    defaults.update(overrides)
    return defaults


def test_success_all_conditions_met():
    """The one allowlisted type, correct citation, high confidence,
    sufficient evidence, low amount -- every gate condition passes."""
    gate = apply_gate(resolution(), report_row())
    assert gate["final_decision"] == "auto_resolve"
    assert gate["agent_status"] == "success"
    assert gate["reclassified"] is False
    assert gate["policy_id_consistent"] is True
    print("PASS -- all conditions met -> auto_resolve")


def test_policy_missing():
    """Matcher's own exception_type isn't in the policy KB at all --
    must escalate, never fall through to auto-resolve."""
    gate = apply_gate(
        resolution(),
        report_row(final_exception_type="not_a_real_exception_type"),
    )
    assert gate["final_decision"] == "escalate"
    assert gate["agent_status"] == "policy_missing"
    assert gate["policy_id_consistent"] is False
    print("PASS -- unknown matcher exception_type -> escalate (policy_missing)")


def test_policy_id_mismatch_forces_escalation():
    """Allowlisted type, high confidence, sufficient evidence -- but the
    agent cites the WRONG policy_id. Every other condition would pass;
    this alone must still force escalation (the core authority-boundary
    guarantee of this whole module)."""
    gate = apply_gate(
        resolution(policy_id="POLICY-005"),  # partial_refund's ID, not deemed_success's
        report_row(),
    )
    assert gate["final_decision"] == "escalate"
    assert gate["agent_status"] == "policy_id_mismatch"
    assert gate["policy_id_consistent"] is False
    print("PASS -- wrong policy_id citation -> escalate, even with everything else passing")


def test_reclassification_is_informational_not_authoritative():
    """Agent reclassifies the exception to a different (more lenient)
    type, but still cites the ORIGINAL matcher-authoritative policy_id.
    The reclassification must be recorded -- but the type it changes
    auto-resolve eligibility for is the matcher's, not the agent's, and
    signature_verification_failed is neither allowlisted nor
    policy-permitted, so this must escalate regardless of confidence."""
    gate = apply_gate(
        resolution(exception_type="deemed_success_ambiguous", policy_id="POLICY-010",
                   confidence=0.99),
        report_row(final_exception_type="signature_verification_failed"),
    )
    assert gate["reclassified"] is True
    assert gate["agent_status"] == "reclassification_overridden"
    assert gate["final_decision"] == "escalate"
    print("PASS -- reclassification recorded but never authoritative -> escalate")


def test_not_in_allowlist():
    """Correct policy citation, high confidence, sufficient evidence --
    but the matcher's type simply isn't on the automation allowlist.
    Currently only deemed_success_ambiguous is; everything else must
    always escalate no matter how confident the agent is."""
    gate = apply_gate(
        resolution(exception_type="unexplained_shortage", policy_id="POLICY-007",
                   confidence=0.99),
        report_row(final_exception_type="unexplained_shortage"),
    )
    assert gate["final_decision"] == "escalate"
    assert gate["agent_status"] == "normal_escalation"
    assert any("AGENT_AUTO_RESOLVABLE_TYPES" in r for r in gate["gate_reasons"])
    print("PASS -- type not on allowlist -> escalate")


def test_confidence_below_threshold():
    gate = apply_gate(resolution(confidence=0.70), report_row())
    assert gate["final_decision"] == "escalate"
    assert any("confidence" in r for r in gate["gate_reasons"])
    print(f"PASS -- confidence 0.70 < {config.AUTO_RESOLVE_CONFIDENCE_THRESHOLD} -> escalate")


def test_insufficient_evidence():
    gate = apply_gate(resolution(sufficient_evidence=False), report_row())
    assert gate["final_decision"] == "escalate"
    assert any("insufficient" in r for r in gate["gate_reasons"])
    print("PASS -- sufficient_evidence=False -> escalate")


def test_amount_exceeds_risk_ceiling():
    gate = apply_gate(
        resolution(),
        report_row(net_delta_rupees=71098.29, ledger_expected_net_rupees=71098.29),
    )
    assert gate["final_decision"] == "escalate"
    assert any("risk ceiling" in r for r in gate["gate_reasons"])
    print(f"PASS -- Rs.71,098.29 > Rs.{config.AUTO_RESOLVE_RISK_CEILING_RUPEES:,.2f} ceiling -> escalate")


def test_missing_amount_fields_treated_as_zero_not_a_crash():
    """held_for_risk_review-style rows can carry None for both amount
    fields (see cash_position/engine.py's own note on this). The gate
    must not crash on that -- it should treat the risk amount as 0,
    never raise, and let the OTHER conditions (allowlist, in this case)
    still decide the outcome."""
    gate = apply_gate(
        resolution(exception_type="held_for_risk_review", policy_id="POLICY-008"),
        report_row(final_exception_type="held_for_risk_review",
                   net_delta_rupees=None, ledger_expected_net_rupees=None),
    )
    assert gate["final_decision"] == "escalate"  # not allowlisted, regardless of amount
    print("PASS -- None amount fields handled without crashing (amount_at_risk=0)")


def test_root_cause_contradiction_flagged_informationally_not_gating():
    """Idea sharpened by checking a peer Razorpay buildathon repo
    (SuryaSK-dev/razorpay-ai-finance-controller) past its README into its
    actual src/agent/explanation_validator.py, which rejects an LLM
    explanation using language that contradicts its own verified status.
    Verified against every real root_cause in data/audit_log.jsonl and
    data/investigation_log.jsonl (1,018 entries) before adopting the
    phrase lists: zero false positives. This test proves the flag fires
    on genuinely contradicting text, and -- critically -- that it never
    changes final_decision, since it hasn't earned that promotion yet
    (unlike unknown_evidence_citations, which was verified against real
    data and THEN made a hard gate condition)."""
    gate = apply_gate(
        resolution(exception_type="unexplained_shortage", policy_id="POLICY-007",
                   root_cause="Investigation complete -- this issue is fully resolved, no further action needed."),
        report_row(final_exception_type="unexplained_shortage"),
    )
    assert gate["final_decision"] == "escalate"  # unaffected by the flag -- not on the allowlist regardless
    assert gate["root_cause_consistent"] is False
    assert "fully resolved" in gate["root_cause_contradiction_flags"]
    print("PASS -- contradicting root_cause text flagged, but does not change final_decision")


def test_root_cause_consistent_on_normal_text():
    """The default fixture text (and any ordinary evidence-citing
    explanation) must NOT trip the check -- this is the false-positive
    guard, mirroring the real-data sweep that motivated the phrase lists
    in the first place. Checked on both outcomes since the accepted
    phrase set differs by gate_decision."""
    auto = apply_gate(resolution(), report_row())
    assert auto["final_decision"] == "auto_resolve"
    assert auto["root_cause_consistent"] is True

    escalated = apply_gate(
        resolution(exception_type="unexplained_shortage", policy_id="POLICY-007"),
        report_row(final_exception_type="unexplained_shortage"),
    )
    assert escalated["final_decision"] == "escalate"
    assert escalated["root_cause_consistent"] is True
    print("PASS -- ordinary root_cause text never false-flags on either outcome")


ALL_TESTS = [
    test_success_all_conditions_met,
    test_policy_missing,
    test_policy_id_mismatch_forces_escalation,
    test_reclassification_is_informational_not_authoritative,
    test_not_in_allowlist,
    test_confidence_below_threshold,
    test_insufficient_evidence,
    test_amount_exceeds_risk_ceiling,
    test_missing_amount_fields_treated_as_zero_not_a_crash,
    test_root_cause_contradiction_flagged_informationally_not_gating,
    test_root_cause_consistent_on_normal_text,
]


if __name__ == "__main__":
    for t in ALL_TESTS:
        print(f"{t.__name__}:")
        t()
        print()
    print(f"All {len(ALL_TESTS)} gate tests passed.")
