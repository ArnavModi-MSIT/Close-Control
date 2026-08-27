"""Structured output schema for the Exception Resolution Agent.

This Pydantic model is the canonical, provider-neutral application-level
schema. Providers give it different levels of enforcement -- Anthropic's
structured outputs guarantee schema compliance via constrained decoding
(no manual parsing needed there); Groq and Ollama use JSON mode, which is
a weaker guarantee, so their providers validate the response against this
same model and retry once on failure. Either way, nothing downstream ever
sees an unvalidated response.

policy_id is a REQUIRED field, not optional -- this is the grounded-
generation citation requirement: it's structurally impossible for the
agent to propose a resolution without citing which policy rule it applied.
Note: the schema does not by itself guarantee policy_id/exception_type
consistency or that exception_type is a known type -- that's enforced by
the gate (agent/gate.py), which treats a mismatch as a hard escalation.
"""

from pydantic import BaseModel, Field


class ExceptionResolution(BaseModel):
    exception_type: str = Field(
        description="The exception type, confirmed or reclassified from the evidence. "
                    "Must be one of the known types in the policy KB.")
    policy_id: str = Field(
        description="The POLICY-### ID of the rule this resolution is grounded in. "
                    "Required -- every resolution must cite a specific policy.")
    root_cause: str = Field(
        description="Plain-language explanation of what happened, citing SPECIFIC evidence "
                    "values provided (amounts, deltas, dates) -- not a generic description.")
    evidence_used: list[str] = Field(
        description="Which specific evidence fields were used to reach this conclusion, "
                    "e.g. ['net_delta_rupees', 'risk_class'].")
    recommended_action: str = Field(
        description="The specific action to take, per the cited policy's resolution_action.")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence this classification and action are correct, 0.0-1.0. "
                    "Should be LOW if the evidence is ambiguous or the policy doesn't "
                    "clearly cover this exact case.")
    sufficient_evidence: bool = Field(
        description="True only if the provided evidence is actually sufficient to support "
                    "this conclusion. If the policy doesn't clearly cover this case, or "
                    "evidence is missing/contradictory, set this False and explain why in "
                    "root_cause instead of guessing.")
