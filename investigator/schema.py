"""Result schema for the investigation agent.

Deliberately a superset of agent/schema.py's ExceptionResolution -- every
field that schema has, this has too, with the same meaning, so an
InvestigationResult can be handed to agent/gate.py's apply_gate()
completely unchanged. The extra fields (investigation_summary,
investigation_log, drafted_communication) are additive context the
existing gate simply ignores; they don't change what the gate decides,
only what a human reviewer sees about how the verdict was reached.
"""

from typing import Optional

from pydantic import BaseModel, Field


class ToolCallRecord(BaseModel):
    step: int
    tool_name: str
    arguments: dict
    result: dict


class InvestigationResult(BaseModel):
    exception_type: str
    policy_id: str
    root_cause: str
    evidence_used: list[str] = Field(default_factory=list)
    recommended_action: str
    confidence: float = Field(ge=0.0, le=1.0)
    sufficient_evidence: bool

    investigation_summary: str = ""
    drafted_communication: Optional[str] = None
    investigation_log: list[ToolCallRecord] = Field(default_factory=list)
    tool_rounds_used: int = 0
    stopped_reason: str = "model_finished"  # or "max_rounds_exceeded"
    elapsed_seconds: float = 0.0  # total wall-clock for investigate(), all rounds + final answer
