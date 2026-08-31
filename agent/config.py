"""Exception Resolution Agent configuration."""

import os

try:
    from dotenv import load_dotenv
    # looks for .env in the project root (one level up from agent/)
    _env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv not installed -- falls back to real environment variables only

ANTHROPIC_MODEL = "claude-sonnet-5"  # renamed from MODEL for clarity -- was ambiguous
                                       # next to GROQ_MODEL/OLLAMA_MODEL
MAX_TOKENS = 1024

# which backend to use: mock (default, $0, no network) | groq (free tier) | anthropic (paid)
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "mock").lower()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# Literal loopback, not the hostname -- see CLAUDE.md's Environment section.
# On this Windows machine, resolving "localhost" tries IPv6 first and only
# falls back to IPv4 after a real ~2s timeout, measured via requests.get():
# "localhost" averages 2053ms/call, "127.0.0.1" averages 23ms/call (8
# calls each, dead consistent) -- an 89x difference on every single Ollama
# HTTP call this project makes.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")

# confidence + risk gate thresholds (see gate.py)
AUTO_RESOLVE_CONFIDENCE_THRESHOLD = 0.85
AUTO_RESOLVE_RISK_CEILING_RUPEES = 5000.00

# exception types the policy permits auto-resolving via the agent at all,
# even at high confidence -- everything else always escalates to a human
# regardless of what the LLM proposes (matches the matcher's own
# AUTO_RESOLVABLE_MODES philosophy: confidence alone never authorizes
# a financial action)
AGENT_AUTO_RESOLVABLE_TYPES = {
    "deemed_success_ambiguous",  # can auto-confirm once resolution criteria met
}

# default provider is "mock" -- $0, no network call, ever, unless explicitly
# overridden via LLM_PROVIDER in .env. This is a deliberate safety default:
# you should never accidentally spend money by just running the script.
API_KEY = os.environ.get("ANTHROPIC_API_KEY")  # only used if LLM_PROVIDER=anthropic
OFFLINE_MODE = LLM_PROVIDER == "mock"

AUDIT_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "audit_log.jsonl")
