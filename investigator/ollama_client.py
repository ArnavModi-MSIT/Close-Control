"""Thin wrapper over Ollama's /api/chat for tool-calling rounds, plus one
final structured-output call once the model is done investigating.

Deliberately two separate call shapes rather than one: tool-calling and
format="json" aren't combined in the same request here, on purpose --
investigation rounds ask "what do you want to check next," the final
round asks "given everything you found, what's your structured verdict."
Mixing the two makes it much harder to tell whether a bad response is a
tool-schema problem or a JSON-shape problem.
"""

import json

import requests

from . import config

FINAL_SCHEMA_INSTRUCTION = """
You have finished investigating. Respond with ONLY a JSON object (no markdown
fences, no other text, no further tool calls) with exactly these fields:
{
  "exception_type": "<string>",
  "policy_id": "<string, e.g. POLICY-004>",
  "root_cause": "<string, cite specific evidence AND investigation findings>",
  "evidence_used": ["<string>", ...],
  "recommended_action": "<string>",
  "confidence": <float 0.0-1.0>,
  "sufficient_evidence": <true or false>,
  "investigation_summary": "<1-2 sentences on what you checked and why it changed or confirmed your view>",
  "drafted_communication": "<if the recommended action involves contacting the bank or treasury ops, a ready-to-send draft; otherwise null>"
}
"""


class OllamaToolClient:
    def __init__(self, model: str = config.INVESTIGATOR_MODEL, host: str = config.OLLAMA_HOST,
                 think: bool | None = config.THINK_MODE):
        self.model = model
        self.host = host.rstrip("/")
        # None = don't send the field at all, i.e. whatever Ollama/the model
        # defaults to for this model. Explicit True/False lets a caller A/B a
        # reasoning model's "thinking" tokens against real elapsed-time/
        # quality data instead of guessing -- see investigator/config.py's
        # THINK_MODE default (False) for the measured 4x-speedup evidence.
        self.think = think

    def chat_with_tools(self, messages: list, tools: list) -> dict:
        """One round: send the conversation so far + available tools.
        Returns the raw assistant message dict (may contain tool_calls,
        may contain a plain content string, or both depending on model)."""
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": {"temperature": 0.1},
        }
        if self.think is not None:
            payload["think"] = self.think
        resp = requests.post(f"{self.host}/api/chat", json=payload, timeout=300)
        resp.raise_for_status()
        return resp.json()["message"]

    def _final_answer_call(self, messages: list) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.1},
        }
        if self.think is not None:
            payload["think"] = self.think
        resp = requests.post(f"{self.host}/api/chat", json=payload, timeout=300)
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    def final_answer(self, messages: list, schema_instruction: str = FINAL_SCHEMA_INSTRUCTION) -> dict:
        """Last round: no tools, force a structured JSON verdict. Retries
        once on malformed JSON, feeding the parse error back to the model --
        same defensive pattern agent/providers/ollama.py's resolve() and
        groq.py already use for the single-shot agent path. Was previously a
        single json.loads() with no retry at all; loop.py's own
        `except Exception` around this call caught a JSONDecodeError but only
        to immediately fall back to a zero-confidence escalation, no recovery
        attempt. The investigator deliberately runs a SMALLER model than the
        single-shot path (qwen3:1.7b, chosen for speed over qwen3:8b) -- if
        anything it benefits MORE from a retry, not less. A second failure
        still propagates to loop.py's existing except Exception, which
        already produces a safe escalation default -- no need to duplicate
        that fallback here. Found via external review.

        schema_instruction defaults to this module's own investigator-shaped
        FINAL_SCHEMA_INSTRUCTION (every existing investigator/ call site is
        unaffected), but is now a parameter so a different caller can ask
        for a differently-shaped final answer through the exact same
        tool-calling client -- see qa_agent/loop.py, which reuses this
        class entirely rather than duplicating the HTTP/retry logic for a
        second time."""
        base_messages = messages + [{"role": "user", "content": schema_instruction}]
        content = self._final_answer_call(base_messages)
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            retry_messages = base_messages + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"Your previous response was not valid JSON: {e}. "
                                             f"Return ONLY the corrected JSON object, nothing else."},
            ]
            content2 = self._final_answer_call(retry_messages)
            return json.loads(content2)
