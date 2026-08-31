# Reconciliation Tool — AI Finance Controller

Submission for **Razorpay's `/buildathon`, Track 04: "AI Finance
Controller"** ("Run the books and the cash position").

A multi-source settlement reconciliation system: payment gateway vs. two
banking partners vs. Razorpay's own internal settlement ledger vs.
Razorpay Capital's loan-recovery ledger, with an AI layer for the
exception tail, a human review workflow, a cash-position projection, a
tool-using investigation agent, a free-text Q&A agent, and a scheduled
re-verification job.

The framing goes beyond a literal reading of the brief — the goal is for
this pipeline to be plausibly adoptable by Razorpay itself, not just to
clear the track's minimum bar. That's why the dataset models multi-bank
ingestion and an internal ledger rather than one clean bank feed, and why
the scheduling layer is built on Apache Airflow rather than a bespoke
script.

**The one rule that is never violated: AI proposes, deterministic code
disposes.** The LLM never touches a number and never self-authorizes a
financial action. A plain-Python gate — no LLM — is the only thing that
ever decides auto-resolve vs. escalate, and it trusts the deterministic
matcher's own classification of an exception, never the LLM's opinion of
what the exception is.

---

## Architecture

```
LAYER 1  data_generation/     synthetic gateway+bank+ledger+loan-book data, seeded (reproducible)
         ingestion/           bank round-trip: canonical -> 2 partners' raw formats -> canonical
LAYER 2  matching/            deterministic multi-pass matcher, zero ML/LLM
LAYER 3  agent/               single-shot Exception Resolution Agent + deterministic gate
LAYER 4  cash_position/       deterministic aggregation on top of the matcher's report
LAYER 5  review_backend/      FastAPI + Postgres human review queue (approve/override/escalate)
LAYER 6  investigator/        tool-using multi-step investigation agent
LAYER 7  airflow/             closed-loop re-verification scheduler
LAYER 8  qa_agent/            free-text Settlement Q&A agent, grounded in real tool calls
         ui/                  demo reel (showcase.html) + review-queue-app/ (ops tool)
```

