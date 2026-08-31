"""Result schema for the Settlement Q&A agent."""

from typing import Optional

from pydantic import BaseModel, Field

from investigator.schema import ToolCallRecord  # same shape, no reason to redefine it


class GroundingCheck(BaseModel):
    """Output of qa_agent/grounding.py's check_grounding() -- surfaced to the
    reviewer, never a hard block on the answer itself. Same "informational,
    not gating" contract as agent/evidence.py's unknown_evidence_citations:
    a human decides what to do with an ungrounded claim, the system never
    silently hides the answer just because part of it couldn't be verified."""
    claimed_numbers: list[float] = Field(default_factory=list)
    ungrounded_numbers: list[float] = Field(default_factory=list)
    all_grounded: bool = True


class QAResult(BaseModel):
    question: str
    answer: str
    citations: list[str] = Field(default_factory=list)
    grounding: GroundingCheck

    tool_log: list[ToolCallRecord] = Field(default_factory=list)
    tool_rounds_used: int = 0
    stopped_reason: str = "model_finished"
    elapsed_seconds: float = 0.0
    model: Optional[str] = None
