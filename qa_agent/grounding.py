"""Numeric-grounding check for the Q&A agent's final answer.

The single safety property this whole module exists to enforce: never let
the model state a rupee amount, count, or percentage in its free-text
answer that didn't actually come from a real tool result. Same "the LLM
never touches a number" principle this project enforces everywhere else
(agent/gate.py, investigator/tools.py's compute_delta), applied to a new
surface -- a free-text NARRATIVE answer has nowhere as clean a place to
enforce it as a structured field does, so this checks the answer text
itself after the fact, the same way agent/evidence.py's
validate_evidence_citations() checks a citation list after the fact
rather than constraining generation directly.

Implemented informationally (surfaced to the reviewer, same as
unknown_evidence_citations) rather than as a hard reject -- consistent
with this project's "escalate/flag, don't silently drop" discipline
elsewhere.
"""

import re

from . import config
from .schema import GroundingCheck

# Matches a number (optionally rupee-prefixed, optionally comma-grouped,
# optionally with a decimal part, optionally percent-suffixed) that isn't
# itself part of a larger identifier token -- the lookbehind keeps this
# from firing inside something like "trn-000237" (blocked by the "-"
# immediately before the digits) while still catching bare figures like
# "617" or "70.2%".
#
# The trailing lookahead deliberately does NOT exclude "." (unlike the
# leading lookbehind, which still does) -- found via a real live-Ollama
# answer, not assumed: "...at risk is Rs.554,612.74." has the real number
# immediately followed by a sentence-ending period. With "." excluded on
# the trailing side, the greedy (?:\.\d+)? group's own match attempt at
# "554,612.74" failed the lookahead (next char after a full match is that
# same trailing "."), forcing the engine to backtrack all the way down to
# "554" -- the shortest prefix followed by a non-excluded character (the
# comma, which was never excluded either). That produced a real false
# "ungrounded" flag on a genuinely grounded number. A period ending a
# sentence right after a number is completely ordinary prose, not
# evidence the digits before it are part of some larger identifier, so it
# does not belong in this side's exclusion set.
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])"
    r"(?:₹|Rs\.?\s?)?"
    r"(\d[\d,]*(?:\.\d+)?)"
    r"%?"
    r"(?![A-Za-z0-9_/-])"
)


def extract_numbers(text: str) -> list[float]:
    """Every standalone numeric literal in `text`, comma-grouping and
    currency prefixes stripped. Not claimed to be perfect (a bare year
    like "2026" parses as a number too), which is fine -- this check is
    informational, not a hard gate, so false positives on incidental
    numbers are an acceptable cost for catching genuinely invented
    figures."""
    numbers = []
    for m in _NUMBER_RE.finditer(text):
        raw = m.group(1).replace(",", "")
        try:
            numbers.append(float(raw))
        except ValueError:
            continue
    return numbers


def _walk_numbers(obj) -> list[float]:
    """Recursively pull every real number out of a tool result -- dicts,
    lists, and numeric-looking strings all searched, since a tool result
    is arbitrary JSON-shaped data, not a flat record."""
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
        return extract_numbers(obj)
    return []


def collect_grounded_numbers(tool_log) -> set:
    """Every number that genuinely appeared in a real tool call result
    during this conversation -- the ground truth this answer is checked
    against. tool_log is a list of ToolCallRecord (or anything with a
    `.result` dict attribute)."""
    grounded = set()
    for record in tool_log:
        for n in _walk_numbers(record.result):
            grounded.add(round(n, 4))
    return grounded


def _pairwise_derived_numbers(grounded: set) -> set:
    """Every sum and absolute difference of two distinct grounded numbers.

    Found via a real live answer, not assumed: search_cases() returns
    {"total_matches": 497, "returned_count": 20, ...}, and a routine
    "497 total, 20 shown, the remaining 477 are truncated" answer states
    477 (= 497 - 20) -- a correct, simple derivation from two genuinely
    grounded numbers, never invented, but 477 itself never appears as a
    literal value anywhere in the tool result, so it was flagged
    "ungrounded." That's exactly the "must not cry wolf on real evidence"
    failure mode this project already fixed once for evidence citations
    (agent/evidence.py) -- same principle, applied here.

    Deliberately ONLY sum and difference, not products or ratios: those
    would open real room for a genuinely fabricated number to coincidentally
    match some combination, while a plain total-minus-shown or two-part-sum
    is what a Q&A answer routinely and legitimately needs to state. Pairwise
    over a tool-result-sized set (at most a few dozen real numbers) is
    trivially cheap -- not run over the derived set itself, so this never
    recurses into three-or-more-way combinations."""
    values = list(grounded)
    derived = set()
    for i, a in enumerate(values):
        for b in values[i + 1:]:
            derived.add(round(a + b, 4))
            derived.add(round(abs(a - b), 4))
    return derived


def _is_grounded(claimed: float, grounded: set) -> bool:
    # Same tolerance shape as cash_position.config's reconciliation-tie
    # check: a flat rupee floor OR a relative percentage, whichever is
    # larger -- generous enough that the model restating "Rs.5,54,613"
    # for a real "Rs.5,54,612.74" isn't flagged, tight enough that an
    # invented six-figure sum still is.
    tol = max(config.GROUNDING_TOLERANCE_RUPEES, abs(claimed) * config.GROUNDING_TOLERANCE_PCT)
    return any(abs(claimed - g) <= tol for g in grounded)


def check_grounding(answer_text: str, tool_log) -> GroundingCheck:
    """The public entry point: does every number in `answer_text` trace
    back to something a real tool call actually returned in `tool_log`,
    either directly or as a simple sum/difference of two real values?"""
    claimed = extract_numbers(answer_text)
    grounded_set = collect_grounded_numbers(tool_log)
    checkable = grounded_set | _pairwise_derived_numbers(grounded_set)
    ungrounded = [c for c in claimed if not _is_grounded(c, checkable)]
    return GroundingCheck(
        claimed_numbers=claimed,
        ungrounded_numbers=ungrounded,
        all_grounded=len(ungrounded) == 0,
    )