**Data flow**: `data_generation` → `matching` → (`agent` or `investigator`,
same output shape) → the deterministic gate → an append-only audit log →
`review_backend` (human decisions, plus Airflow's automated ones) →
`ui/review-queue-app/`. `cash_position`, `qa_agent`, and the demo reel all
sit off the matcher's report independently — they answer questions about
what the pipeline already computed, and never feed back into it.

**Critical invariant**: the AI layer only ever sees the transactions the
deterministic matcher itself could not resolve — roughly 30% of the
dataset. The other 70%+ is resolved with zero ML/LLM involvement.

---

## Verified results

Every number below is computed live by the project's own scripts against
a seeded, reproducible dataset — never hand-typed. Reproduce any of them
by running the corresponding script listed in *How to run* below.

- **2,072 transactions**, seeded and reproducible. Bank statement: 222
  postings across 2 fictional banking partners (one speaking real
  ISO 20022 / CAMT.053 XML, the other a proprietary CSV format), plus a
  4th source — 18 Razorpay Capital loan recoveries.
- **Matcher accuracy: 100% (2,072/2,072)** settlement-aware accuracy,
  **0.72%** false-auto-resolve rate, **100%** hard-negative resolution
  (40/40 deliberately confusable same-merchant/same-amount pairs
  correctly separated). Deterministic, zero LLM calls, ~1,200–2,900
  transactions/sec.
- **70.2% resolved with zero ML/LLM involvement**: 1,397 clean + 58
  auto-resolved by deterministic rules. The remaining ~30% escalates to
  the AI layer, which is gated by a 7-condition deterministic check
  before anything can auto-resolve.
- **A naive baseline is scored on the same data**: exact-match-only logic
  (no tolerance windows, no split detection) resolves 198/208 settlements
  vs. this system's 208/208 — quantifying why the multi-pass tolerance
  logic matters, not just asserting it.
- **A second, independent random seed gives nearly identical results**
  (100.0% accuracy, 0.68% false-auto-resolve) — the matcher isn't
  exploiting a quirk of one seed's random draws.
- **RAG ablation** (real, live local LLM): retrieval ON = 100% policy
  -citation accuracy; OFF = 6.2%. Mean confidence is identical either way
  — an ungrounded model doesn't sound less confident, it just confidently
  cites the wrong policy.
- **Root-cause clustering**: the 617-case escalated queue collapses to
  130 distinct root causes (4.75x amplification) — one upstream event
  (e.g. a settlement missing a bank reference) fans out into dozens of
  separately-escalated cases that all clear once the one root cause is
  explained.
- **Adversarial proof**: a hostile bank-narration prompt-injection string,
  run through the real tool-calling pipeline, cannot smuggle a false
  auto-resolve past the gate — proven with a fully-compliant, maximally
  -confident compromised model, not a strawman.

See `evaluate.py`'s own output for the full, current numbers and how each
one is computed — this README summarizes, it doesn't replace it.

---

## How to run

**Quick start (deterministic pipeline only, zero LLM calls, always
works):**

```bash
python scripts/run_demo.py                    # env checks, live pipeline, seed, serve
python scripts/run_demo.py --skip-server      # checks + pipeline only
```

**Full pipeline, in order:**

```bash
python scripts/generate_data.py                          # synthetic dataset + ingestion round-trip
python run_matcher.py                             # deterministic matching
python scripts/evaluate.py                                # scores the matcher against ground truth
python scripts/run_agent.py --mode mock                   # $0 mock provider (default demo mode)
python scripts/run_cash_position.py
python scripts/seed_review_queue.py                       # needs a local Postgres (see below)
cd ui/review-queue-app && npm install && npm run build && cd ../..
python -m uvicorn review_backend.main:app --port 8000
# open http://127.0.0.1:8000/review-queue/
```

**Prerequisite** for the review queue and anything downstream of it: a
local Postgres, started once per machine reboot:

```bash
docker compose -f postgres/docker-compose.yaml up -d
```

An optional local Redis cache speeds up repeated dashboard polling (never
a hard dependency — the app works identically, just slower, without it):

```bash
docker compose -f redis/docker-compose.yaml up -d
```

**Live LLM calls** (investigation agent, Settlement Q&A) run against a
local [Ollama](https://ollama.com) install by design — no API key, no
network dependency, nothing that can fail due to venue wifi:

```bash
ollama pull qwen3:1.7b
python scripts/run_investigator.py --n 1
python scripts/run_qa.py "How much cash is confirmed right now?"
```

---

## Repository structure

```
data_generation/   synthetic dataset generator (payments, settlements, bank, ledger, loan book)
ingestion/         bank-partner round-trip (canonical -> raw format -> canonical)
matching/          deterministic multi-pass reconciliation engine
agent/             single-shot exception-resolution agent + the deterministic gate
cash_position/     cash-position snapshot + bank-reconciliation bridge
review_backend/    FastAPI + Postgres human review queue, state machine, audit trail
investigator/      tool-using multi-step investigation agent
qa_agent/          free-text Settlement Q&A agent
airflow/           closed-loop re-verification scheduler (opt-in)
ui/                demo reel + the React/TypeScript review-queue app
scripts/           every CLI entrypoint (run_*.py, evaluate.py, generate_data.py, ...)
tests/             every test_*.py
```

`run_matcher.py`, `corrections.py`, and `journal_entries.py` stay at the
repository root rather than in `scripts/` — `review_backend/main.py`
imports all three directly at runtime, so they need to be importable as
top-level modules, not just runnable as scripts.

Every layer has a corresponding `test_*.py` in `tests/`
(matching logic, gate branch coverage, ingestion round-trips, chargeback
and loan-recovery detection, adversarial prompt-injection resistance,
ground-truth isolation, exhaustive exception-priority coverage, agent
-immutability, and more) — run any of them directly, e.g.
`python tests/test_gate.py`.

---

## Known limitations

- Greedy bank-row consumption during matching is order-dependent — a
  losing settlement in a rare ambiguous-shared-candidate case is left
  unmatched, never wrong-matched (tested, documented).
- Settlement splits are 1:2 only, not general N-way.
- Float rupee arithmetic rather than integer paise, safely absorbed by a
  ±₹0.02 tolerance at this dataset's scale.
- The bank-reconciliation bridge reports a small, genuine, explained
  residual (~0.13%) rather than forcing an exact tie.
- Airflow's re-verification job only produces real closures against the
  included stream simulator — the main static dataset never changes, so
  it correctly reports zero closures against it.

---

## Design principles

- **Escalate over guess, always.** Every ambiguity-handling decision
  errs toward escalating for human review over silently picking one and
  hoping.
- **Ground truth is sacred.** No operational code path — matching,
  agent, cash position, investigator, review backend, ingestion,
  Airflow — ever reads the answer key. Only the evaluation script does,
  and only for scoring. This is proven with a static-analysis guard, not
  left to manual discipline.
- **The AI's original proposal is immutable.** Once a case is seeded,
  later layers (an investigation result, a scheduled re-verification) are
  additive, never destructive, and always attributable to a specific
  actor — a human's review, the investigation agent, or the automated
  re-verification job.
