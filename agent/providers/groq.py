"""Groq provider -- free tier, OpenAI-compatible chat completions API.
Groq doesn't have Claude's constrained-decoding structured outputs, so
this uses JSON mode plus an explicit schema instruction in the prompt,
then validates the response with Pydantic. On validation failure,
retries once with the error fed back to the model. On a 429 rate-limit
response, backs off and retries using the wait time Groq itself reports
(rather than failing immediately or guessing a delay) -- the free tier's
TPM budget is easy to hit across a batch of calls, and this is a real,
expected condition to handle gracefully, not an error to give up on.
"""

import json
import re
import time

import requests
from pydantic import ValidationError

from .base import LLMProvider
from ..schema import ExceptionResolution

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_RATE_LIMIT_RETRIES = 4

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


def _parse_retry_after(response) -> float:
    """Groq's 429 body includes 'Please try again in 12.9s' -- use that
    exact figure (plus a small safety margin) instead of a fixed guess."""
    try:
        msg = response.json().get("error", {}).get("message", "")
        match = re.search(r"try again in ([\d.]+)s", msg)
        if match:
            return float(match.group(1)) + 1.0
    except Exception:
        pass
    return 15.0  # fallback if the message format ever changes


class GroqProvider(LLMProvider):
    name = "groq"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def _call(self, system_prompt: str, user_message: str, extra: str = "") -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt + SCHEMA_INSTRUCTION},
                {"role": "user", "content": user_message + extra},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 1024,
        }

        for attempt in range(MAX_RATE_LIMIT_RETRIES):
            resp = requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=30,
            )
            if resp.status_code == 429:
                wait = _parse_retry_after(resp)
                print(f"  [GROQ RATE LIMITED] waiting {wait:.1f}s "
                      f"(attempt {attempt + 1}/{MAX_RATE_LIMIT_RETRIES})...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)

        # exhausted retries -- raise so the caller's normal error handling takes over.
        # Deliberately NOT resp.raise_for_status() first: if this line is reached,
        # every attempt returned 429 (any other status exits the loop early via
        # return above), so raise_for_status() would always raise a generic
        # HTTPError here and this message would never be seen -- found by an
        # external review pass, confirmed by tracing the control flow: harmless
        # today (HTTPError is still a RequestException, so resolve()'s except
        # branch catches it either way), but the more useful diagnostic was
        # unreachable dead code.
        raise requests.exceptions.RequestException(
            f"Rate limited after {MAX_RATE_LIMIT_RETRIES} retries")

    def resolve(self, system_prompt: str, user_message: str) -> ExceptionResolution:
        try:
            raw = self._call(system_prompt, user_message)
            return ExceptionResolution(**raw)
        except (ValidationError, json.JSONDecodeError, KeyError) as e:
            print(f"  [GROQ PARSE FAILED, retrying once] {type(e).__name__}: {e}")
            try:
                raw = self._call(
                    system_prompt, user_message,
                    extra=f"\n\nYour previous response was invalid: {e}. "
                          f"Return ONLY the corrected JSON object, nothing else."
                )
                return ExceptionResolution(**raw)
            except Exception as e2:
                print(f"  [GROQ PARSE FAILED TWICE] {type(e2).__name__}: {e2}")
                return ExceptionResolution(
                    exception_type="unknown", policy_id="NONE",
                    root_cause=f"[GROQ PARSE FAILED TWICE: {e2}] Escalating without agent reasoning.",
                    evidence_used=[], recommended_action="Escalate for manual review -- agent output invalid.",
                    confidence=0.0, sufficient_evidence=False,
                )
        except requests.exceptions.RequestException as e:
            print(f"  [GROQ API CALL FAILED] {type(e).__name__}: {e}")
            body = getattr(getattr(e, "response", None), "text", None)
            if body:
                print(f"  [GROQ RESPONSE BODY] {body[:500]}")
            return ExceptionResolution(
                exception_type="unknown", policy_id="NONE",
                root_cause=f"[GROQ API CALL FAILED: {e}] Escalating without agent reasoning.",
                evidence_used=[], recommended_action="Escalate for manual review -- agent call failed.",
                confidence=0.0, sufficient_evidence=False,
            )
