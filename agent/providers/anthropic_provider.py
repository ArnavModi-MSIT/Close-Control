"""Anthropic provider. Only used if LLM_PROVIDER=anthropic is explicitly
set -- never the default, since it's the only paid option here. Uses
Claude's native structured outputs (constrained decoding), so no manual
JSON parsing or retry-on-malformed-output is needed, unlike the Groq path.
"""

from .base import LLMProvider
from ..schema import ExceptionResolution


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, max_tokens: int):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens

    def resolve(self, system_prompt: str, user_message: str) -> ExceptionResolution:
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key)

        try:
            response = client.messages.parse(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_message}],
                output_format=ExceptionResolution,
            )
        except Exception as e:
            print(f"  [ANTHROPIC CALL FAILED] {type(e).__name__}: {e}")
            return ExceptionResolution(
                exception_type="unknown", policy_id="NONE",
                root_cause=f"[AGENT CALL FAILED: {e}] Escalating without agent reasoning.",
                evidence_used=[], recommended_action="Escalate for manual review -- agent call failed.",
                confidence=0.0, sufficient_evidence=False,
            )

        if response.stop_reason == "refusal":
            return ExceptionResolution(
                exception_type="unknown", policy_id="NONE",
                root_cause="[AGENT REFUSED] Model declined to respond.",
                evidence_used=[], recommended_action="Escalate for manual review -- agent refused.",
                confidence=0.0, sufficient_evidence=False,
            )

        return response.parsed_output
