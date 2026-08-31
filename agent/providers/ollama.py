"""Ollama provider -- runs entirely on your own machine, no API key, no
network call, no rate limit, no billing. The most reliable option for a
live demo: nothing can fail due to venue wifi or a third-party outage.

Requires Ollama installed and running locally (https://ollama.com), with
a model pulled, e.g.:
    ollama pull llama3.1:8b

Uses Ollama's native /api/chat endpoint with format="json" for structured
output. Like Groq, this isn't constrained decoding (no hard schema
guarantee), so responses are validated with Pydantic and retried once on
failure, same defensive pattern as the Groq provider.
"""

import json

import requests
from pydantic import ValidationError

from .base import LLMProvider
from ..schema import ExceptionResolution

SCHEMA_INSTRUCTION = """
Respond with ONLY a JSON object (no markdown fences, no other text) with exactly these fields:
{
  "exception_type": "<string>",
  "policy_id": "<string, e.g. POLICY-004>",
  "root_cause": "<string, cite specific evidence values>",
  "evidence_used": ["<string>", ...],
  "recommended_action": "<string>",
  "confidence": <float 0.0-1.0>,
  "sufficient_evidence": <true or false>
}
"""


class OllamaProvider(LLMProvider):
    name = "ollama"

    # Literal loopback, not the hostname -- see agent/config.py's OLLAMA_HOST
    # for the measured reason (89x latency difference on this Windows machine).
    def __init__(self, model: str, host: str = "http://127.0.0.1:11434"):
        self.model = model
        self.host = host.rstrip("/")

    def _call(self, system_prompt: str, user_message: str, extra: str = "") -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt + SCHEMA_INSTRUCTION},
                {"role": "user", "content": user_message + extra},
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.2},
        }
        resp = requests.post(f"{self.host}/api/chat", json=payload, timeout=120)
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        return json.loads(content)

    def resolve(self, system_prompt: str, user_message: str) -> ExceptionResolution:
        try:
            raw = self._call(system_prompt, user_message)
            return ExceptionResolution(**raw)
        except (ValidationError, json.JSONDecodeError, KeyError) as e:
            print(f"  [OLLAMA PARSE FAILED, retrying once] {type(e).__name__}: {e}")
            try:
                raw = self._call(
                    system_prompt, user_message,
                    extra=f"\n\nYour previous response was invalid: {e}. "
                          f"Return ONLY the corrected JSON object, nothing else."
                )
                return ExceptionResolution(**raw)
            except Exception as e2:
                print(f"  [OLLAMA PARSE FAILED TWICE] {type(e2).__name__}: {e2}")
                return ExceptionResolution(
                    exception_type="unknown", policy_id="NONE",
                    root_cause=f"[OLLAMA PARSE FAILED TWICE: {e2}] Escalating without agent reasoning.",
                    evidence_used=[], recommended_action="Escalate for manual review -- agent output invalid.",
                    confidence=0.0, sufficient_evidence=False,
                )
        except requests.exceptions.ConnectionError:
            print(f"  [OLLAMA NOT RUNNING] Could not connect to {self.host}. "
                  f"Is 'ollama serve' running? Is the model pulled ('ollama pull {self.model}')?")
            return ExceptionResolution(
                exception_type="unknown", policy_id="NONE",
                root_cause=f"[OLLAMA CONNECTION FAILED] Could not reach {self.host}. "
                           f"Escalating without agent reasoning.",
                evidence_used=[], recommended_action="Escalate for manual review -- agent call failed.",
                confidence=0.0, sufficient_evidence=False,
            )
        except requests.exceptions.RequestException as e:
            print(f"  [OLLAMA CALL FAILED] {type(e).__name__}: {e}")
            return ExceptionResolution(
                exception_type="unknown", policy_id="NONE",
                root_cause=f"[OLLAMA CALL FAILED: {e}] Escalating without agent reasoning.",
                evidence_used=[], recommended_action="Escalate for manual review -- agent call failed.",
                confidence=0.0, sufficient_evidence=False,
            )
