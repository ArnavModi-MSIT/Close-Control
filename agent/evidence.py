"""Builds the grounded evidence block the agent sees. The LLM only ever
sees these specific, pre-computed numbers -- never raw dataframes, never
asked to do arithmetic itself. Every field here is real, computed by
deterministic code upstream (matcher/ledger_check), not invented."""

# The exact report_row fields ever shown to the model in build_evidence()'s
# block below -- kept as its own list (not derived from the f-string) so
# validate_evidence_citations() has a single, explicit source of truth for
# "was this actually shown to the LLM," distinct from seed_review_queue.py's
# _build_evidence_fields_cited(), which checks a broader question ("is this
# a real report_row field at all") for display purposes, not citation honesty.
_KNOWN_EVIDENCE_FIELD_NAMES = {
    "transaction_id", "merchant_id", "settlement_id", "final_exception_type",
    "all_signals", "risk_class", "match_status", "match_pass",
    "ledger_expected_net_rupees", "observed_net_rupees", "net_delta_rupees",
}

# build_evidence() below labels every line it shows the model as [EVIDENCE-N]
# -- both GENERAL_INSTRUCTIONS prompts (agent/client.py's and, explicitly,
# investigator/loop.py's "cite ... EVIDENCE-N fields you used") point the
# model at citing THAT label, not the underlying field name. Found via a
# real live investigation (qwen3:1.7b on trn-000001): every one of its 5
# genuine EVIDENCE-N citations was being flagged as "unknown" because
# KNOWN_EVIDENCE_FIELDS only ever held raw field names -- a real correctness
# gap, not a theoretical one. EVIDENCE_LABEL_COUNT=10 matches
# build_evidence()'s own line count exactly (kept adjacent to it below so a
# future added/removed line is an obvious paired edit); the raw field names
# are kept too since the mock provider (agent/providers/mock.py) cites
# 'final_exception_type' directly, a legitimate second convention.
EVIDENCE_LABEL_COUNT = 10
KNOWN_EVIDENCE_FIELDS = _KNOWN_EVIDENCE_FIELD_NAMES | {f"EVIDENCE-{i}" for i in range(1, EVIDENCE_LABEL_COUNT + 1)}

# Which report_row field each [EVIDENCE-N] label actually shows -- the
# single source of truth for mapping a citation label back to a real,
# displayable value. Kept in exact sync with build_evidence()'s line order
# below (EVIDENCE-7 shows two fields on one line; match_status is the
# primary one). Used by seed_review_queue.py's _build_evidence_fields_cited()
# to resolve a citation like "EVIDENCE-4" to its real value for the review
# UI -- found necessary via a live browser check (trn-000072) that showed
# EVERY citation, including genuinely valid ones, as "(not a known evidence
# field)": that helper's old `if name in report_row` lookup could never
# match a label string like "EVIDENCE-4" against report_row's real keys
# like "final_exception_type", regardless of citation validity.
EVIDENCE_LABEL_TO_FIELD = {
    "EVIDENCE-1": "transaction_id",
    "EVIDENCE-2": "merchant_id",
    "EVIDENCE-3": "settlement_id",
    "EVIDENCE-4": "final_exception_type",
    "EVIDENCE-5": "all_signals",
    "EVIDENCE-6": "risk_class",
    "EVIDENCE-7": "match_status",
    "EVIDENCE-8": "ledger_expected_net_rupees",
    "EVIDENCE-9": "observed_net_rupees",
    "EVIDENCE-10": "net_delta_rupees",
}


