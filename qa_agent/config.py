"""Config for the Settlement Q&A agent.

Deliberately separate from investigator/config.py, mirroring the same
"additive, optional module" relationship investigator/ has to agent/ --
this is a new capability (direction #2 from the buildathon brief,
"Settlement Q&A agent"), not a replacement for anything. It shares
investigator/'s Ollama-first reasoning (nothing can fail due to venue
wifi) and its tool-calling client, but answers a genuinely different
question: not "what should happen to this one escalated case" but
"tell me something true about the whole portfolio, grounded in real
numbers, citing where each number came from."
"""

import os

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Same model family as investigator/ for the same measured reason (Qwen3
# family scored 0.933 F1 on a real tool-calling benchmark vs Llama 3.3
# 70B's 0.607 F1 -- see investigator/config.py). qwen3:1.7b as the
# default for the same speed/cost tradeoff already validated there.
QA_MODEL = os.environ.get("QA_MODEL", "qwen3:1.7b")

# Same safety property as investigator/'s MAX_TOOL_ROUNDS: a hard ceiling,
# not a performance knob -- without it a model that keeps re-querying the
# same data could run indefinitely on what should be a bounded lookup.
MAX_TOOL_ROUNDS = 6

THINK_MODE = False

# search_cases()'s result cap -- a portfolio question ("show me all
# escalated cases") could otherwise return hundreds of rows into the
# model's context, most of which it will never actually use in its
# answer. Capped, with the TRUE total count always included alongside the
# capped list, so the model (and a human reading the tool trace) always
# knows whether it's looking at everything or a sample.
SEARCH_CASES_MAX_RESULTS = 20

# Numeric-grounding tolerance for check_grounding() (qa_agent/grounding.py):
# a number the model restates is considered grounded if it's within this
# of some number that actually appeared in a tool result. Same tolerance
# shape as cash_position.config.RECONCILIATION_TIE_TOLERANCE_* (flat
# rupee floor OR a relative percentage, whichever is larger) -- deliberate
# reuse of an already-established, already-reasoned-through tolerance
# pattern rather than inventing a new one. Generous enough that the model
# restating "Rs.5,54,613" for a real "Rs.5,54,612.74" isn't flagged as a
# hallucination, tight enough that an invented figure still gets caught.
GROUNDING_TOLERANCE_RUPEES = 1.00
GROUNDING_TOLERANCE_PCT = 0.005
