"""The gate: confidence alone never authorizes a financial action -- and,
critically, the LLM's OWN OPINION of what the exception is does not
either. This is plain, auditable code -- no LLM involved in the gate
decision itself, only in producing the proposal the gate evaluates.

Authority boundary (this is the core fix in this version): the
deterministic matcher's exception_type is authoritative for policy
lookup and the auto-resolve allowlist. If the LLM reclassifies the
exception to something else, that's recorded and surfaced, but it never
changes which policy governs the decision or whether auto-resolution is
even on the table. Without this, a sufficiently confident (or simply
wrong) reclassification could smuggle a high-risk case into a policy
bucket that permits automation -- exactly the kind of authority
inversion a financial control boundary must not allow.

    AUTO-RESOLVE only if ALL of:
      - matcher's exception_type is in AGENT_AUTO_RESOLVABLE_TYPES
        (the explicit, human-curated allowlist -- separate from and
        narrower than "policy.auto_resolvable", which describes what's
        theoretically permitted, not what THIS deployment automates)
      - policy (looked up via the MATCHER's type, not the LLM's) permits
        auto-resolution
      - the LLM's policy_id citation matches the policy the matcher's
        type actually maps to (citing a different, possibly more lenient
        policy is treated as a hard escalation, not a soft warning)
      - agent confidence >= threshold
      - sufficient_evidence == True (agent's own admission it had enough
        to go on -- a floor, not a proof of semantic correctness)
      - amount at risk < risk ceiling
    Otherwise: ESCALATE, regardless of how confident the agent sounded
    or what it reclassified the case as.
"""

from . import config
from .policy_kb import get_policy
from .evidence import validate_evidence_citations


def _compute_amount_at_risk(report_row: dict) -> float:
    return max(
        abs(report_row.get("net_delta_rupees") or 0),
        abs(report_row.get("ledger_expected_net_rupees") or 0),
    )


def is_investigation_worthwhile(report_row: dict) -> bool:
    """True only if the matcher's OWN exception_type + amount at risk make
    auto-resolve structurally reachable at all for this case -- i.e. an LLM's
    extra reasoning (single-shot or investigator/'s multi-round tool use)
    could actually change apply_gate()'s final_decision below.

    This is deliberately the allowlist + risk-ceiling half of apply_gate()'s
    six conditions, computed with ZERO LLM involvement, and callable BEFORE
    any LLM call is made -- not a new gate, just the part of the existing
    gate logic that never needed a proposal in the first place. A case that
    fails this can still be auto_resolve-ineligible in every other way
    (policy_id citation, confidence, sufficient_evidence), but a case that
    fails THIS will escalate no matter what the LLM says, since apply_gate()
    hard-blocks on the allowlist before it even looks at confidence.

    Used by run_investigator.py to avoid spending investigator/'s
    multi-minute-per-case budget on cases whose gate outcome is already
    fixed regardless of investigation depth -- routing is a decision too,
    and per this project's own rule it stays deterministic, not a trained
    classifier guessing at a boundary we can already compute exactly.
    """
    matcher_exception_type = report_row.get("final_exception_type")
    if matcher_exception_type not in config.AGENT_AUTO_RESOLVABLE_TYPES:
        return False
    return _compute_amount_at_risk(report_row) < config.AUTO_RESOLVE_RISK_CEILING_RUPEES


