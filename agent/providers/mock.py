"""Deterministic, zero-cost mock provider. No network call. Used for
development, tests, and as the default so nothing ever accidentally
costs money."""

from .base import LLMProvider
from ..schema import ExceptionResolution
from ..policy_kb import get_policy


class MockProvider(LLMProvider):
    name = "mock"
    model = "mock"

    def resolve(self, system_prompt: str, user_message: str) -> ExceptionResolution:
        # pull exception_type back out of the evidence block (EVIDENCE-4 line)
        exc_type = "unknown"
        for line in user_message.splitlines():
            if line.startswith("[EVIDENCE-4]"):
                exc_type = line.split(":", 1)[1].strip()
                break

        try:
            policy = get_policy(exc_type)
        except KeyError:
            policy = {"policy_id": "NONE", "resolution_action": "Escalate -- no matching policy.",
                      "auto_resolvable": False}

        return ExceptionResolution(
            exception_type=exc_type,
            policy_id=policy["policy_id"],
            root_cause=f"[MOCK PROVIDER -- not a real LLM response] Deterministic "
                       f"policy-KB lookup for exception_type={exc_type}.",
            evidence_used=["final_exception_type"],
            recommended_action=policy["resolution_action"],
            confidence=0.5,
            sufficient_evidence=False,
        )
