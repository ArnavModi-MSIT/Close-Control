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
# contradict the decision the gate actually reached? (e.g. an auto-resolving
# case whose root_cause text reads like an escalation, or vice versa).
#
# A naive single-word contradiction list does NOT transfer cleanly to this project's
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


# Internal-jargon leakage guard for investigator/'s drafted_communication --
# "a ready-to-send draft" (investigator/ollama_client.py's own prompt
# wording) for contacting the bank or treasury ops, i.e. genuinely external-
# facing text, unlike root_cause/evidence_used which stay inside this
# system. A document sent to an external financial-institution contact
# must never expose the machinery that produced it -- a fluent draft that
# leaks internal decision-state vocabulary ("confidence score", "policy
# gate") looks unprofessional at best and confusing at worst to a
# recipient who has no idea what "POLICY-009" or "risk_class" means.
#
# Checked against every real drafted_communication in data/investigation_log.jsonl
# (211 non-null real drafts) before adopting this: NOT hypothetical -- 55 of
# 211 already cite a raw POLICY-### id, and one draft (trn-000098) reads
# "Escalate for refund per POLICY-009. No auto-resolution permitted." Word-
# boundary regex used for short/ambiguous tokens like "gate" specifically
# because a naive substring check would false-positive on
# "investigate"/"gateway", both of which appear routinely in real drafts --
# verified zero real standalone "gate" matches exist, so this guard doesn't
# need to loosen anything to stay clean, only avoid a self-inflicted
# false-positive risk.
import re as _re

_LEAKAGE_SUBSTRING_PHRASES = (
    "auto-resolution", "auto-resolve", "auto_resolve", "confidence score",
    "risk_class", "risk class", "exception_type", "exception type",
    "sufficient_evidence", "as an ai", "language model", "i cannot", "i'm unable",
)
_LEAKAGE_WORD_BOUNDARY_PATTERNS = (
    _re.compile(r"\bgate\b", _re.IGNORECASE),
    _re.compile(r"\bthreshold\b", _re.IGNORECASE),
    _re.compile(r"\bPOLICY-\d+\b", _re.IGNORECASE),
)


def check_communication_leakage(drafted_communication: str) -> list[str]:
    """Returns any internal-decision-machinery phrases found in a drafted,
    externally-facing communication -- a bank/treasury contact has no
    context for 'POLICY-009' or 'risk_class'. Empty list means clean, not
    proof the prose is otherwise appropriate. Informational only, same as
    check_root_cause_contradiction() -- nothing currently blocks a case on
    this, it's a signal for a human reviewing the draft before sending it."""
    if not drafted_communication:
        return []
    text = drafted_communication.lower()
    flags = [p for p in _LEAKAGE_SUBSTRING_PHRASES if p in text]
    for pat in _LEAKAGE_WORD_BOUNDARY_PATTERNS:
        m = pat.search(drafted_communication)
        if m:
            flags.append(m.group(0).lower())
    return flags


# Numeric-grounding check: does every number the model states in a free-text
# field actually trace back to a real number it was shown or a tool call
# actually returned? Same "the LLM never touches a number" principle this
# gate already enforces on citations and policy IDs, applied to prose --
# a free-text narrative has nowhere as clean a place to enforce it as a
# structured field does, so this checks the text after the fact, the same
# way validate_evidence_citations() checks a citation list after the fact
# rather than constraining generation directly.
#
# The single shared implementation -- qa_agent/grounding.py wraps this for
# its own Q&A-answer surface rather than keeping an independently
# -maintained copy. Public names (extract_numbers, collect_grounded_numbers,
# check_numeric_grounding) are kept stable since qa_agent/grounding.py and
# its own tests import them directly.
#
# The leading `-?` inside the capture group (not before it) was added after
# the same real-data sweep: "= -8,660.31)" was extracting only "660.31" --
# the lookbehind correctly refuses to start a match AT the "-" preceded by
# a digit-excluded char, but a naive minus-then-digit split leaves the
# comma-grouped run's own leading digit stranded on the wrong side of the
# sign. Consuming the sign as part of the token (only once, only when a
# digit immediately follows it) fixes that without weakening the
# trn-000237-style identifier exclusion at all: a hyphen directly between
# two digit runs with no space (e.g. "617-154") still can't start a match
# on either side, since the lookbehind/lookahead still treat that shape as
# one opaque token, exactly as before this change.
_NUMBER_RE = _re.compile(
    r"(?<![A-Za-z0-9_./-])"
    r"(?:₹|Rs\.?\s?)?"
    r"(-?\d[\d,]*(?:\.\d+)?)"
    r"%?"
    r"(?![A-Za-z0-9_/-])"
)


def extract_numbers(text: str) -> list[float]:
    """Every standalone numeric literal in `text`, comma-grouping and
    currency prefixes stripped. Not claimed to be perfect (a bare year like
    "2026" parses as a number too), which is fine -- this check is
    informational, not a hard gate, so false positives on incidental
    numbers are an acceptable cost for catching genuinely invented
    figures."""
    numbers = []
    for m in _NUMBER_RE.finditer(text or ""):
        raw = m.group(1).replace(",", "")
        try:
            numbers.append(float(raw))
        except ValueError:
            continue
    return numbers