# Explanation-faithfulness check: does the model's own free-text root_cause
# contradict the decision the gate actually reached? Idea sharpened by
# checking a peer Razorpay buildathon repo (SuryaSK-dev/razorpay-ai-finance-
# controller) past its README into its actual src/agent/explanation_validator.py,
# which rejects an LLM explanation that uses language contradicting its own
# verified status (e.g. a "MATCH" explanation that says "review"/"rejected").
#
# A naive port of that word list does NOT transfer cleanly to this project's
# domain: build_evidence() shows the model real fields named match_status/
# match_pass, so real root_cause text routinely and legitimately contains
# "matched" (e.g. "Match status is 'matched (via pass: exact)'") -- a short
# single-word list would false-positive constantly on our own evidence
# vocabulary, not the model's decision language. Phrases below are
# deliberately multi-word and decision-level rather than evidence-level,
# and were verified against every real root_cause in data/audit_log.jsonl
# and data/investigation_log.jsonl (1,018 real entries, both escalate and
# auto_resolve) before being adopted: zero false positives.
#
# Deliberately informational only, same as unknown_evidence_citations was
# before its own real-data verification pass promoted it to a hard gate
# condition (see agent/gate.py's module docstring) -- this one hasn't had
# that same scrutiny yet, so it's surfaced to a human reviewer, not wired
# into auto_resolve.
_ESCALATION_CONTRADICTING_PHRASES = (
    "no further action needed", "no further action required", "fully resolved",
    "issue resolved", "matter resolved", "case resolved", "reconciliation complete",
    "nothing further to investigate", "safe to close", "safe to auto-resolve",
)
_AUTO_RESOLVE_CONTRADICTING_PHRASES = (
    "requires manual review", "requires human review", "needs manual intervention",
    "cannot be automatically resolved", "should be escalated", "escalate this case",
    "insufficient evidence to resolve", "needs further investigation",
)


def check_root_cause_contradiction(root_cause: str, gate_decision: str) -> list[str]:
    """Returns any decision-contradicting phrases found in root_cause, given
    the gate's own final_decision ('escalate' or 'auto_resolve'). Empty list
    means no contradiction detected -- not proof the text is fully faithful,
    just that it doesn't contain a known red flag."""
    if not root_cause or gate_decision not in ("escalate", "auto_resolve"):
        return []
    text = root_cause.lower()
    phrases = (_ESCALATION_CONTRADICTING_PHRASES if gate_decision == "escalate"
               else _AUTO_RESOLVE_CONTRADICTING_PHRASES)
    return [p for p in phrases if p in text]


def validate_evidence_citations(evidence_used: list[str], extra_valid_ids: frozenset = frozenset()) -> list[str]:
    """Returns whichever cited names in evidence_used do NOT correspond to a
    field actually shown in the evidence block -- i.e. the model citing
    something it was never given. This list IS a hard gate condition now
    (agent/gate.py's 7th: any name returned here blocks auto_resolve, not
    merely a human-visible flag) -- see gate.py's own module docstring for
    why. Still returned as a plain list rather than a bool so a human
    reviewer sees exactly WHICH citation was fabricated, not just that one
    was.

    extra_valid_ids: for investigator/ results only -- TOOL-1, TOOL-2, etc.,
    one per real investigation_log entry for that specific case. These are
    dynamic (per-investigation, not static like KNOWN_EVIDENCE_FIELDS), so
    the caller computes and passes them; agent/'s single-shot path never
    passes any (default empty), so its validation is unchanged."""
    valid = KNOWN_EVIDENCE_FIELDS | extra_valid_ids
    return [name for name in evidence_used if name not in valid]


def build_evidence(report_row: dict) -> str:
    """report_row is one row from matching.report.build_report()'s output
    (as a dict). Returns a labeled evidence block for the prompt."""
    lines = [
        f"[EVIDENCE-1] transaction_id: {report_row.get('transaction_id')}",
        f"[EVIDENCE-2] merchant_id: {report_row.get('merchant_id')}",
        f"[EVIDENCE-3] settlement_id: {report_row.get('settlement_id')}",
        f"[EVIDENCE-4] matcher-detected exception_type: {report_row.get('final_exception_type')}",
        f"[EVIDENCE-5] all detected signals (may include multiple co-occurring issues): "
        f"{report_row.get('all_signals')}",
        f"[EVIDENCE-6] risk_class (deterministic): {report_row.get('risk_class')}",
        f"[EVIDENCE-7] settlement match status: {report_row.get('match_status')} "
        f"(via pass: {report_row.get('match_pass')})",
        f"[EVIDENCE-8] ledger expected net amount (rupees): {report_row.get('ledger_expected_net_rupees')}",
        f"[EVIDENCE-9] gateway observed net amount (rupees): {report_row.get('observed_net_rupees')}",
        f"[EVIDENCE-10] net delta (observed - expected, rupees): {report_row.get('net_delta_rupees')}",
    ]
    return "\n".join(lines)


def build_policy_block(policy: dict, policy_id: str) -> str:
    return (
        f"[{policy_id}] {policy['description']}\n"
        f"  Typical cause: {policy['typical_cause']}\n"
        f"  Resolution action: {policy['resolution_action']}\n"
        f"  Policy-permitted auto-resolvable: {policy['auto_resolvable']}\n"
        f"  Policy risk class: {policy['risk_class']}"
    )
