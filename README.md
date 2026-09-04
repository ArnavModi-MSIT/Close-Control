# Close Control

**An AI Finance Controller for multi-source settlement reconciliation.**

A multi-source settlement reconciliation system: payment gateway vs. two
banking partners vs. Razorpay's own internal settlement ledger vs.
Razorpay Capital's loan-recovery ledger. A deterministic matcher resolves
the majority of transactions with zero AI involvement; an AI layer
investigates the exception tail under a gate that never lets a model
self-authorize a financial decision.

**Core rule: AI proposes, deterministic code disposes.** No LLM ever
writes a number or approves an action. A plain-Python gate — no model
involved — is the only thing that decides auto-resolve vs. escalate, and
it trusts the deterministic matcher's own classification of an exception,
never the LLM's opinion of what the exception is.

---

## Demo video

The fastest way to see the pipeline, the review queue, and the AI tools
in action:

<video src="https://github.com/user-attachments/assets/61a97019-9bbc-4a85-8e5c-547a7d594c81" controls="controls" style="max-width: 100%; height: auto;"></video>

---

## Architecture

```
LAYER 1  data_generation/ + ingestion/   synthetic multi-bank dataset, seeded and reproducible
LAYER 2  matching/                        deterministic multi-pass matcher, zero ML/LLM
LAYER 3  agent/                           single-shot exception-resolution agent + deterministic gate
LAYER 4  cash_position/                   cash-position snapshot + bank-reconciliation bridge
LAYER 5  review_backend/                  FastAPI + Postgres human review queue
LAYER 6  investigator/                    tool-using multi-step investigation agent
LAYER 7  airflow/                         closed-loop re-verification scheduler
LAYER 8  qa_agent/                        free-text Settlement Q&A agent
         ui/                              review-queue dashboard (React + TypeScript)
```

Data flows one direction: `data_generation → matching → agent/investigator
→ gate → review_backend → ui`. `cash_position` and `qa_agent` sit off the
matcher's output independently — they answer questions about what already
happened, and never feed back into a decision.

---

## Verified results

- **100% matcher accuracy** (2,072/2,072 transactions), **0.72%**
  false-auto-resolve rate, zero LLM calls, ~1,200–2,900 transactions/sec.
- **70.2% resolved with zero AI involvement** by the deterministic matcher
  alone. The remaining ~30% escalates under a 7-condition gate before
  anything can auto-resolve.
- **100% hard-negative resolution** — 40/40 deliberately confusable
  same-merchant/same-amount pairs correctly kept separate.
- **Adversarial-proven**: a hostile prompt-injection string, run through
  the real tool-calling pipeline, cannot smuggle a false auto-resolve past
  the gate.

Full numbers, per-layer breakdowns, and reproduction commands: run
`scripts/evaluate.py`, or see `CLAUDE.md`.

---

## Quick start

```bash
python scripts/run_demo.py
```

Runs the deterministic pipeline live and serves the review dashboard at
`http://127.0.0.1:8000/review-queue/`. Zero LLM calls by default — the AI
layers are shown through pre-recorded, audited results.

For the full pipeline, Postgres/Redis setup, and live-LLM commands, see
`CLAUDE.md`.

---

## Known limitations

- Greedy bank-row consumption is order-dependent — a losing settlement in
  a rare ambiguous case is left unmatched, never wrong-matched (tested).
- Settlement splits are 1:2 only, not general N-way.
- The bank-reconciliation bridge reports a small (~0.13%), genuine,
  explained residual rather than forcing an exact tie.

---

## Design principles

- **Escalate over guess, always.** Every ambiguity-handling decision errs
  toward human review over silently picking one and hoping.
- **Ground truth is sacred.** No operational code path reads the answer
  key — only the evaluation script does, and only for scoring. Enforced
  by a static-analysis guard, not manual discipline.
- **The AI's proposal is immutable.** Later layers — an investigation
  result, a scheduled re-verification — are additive, never destructive,
  and always attributable to a specific actor.

---

For the complete engineering record — every design decision, every
verification pass, every known limitation — see `CLAUDE.md`.
