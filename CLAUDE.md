# CLAUDE.md — AI Finance Controller

**Purpose of this file:** the single source of truth for this project —
for an AI assistant picking it up cold, and for a human, since this is
currently the *only* documentation in the repo (README.md, EVALUATION.md,
LIMITATIONS.md, SUBMISSION_PITCH.md, and REVIEW_QUEUE_DESIGN.md were all
removed as redundant once this file covered the same ground more
completely). This describes **current state** — what the system is, how
it works, how to run it, what's verified, what's known-limited, what's not
built. It is not a chronological session log; when something changes,
update the relevant section in place rather than appending a dated entry.

---

## 1. What this is

Submission for **Razorpay's `/buildathon`, Track 04: "AI Finance
Controller"** ("Run the books and the cash position"). The chosen
finance-ops loop: multi-source settlement reconciliation (payment gateway
vs. two banking partners vs. Razorpay's own internal settlement ledger),
with an AI layer for the exception tail, a human review workflow, a
cash-position projection, a tool-using investigation agent, and a scheduled
re-verification job.

The framing is deliberately not just a literal brief reading — the goal is
for this pipeline to be plausibly adoptable BY Razorpay itself, not just to
clear the track's minimum bar. That's why the dataset models multi-bank
ingestion and an internal ledger rather than one clean bank feed, and why
the scheduling layer is built on Apache Airflow rather than a bespoke
script — these choices exist to make the demo look like what Razorpay
would actually operate.

**The one rule that must never be violated: *AI proposes, deterministic
code disposes.*** The LLM never touches a number, never self-authorizes a
financial action. `agent/gate.py` — plain Python, no LLM — is the only
thing that ever decides auto-resolve vs. escalate, and it trusts the
matcher's own `exception_type`, never the LLM's opinion of what the
exception is. Every layer added since (review queue, investigator,
closed-loop re-verification) extends this principle one level deeper, not
around it — see §7's `auto_closed` mechanism for the most recent example.

---

## 2. Architecture

```
LAYER 1  data_generation/     synthetic gateway+bank+ledger data, seeded (RNG_SEED=42)
         ingestion/           bank round-trip: canonical -> 2 partners' raw formats -> canonical,
                               runs at the END of generate_data.py, upstream of
                               data/bank_statement.csv; matching/ never knows it exists
LAYER 2  matching/            deterministic multi-pass matcher, zero ML/LLM
LAYER 3  agent/               single-shot Exception Resolution Agent + deterministic gate
LAYER 4  cash_position/       deterministic aggregation on top of matching/'s report
LAYER 5  review_backend/      FastAPI+Postgres human review queue (approve/override/escalate)
LAYER 6  investigator/        tool-using multi-step investigation agent
LAYER 7  airflow/             closed-loop re-verification scheduler -- calls
                               review_backend's POST /api/reverify over HTTP, owns
                               orchestration only, never imports project code
         ui/                  showcase.html (demo reel) + review-queue-app/ (ops tool)
```

**Data flow**: `data_generation` → `matching` → (`agent` OR `investigator`,
same output shape) → `agent/gate.py` → `data/audit_log.jsonl` →
`review_backend` (human decisions, plus `airflow`'s automated ones) →
`ui/review-queue-app/`. `cash_position` and `ui/showcase.html` both sit off
`matching`'s report independently.

**Critical invariant**: `run_agent.py` and `run_investigator.py` BOTH only
ever see cases where `report[final_exception_type.notna() &
~auto_resolve_eligible]` — i.e. what the deterministic matcher itself could
not resolve (603 of 2,040 transactions). `agent/gate.py` is shared,
unmodified, by both agent paths — the investigator is a drop-in richer
proposer, not a parallel system with its own rules.

---

## 3. Repository structure

```
RazorPay/
├── CLAUDE.md                    ← this file, the only doc in the repo
├── .env                           local config (LLM_PROVIDER, API keys if using
│                                    anthropic), gitignored, not committed
├── .gitignore
├── requirements.txt              pandas, numpy, pydantic, python-dotenv, requests,
│                                   anthropic (optional), fastapi, uvicorn, httpx (test-only)
│
├── run_demo.py                  ← ONE-COMMAND DEMO ENTRYPOINT: env checks, live
│                                   deterministic pipeline, seed, serve. Zero LLM calls.
├── generate_data.py                Layer 1 entrypoint (also runs the ingestion/
│                                     round-trip); --out-dir for a seed-robustness regen
├── run_matcher.py                   Layer 2 entrypoint, exposes run(data_dir) reused everywhere
├── evaluate.py                       scores the matcher against ground_truth.csv (the ONLY
│                                       file that reads ground truth); --data-dir to score a
│                                       seed-robustness regen
├── run_agent.py                      Layer 3 entrypoint, --mode mock/sample/full,
│                                       --concurrency N, --reset-log
├── run_cash_position.py               Layer 4 entrypoint, --as-of, --horizon-days
├── run_reconciliation_statement.py     Layer 4, bank-reconciliation-bridge CLI (books
│                                         ending balance -> bank statement ending balance)
├── run_rag_ablation.py                 RAG (policy retrieval) ON/OFF ablation for agent/
├── export_dashboard_data.py             dumps data/dashboard_data.json for ui/showcase.html
├── seed_review_queue.py                  seeds review_backend's Postgres database from
│                                           audit_log.jsonl, hash-based, rejects conflicting
│                                           re-seeds rather than silently overwriting
├── backfill_evidence_display.py            one-time backfill: recomputes cases.evidence_fields_cited
│                                             for already-seeded cases, see §7's review_backend/ section
├── run_investigator.py                   Layer 6 entrypoint, --transaction-id, --n,
│                                           --exception-type, --reachable-only, --concurrency
├── evaluate_investigator.py               investigator accuracy report, ZERO LLM calls --
│                                            scores whatever's in investigation_log.jsonl
├── run_baseline_naive.py                   naive-vs-full-system matching comparison
├── run_judge_demo.py                        5-minute guided walkthrough, zero live LLM calls
├── run_stream_simulator.py                   SIMULATED real-time stream (not a live Razorpay
│                                               integration), own DB/audit-log/port (8001),
│                                               fully isolated from the main demo
├── test_ambiguity.py                         7 scenarios proving the matcher's ambiguity logic
├── test_gate.py                               9 unit tests, full agent/gate.py branch coverage
├── test_ingestion.py                           per-connector (Suryaan/Northbridge) round-trip +
│                                                 unsupported-transaction-type-code proofs, isolated
│                                                 from the combined warehouse.py identity check so a
│                                                 single connector's regression is attributable
├── test_review_api.py                          55 API/state-machine tests over real HTTP via
│                                                 TestClient, ephemeral per-run Postgres database
├── migrate_sqlite_to_postgres.py                 one-time migration, legacy SQLite -> Postgres,
│                                                   read-only against the source, verifies row
│                                                   counts + full set-equality before trusting it
│
├── data_generation/    (Layer 1)
│   ├── config.py                 constants: merchants, MDR rates, failure-mode weights, seed
│   │                                (RNG_SEED, overridable via RNG_SEED_OVERRIDE for
│   │                                 seed-robustness checks only)
│   ├── utils.py, payments.py, settlements.py, hard_negatives.py, ground_truth.py, validation.py
│   └── sources/  gateway.py, bank.py, ledger.py (Razorpay's own INDEPENDENT internal ledger)
│
├── ingestion/          (Layer 1 -- bank ingestion round-trip)
│   ├── config.py                  static merchant->partner assignment, orphan-credit definitions
│   ├── warehouse.py                 orchestrates to_raw/normalize + an identity-preservation
│   │                                  assertion (real safety net -- caught a real bug once)
│   └── connectors/  base.py, suryaan.py (CAPS_SNAKE, DD-MM-YYYY), northbridge.py (camelCase, DD/MM/YYYY)
│
├── matching/            (Layer 2 -- see §7)
│   ├── __init__.py, config.py, loaders.py, blocking.py, engine.py,
│   │   settlement_builder.py, ledger_check.py, report.py
│   └── diagnostics.py              observational only, never imported by the matching path
│                                     itself -- candidate-block/overlap stats, consumption +
│                                     conservation invariants, called from evaluate.py
│
├── agent/               (Layer 3 -- see §7)
│   ├── client.py, gate.py, schema.py, policy_kb.py, evidence.py, audit.py, config.py
│   └── providers/  (ollama.py, anthropic.py, groq.py, mock.py -- pluggable)
│
├── cash_position/        (Layer 4)
│   ├── engine.py, config.py, reconciliation_statement.py
│
├── review_backend/         (Layer 5 -- see §7)
│   ├── db.py, state_machine.py, config.py, models.py, main.py, cache.py
│
├── postgres/                 (Layer 5's storage -- local, free, opt-in like airflow/)
│   ├── docker-compose.yaml      single postgres:16.14 service (pinned to the exact patch
│   │                              version, not the floating :16 tag -- see §7), port 5433
│   │                              (5432 was already taken by an unrelated native install)
│   ├── init/01-create-databases.sh   creates review_queue_stream on first boot
│   └── .env                       local dev credentials only, zero real-world exposure
│
├── redis/                      (Layer 5's cache -- local, free, optional; a pure
│   │                              performance layer, never a hard dependency)
│   ├── docker-compose.yaml         single redis:7.2.16-bookworm service (pinned to the
│   │                                 exact patch version, not the floating :7.2-bookworm
│   │                                 tag -- see §7), port 6379 (confirmed free -- Airflow's
│   │                                 own internal Redis is never published to the host)
│   └── .env                          REDIS_PORT override
│
├── investigator/             (Layer 6 -- see §7)
│   ├── config.py, tool_schema.py, tools.py, ollama_client.py, loop.py, schema.py
│
├── airflow/                    (Layer 7 -- see §7, opt-in, not part of run_demo.py's path)
│   ├── docker-compose.yaml        Apache's own official file, fetched via curl, not
│   │                                hand-written; one edit (AIRFLOW__CORE__LOAD_EXAMPLES=false)
│   ├── .env                        AIRFLOW_UID
│   └── dags/reverification_dag.py   one PythonOperator, calls POST /api/reverify over HTTP
│
├── ui/
│   ├── showcase.html / styles.css / script.js     demo reel, vanilla HTML/CSS/JS deliberately
│   │                                                (meant for standalone GitHub Pages hosting,
│   │                                                no backend) -- warm/light theme, no dark mode
│   └── review-queue-app/                            React+TS+Tailwind+Recharts ops UI
│       ├── src/  (components/, hooks/, api.ts, types.ts, lib/format.ts)
│       └── dist/   <- what review_backend/main.py mounts at /review-queue; build before
│                      demoing (`cd ui/review-queue-app && npm install && npm run build`)
│
└── data/    (generated, gitignored entirely)
    ├── gateway.json, bank_statement.csv, internal_settlement_ledger.csv, ground_truth.csv,
    │   dataset_metadata.json          Layer 1 outputs (bank_statement.csv is
    │                                    POST-ingestion-round-trip, includes 4 orphan bank credits)
    ├── warehouse/raw/{suryaan,northbridge}.csv     Layer 1 bronze layer -- each partner's own
    │                                                 genuinely different raw export format
    ├── audit_log.jsonl                    Layer 3 output, appends across runs
    ├── cash_position_forecast.csv, rag_ablation_detail.csv, dashboard_data.json
    ├── review_queue.db                      Layer 5's LEGACY SQLite DB -- superseded by
    │                                           Postgres (postgres/), kept as a rollback
    │                                           safety net, never deleted (see §7 Layer 5)
    ├── investigation_log.jsonl                Layer 6 output
    ├── investigator_eval_detail.csv             evaluate_investigator.py's per-case output
    ├── stream/                                    run_stream_simulator.py's filtered
    │                                                gateway/bank/ledger snapshots, overwritten
    │                                                atomically every tick (see §7)
    ├── stream_audit_log.jsonl                      stream-only equivalent of audit_log.jsonl
    │                                                  above -- NEVER the same file, never
    │                                                  touched together (the stream's case data
    │                                                  itself lives in Postgres's separate
    │                                                  review_queue_stream database, not here)
    └── (all "stream_*" files and stream/ are throwaway, safe to wipe via --reset)
```

---

## 4. Environment & setup

- **Windows.** The Bash tool defaults to the GLOBAL Python
  (`C:\Users\ymodi\AppData\Local\Programs\Python\Python310\python.exe`),
  which does NOT have `fastapi`/`uvicorn`/`python-dotenv`/`anthropic`
  installed. **Always use `.venv/Scripts/python.exe` explicitly** for
  anything touching `review_backend/`, `investigator/`, or `.env`
  loading — the global python will silently `ModuleNotFoundError` or skip
  `.env`.
- **Ollama**, installed and running locally (`http://localhost:11434`),
  managed by the user themselves — never run `ollama serve` to "fix" a
  connection-refused error; report it and wait. Models: `llama3.1:8b`
  (used by `agent/`), `qwen3:1.7b` (the current default for
  `investigator/`) and `qwen3:8b` (the heavier fallback — see §7's
  investigator/ section for both models' real validation numbers; the
  qwen3 family was originally chosen for measured tool-calling
  reliability, not preference: Llama 3.3 70B scored 0.607 F1 on a real
  tool-calling benchmark vs. Qwen3 8B's 0.933), plus
  `phi3:mini`, `gemma2:9b`, `mistral:latest`, `meditron:latest`.
- **No local GPU.** Single-shot agent calls: ~26 sec/case on CPU.
  Multi-step investigator calls with `THINK_MODE = False` (the current
  default — see §7's investigator/ section for the measured 4x-speedup
  evidence): ~48-160s/case on CPU, model-dependent. With thinking left on
  it was measured at 635s for a single case — **always run
  `run_investigator.py` with `run_in_background: true`** regardless. Keep local test
  batches small (`--transaction-id` for one case, `--n 1` at most); if a
  larger batch is genuinely needed for chart/demo data, Kaggle's free T4×2
  GPU tier is a real, working option (Ollama runs fine in a Kaggle
  notebook container — `curl -fsSL https://ollama.com/install.sh | sh`,
  install `zstd` first if the installer complains, then `ollama pull
  qwen3:8b`) — confirmed ~65-120s/case with GPU offload vs. minutes/case
  on local CPU.
- **Docker Desktop**, required for two things: the local Postgres
  (`postgres/`, Layer 5's storage — lightweight, one container) and the
  Airflow layer (§7, Layer 7, opt-in — Airflow's official CeleryExecutor
  stack needs ≥4GB, ideally 8GB, Docker memory; Postgres alone needs
  nowhere near that). WSL2 backend confirmed present. Port 5432 is taken
  by an unrelated native Postgres install already on this machine — the
  project's own Postgres container uses 5433 instead, confirmed free.
- **Killing local servers reliably on this machine**: `kill %1` /
  Bash-tool job control is unreliable for backgrounded Windows processes.
  Use PowerShell instead:
  `Get-NetTCPConnection -LocalPort <port> -State Listen | Select -Expand OwningProcess | % { Stop-Process -Id $_ -Force }`
- **Ports in normal use**: review_backend (main demo) on 8000, the stream
  simulator's own server on 8001, Airflow's webserver on 8080 (default
  login `airflow`/`airflow`, both from `_AIRFLOW_WWW_USER_USERNAME`/
  `_PASSWORD` defaulting to that literal string in the fetched compose
  file). Check before assuming a port is free — nothing here persists
  between sessions by default.
- **This machine has real, reproducible quirks worth knowing**: the
  browser-automation tool's screenshots don't reliably composite in this
  environment (DOM measurement / `get_page_text` / `javascript_tool` are
  the reliable checks instead); it can also misreport
  `document.hidden === true` for its own tab even when focused (relevant
  if something's timer-based logic pauses on backgrounding — e.g. React
  Query's polling needed `refetchIntervalInBackground: true` because of
  this); Git Bash on Windows silently rewrites absolute-looking Unix paths
  passed to `docker compose exec` (fix: prefix the command with
  `MSYS_NO_PATHCONV=1`). When something looks broken, check whether it's
  the actual code or a measurement/environment artifact before "fixing"
  it — this has gone wrong in both directions before.

---

## 5. How to run everything

**Prerequisite for anything touching the review queue (Layer 5 and up)**:
a local Postgres, once per machine reboot / Docker restart:
```bash
docker compose -f postgres/docker-compose.yaml up -d
```
`review_backend/`'s data (`cases`/`reviews`) lives in Postgres now, not a
SQLite file — see §7's Layer 5 storage note. `run_demo.py` checks for this
and gives a clear fix-it message if it's not reachable; the deterministic
pipeline itself (§6's Layers 1-4) needs nothing from this.

**Optional, for faster repeated `/api/stats`/reconciliation-statement
polling**: a local Redis cache, same lifecycle as Postgres's compose:
```bash
docker compose -f redis/docker-compose.yaml up -d
```
Purely a performance layer — never required, the app works identically
(just slower) if this is never started. See §7's Layer 5 cache note.

**Quick start (deterministic only, zero LLM calls, always works):**
```bash
python run_demo.py                    # env checks, live pipeline, seed, serve both UIs
python run_demo.py --skip-server      # checks + pipeline only
python run_demo.py --live-case        # ALSO sends one real case through the investigator
```

**Full pipeline, in order, if running pieces individually:**
```bash
python generate_data.py                          # Layer 1: synthetic dataset + ingestion round-trip
python run_matcher.py                             # Layer 2: deterministic matching
python evaluate.py                                # scores Layer 2 against ground truth
python run_agent.py --mode mock                   # Layer 3: $0 mock provider (default demo mode)
python run_investigator.py --n 1                  # Layer 6: one real case per exception type, live LLM
python run_cash_position.py                       # Layer 4
python run_reconciliation_statement.py            # Layer 4, bank-reconciliation bridge
python seed_review_queue.py                       # seeds review_backend from audit_log.jsonl (needs Postgres up)
cd ui/review-queue-app && npm install && npm run build && cd ../..
.venv/Scripts/python.exe -m uvicorn review_backend.main:app --port 8000
# open http://127.0.0.1:8000/review-queue/
```

**Simulated real-time stream** (the only place data changes over time —
required for closed-loop re-verification to have anything real to do):
```bash
python run_stream_simulator.py                                    # 5 min, port 8001
python run_stream_simulator.py --duration-minutes 3 --tick-seconds 2
python run_stream_simulator.py --reset                              # wipe stream state, start clean
python run_stream_simulator.py --skip-server                        # stream only, no server
```
Replays the 2,040-transaction dataset in `captured_at` order, releasing
progressively more of it on a timer, and re-runs the real, unmodified
deterministic matcher + $0 mock agent + gate against whatever has
"arrived" so far each tick. Not a live Razorpay integration — the header
shows an explicit "Live simulation" badge. Runs against completely
separate storage from the main demo (`data/stream/`,
`data/stream_audit_log.jsonl`, and the `review_queue_stream` Postgres
database — a different database on the same local server, never the same
one as the main demo's `review_queue`) via `REVIEW_QUEUE_DATABASE_URL` /
`REVIEW_QUEUE_MODE` / `CASH_POSITION_DATA_DIR` env-var overrides — never
touches the curated main database.

**Closed-loop re-verification (Airflow, Layer 7)**, opt-in, real infra:
```bash
# Terminal 1 -- the stream simulator (the only place this produces real closures)
python run_stream_simulator.py --duration-minutes 5 --tick-seconds 3

# Terminal 2 -- Airflow (first run pulls images, takes a few minutes)
cd airflow
docker compose up -d
# open http://localhost:8080 (login airflow/airflow), unpause
# closed_loop_reverification, then use "Trigger DAG" for on-demand timing
# control instead of waiting on its 1-minute schedule.

docker compose down        # tear down when done (add -v to also drop volumes)
```
The main static demo dataset (`data/`) never changes, so re-verification
against it always — correctly — reports zero closures; that's expected,
not a bug. See §7 Layer 7 for the mechanism.

---

## 6. Known-good numbers (re-verify if anything seems off, don't assume stale)

- Dataset: 2,049 ground-truth rows, 2,040 ledger transactions, seeded
  (`RNG_SEED=42`, reproducible). Bank statement: 190 postings (186 real +
  4 orphan bank credits), split across 2 fictional banking partners
  (Suryaan Bank, Northbridge Bank).
- Matcher: 176/176 settlements resolved (136 matched + 40 ambiguous),
  100% settlement-aware accuracy, 0.74% false-auto-resolve rate
  (15/2,040), 100% hard-negative resolution (40/40). Auto-resolve
  precision 98.96% (1422/1437 predicted auto-resolves correct), coverage
  73.91% (1422/1924 that should have auto-resolved actually did).
  Seed-robustness (seed=1337, independent regen): 99.95% accuracy
  (2039/2040), 0.69% false-auto-resolve — not accidentally tuned to one
  seed's random draws. Measured throughput: ~1,645 txn/s, 2,040
  transactions in ~1.24s, deterministic, zero LLM calls.
- Auto-resolve eligibility, precisely: 3 exception types auto-resolve at
  the **matcher** level before any LLM is invoked (`timing_lag_beyond_t2`,
  `fee_variance`, `duplicate_retry`); 1 additional type
  (`deemed_success_ambiguous`) is eligible at the **agent-gate** level,
  and only when all 6 gate conditions hold simultaneously (allowlist
  membership, policy permits, policy_id citation match, confidence≥0.85,
  sufficient_evidence=True, amount<₹5,000). Of the 603 escalated cases,
  only 8 (1.3%) are even *structurally reachable* for that path — allowlist
  membership + amount<₹5,000 alone, computed with zero LLM involvement via
  `agent.gate.is_investigation_worthwhile()`. The other 595 (98.7%) will
  escalate regardless of investigation depth, since the gate hard-blocks on
  the allowlist before it ever looks at confidence or evidence — see §7's
  investigator/ section for how `run_investigator.py --reachable-only` uses
  this to avoid spending its multi-minute-per-case budget where it
  structurally cannot change the outcome.
- Naive baseline comparison: exact account+date+amount matching (no
  window/split/shortage/overage/ambiguity logic) resolves 166/176
  settlements vs. this system's 176/176 — the quantified answer to "why
  does the multi-pass tolerance logic matter."
- Agent split: 1,397 clean + 40 auto-resolved deterministically + 603
  reach the agent = 70.4% resolved with zero ML/LLM.
- RAG ablation (real, live Ollama): retrieval ON = 100% policy-citation
  accuracy; OFF = 6.2%. Mean confidence identical (0.90) either way — the
  model doesn't get less confident when ungrounded, it just confidently
  cites the wrong policy. This is why the gate's citation-match check is a
  hard rule, not a soft signal.
- Investigator accuracy (107-case broad sample across all 8 escalated
  exception types, mixed model — 92 `qwen3:8b` + 15 `qwen3:1.7b`, run via
  `evaluate_investigator.py --n-per-type 700`): 100% policy citation
  correct, 0% hallucinated, mean confidence 0.93, 87.9% sufficient-evidence
  rate, 7.5% gate auto-resolve rate — directly comparable to the
  single-shot RAG-ON numbers above. 8 `deemed_success_ambiguous` cases
  (incl. `trn-000237`) have genuinely auto-resolved with a full tool-call
  trace. Mean latency 89.3s/case, p95 131.0s. Re-verify with
  `python evaluate_investigator.py --n-per-type 700` — the number grows as
  more of the 603-case backlog gets investigated (top up via
  `run_investigator.py --exception-type <type> --n <k>`, which skips
  already-investigated cases).
- Cash position (as-of 2026-07-25): projected ₹1,23,21,981.75 (confirmed
  ₹1,04,89,323.45 + in-transit ₹18,32,658.30), at-risk ₹49,93,749.90
  excluded, 319/2,040 not-yet-captured as of that snapshot (by design).

---

## 7. Design decisions worth knowing, per layer

### data_generation/ (Layer 1)
`config.HARD_NEGATIVE_PAIRS = 20` — single source of truth, following an
external review pass that found this literal `20` duplicated at both
`generate_data.py`'s `add_hard_negatives()` call site and its
`dataset_metadata.json` write, with nothing keeping the two in sync if one
were ever edited alone.

`validation.py` gained real invariants beyond dtype/PK-uniqueness/leakage
checks: hard-negative row count matches `2 × HARD_NEGATIVE_PAIRS` and every
pair stays two distinct `transaction_id`s after all joins; every configured
`FAILURE_MODES` key actually appears at least once in the generated data
(a missing scenario would otherwise look like a matcher success — nothing
to fail on); referential integrity (every gateway/ledger `transaction_id`
reference resolves against `gt_df`, not `payments` — hard negatives are a
deliberately separate ID space only ever merged into `gateway_df`/
`ledger_df`/`gt_df`, never back into `payments` itself, which was the
actual bug caught while building this exact check); and a *global*
gateway-vs-bank conservation check, filtered through the same eligible
population the existing per-settlement check already uses (an unfiltered
version doesn't hold — `held_for_risk_review` rows can carry a computed
-but-never-real `settlement_amount_paise`, the same caveat
`cash_position/engine.py` already documents for `observed_net_rupees`).

**A real, subtle consistency gap, found and now proven, not just fixed**:
`data_generation/config.py`'s `AUTO_RESOLVABLE_MODES` (what `ground_truth.py`
uses to label `expected_auto_resolvable`) and `matching/ledger_check.py`'s
own per-type `auto_resolve_eligible` logic are two independently-maintained
notions of "which exception types can auto-resolve" — nothing in the code
couples them; they'd only ever been kept in sync by hand. `evaluate.py`'s
new §5a checks this directly against the matcher's real output on every
run (currently consistent across all 10 exception types it produces) —
if they ever silently diverged, evaluate.py's accuracy numbers would be
scoring the matcher against the wrong oracle without anyone noticing.

**`ingestion/` hardening, following a separate external review pass on the
multi-partner round-trip**: `_assert_orphans_will_never_match()`'s safety
floor now imports `matching.config.AMOUNT_BLOCK_TOLERANCE_PCT` directly
instead of a hand-copied `1.5` literal (same "single source of truth"
class of fix as `HARD_NEGATIVE_PAIRS` above — if the real blocking
tolerance ever changed, the old literal would silently stop reflecting it).
`_assert_identity_preserved()` gained two real checks it was missing:
`settlement_posting_id` uniqueness is now asserted *before* being trusted
as the round-trip index (previously unenforced — a duplicate would have
made the index-based comparison silently misleading), and `bank_txn_id`
(deliberately excluded from `IDENTITY_COLUMNS` since each partner reissues
its own numbering) is now checked for non-null/uniqueness on its own,
since nothing else was validating it at all. `_partner_for_account()` and
both connectors' `normalize()` now raise a clear error instead of a
generic `KeyError`/silent pass-through for an unknown bank account or an
unsupported transaction-type code.

**Ingestion now has its own isolated RNG** (`ingestion/config.py`'s
`INGESTION_RNG_SEED = 4242`, `ingestion_rand_id()`/`ingestion_rand_utr()`)
instead of drawing raw-export IDs (`Txn_Ref_No`/`transactionId`, orphan
UTRs) from the same shared global `random` stream every other
`data_generation/` module depends on — the module's own docstring already
claimed full reproducibility isolation, but that was only true for the
static partner *assignment*, not the raw ID generation. Verified this
changes only cosmetic raw ID strings (confirmed: `Txn_Ref_No` differs
before/after, every canonical field — amount, dates, UTR, narration —
byte-identical) and does not touch canonical dataset generation at all;
re-ran the full pipeline afterward and every downstream number is
unchanged (1,397 clean / 40 auto-resolve / 603 escalated, all test suites
green).

**Ingestion-control metrics and per-connector tests**, from the same
review pass: `_assert_identity_preserved()` now returns a structured
summary on success instead of only raising on failure (`round_trip_ok`,
`rows_before`/`rows_after`, `identity_fields_checked`,
`bank_txn_id_non_null`/`_unique`) — an internal-only assertion had no
auditable trace once it passed, nothing a UI ingestion-control card or a
human verifying the demo could actually see. `run_ingestion()` now returns
a 3-tuple `(bank_df_final, example, metrics)` — `metrics` adds
`partners_processed`, `raw_rows`, `normalized_rows`, `orphan_rows`,
`rows_round_tripped`, and the identity check's own result, nested. Threaded
through `generate_data.py` into `dataset_metadata.json`'s
`ingestion_metrics` field and a new printed summary line. `test_ingestion.py`
(new) proves each connector's own round trip in isolation — Suryaan and
Northbridge each get their own canonical→raw→canonical fixture (with and
without a UTR) plus a proof that an unsupported transaction-type code
raises rather than silently passing through — separate from
`ingestion/warehouse.py`'s combined check on the real 190-row dataset, so
a single connector's regression is immediately attributable instead of
buried in a combined diff.

### matching/ (Layer 2)
Known, accepted, verified-not-bugs: greedy bank-row consumption is
order-dependent (a loser is left safely unmatched, never wrong-matched —
see `test_ambiguity.py` Scenario 7); settlement splits are 1:2 only despite
"1:N" comments in places (matcher and data generator agree, so it's
consistent, just narrower than the label implies); float rupee arithmetic
instead of integer paise, absorbed by `EXACT_MATCH_TOLERANCE_RUPEES=0.02`
at this dataset's scale. Never compares reference IDs (UTRs, order IDs) for
matching decisions — only `bank_account_id` + date window + amount
tolerance, so reference-format variation across banks is a non-issue by
construction, not a gap.

**One real, permanent fix, found via a one-off higher-volume regeneration
during development (not a supported repo feature — the dataset was reverted
to the curated 2,000-payment scale afterward)**: `ledger_check.py`'s
`check_ledger_vs_gateway()` used to re-scan the full `successful` gateway
DataFrame (`successful[successful["transaction_id_ref"] == txn_id]`) inside
a `for _, led in ledger.iterrows()` loop — an O(ledger_rows × gateway_rows)
full-table scan per row. Unnoticeable at the curated 2,000-payment scale
(~4M comparisons) but didn't finish in 5+ minutes at a 50x-larger volume
(~10B comparisons) — profiled, confirmed as the actual bottleneck (not
`blocking.py`, which stays cheap regardless of payment volume since it
operates per-settlement, not per-payment). Fixed by pre-grouping
`successful` by `transaction_id_ref` once (`{txn_id: grp for txn_id, grp in
successful.groupby(...)}`) instead of re-scanning per row — O(1) lookup per
row instead. Verified behavior-preserving: `evaluate.py`'s numbers against
the curated dataset are byte-identical before/after (1,397 clean / 40
auto-resolve / 603 escalated, 40/40 hard negatives), throughput actually
improved (~2,909 txn/s measured post-fix vs. the previously-documented
~1,645 txn/s, likely just measurement noise from the old O(n²) cost being
negligible but nonzero even at 2,000 rows).

**A known architectural fact, worth remembering if scale testing ever comes
up again**: `data_generation/settlements.py`'s `missing_utr_groups` flags a
settlement's ENTIRE bank posting as missing-UTR if ANY single member
payment happened to draw the `missing_bank_reference` failure mode at
generation time (an OR across the whole group). Harmless at the curated
scale (~11 payments/settlement on average, ~10% of settlements affected)
but would saturate toward 100% for much larger groups, since settlement
*count* is capped at `merchants × BATCH_DAYS` regardless of payment volume
(`settlement_id` is assigned per `(merchant_id, settle_day)`) — confirmed
for real during the same one-off regeneration above: 129/215 settlements
affected, 98.9% of ALL payments escalated, a ~51x amplification of the
generator's own healthy ~1% per-payment rate. Not a bug in the curated
dataset (which never exhibits this), just a latent property of the
generator's settlement-batching design.

**Candidate-block diagnostics and invariants (`matching/diagnostics.py`,
new)**, following an external review of the matching layer that flagged
candidate-overlap visibility as the single highest-value missing
diagnostic — measure whether greedy consumption order is a live risk on
this dataset, not just a theoretical one (the review's own repeated
recommendation: "first measure the overlap"). Three functions, all purely
observational, never imported by `blocking.py`/`engine.py`/`report.py`
themselves: `candidate_block_stats()` (block-size distribution + how many
distinct blocks each bank row falls into before any scoring narrows it),
`verify_consumption_invariants()` (raises `AssertionError` — same
fail-loud pattern as `ingestion/warehouse.py`'s identity checks — if any
bank_txn_id was ever claimed by more than one settlement, or a matched id
doesn't exist in the real bank statement; returns a structured summary on
success), `settlement_conservation_summary()` (matched_total vs.
expected_total classified within-tolerance/shortage/overage). Wired into
`evaluate.py` as new sections **1b** and **1c**.

**A real, measured, initially-surprising finding**: 176/186 bank rows
(94.6%) that appear in any candidate block fall inside **2+** settlements'
block-level candidate pool — a much higher raw overlap than "the matcher
works, so it's probably fine" would suggest. This is the wide
±50%-amount/±4-day blocking net (`blocking.py`) casting broadly on
purpose, BEFORE any real scoring narrows it — not the same thing as
genuine match-time ambiguity. Section 1c is the actual safety proof: on
the real dataset, `verify_consumption_invariants()` confirms **zero**
bank rows were ever double-consumed, and `settlement_conservation_summary()`
confirms **zero** "exact"/"split" pass settlements have a real
(non-rounding) delta — the engine's own tie/conflict/ambiguity-escalation
logic (see `test_ambiguity.py`) is doing its job. The known,
accepted, order-dependent-greedy-consumption limitation (above) stays
exactly that — documented and tested, not newly proven dangerous — but is
now backed by a measured overlap number instead of an assumption either way.

**Reviewed and found FALSE**: the same review questioned whether
`timing_lag_beyond_t2`'s runtime check (`report.py`:
`actual_settle_date > expected_date`) independently accounts for business
days. It doesn't need to — `expected_settlement_date` is already computed
via `add_business_days(captured_date, 2)` at generation time
(`data_generation/sources/ledger.py`), so the simple date comparison at
match time correctly operates on an already-business-day-aware T+2
deadline. No rename or behavior change needed; verified by reading the
generator, not assumed.

**Cheap documentation-only clarifications from the same review** (zero
behavior change, verified via full test suite + `evaluate.py` before/after):
`engine.py`'s docstring now states plainly that the split pass supports a
two-row split only, not general N-way, despite the "1:N" name used
throughout `report.py`/CLAUDE.md/the review-queue UI (kept as the
established name rather than renamed, since that would touch every one of
those surfaces for a labeling nuance, not a behavior change — matches this
file's own pre-existing Known Limitations entry). `ledger_check.py` gained
inline comments stating the `fee_variance` pass's actual semantic
relationship (`observed_net = expected_net - fee/tax variance`, so
`fee_delta + net_delta ≈ 0` is a real test, not a sign coincidence) and the
`partial_refund` pass's relied-upon invariant (a negative adjustment always
means a refund in this dataset — true by construction of the generator,
would need a real distinguishing signal if the data model ever grows other
negative-adjustment causes).

**Reviewed and found already-covered**: the review's "make
`auto_resolve_eligible` a deterministic function of `final_exception_type`"
suggestion (#15) turned out to already be verified by `evaluate.py`'s
existing §5a consistency check (added earlier this session for a related
but different reason) — it already groups by `final_exception_type` and
flags any case where `auto_resolve_eligible` isn't uniform within a type,
which is exactly this invariant. The review's "assert successful-gateway-
rows-per-transaction == 1 for non-duplicates" suggestion (#11) is also
already true by construction: `ledger_check.py`'s own `duplicate_txn_ids`
set is computed from the exact same `groupby(...).size() > 1` condition,
so any transaction_id NOT in it already has count ∈ {0, 1} by definition,
and the count-0 case is separately handled by the `matches.empty` branch
above it — no runtime assertion would ever have anything to catch.

**UI: primary + secondary exception signals surfaced**, following the same
review (#14) — `all_signals` was already computed and returned by the API
(`report.py`, `review_backend/main.py`) but never rendered anywhere in
`ui/review-queue-app`. `DetailPanel.tsx`'s case-summary section now shows
the matcher's authoritative `matcher_exception_type` (previously not shown
at all — only the AI's own, possibly-reclassified `agent_exception_type`
was visible) plus an "Also observed" row for any co-occurring signals
`EXCEPTION_PRIORITY` subordinated. Verified live in the browser
(`trn-000072`: primary `deemed_success_ambiguous`, secondary
`missing_bank_reference`) and via a clean `npm run build`.

**A real regression in `seed_review_queue.py`, found via the UI check
above**: `DetailPanel.tsx`'s "Evidence cited" section showed
`(not a known evidence field)` for every citation on every investigator-
primary case, including genuinely valid ones (`trn-000072`: all 6, incl. 2
real tool calls). Root cause: `_build_evidence_fields_cited()`'s
`if name in report_row` lookup can never match a label string like
`"EVIDENCE-4"` against `report_row`'s real keys (`"final_exception_type"`
etc.) — regardless of citation validity. Fixed using `agent/evidence.py`'s
new `EVIDENCE_LABEL_TO_FIELD` mapping (single source of truth, paired with
`build_evidence()`) for `EVIDENCE-N` citations, and positional lookup
against `investigation_log` for `TOOL-N` citations (matching
`investigator/loop.py`'s own `tool_evidence_ids()` convention exactly).
While fixing this, found and fixed a SEPARATE, more serious regression
from earlier this session: `_primary_from_investigation()`'s
`SimpleNamespace` never set `evidence_used`, which `apply_gate()`'s
citation-validation code (added earlier this session) now reads —
`AttributeError` on any brand-new case whose first seed already has a real
investigation on record. Never actually hit by any `seed_review_queue.py`/
`run_demo.py` run so far this session, since every case seeded up to that
point took the "unchanged" re-seed path, which never calls that function
at all — only surfaced building `backfill_evidence_display.py` (below).
Both fixes verified: `test_gate.py` (9/9), `test_review_api.py` (51/51),
and a real `seed_review_queue.py` re-seed run, all green.

**`backfill_evidence_display.py` (new, one-time)**: `evidence_fields_cited`
is a stored column, computed once at insert time — fixing the function
above doesn't retroactively correct already-seeded cases. This script
recomputes it for every existing case (a pure function of
`(evidence_used, report_row, investigation_log)`, none of which are the
AI's frozen proposal content — safe to recompute, matches this file's
"AI's original proposal is immutable" rule since nothing about the
proposal itself changes, only how a citation is cross-referenced for
display) and writes only the rows that actually changed. Run once against
the live main demo database: 603 scanned, 57 updated (the 92
investigator-primary cases minus ones with empty `evidence_used` or
citations that already happened to match under the old lookup) — the
other 511 agent-primary cases were already correct, since the default
mock provider (`agent/providers/mock.py`) cites raw field names directly,
which the old lookup handled correctly by coincidence. `--dry-run` reports
what would change without writing.

### agent/ (Layer 3)
Single-shot: `agent/client.py`'s `resolve_exception(report_row,
use_policy_retrieval=True)`. `agent/gate.py` has 6 conditions, ALL
required for auto-resolve — see §6 above. Full branch coverage in
`test_gate.py` (9 tests). `run_agent.py`'s audit log appends by default
(`--reset-log` to wipe) — an audit trail that erases itself isn't one.
`agent/audit.py`'s log write now specifies `encoding="utf-8"` explicitly
(found via external review — Windows' default `open()` encoding isn't
UTF-8, and this exact failure mode already hit `run_investigator.py`/
`test_ambiguity.py` elsewhere in this project for the same rupee-sign
reason).

**Evidence citation validation, informational only** — `agent/evidence.py`'s
`validate_evidence_citations()` checks a resolution's `evidence_used`
against `KNOWN_EVIDENCE_FIELDS` (the exact fields `build_evidence()` shows
the model), surfaced as `gate_result["unknown_evidence_citations"]` /
`["all_evidence_citations_valid"]` and persisted to the audit log. Does
NOT gate auto-resolve — `apply_gate()`'s 6 conditions are unchanged, all 9
`test_gate.py` tests still pass — this is visibility into a hallucinated
citation for a human reviewer, not a new automated block. A distinct
question from `agent.gate.is_investigation_worthwhile()`, which stays the
only thing that decides eligibility.

### review_backend/ (Layer 5) — human review queue
FastAPI + Postgres, single port, serves the API + the built React app + the
static `ui/` files from one origin (no CORS to configure).

**Storage**: local Postgres (`postgres/docker-compose.yaml`, free/local
only, no cloud dependency — a standing project constraint), a genuinely
separate server from Airflow's own internal Postgres metadata store (no
cross-wiring between the two). Migrated from SQLite for real write
concurrency (every human review, every `/api/reverify` batch close used to
serialize through one file lock). Three isolated **databases** on the one
server (not schemas — the same strength of isolation the old separate
SQLite files had): `review_queue` (main demo), `review_queue_stream`
(stream demo, Layer 7's Airflow job targets this one — the main database
never changes, so re-verification against it always correctly reports zero
closures), and an ephemeral `review_queue_test_<pid>` per `test_review_api.py`
run. `REVIEW_QUEUE_DATABASE_URL` selects which; `REVIEW_QUEUE_MODE=stream`
flags the stream instance for the frontend's "live simulation" badge.
`psycopg` v3 (`[binary]` extra — no C compiler/`pg_config` needed on
Windows). The original SQLite file (`data/review_queue.db`) is kept as a
rollback safety net, never deleted.

**Image pinning**, following an external review of `postgres/docker-compose.yaml`
(the other 6 of its 7 claims were already true when checked — healthcheck,
`restart: unless-stopped`, explicit documented non-conflicting ports, secrets
in a gitignored `.env` not the compose file, and `run_demo.py`'s existing
Postgres-reachability check, all already present): `image: postgres:16`
floated on patch version, not fully reproducible across machines/judging
runs. Pinned to `postgres:16.14` (the exact version already verified
running via `docker exec review-queue-postgres postgres --version`), not a
guessed tag. Verified safe: `docker compose up -d` recreated the container
against the pinned tag (the named volume `review-queue-postgres-data`
persisted, no data loss), came up healthy, `test_review_api.py` still
51/51.

**Cache**: local Redis (`redis/docker-compose.yaml`, free/local only —
same constraint as Postgres/Airflow), a genuinely separate container from
Airflow's own internal Redis (its Celery broker — the two never share
infrastructure). `review_backend/cache.py`'s `cached_or_compute()` wraps
`_cash_position_stats()` and `reconciliation_statement()` — both re-run
the full deterministic matcher, and the frontend polls `/api/stats` every
3 seconds unconditionally (`STATS_POLL_MS`,
`ui/review-queue-app/src/hooks/useQueries.ts`), so those two were being
recomputed forever even against the static main demo, which never
changes. **Deliberately never a hard dependency** — unlike Postgres, any
Redis error (down, timeout, never started) falls straight through to
computing directly, just slower; `run_demo.py` gained no readiness check
for it, and every operation in `cache.py` degrades with a `[WARN]` rather
than raising. Cache keys include `CASH_POSITION_DATA_DIR` and
`DEFAULT_AS_OF` so the main and stream demos sharing one Redis never
cross-contaminate (verified live: distinct keys, genuinely different
cached values, confirmed via direct Redis reads while both servers ran
simultaneously). 8-second TTL as a safety net; `run_stream_simulator.py`'s
tick loop actively invalidates its own keys after every snapshot write
(verified live: the stream's cached figure genuinely changes tick to
tick, not frozen at whatever was first cached) so live-simulation data
never waits out the TTL window. `REVIEW_QUEUE_REDIS_URL` overrides the
connection string, mirroring `db.py`'s `DATABASE_URL` pattern.

**Image pinning**, following a second external review (same review pattern
as postgres's above, this time on `redis/docker-compose.yaml` — the other 5
of its 6 claims were already true: healthcheck, `restart: unless-stopped`,
host exposure legitimately needed since `review_backend`/
`run_stream_simulator.py` both connect from host Python not a container,
env-var-driven config with no secrets, and Redis's missing startup check is
*deliberate by design*, not a gap — adding one would contradict the
"never a hard dependency" principle directly above): `image:
redis:7.2-bookworm` floated on patch version, same class of issue as
Postgres's. Pinned to `redis:7.2.16-bookworm` (the exact version already
verified running via `docker exec review-queue-redis redis-server
--version`). Verified safe: `docker compose up -d` recreated the container
against the pinned tag, came up healthy, and `test_review_api.py`'s own
`[CACHE] MISS`/`[CACHE] HIT` cycle confirmed the app was genuinely reaching
the recreated container, not silently falling back to direct computation —
51/51 still passing.

**Files**: `db.py` (schema + migrations), `cache.py` (Redis, see above),
`state_machine.py` (explicit transition table), `config.py`
(`MANAGER_APPROVAL_THRESHOLD_RUPEES=50000`), `models.py` (Pydantic
validation, override field allowlist), `main.py` (the API).

**State machine**:
```
auto_resolved --revert--> pending
pending --analyst approve--> approved                    [tier 1]
pending --analyst approve--> pending_manager_approval     [tier 2]
pending_manager_approval --DIFFERENT manager approves--> approved
pending/pending_manager_approval --auto_closed (system)--> auto_closed
pending/pending_manager_approval --override--> overridden  [terminal]
any non-terminal (incl. auto_closed) --escalate--> escalated
approved/overridden are hard-terminal: re-approving/re-overriding rejected (409)
auto_resolved accepts ONLY revert (409 otherwise -- must revert into pending first)
auto_closed accepts ONLY escalate (409 on approve/override -- a human can
  always reopen an automated closure, but not silently override it)
```
`auto_resolved` is a case's derived initial status when the gate itself
decided auto_resolve, before any human review — not a review decision, a
starting state. `auto_closed` is the closed-loop re-verification job's
own decision (`reviewer_name="system:closed-loop-reverification"`,
`reviewer_role="analyst"` — no separate `reviewer_role` value needed since
the decision string itself carries the "not human" semantic) — see the
Layer 7 section below.

**Reviewed and found already-addressed**, following an external review of
the backend/state-machine/cache/database layer (most of its 25 items):
system-vs-human actor distinction (#2/#14) — already computed live in
`_build_activity()` from `reviewer_name.startswith("system:")`
(`actor_type: "ai"|"system"|"human"` on every activity-feed entry), so a
redundant stored column would just be a second source of truth to keep in
sync; caching a `None` "data not ready" result (#4) — `cache.py`'s own
docstring already reasons through this deliberately (a real cached value,
distinguishable from "key doesn't exist," and `run_stream_simulator.py`'s
tick loop already actively invalidates rather than waiting out the TTL);
cache key versioning (#5) and `jsonable_encoder` usage (#6) — both already
correct, no semantic change made here to require a version bump;
`cases`/`reviews` append-only-by-convention (#7/#8) — confirmed no
PUT/PATCH/DELETE route exists anywhere in `main.py`, and its own docstring
already states this explicitly; override validation strength (#15) —
confirmed already correct (allowlist + stale-value check); repeated
`escalated → escalated` (#12/#17) — already intentional and already
documented in the state diagram above (`any non-terminal (incl.
auto_closed) --escalate--> escalated`); the state diagram including
`auto_closed` (#10) — already present, see above.

**Reviewed and deliberately NOT implemented**: making
`expected_review_count` mandatory (#13) — checked every real caller first
(`ReviewForm.tsx` always supplies it; so does `/api/reverify`), so
tightening it looked safe at first, but `test_review_api.py` has ~26 review
submissions across tier/validation/override test scenarios that
deliberately omit it (they're testing state-machine behavior, not the
concurrency guard, and don't want to plumb a review count through every
unrelated assertion) — making it required would force every one of those
to start tracking case state for no real protection gain in a
single-operator local demo. The real UI path already always supplies it,
which achieves the practical goal without the blast radius.

**`previous_status` added to `reviews`** (#9): `resulting_status` was
already stored; `reviews.previous_status` (new column, `_REVIEWS_MIGRATIONS`
in `db.py`) now also captures the status immediately BEFORE each event,
populated from `submit_review()`'s already-computed `current_status` — makes
every review event self-contained for audit without replaying the whole
prior sequence. Flows through to the API automatically (`_row_to_review_dict()`
does `dict(row)` over a `SELECT *`), no endpoint code change needed;
`ReviewHistoryItem` (`types.ts`) updated to match. Migration verified live
against the real database; `test_review_api.py` still passing (see Layer
7's re-verification-semantics section below for the running total and the
later additions on top of this).

**`seed_review_queue.py`**: hashes each case (audit entry + matcher
report row combined) — same hash on re-seed is a no-op, a different hash
is an explicit conflict, printed and left untouched, never silently
overwritten. Entries whose `root_cause` starts with the investigator's
`_TOTAL_FAILURE_MARKER` (a failed run's placeholder) are treated as
"never investigated," so a failed Ollama call can't permanently block a
later real result from enriching that case.

**Primary-proposal source, decided once per case at first-seed time**:
`investigator/`'s multi-round investigation (`data/investigation_log.jsonl`)
becomes the case's PRIMARY AI proposal — populating the same
`agent_exception_type`/`agent_root_cause`/`agent_confidence`/etc. columns,
with `agent/gate.py`'s `apply_gate()` recomputed fresh against it (the log
only stores the shallow `gate_decision`, not the full per-condition
breakdown) — if a real investigation already exists at the moment a case
is first inserted. Otherwise the single-shot `agent/`'s proposal
(`data/audit_log.jsonl`) is used, exactly as before. Which source won is
recorded in a `resolution_source` column (`'agent'` | `'investigator'`).
This choice is made ONCE, at first insert, never retroactively — a case
seeded before its investigation existed keeps the single-shot agent/'s
proposal as primary forever, even if `investigator/` catches up later
(that stays an additive enrichment of the `investigation_*` columns, not
a swap of the frozen primary proposal — preserves the "AI's original
proposal is immutable" rule in §9 exactly). Snapshot after a full reseed
against the curated dataset: 92/603 cases have `investigator/` as primary
(all 92 real, non-failed entries in `investigation_log.jsonl` at that
time — re-verify this number, it grows as more cases get investigated).

**Frontend**: React + TypeScript + Tailwind v4 (CSS-first `@theme`
config, no `tailwind.config.js`) + Recharts + TanStack React Query.
`ui/review-queue-app/src/`: `types.ts` (hand-mirrors the API response
shapes), `api.ts`, `hooks/useQueries.ts`, `lib/format.ts`, `App.tsx`,
`components/` (KpiCards, charts/, FilterBar, CaseTable, DetailPanel +
`detail/` subfolder). `STATUS_LABELS`/`STATUS_COLORS` (`lib/format.ts`)
and `STYLES`/`DOT` (`components/ui/Pill.tsx`) are exhaustive
`Record<CaseStatus,...>` types — adding a new status without an entry
there is a **build failure**, not cosmetic polish, worth remembering.

**Wording/attribution fixes, following an external frontend review**: three
places (`AiBanner.tsx`, `ReviewForm.tsx`'s revert-only form, `CaseTable.tsx`'s
table badge) said "the AI resolved this" for an `auto_resolved` case — since
the deterministic gate is what actually authorizes auto-resolution (the AI
only proposes, see this file's core rule), all three now attribute the
decision to the gate, with the AI's role stated as "proposed." Separately,
`AutoClosedBanner.tsx` claimed a specific causal reason ("the underlying
condition (e.g. a delayed bank posting) has resolved") the system has no
actual evidence for — it only knows the transaction is now clean, not *why*.
Rewritten to state only what's verified (previously open → later matcher
run found it genuinely clean, not merely reclassified) and point to the
activity log for the real before/after detail, which now genuinely contains
it (see Layer 7's re-verification section). The two banners now also read
as distinctly different lifecycles (auto-resolved from the start with no
review needed at all, vs. previously open and later closed by a fact
changing) rather than two interchangeable "automated, done" states.

**Investigation tool trace redesigned**: `InvestigationSection.tsx`'s
`ToolTraceItem` used to cram `[step] tool_name(args)` and a 200-char-truncated
result into one compact code block with no way to see more. Now visually
separates Tool / Arguments / Result, pretty-prints the JSON result, and adds
a "Show full result" toggle for results over 200 chars — the compact preview
stays the default, full evidence is one click away. The `gate_decision ===
"auto_resolve"` badge was also ambiguous: `investigation_gate_decision` is
the investigation's own gate verdict, which is only what actually happened
to the CASE when this investigation is the primary proposal
(`resolution_source === "investigator"`) — for an enrichment-only
investigation (primary is the single-shot agent), it's a counterfactual
("this evidence alone would clear the gate"), not a claim about the case's
real status, which could still be `pending`. `DetailPanel.tsx` now passes
`isCasePrimary` through so the badge reads "AUTO-RESOLVED" only when true,
"GATE: WOULD AUTO-RESOLVE" otherwise (with a tooltip explaining the
distinction either way).

**Override flow now shows old → new explicitly**: `ReviewForm.tsx` captured
`override_old_value` programmatically for the API payload but never showed
it to the reviewer before submission — added an "AI's current value" readout
above the "Override to" field, reactively updated as the field selector
changes, so a reviewer sees exactly what they're changing before they submit.

**Stale-review (409) handling**: `ReviewForm.tsx` used to show the backend's
raw error text for every failure alike. A 409 specifically (case changed —
`expected_review_count` mismatch, or a stale `override_old_value`) now gets
its own distinct message plus a "Refresh case" button
(`queryClient.invalidateQueries(["case", transaction_id])`) instead of
leaving the reviewer to figure out on their own that a refetch is what's
needed.

**Two real bugs found via live browser verification of the above** (not
theoretical — both confirmed with real cases, both fixed):
1. `agent/evidence.py`'s `EVIDENCE_LABEL_TO_FIELD` maps `EVIDENCE-1` to
   `transaction_id`, but `seed_review_queue.py`/`backfill_evidence_display.py`
   both build `report_by_txn` via `report.set_index("transaction_id")
   .to_dict(orient="index")` — which drops `transaction_id` as a regular
   dict key (it becomes the index instead), so every `EVIDENCE-1` citation
   resolved to `None` regardless of validity. Fixed by passing
   `transaction_id` as `_build_evidence_fields_cited()`'s own explicit
   parameter instead of mutating `report_by_txn`'s dicts (an initial
   attempt at exactly that mutation caused a real, separate regression —
   see `cash_position/`'s section below, "A real regression caught
   mid-fix," for the full story); re-ran `backfill_evidence_display.py`
   against the live database (27 of 603 cases corrected — those citing
   `EVIDENCE-1`).
2. `DetailPanel.tsx`'s "Evidence cited" section rendered every value with
   `String(f.value)` — for a `TOOL-N` citation (whose value is a whole
   tool-result object/dict, not a scalar), this produced the literal text
   `[object Object]` instead of the actual evidence. Fixed to
   `JSON.stringify(value, null, 2)` for object/array values, and to check
   the API's own `note` field directly (rather than inferring "unknown
   citation" from `value === null`, which coincidentally worked before but
   wasn't actually checking the right thing).
Both verified live in the browser (`trn-001695` for #1, `trn-000072` for
#2) and via a clean `npm run build`; `test_review_api.py` unaffected (55/55).

### investigator/ (Layer 6) — tool-using investigation agent
`agent/client.py` is a single-shot classifier (one LLM call, one verdict)
— not actually agentic. `investigator/` is a hand-rolled (no framework)
multi-step ReAct-style loop: the model gets real tools, decides what to
investigate, calls tools across up to `MAX_TOOL_ROUNDS=6` rounds, then is
forced into a final structured verdict (`InvestigationResult`, a strict
superset of `agent/schema.py`'s `ExceptionResolution`) that flows into the
same, unmodified `agent/gate.py`.

**Tools** (`tools.py`, all deterministic Python over real data, no tool
invents a number): `get_transaction_details` (the case's own authoritative
gateway record — payment method, gross/fee/tax, `signature_valid`,
refund fields — beyond the compact initial evidence block, for cases like
`signature_verification_failed`/`partial_refund` where the initial block
alone isn't enough), `get_settlement_details` (settlement-level match
facts — member transactions, matched bank posting(s), confidence, amount
delta — for N:1/1:N cases where the settlement itself, not any one member
payment, is the unit that matters), `calculate_settlement_variance` (one
call for the full financial breakdown — gross, expected fee/tax, expected
vs. observed net — instead of manual `compute_delta` chains for a
domain-shaped calculation), `lookup_related_transactions`,
`search_bank_statement` (derives its own date window/expected amount from
context — never takes raw dates from the model, which would otherwise
have nothing to anchor to and could hallucinate a search window),
`compute_delta`. All three new tools were added following the investigator
review pass below; they use `matching/loaders.py`'s `_rupees` columns
(added alongside the raw `_paise` ones by `load_sources()`) directly, no
manual paise conversion. `ollama_client.py` has two call shapes: `chat_with_tools` for
investigation rounds, `final_answer` (`format="json"`) for the verdict —
both wrapped in defensive error handling, so a slow/failed round degrades
to a safe low-confidence "escalate without a completed verdict" result
instead of crashing, even under a total Ollama outage.

`run_investigator.py`: `--transaction-id <id>` for one case, `--n <k>`
for a stratified sample (k per exception type — NOT a total), or
`--exception-type <type> --n <k>` to target a specific type's backlog
directly, skipping cases already in `investigation_log.jsonl` (useful for
topping up coverage without re-running cases already done). Writes each
result to the log immediately after that case finishes, not batched at
the end — a multi-hour run survives being cancelled partway. `--model
<name>` overrides `config.INVESTIGATOR_MODEL` for any Ollama model already
pulled — see below for the two real findings that shaped the defaults.

**Speed, verified not guessed**: real logged timings showed even a single
tool round costs 45-75s baseline, climbing further per round — pointing at
Qwen3's "thinking" mode (hidden chain-of-thought tokens before every
visible response) rather than round count (no case has ever needed more
than 4 of the 6 allowed rounds). A controlled A/B on the identical real
case confirmed it: `qwen3:8b` with thinking on took 635.1s; with `think:
false` sent in the Ollama request, 159.6s — same 2 tool calls, same
exception_type, same policy_id, same 0.95 confidence, same gate outcome,
byte-for-byte identical, just 4x faster. `investigator/config.py`'s
`THINK_MODE = False` is now the default for exactly this reason (still
overridable via `OllamaToolClient(think=...)`).

**`qwen3:1.7b` is now the default model** (`investigator/config.py`'s
`INVESTIGATOR_MODEL`, a deliberate choice, not the original one): validated
across 4 real cases spanning 4 different exception types
(`missing_bank_reference`, `deemed_success_ambiguous`, `partial_refund`,
`signature_verification_failed`) — 4/4 correct exception_type, 4/4 correct
policy_id citation, 0 tool-call errors, confidence 0.90-0.95, ~48-97s/case
(vs. `qwen3:8b`'s 159.6s with thinking off). Real evidence, but a much
smaller sample than the published benchmark that originally justified
`qwen3:8b` — worth re-checking against a broader batch if reliability
issues ever show up; `qwen3:8b` remains available as the more heavily
-validated fallback via `--model qwen3:8b` or `INVESTIGATOR_MODEL=qwen3:8b`.
(For reference, `phi3:mini` was also tried and ruled out: fastest at
42.7s, but its tool-call request errored with a 400 and it never actually
called a tool — the verdict only looked right because it pattern-matched
the evidence block directly, which defeats the entire point of an
agentic, tool-verified investigation.)

**Deterministic pre-routing, not a trained classifier**: every run prints
how many of the escalated backlog are *structurally reachable* for
auto-resolve at all (`agent.gate.is_investigation_worthwhile()` — allowlist
membership + amount<₹5,000, zero LLM involvement, computable before any
call is made) — currently 8/603 (1.3%). `--reachable-only` restricts
stratified/`--exception-type` sampling to that pool, so a large top-up
batch (e.g. a Kaggle GPU session) can spend its multi-minute-per-case
budget only where investigation depth could actually change the gate's
decision, instead of on the 595 cases guaranteed to escalate regardless.
Deliberately NOT an ML model: a classifier trained to predict this boundary
would just be approximating a two-line boolean we can already compute
exactly, with less accuracy and a training pipeline to maintain — the
project's "AI proposes, deterministic code disposes" rule applies to the
routing decision too, not just the final auto-resolve/escalate one.
`--transaction-id` is never affected by `--reachable-only` — an explicit
request is always honored, e.g. to demo an investigation trace on a case
that's going to escalate anyway.

Verified real, model self-corrects when it calls a tool with an
undeclared argument (a `TypeError` gets fed back as a tool result, and the
model retries correctly on the next call) — reproduced independently
across multiple real cases.

**Two real fixes, following an external review pass on the tool layer**:

`search_bank_statement()`'s own docstring always claimed it searched for an
*unclaimed* bank posting, but the implementation searched the entire bank
dataframe by date+amount alone — a date/amount coincidence with a posting
already consumed by a DIFFERENT settlement's match could be shown as if it
were available evidence for the current case. `ToolContext` now takes
`settlement_matches` (from `run_matcher.run()`, already available at every
call site) and builds `claimed_bank_txn_ids` from every settlement's
`matched_bank_txn_ids`; each returned candidate now carries a
`candidate_status` (`unclaimed` vs `already_matched_elsewhere`) plus an
`unclaimed_candidate_count` summary, and the tool schema's description
tells the model to only cite unclaimed ones. Confirmed this is a real,
observed phenomenon on the curated dataset, not theoretical: `trn-000007`
shows `candidate_count: 1` but `unclaimed_candidate_count: 0` — the one
candidate is already matched elsewhere.

A tool round failing mid-investigation (`stopped_reason:
"tool_round_failed: ..."`) previously didn't constrain the model's own
*separate* `final_answer()` call at all — the model could still claim
`sufficient_evidence=True` after an incomplete investigation, and nothing
stopped that from reaching `agent/gate.py`'s `sufficient_evidence`
condition. `investigator/loop.py` now deterministically overrides
`sufficient_evidence=False` (and caps confidence at 0.3) whenever
`stopped_reason` indicates a failed round, regardless of what the model
itself claimed — the override, not a flag for a human to notice later.

**Tool-result evidence citations (`TOOL-N`)**: `agent/evidence.py`'s
`KNOWN_EVIDENCE_FIELDS` only covers the static `EVIDENCE-N` fields shown
in the initial block — it has no way to know about a per-investigation
tool result. `GENERAL_INSTRUCTIONS` (`investigator/loop.py`) now tells the
model to cite tool results as `TOOL-1`, `TOOL-2`, ... in call order
(alongside any `EVIDENCE-N` fields used), and only to cite
`search_bank_statement` candidates marked `unclaimed`, never
`already_matched_elsewhere`. `investigator/loop.py`'s `tool_evidence_ids()`
computes the valid `TOOL-N` set from `InvestigationResult.investigation_log`
(one per real tool call, in order) and is threaded through
`agent/gate.py`'s `apply_gate()` via its new `extra_valid_evidence_ids`
parameter (optional, defaults to `frozenset()` — `agent/`'s single-shot
path never passes any, so its citation validation is unchanged). Every
investigator call site passes it: `run_investigator.py`, `run_demo.py`'s
`--live-case`, and `seed_review_queue.py`'s `_primary_from_investigation()`
(which recomputes the gate from the raw JSONL log entry, so it derives the
same `TOOL-N` set inline from `investigation_log`'s length rather than
calling `tool_evidence_ids()` directly, since that function expects an
`InvestigationResult` object, not a raw dict). Without this, every
tool-cited investigator verdict would show a false-positive "unknown
evidence citation" on `unknown_evidence_citations` — informational only,
never a gate condition, but a real correctness gap for what a human
reviewer sees.

**A second, deeper citation bug found via that same live test**:
`agent/evidence.py`'s `KNOWN_EVIDENCE_FIELDS` held only raw report-row
field names (`transaction_id`, `merchant_id`, ...), but `build_evidence()`
labels every line it shows the model `[EVIDENCE-N]`, and both
`GENERAL_INSTRUCTIONS` prompts (`agent/client.py`'s and, explicitly,
`investigator/loop.py`'s "cite ... EVIDENCE-N fields you used") point the
model at citing that label. A real live run (`qwen3:1.7b` on
`trn-000001`) proved it: all 5 of its genuine `EVIDENCE-N` citations were
flagged as unknown before the fix, 0 after. `KNOWN_EVIDENCE_FIELDS` is now
the union of both conventions — the raw field names (kept for
`agent/providers/mock.py`, which cites `final_exception_type` directly)
and `EVIDENCE-1`..`EVIDENCE-10` (`agent/evidence.py`'s new
`EVIDENCE_LABEL_COUNT` constant, kept adjacent to `build_evidence()` so
the two stay paired).

### cash_position/ (Layer 4)
`engine.py` builds the confirmed/in-transit/at-risk snapshot;
`reconciliation_statement.py` builds a full bank-reconciliation bridge
(Books Ending Balance → deductions → adjusted confirmed balance, tied to
Bank Statement Ending Balance via settlement-level population matching).
The bridge's final variance (~0.13% on the main dataset) is a real,
explained residual — shortage/overage-tolerance amounts inside batched
settlements that contain both confirmed and unconfirmed member
transactions can't be attributed to one member over another without
inventing a rule — reported honestly, not forced to zero.

Two invariants proven, not just claimed, following an external review pass:
`_bank_side_coverage()`'s five bank-side buckets (matched-confirmed,
matched-other-exception, ambiguous, orphan, unexplained) partition the
full bank statement by construction — `unexplained_mask` is defined as the
literal complement of the other four — but this is now enforced, not just
asserted in a comment: a nonzero `unexplained_count` raises
`ReconciliationInvariantError`, which propagates as a real HTTP 503 (see
review_backend/main.py's existing error handling), never a silently wrong
dashboard number. `reconciliation_tied` (a dashboard-ready boolean) uses a
*relative* tolerance (`config.RECONCILIATION_TIE_TOLERANCE_PCT = 0.5%`,
`cash_position/config.py`), not exact-zero — the known ~0.13% residual
above is real and explained, so an exact-zero tolerance would flag known
-good state as untied; 0.5% comfortably clears it while still catching a
genuinely broken bridge. `build_cash_position()` also now returns
`forecast_complete`/`transactions_beyond_horizon_count`/
`rupees_beyond_horizon` as structured fields (previously only a stdout
warning from `build_daily_forecast()` — invisible to any API caller).

**Terminology tightening, following an external review of the latest
reconciliation/cash-position/stream files** (most of its 19 claims were
either already true when checked — the bank-side breakdown already shows
the exact tree structure requested, `books_ending_balance` was already
labeled "(internal settlement ledger)" in the CLI, orphan wording was
already conservative/evidence-based, `requirements.txt`/cache/hash-fingerprint
behavior were all explicitly "keep as-is" per the review's own text — or
out of scope for a hackathon per the review's own hedging): `run_reconciliation_statement.py`'s
"the number that actually proves the bridge ties out" only ever existed as
an internal code *comment*, never printed — the real CLI output already
only claims "RECONCILIATION CHECK," never a hard tie-out guarantee. Still,
the CLI never surfaced the `reconciliation_tied` boolean
`build_reconciliation_statement()` already computes — added an explicit
three-way classification line (`TIED` / `EXPLAINED RESIDUAL` /
`CONTROL FAILURE`) using that existing field, so a reader doesn't have to
eyeball the raw rupee/percent figures to know which bucket a run falls
into. `run_cash_position.py`'s "CASH POSITION SNAPSHOT" headline retitled
"CASH POSITION — SETTLEMENT-DERIVED SNAPSHOT" with an explicit one-line
qualifier — it's a classification of matcher-reconciled transactions, not
a separately-sourced treasury/GL cash figure. `run_demo.py` now prints
"AI artifacts: using previously generated, audited LLM results / LLM
calls this run: 0" explicitly in its environment-check section, instead of
leaving that fact to the docstring only a source-reader would see.

**Stream `as_of` labeling**: CLAUDE.md's own Known Limitations table
already documents that cash-position `as_of` stays fixed
(`cash_position/config.py`'s `DEFAULT_AS_OF`) while the stream simulator
advances its own simulated clock — a real, accepted, unfixed gap (fully
deriving `as_of` from the simulated clock would need the running
`review_backend` subprocess to track live state, a materially bigger
change than a local demo warrants). Rather than leave that invisible in
the UI, `ui/review-queue-app/src/components/KpiCards.tsx`'s "Reconciled
₹"/"In transit ₹" cards now carry an explicit tooltip (a `*` marker plus
title text) whenever `stream_mode` is true, so the badge showing a "live"
simulated clock next to these two static-reference-date figures can't be
misread as both being driven by the same timestamp. No tooltip in the
main static demo, where there's no second clock to be inconsistent with.

**Stream tick-parameter validation**: `run_stream_simulator.py` now
rejects `--tick-seconds <= 0` / `--duration-minutes <= 0` outright (the
old code would have divided by zero or silently done nothing useful), and
caps the projected tick count at 5,000 — a tiny `--tick-seconds` would
otherwise silently queue up thousands of full matcher runs. Also now
prints "Fresh demo run" explicitly (previously only the resuming case
printed anything at all) for the same reason `run_demo.py`'s new AI
-artifacts line exists: state that should be obvious don't leave it
implicit.

**A real regression caught mid-fix, before it reached committed data**:
the first attempt at fixing `EVIDENCE-1`'s "(transaction_id)" display bug
(see `agent/` section above) mutated `report_by_txn`'s dicts in place to
restore `transaction_id` as a key — but `seed_review_queue.py`'s
`_canonical_hash()` hashes that exact same dict for its conflict-detection
check, so every existing case's freshly-recomputed hash stopped matching
its stored one, and a `run_demo.py --skip-server` run flagged all 603
cases as "conflicted." The safety design worked exactly as intended —
conflicts are refused, never silently overwritten, so no data was actually
corrupted — but the fix itself was wrong. Corrected by leaving
`report_by_txn` untouched and instead threading `transaction_id` as
`_build_evidence_fields_cited()`'s own explicit parameter (used only when
resolving the `EVIDENCE-1 → transaction_id` mapping); both
`seed_review_queue.py` and `backfill_evidence_display.py` updated to
match the new signature. Verified: `run_demo.py --skip-server` back to
"Already seeded, unchanged: 603," and a fresh `backfill_evidence_display.py`
run correctly found only the one genuinely-new case needing the fix.

### airflow/ (Layer 7) — closed-loop re-verification
**The gap it closes**: once a case is escalated into the review queue, it
stayed open forever even if the underlying condition later resolved (e.g.
a delayed bank posting finally arrives). The static main demo dataset
never changes, so this is only ever demonstrable against the stream
simulator's progressively-revealed data.

**Mechanism**: `POST /api/reverify` (`review_backend/main.py`) re-runs
`run_matcher.run(CASH_POSITION_DATA_DIR)`, finds every case still awaiting
human review whose transaction the matcher now reports **fully clean**
(`final_exception_type is None`), and calls the existing `submit_review()`
directly in-process with `decision="auto_closed"` — reusing 100% of
existing optimistic-concurrency, validation, and audit-trail logic.
Two-pass design (read candidates, then write with no held-open connection)
so a concurrent human review — or a concurrent second `/api/reverify`
call — loses the race safely via the pre-existing `expected_review_count`
guard rather than corrupting anything. Verified under real concurrent load
(Airflow's own scheduled run racing a manual trigger): zero duplicate
closures, zero crashes.

**A real correctness bug, found via external review and fixed**: the
candidate check used to be "is this transaction_id still in the set of
currently-escalated, non-matcher-auto-resolvable exceptions" — which
conflates "the ORIGINAL condition is gone" with "no exception remains at
all." A transaction originally escalated as `missing_bank_reference`
could re-run as `fee_variance` (one of the 3 types the matcher itself
auto-resolves, see §6) and would have dropped out of that set — getting
silently `auto_closed` with a misleading "the underlying condition has
resolved" note, even though a real, different, never-reviewed exception
had simply replaced the original one. Fixed to require the fresh matcher
run show the transaction as genuinely clean (`is_clean`), full stop —
reclassification to a different, still-technically-auto-resolvable type
now correctly leaves the case open. A candidate transaction_id missing
from the fresh matcher run entirely (shouldn't happen in practice — see
below) is also left untouched, never treated as "safe to close." Proven
with a new `test_review_api.py` scenario (`trn-test-rv-reclassified`)
that would have caught the original bug (53/53 passing at the time, up
from 51 — see the response-categorization paragraph below for the further
2 added on top). The existing `fake_run_matcher()` test fixture was also corrected
to include resolved cases as genuinely clean ROWS (`final_exception_type
=None`) rather than omitting them from the report entirely — the real
matcher always produces a row for every ledger transaction it can see,
it never just drops one, so the old fixture was testing a shape that
can't actually happen.

**`/api/reverify`'s response now categorizes every still-open case, not
just closures**, following a second external review pass on the same
mechanism: `checked` cases (every `pending`/`pending_manager_approval`
case examined) now split into `closed` (genuinely clean, unchanged from
before), `changed_exception` (still has an exception, but a DIFFERENT one
than `cases.matcher_exception_type` — informational only, no state
transition exists or was added for this, the case simply stays `pending`)
and `still_open` (same original exception persists, or the transaction
isn't currently observable at all). Without this, "still pending" looked
identical whether the matcher re-confirmed the exact same original problem
or quietly swapped in a different, never-reviewed one — real audit/demo
value, not just cosmetics. `changed_exception` entries carry
`{transaction_id, original_exception_type, current_exception_type}`.
`airflow/dags/reverification_dag.py`'s task log updated to print the new
counts and, for each reclassified case, an explicit
`original -> current -- remains open` line, closing the loop from backend
to the Airflow UI a judge would actually look at. Verified live against
the real main-demo database (`checked=597, closed=[], changed_exception=[],
still_open=[...597 ids]` — correct, since that dataset never changes) and
via two new `test_review_api.py` assertions on the synthetic reclassified
case: 55/55 passing.

**Reviewed and found not worth pursuing**: the same review flagged a
narrower TOCTOU risk — the matcher snapshot used for candidate selection
could theoretically go stale by the time pass 2's write actually commits.
The review's own text hedges this as "probably acceptable for the current
controlled local simulator," and it's structurally minor here: both passes
already read from the SAME in-memory `report` (one matcher run per
`/api/reverify` call, not one per candidate), and
`run_stream_simulator.py`'s tick-loop snapshot writes are already atomic
(`os.replace()`, fixed earlier — see below) so a reverify call can only
ever see a fully-pre-tick or fully-post-tick snapshot, never a torn one.
Re-running the full matcher before every individual candidate's close
would add real cost (matcher runs are ~0.7-1.2s at this scale) for a
race window measured in seconds on a synthetic single-operator demo — not
implemented, consistent with this project's "don't add defensiveness for
scenarios that can't happen" discipline.

**Orchestration**: Apache's own official Docker Compose file (fetched via
`curl`, not hand-written) — CeleryExecutor, 8 services (postgres, redis,
apiserver, scheduler, dag-processor, worker, triggerer, init). Heavier
than a toy setup but genuinely the real, current, production-lineage
deployment topology, which is the point. `airflow/dags/reverification_dag.py`
is a single `PythonOperator` calling `requests.post()` against
`http://host.docker.internal:8001/api/reverify` — Airflow's containers
never import this project's Python or touch `requirements.txt`, they only
make one HTTP call on a schedule (`*/1 * * * *`) or on manual trigger.
`retries=2, retry_delay=30s` on that task, added following an external
review pass — safe specifically because a retry after a lost response
just re-reads whichever cases are still pending (already-closed ones drop
out of the next candidate list), never double-processes anything.

**A real bug found and fixed while building this**: `run_stream_simulator.py`'s
`write_filtered_snapshot()` used to overwrite `data/stream/*` files
directly every tick (no atomic swap). `POST /api/reverify` was the first
code in the project to ever read that directory from an independent,
concurrent process — a read landing mid-write produced a torn file that
crashed the matcher several layers downstream, in code that was never
actually wrong about the data itself. Fixed at the root: writes now go to
a `.tmp` file, swapped into place with `os.replace()` (atomic on POSIX and
Windows). Worth remembering for any future code that reads `data/stream/`
from outside the simulator's own tick loop.

---

## 8. Known limitations

| Limitation | Impact | Why acceptable today | Production fix |
|---|---|---|---|
| Greedy bank-row consumption is order-dependent | A losing settlement in a rare ambiguous-shared-candidate case is left unmatched, never wrong-matched | Documented, tested (`test_ambiguity.py` Scenario 7), safe failure mode | Global assignment solver |
| Settlement splits are 1:2 only | Narrower than "1:N" comments imply | Matcher and data generator agree, internally consistent | Generalize to N-way splits |
| Float rupee arithmetic, not integer paise | Rounding risk at scale | `EXACT_MATCH_TOLERANCE_RUPEES=0.02` safely absorbs float64 precision at this dataset's volumes | Integer paise or `Decimal` |
| Reconciliation-bridge residual (~0.13%) | Small unattributed variance | Genuine shortage/overage-tolerance amounts inside mixed (confirmed + unconfirmed member) settlements, can't be attributed without inventing a rule | Explicit per-member attribution logic |
| Cash-position `as_of` under the stream simulator | Uses a fixed date tuned for the complete dataset | Fine for demo purposes, not for a genuinely live feed | Derive `as_of` from the stream's own simulated clock |
| Airflow's re-verification only produces real closures against the stream simulator | The main static demo always reports 0 closures | Correct, not a bug — the main dataset never changes | N/A — inherent to a static demo dataset |

Closed-loop re-verification (previously the one open item here — cases
stayed open forever) is now built; see §7's Layer 7 section. Postgres for
`review_backend/`'s own data (previously the other open item — SQLite was
a single-writer bottleneck) is also now built; see §7's Layer 5 storage
note.

**Not yet done** (queued, no code written):
- Cross-case root-cause clustering (would use `sentence-transformers`,
  local/free, for embedding).
- Langfuse tracing integration (identified as the right tool for this;
  `investigation_log` on `InvestigationResult` currently serves as a
  manual, less-featured substitute).

---

## 9. Working principles

- **Verify before claiming, always.** Every number in §6 was checked by
  actually running code, not assumed. When you fix something, re-run and
  show the actual before/after.
- **External reviews (uploaded docs from other AI tools) are unverified
  input, not ground truth.** They reliably contain a mix of real findings
  and confidently wrong ones. Verify every specific, falsifiable claim
  against the actual running code before acting on it, and say clearly
  which parts turned out wrong — don't just silently fix the real ones.
- **This machine has real, reproducible environment quirks** (§4). When
  something looks broken, check whether it's the actual code or a
  measurement/environment artifact before "fixing" it — confirmed cases of
  both directions this project.
- **Escalate > guess, always.** Every ambiguity-handling decision
  (matcher, agent, gate, review-queue state machine, re-verification) errs
  toward escalating for human review over silently picking one and hoping.
  Never optimize away escalations to make a metric look better.
- **Ground truth is sacred.** Never let `matching/`, `agent/`,
  `cash_position/`, `investigator/`, `review_backend/`, `ingestion/`, or
  `airflow/` read `ground_truth.csv`. Only `evaluate.py` touches it, only
  for scoring.
- **The AI's original proposal is immutable.** `seed_review_queue.py`
  never overwrites a seeded case's frozen AI-proposal columns; later
  layers (investigation results, re-verification's `auto_closed` decision)
  are additive, never destructive, and always attributable (a human's
  review, the investigator, or `system:closed-loop-reverification` — never
  ambiguous which).
- **Repo hygiene**: this repo is not git-initialized. Deletions are
  unrecoverable — when removing something with real content, prefer
  moving it aside over hard-deleting unless it's genuinely valueless
  (generated caches, unmodified scaffold boilerplate, stray temp files).
  `__pycache__/`, `airflow/logs|config|plugins/` are gitignored; none of
  them should ever need manual cleanup beyond an occasional sweep.
- **Update this file in the same turn as the change, not "later."** When
  a real change lands — a new file, a fixed bug, a verified number, a
  scope decision — reflect it here before moving on. Keep it describing
  current state, not a growing history of turns.
