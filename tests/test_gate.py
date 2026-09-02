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
from agent.evidence import check_communication_leakage
from agent import config
from investigator.schema import InvestigationResult, ToolCallRecord


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
    """Proves the model's own free-text root_cause is checked for language
    that contradicts its own decision (e.g. an auto-resolving case whose
    text reads like an escalation). Verified against every real root_cause
    in data/audit_log.jsonl and
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


def test_communication_leakage_catches_real_observed_leak():
    """Proves the internal-jargon leakage guard catches a genuine leak of
    internal decision-state vocabulary into an external-facing draft, not
    just a synthetic example: this exact string (trimmed) is a REAL
    drafted_communication already sitting in
    data/investigation_log.jsonl for trn-000098, found by scanning all 211
    real non-null drafts before building this check."""
    leaks = check_communication_leakage(
        "Urgent: Duplicate charge detected for transaction trn-000098 "
        "(merchant merch_004). Escalate for refund per POLICY-009. No auto-resolution permitted.")
    assert "policy-009" in leaks
    assert "auto-resolution" in leaks
    print("PASS -- real observed leak (POLICY-### citation + auto-resolution) caught")


def test_communication_leakage_word_boundary_avoids_false_positive():
    """'gate' must not fire on 'investigate'/'gateway' -- verified against
    all 211 real drafts that this collision would otherwise have produced
    9 false positives, all from those two words, zero genuine leaks."""
    leaks = check_communication_leakage(
        "Please investigate the gateway settlement discrepancy for this transaction.")
    assert leaks == []
    print("PASS -- 'investigate'/'gateway' text does not false-positive on the 'gate' guard")


def _investigation(**overrides):
    """An InvestigationResult with one real-shaped tool call, so the
    numeric-grounding check has something concrete to ground against."""
    defaults = dict(
        exception_type="unexplained_shortage",
        policy_id="POLICY-007",
        root_cause="test fixture -- not a real LLM response",
        evidence_used=["EVIDENCE-8"],
        recommended_action="test fixture",
        confidence=0.90,
        sufficient_evidence=True,
        investigation_log=[
            ToolCallRecord(step=1, tool_name="search_bank_statement", arguments={},
                            result={"searched_expected_amount_rupees": 500.0, "candidate_count": 0}),
        ],
    )
    defaults.update(overrides)
    return InvestigationResult(**defaults)


def test_numeric_grounding_scoped_to_investigator_only():
    """The single-shot agent/client.py path never calls a tool, so it has
    no investigation_log at all -- checking its root_cause against an
    empty tool log would flag every real number it legitimately cited from
    the static evidence block as fabricated. apply_gate() must skip the
    check entirely for this path (numerically_grounded stays True) rather
    than false-flag it."""
    gate = apply_gate(
        resolution(root_cause="A wildly specific Rs.9,87,654.32 figure appears here."),
        report_row())
    assert gate["numerically_grounded"] is True
    assert gate["numeric_grounding_flags"] == []
    print("PASS -- single-shot resolution (no investigation_log) is never checked")


def test_numeric_grounding_catches_fabricated_number():
    """A number in root_cause that never appeared in any real tool result
    (and isn't the report_row's own expected/observed/delta) is flagged --
    proving the check actually catches something, not just stays quiet."""
    gate = apply_gate(
        _investigation(root_cause="The shortfall is exactly Rs.42,000.00, confirmed."),
        report_row())
    assert gate["numerically_grounded"] is False
    assert 42000.0 in gate["numeric_grounding_flags"]
    print("PASS -- a number absent from both the tool log and the evidence block is flagged")


def test_numeric_grounding_real_observed_case_not_false_flagged():
    """Regression test for a real false positive found and fixed while
    building this check, against trn-001454's actual logged investigation:
    root_cause states 'reduced ... by Rs.8,660.31' where the real
    net_delta_rupees is -8660.31 (a shortfall, prose drops the sign) --
    the number-extraction regex was originally losing the leading digit
    group of a negative comma-grouped figure entirely ('-8,660.31' ->
    '660.31'), and separately, extra_grounded_numbers didn't cover the
    sign-stripped case at all. Both are fixed; this proves it stays fixed."""
    gate = apply_gate(
        _investigation(
            root_cause="A refund/adjustment reduced the settlement amount by Rs.8,660.31 "
                       "(delta: observed - expected = 14,777.05 - 23,437.36 = -8,660.31).",
            investigation_log=[
                ToolCallRecord(step=1, tool_name="search_bank_statement", arguments={},
                                result={"searched_expected_amount_rupees": 23437.36, "candidate_count": 0}),
            ],
        ),
        report_row(net_delta_rupees=-8660.31, ledger_expected_net_rupees=23437.36,
                   observed_net_rupees=14777.05))
    assert gate["numerically_grounded"] is True, gate["numeric_grounding_flags"]
    print("PASS -- real observed case (negative delta stated unsigned in prose) no longer false-flags")


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
    test_communication_leakage_catches_real_observed_leak,
    test_communication_leakage_word_boundary_avoids_false_positive,
    test_numeric_grounding_scoped_to_investigator_only,
    test_numeric_grounding_catches_fabricated_number,
    test_numeric_grounding_real_observed_case_not_false_flagged,
]


if __name__ == "__main__":
    for t in ALL_TESTS:
        print(f"{t.__name__}:")
        t()
        print()
    print(f"All {len(ALL_TESTS)} gate tests passed.")