def apply_gate(resolution, report_row: dict, extra_valid_evidence_ids: frozenset = frozenset()) -> dict:
    reasons = []
    agent_status = "normal_escalation"

    matcher_exception_type = report_row.get("final_exception_type")
    amount_at_risk = _compute_amount_at_risk(report_row)

    # --- authoritative policy lookup: MATCHER's type, never the LLM's ---
    try:
        policy = get_policy(matcher_exception_type)
        policy_permits = policy["auto_resolvable"]
        authoritative_policy_id = policy["policy_id"]
    except KeyError:
        policy_permits = False
        authoritative_policy_id = None
        agent_status = "policy_missing"
        reasons.append(f"no policy found for matcher exception_type "
                        f"'{matcher_exception_type}'")

    # --- reclassification is informational, never authoritative ---
    reclassified = (resolution.exception_type != matcher_exception_type)
    if reclassified:
        reasons.append(f"agent reclassified '{matcher_exception_type}' as "
                        f"'{resolution.exception_type}' -- reclassification is recorded "
                        f"but does NOT change which policy governs this decision")
        if agent_status == "normal_escalation":
            agent_status = "reclassification_overridden"

    # --- policy_id citation must match what the matcher's type actually maps to ---
    policy_id_consistent = (authoritative_policy_id is not None
                             and resolution.policy_id == authoritative_policy_id)
    if not policy_id_consistent and authoritative_policy_id is not None:
        reasons.append(f"agent cited policy_id '{resolution.policy_id}' but "
                        f"'{matcher_exception_type}' maps to '{authoritative_policy_id}' "
                        f"-- citation mismatch, treated as escalation")
        if agent_status == "normal_escalation":
            agent_status = "policy_id_mismatch"

    # --- explicit allowlist: separate from and narrower than policy.auto_resolvable ---
    allowlisted = matcher_exception_type in config.AGENT_AUTO_RESOLVABLE_TYPES
    if not allowlisted:
        reasons.append(f"'{matcher_exception_type}' is not in AGENT_AUTO_RESOLVABLE_TYPES "
                        f"(this deployment's explicit automation allowlist)")

    confidence_ok = resolution.confidence >= config.AUTO_RESOLVE_CONFIDENCE_THRESHOLD
    if not confidence_ok:
        reasons.append(f"confidence {resolution.confidence:.2f} below threshold "
                        f"{config.AUTO_RESOLVE_CONFIDENCE_THRESHOLD}")

    if not resolution.sufficient_evidence:
        reasons.append("agent flagged evidence as insufficient")

    risk_ok = amount_at_risk < config.AUTO_RESOLVE_RISK_CEILING_RUPEES
    if not risk_ok:
        reasons.append(f"amount at risk Rs.{amount_at_risk:,.2f} exceeds risk ceiling "
                        f"Rs.{config.AUTO_RESOLVE_RISK_CEILING_RUPEES:,.2f}")

    auto_resolve = (
        allowlisted and policy_permits and policy_id_consistent
        and confidence_ok and resolution.sufficient_evidence and risk_ok
    )

    if auto_resolve:
        agent_status = "success"
        reasons = ["all gate conditions satisfied (matcher-authoritative type, "
                   "allowlisted, policy_id consistent, confidence/evidence/risk all pass)"]

    # Structured, per-condition PASS/FAIL breakdown -- purely a presentation
    # layer over the six booleans already computed above, not new gate logic.
    # Additive only: `gate_reasons` above (free text) is unchanged and still
    # what run_agent.py's categorize_reason(), test_gate.py, and
    # seed_review_queue.py all depend on. This is a separate field so a UI
    # can show "AI recommendation vs. system decision" as six explicit rows
    # instead of only a pass/fail reason list.
    gate_condition_checks = [
        {
            "name": "Automation allowlist",
            "passed": allowlisted,
            "detail": f"'{matcher_exception_type}' is"
                      f"{'' if allowlisted else ' not'} in AGENT_AUTO_RESOLVABLE_TYPES",
        },
        {
            "name": "Policy permits auto-resolution",
            "passed": policy_permits,
            "detail": (f"no policy found for '{matcher_exception_type}'" if authoritative_policy_id is None
                       else f"policy {authoritative_policy_id} "
                            f"{'permits' if policy_permits else 'does not permit'} auto-resolution"),
        },
        {
            "name": "Policy-ID citation match",
            "passed": policy_id_consistent,
            "detail": (f"agent cited '{resolution.policy_id}', matcher's type maps to "
                       f"'{authoritative_policy_id}'" if authoritative_policy_id is not None
                       else "no authoritative policy_id to compare against"),
        },
        {
            "name": "Confidence threshold",
            "passed": confidence_ok,
            "detail": f"{resolution.confidence:.2f} {'>=' if confidence_ok else '<'} "
                      f"{config.AUTO_RESOLVE_CONFIDENCE_THRESHOLD} required",
        },
        {
            "name": "Sufficient evidence",
            "passed": resolution.sufficient_evidence,
            "detail": f"agent's own sufficient_evidence flag = {resolution.sufficient_evidence}",
        },
        {
            "name": "Amount at risk under ceiling",
            "passed": risk_ok,
            "detail": f"Rs.{amount_at_risk:,.2f} {'<' if risk_ok else '>='} "
                      f"Rs.{config.AUTO_RESOLVE_RISK_CEILING_RUPEES:,.2f} ceiling",
        },
    ]

    # Informational only -- does NOT change auto_resolve above, which stays
    # exactly the 6 conditions it always was. Surfaces when the model cites
    # an evidence field it was never actually shown, so a human reviewer can
    # see a hallucinated citation instead of trusting evidence_used at face
    # value. Turning this into an actual 7th gate condition would be a real,
    # deliberate change to auto-resolve eligibility -- not done here.
    unknown_evidence_citations = validate_evidence_citations(resolution.evidence_used, extra_valid_evidence_ids)

    return {
        "final_decision": "auto_resolve" if auto_resolve else "escalate",
        "agent_status": agent_status,
        "matcher_exception_type": matcher_exception_type,
        "agent_exception_type": resolution.exception_type,
        "reclassified": reclassified,
        "policy_id_consistent": policy_id_consistent,
        "gate_reasons": reasons,
        "gate_condition_checks": gate_condition_checks,
        "amount_at_risk_rupees": round(amount_at_risk, 2),
        "confidence_threshold_used": config.AUTO_RESOLVE_CONFIDENCE_THRESHOLD,
        "risk_ceiling_used": config.AUTO_RESOLVE_RISK_CEILING_RUPEES,
        "unknown_evidence_citations": unknown_evidence_citations,
        "all_evidence_citations_valid": len(unknown_evidence_citations) == 0,
    }
