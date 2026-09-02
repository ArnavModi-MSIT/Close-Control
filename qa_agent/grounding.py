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

The extraction/tolerance logic itself lives in agent/evidence.py, shared
with the equivalent check on investigator/'s own root_cause /
drafted_communication free text -- this module is a thin wrapper binding
that shared check to this agent's own tolerance config and result schema.
extract_numbers / collect_grounded_numbers are re-exported here (not just
called internally) for backward compatibility with this module's own
public API.
"""

from agent.evidence import (
    check_numeric_grounding,
    collect_grounded_numbers,  # noqa: F401 -- re-exported, part of this module's public API
    extract_numbers,  # noqa: F401 -- re-exported, part of this module's public API
)
from . import config
from .schema import GroundingCheck


def check_grounding(answer_text: str, tool_log) -> GroundingCheck:
    """The public entry point: does every number in `answer_text` trace
    back to something a real tool call actually returned in `tool_log`,
    either directly or as a simple sum/difference of two real values?"""
    result = check_numeric_grounding(
        answer_text, tool_log,
        tol_rupees=config.GROUNDING_TOLERANCE_RUPEES,
        tol_pct=config.GROUNDING_TOLERANCE_PCT,
    )
    return GroundingCheck(**result)
