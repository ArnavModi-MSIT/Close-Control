"""Config for the tool-using investigation agent.

Deliberately separate from agent/config.py -- this is an additive,
optional upgrade path (multi-step, tool-calling investigation), not a
replacement for the existing single-shot agent/client.py. Its final
output is shaped to be a drop-in ExceptionResolution, so it flows through
the exact same agent/gate.py unchanged -- the investigation gets richer,
the authority boundary does not move.
"""

import os

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Qwen3 family, not Llama (the model the rest of agent/ uses) -- originally
# picked as qwen3:8b specifically because of measured tool-calling
# reliability, not habit: a real evaluation (Docker, 3,570-test multi-round
# agent-loop benchmark, 2026) found Llama 3.3 70B scoring only 0.607 F1 on
# tool-calling, while Qwen3 8B scored 0.933 F1 on the same kind of task.
#
# Now defaulting to the smaller qwen3:1.7b within that same family --
# validated on 4 real cases across 4 different exception types (0 tool-call
# errors, correct exception_type + policy_id on all 4, confidence 0.90-0.95,
# ~48-97s/case vs. qwen3:8b's 159.6s with thinking off). Real evidence, but
# a much smaller sample than the benchmark above that justified the family
# choice -- if tool-calling reliability issues ever show up on a broader
# batch, that's the first thing to re-check, and qwen3:8b remains the
# fallback via INVESTIGATOR_MODEL=qwen3:8b or `run_investigator.py --model
# qwen3:8b`.
INVESTIGATOR_MODEL = os.environ.get("INVESTIGATOR_MODEL", "qwen3:1.7b")

# Hard ceiling on tool-call rounds per case. This is a safety property, not
# a performance knob: without it, a model that gets stuck in a
# call-tool-then-immediately-call-it-again loop could run indefinitely.
# Matches the project's existing philosophy of never trusting the model to
# know when to stop on its own. (In practice, never observed above 4 rounds
# across 118 real investigations -- this cap has never actually been hit.)
MAX_TOOL_ROUNDS = 6

# Ollama's `think` request field for reasoning models (Qwen3, DeepSeek-R1,
# etc.) -- False skips the model's internal chain-of-thought tokens before
# each visible response. Set False by default based on a real, controlled
# A/B test (same case, same model, same tool calls, same verdict, same
# policy citation, same confidence -- byte-for-byte identical outcome --
# but 635.1s -> 159.6s, a measured 4x speedup with zero observed quality
# cost). Not a guess: this is the same standard of evidence the qwen3-vs
# -llama model choice itself was held to.
THINK_MODE = False

# How many days around a transaction's captured_at count as "related" for
# lookup_related_transactions. A business, not a technical, choice --
# separate constant so it's easy to find and justify.
RELATED_TRANSACTION_WINDOW_DAYS = 2
