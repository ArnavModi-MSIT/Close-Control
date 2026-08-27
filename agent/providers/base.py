"""Provider interface. Every backend (mock, Groq, ...) implements resolve()
with this exact signature, so the rest of the agent code never needs to
know which one is active."""

from abc import ABC, abstractmethod

from ..schema import ExceptionResolution


class LLMProvider(ABC):
    name: str = "unknown"
    model: str = "unknown"

    @abstractmethod
    def resolve(self, system_prompt: str, user_message: str) -> ExceptionResolution:
        """Return a validated ExceptionResolution. Must never raise for a
        normal API/parse failure -- catch internally and return a low-
        confidence, sufficient_evidence=False result with the error
        recorded in root_cause instead. Only truly unexpected errors
        (e.g. missing API key) should propagate."""
        raise NotImplementedError