_ISO_DATE_RE = _re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _walk_numbers(obj) -> list[float]:
    """Recursively pulls every real number out of a tool result -- dicts,
    lists, and numeric-looking strings all searched, since a tool result is
    arbitrary JSON-shaped data, not a flat record.

    A string value additionally gets its ISO-date components (year/month/
    day) pulled out separately from extract_numbers()'s own identifier
    -safe scan. Found via a real false-positive sweep of every logged
    investigation, not assumed: search_bank_statement() legitimately
    returns dates as "2026-07-14" -- extract_numbers() correctly treats
    that as an opaque token (the same hyphen-exclusion rule that keeps it
    from extracting "000237" out of "trn-000237"), so a tool result's own
    real date never became a grounded number on its own, even though the
    model's prose restating it as "July 14, 2026" is completely faithful.
    This only widens what a TOOL RESULT can ground -- extract_numbers()
    itself, used on the model's own claimed text, is untouched, so a
    transaction id in the model's own prose still can't smuggle a number
    through."""
    if isinstance(obj, bool):
        return []  # bool is an int subclass in Python; never a real "claimed number"
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, dict):
        out = []
        for v in obj.values():
            out.extend(_walk_numbers(v))
        return out
    if isinstance(obj, list):
        out = []
        for v in obj:
            out.extend(_walk_numbers(v))
        return out
    if isinstance(obj, str):
        numbers = extract_numbers(obj)
        for m in _ISO_DATE_RE.finditer(obj):
            year, month, day = m.groups()
            numbers.extend([float(year), float(int(month)), float(int(day))])
        return numbers
    return []


def _tool_log_result(record):
    """A tool_log entry may be a real ToolCallRecord (investigator/qa_agent's
    own pydantic object -- .result attribute) or a plain dict reconstructed
    from raw JSONL (seed_review_queue.py's SimpleNamespace recompute path,
    which never has a real InvestigationResult to work from) -- accept
    either rather than forcing every caller to normalize first."""
    if isinstance(record, dict):
        return record.get("result")
    return getattr(record, "result", None)


def collect_grounded_numbers(tool_log) -> set:
    """Every number that genuinely appeared in a real tool call result
    during this conversation/investigation -- the ground truth a claim is
    checked against."""
    grounded = set()
    for record in tool_log or []:
        for n in _walk_numbers(_tool_log_result(record)):
            grounded.add(round(n, 4))
    return grounded


def _pairwise_derived_numbers(grounded: set) -> set:
    """Every sum and absolute difference of two distinct grounded numbers --
    a legitimately-derived figure (e.g. "497 total, 20 shown, 477
    remaining") must not be flagged just because it never appears as a
    literal value in any single tool result. Deliberately ONLY sum and
    difference, not products or ratios: those would open real room for a
    genuinely fabricated number to coincidentally match some combination."""
    values = list(grounded)
    derived = set()
    for i, a in enumerate(values):
        for b in values[i + 1:]:
            derived.add(round(a + b, 4))
            derived.add(round(abs(a - b), 4))
    return derived


def _is_grounded(claimed: float, grounded: set, tol_rupees: float, tol_pct: float) -> bool:
    tol = max(tol_rupees, abs(claimed) * tol_pct)
    return any(abs(claimed - g) <= tol for g in grounded)


def check_numeric_grounding(text: str, tool_log, *, extra_grounded_numbers=None,
                             tol_rupees: float = 1.00, tol_pct: float = 0.005) -> dict:
    """Does every number in `text` trace back to something a real tool call
    actually returned in `tool_log`, a number in `extra_grounded_numbers`,
    or a simple sum/difference of two grounded values? Returns
    {claimed_numbers, ungrounded_numbers, all_grounded} -- informational
    only, the same "flag, don't hide" contract as
    validate_evidence_citations() before its own promotion to a hard gate
    condition. tol_rupees/tol_pct default to a flat rupee floor OR a
    relative percentage, whichever is larger -- generous enough that
    restating "Rs.5,54,613" for a real "Rs.5,54,612.74" isn't flagged,
    tight enough that an invented six-figure sum still is.

    extra_grounded_numbers: numbers real but NOT sourced from a tool call --
    e.g. investigator/'s own initial evidence block (observed_net_rupees
    etc.), which the model legitimately sees and cites before it ever calls
    a tool. Found via the same real-data sweep that motivated the ISO-date
    fix above: an investigation's "observed" figure almost always comes
    from that static block, never a tool result (there is no "get me the
    observed amount" tool separate from it), so without this every
    faithful restatement of it read as fabricated."""
    if not text:
        return {"claimed_numbers": [], "ungrounded_numbers": [], "all_grounded": True}
    claimed = extract_numbers(text)
    grounded_set = collect_grounded_numbers(tool_log)
    if extra_grounded_numbers:
        grounded_set = grounded_set | {round(float(n), 4) for n in extra_grounded_numbers}
    # extract_numbers() never captures a leading minus sign (prose says
    # "reduced by Rs.8,660.31", not "Rs.-8,660.31"), but a real grounded
    # figure like net_delta_rupees is routinely negative (a shortfall) --
    # found via the same real-data sweep, a genuinely faithful restatement
    # of a negative delta's magnitude was being flagged as fabricated
    # purely because of the sign convention mismatch. Adding the absolute
    # value alongside every grounded number (not instead of it) fixes that
    # without loosening what counts as a match otherwise.
    grounded_set = grounded_set | {abs(g) for g in grounded_set}
    checkable = grounded_set | _pairwise_derived_numbers(grounded_set)
    ungrounded = [c for c in claimed if not _is_grounded(c, checkable, tol_rupees, tol_pct)]
    return {
        "claimed_numbers": claimed,
        "ungrounded_numbers": ungrounded,
        "all_grounded": len(ungrounded) == 0,
    }


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
