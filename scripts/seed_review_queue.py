"""
Seed the review queue database from data/audit_log.jsonl -- entrypoint.

Only escalated cases (gate_final_decision == "escalate") go into the review
queue; the gate's own auto-resolved cases don't need a human.

Re-running this script is safe: each case is hashed (the audit-log entry
plus the matcher's report row for that transaction, since the evidence
trace needs both). Same transaction_id + same hash -> no-op. Same
transaction_id + a DIFFERENT hash -> refuses with an explicit conflict
error rather than silently overwriting the case a human may have already
reviewed. This was a direct fix for an earlier draft of this design that
specified "upsert by transaction_id" -- an upsert is a mutation, which
directly contradicted this project's own stated rule that the AI's
original proposal must stay visible forever.

Primary-proposal source, decided ONCE per case at first-seed time:
investigator/'s multi-round investigation (data/investigation_log.jsonl)
wins over the single-shot agent/'s proposal (data/audit_log.jsonl) if it
already exists at that moment -- investigator/ is strictly richer
(real tool use, grounded evidence gathering) than a single LLM call.
A case seeded BEFORE its investigation existed keeps the single-shot
agent/'s proposal as primary forever, even if investigator/ catches up
later (that's enrichment, tracked separately -- see
`_investigation_fields()` -- never a retroactive swap of what's already
frozen). `resolution_source` on each case records which source won.

    python scripts/seed_review_queue.py
"""

import os as _os
import sys as _sys
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)
_SCRIPTS_DIR = _os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPTS_DIR)

import os
import json
import types
import hashlib
import datetime as dt

from run_matcher import run
from agent.gate import apply_gate
from review_backend import db
from review_backend.db import SCHEMA_VERSION
from review_backend.config import MANAGER_APPROVAL_THRESHOLD_RUPEES

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
AUDIT_LOG_PATH = os.path.join(DATA_DIR, "audit_log.jsonl")
INVESTIGATION_LOG_PATH = os.path.join(DATA_DIR, "investigation_log.jsonl")


# investigator/loop.py writes this placeholder root_cause when even the
# final-answer call couldn't reach the model at all (e.g. Ollama wasn't
# running) -- a total infra failure, not a real investigation result.
# Defined here (used by both functions below) rather than only where
# _investigation_fields() needed it originally.
_TOTAL_FAILURE_MARKER = "[INVESTIGATION FAILED TO PRODUCE A FINAL ANSWER"


def _is_failure_entry(entry: dict) -> bool:
    return (entry.get("root_cause") or "").startswith(_TOTAL_FAILURE_MARKER)


def _load_investigations(path: str) -> dict:
    """Keyed by transaction_id, keeping the best entry on record if a case
    was investigated more than once (investigation_log.jsonl is append-only
    across runs, same as audit_log.jsonl). Returns {} if the file doesn't
    exist yet -- investigator/ is optional, most cases won't have this.

    "Best" is NOT simply "last line in the file" -- that was a real bug,
    found by tracing why only 6 of 8 structurally-reachable
    deemed_success_ambiguous cases had actually been enriched with a real
    investigation, when investigation_log.jsonl showed all 8 had a genuine
    successful run (confidence 0.95+, sufficient_evidence=True,
    gate_decision=auto_resolve) on record. Two of them (trn-000237,
    trn-000424) had their real successes silently discarded: this project's
    investigation history genuinely alternates success/failure/success/
    failure across separate sessions (local runs, Kaggle retries), and for
    those two the file happens to END on a failure placeholder. The old
    "last line wins, no matter what" logic handed that failure straight to
    _investigation_fields(), which correctly recognized it as not a real
    investigation and reported "never run" -- discarding four genuine
    successes that existed earlier in the same file purely because of
    where a later RETRY happened to get appended, not because the retry
    was actually more informative.

    A failed retry must never erase an already-recorded real result --
    that was _investigation_fields()'s own stated intent ("must not...
    permanently block a later real investigation from being picked up"),
    which only holds if the loader actually surfaces the real one to it.
    Preference order per transaction_id: a real (non-failure) entry always
    wins over a failure entry, regardless of which is later in the file;
    among two entries of the same kind, the later one wins (matching the
    original append-only-log "latest run" intent)."""
    if not os.path.exists(path):
        return {}
    best: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            txn = entry["transaction_id"]
            existing = best.get(txn)
            if existing is None or _is_failure_entry(existing) or not _is_failure_entry(entry):
                # No prior entry, OR the prior one was a failure (a real
                # result -- or even a later failure -- may replace it), OR
                # this new entry is itself real (a real entry always wins
                # over whatever came before, failure or not). The one case
                # that must NOT overwrite: existing is real AND entry is a
                # failure -- falls through to the implicit `else: pass`.
                best[txn] = entry
    return best


def _canonical_hash(audit_entry: dict, report_row: dict) -> str:
    payload = {"audit": audit_entry, "report": report_row}
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _build_evidence_fields_cited(cited_names: list, report_row: dict, transaction_id: str,
                                  investigation_log: list | None = None) -> list:
    """Cross-reference the LLM's self-reported evidence_used field names
    against the matcher's actual report row (for EVIDENCE-N citations) and
    the real tool-call trace (for TOOL-N citations, investigator/ cases
    only), so the review UI shows real values, not just a list of
    field-name strings.

    A citation label like "EVIDENCE-4" is never itself a key in report_row
    (whose real keys are "final_exception_type" etc.) -- a plain
    `if name in report_row` lookup can never match it regardless of
    whether the citation is valid, which is exactly the bug this fixes
    (found live in the review UI: trn-000072 showed all 6 of its real
    citations, including 2 genuine tool calls, as "not a known evidence
    field"). EVIDENCE_LABEL_TO_FIELD (agent/evidence.py) is the single
    source of truth for what each EVIDENCE-N label represents; TOOL-N is
    resolved positionally against investigation_log, exactly matching
    investigator/loop.py's own tool_evidence_ids() citation convention.

    ALSO resolves a citation that names a tool DIRECTLY (e.g.
    "search_bank_statement") instead of its TOOL-N label -- found live,
    the same way, on trn-000555: a case with two genuine, real tool calls
    whose evidence still showed both as "not a known evidence field",
    because the model cited them by tool name rather than the instructed
    TOOL-N shorthand. Checked at scale before treating this as worth
    fixing, not assumed: 148 of 313 real investigations (47%) do this for
    at least one citation -- the model's dominant style for tool-result
    citations, not a rare slip. Resolved against the FIRST investigation_log
    entry with that tool_name (a citation names a TOOL, not a specific call
    index, so there's no more precise match available if a tool was ever
    invoked more than once in one investigation -- doesn't happen in
    practice today, but the ambiguity is real and this is the honest
    resolution of it). Still correctly falls through to "not a known
    evidence field" for a tool name that was never actually called --
    widens the accepted citation FORMAT, not what counts as genuine
    evidence.

    transaction_id is taken as its own argument, NOT read off report_row --
    report_row comes from report_by_txn, whose dicts are also fed into
    _canonical_hash() unmodified; report.set_index("transaction_id") drops
    transaction_id as a regular column (it becomes the dict's own key
    instead), and injecting it back into report_row would change what gets
    hashed, flagging every existing case as "conflicted" on the next
    re-seed (a real regression, caught live and reverted -- see seed()'s
    own comment on report_by_txn)."""
    from agent.evidence import EVIDENCE_LABEL_TO_FIELD

    tool_by_label = {
        f"TOOL-{i + 1}": record for i, record in enumerate(investigation_log or [])
    }
    # First occurrence wins if a tool was ever called more than once in one
    # investigation -- see this function's docstring for why that's the
    # honest resolution of a genuinely ambiguous case, not a guess.
    tool_by_name = {}
    for record in investigation_log or []:
        tool_by_name.setdefault(record.get("tool_name"), record)

    out = []
    for name in cited_names:
        if name in EVIDENCE_LABEL_TO_FIELD:
            field = EVIDENCE_LABEL_TO_FIELD[name]
            value = transaction_id if field == "transaction_id" else report_row.get(field)
            out.append({"field": f"{name} ({field})", "value": value, "cited": True})
        elif name in tool_by_label:
            record = tool_by_label[name]
            out.append({"field": f"{name} ({record.get('tool_name')})", "value": record.get("result"), "cited": True})
        elif name in tool_by_name:
            record = tool_by_name[name]
            out.append({"field": name, "value": record.get("result"), "cited": True})
        elif name in report_row:
            out.append({"field": name, "value": report_row[name], "cited": True})
        else:
            # the LLM is free-text about what it says it used; not every
            # name it gives necessarily matches a real report column or a
            # real tool call -- a genuine hallucinated citation.
            out.append({"field": name, "value": None, "cited": True, "note": "not a known evidence field"})
    return out


def _primary_from_investigation(inv_entry: dict, report_row: dict) -> dict:
    """Primary-proposal fields sourced from a real investigator/ result.
    apply_gate() only reads attributes off its `resolution` argument, and
    InvestigationResult is a strict superset of what it needs, so a
    SimpleNamespace built from the raw JSONL entry stands in fine --
    investigation_log.jsonl only stores gate_decision/gate_agent_status
    (the shallow outcome), not the full per-condition breakdown, so the
    gate is recomputed fresh here rather than trusted from the log."""
    resolution = types.SimpleNamespace(
        exception_type=inv_entry["exception_type"],
        policy_id=inv_entry["policy_id"],
        confidence=inv_entry["confidence"],
        sufficient_evidence=inv_entry["sufficient_evidence"],
        # apply_gate() reads resolution.evidence_used (added this session,
        # for its citation-validation check) -- this SimpleNamespace predates
        # that and was missing the attribute entirely, an AttributeError
        # waiting for the first brand-new case whose first seed already has
        # a real investigation on record (found via backfill_evidence_display.py,
        # never actually hit by seed_review_queue.py's own re-seed runs this
        # session, since every case seeded so far took the "unchanged" path,
        # which never calls this function at all).
        evidence_used=inv_entry.get("evidence_used") or [],
        # apply_gate() also now reads resolution.root_cause (for
        # check_root_cause_contradiction() -- see agent/evidence.py). Same
        # class of gap as evidence_used above, caught while wiring the new
        # check in rather than discovered live: this SimpleNamespace would
        # otherwise silently feed an empty string into that check for every
        # investigator-primary case, making it a permanent no-op here even
        # though apply_gate() itself never raises (getattr default, not an
        # AttributeError this time) -- fixed at the source instead.
        root_cause=inv_entry.get("root_cause") or "",
    )
    # investigation_log.jsonl stores each tool call's own record (not an
    # InvestigationResult object), so tool_evidence_ids() itself can't be
    # called on it directly -- but its logic (one TOOL-N per
    # investigation_log entry, PLUS that entry's own tool_name as an
    # accepted alias -- see tool_evidence_ids()'s docstring for why the
    # alias exists: 148 of 313 real investigations cite a tool by name
    # instead of TOOL-N) is reproduced inline here against the raw JSONL
    # list to stay consistent with GENERAL_INSTRUCTIONS' citation convention
    # (investigator/loop.py) without importing a function that expects a
    # different input type.
    _inv_log = inv_entry.get("investigation_log") or []
    extra_valid_evidence_ids = frozenset(
        {f"TOOL-{i + 1}" for i in range(len(_inv_log))}
        | {entry.get("tool_name") for entry in _inv_log if entry.get("tool_name")}
    )
    gate_result = apply_gate(resolution, report_row, extra_valid_evidence_ids)
    return {
        "agent_exception_type": inv_entry["exception_type"],
        "reclassified": int(bool(gate_result["reclassified"])),
        "agent_root_cause": inv_entry.get("root_cause"),
        "agent_recommended_action": inv_entry.get("recommended_action"),
        "agent_confidence": inv_entry.get("confidence"),
        "agent_policy_id": inv_entry.get("policy_id"),
        "policy_id_consistent": int(bool(gate_result["policy_id_consistent"])),
        "agent_sufficient_evidence": int(bool(inv_entry.get("sufficient_evidence"))),
        "gate_final_decision": gate_result["final_decision"],
        "gate_reasons": gate_result["gate_reasons"],
        "gate_condition_checks": gate_result["gate_condition_checks"],
        "amount_at_risk_rupees": gate_result["amount_at_risk_rupees"],
        "evidence_used": inv_entry.get("evidence_used") or [],
        # investigator/ only ever talks to Ollama (see investigator/config.py,
        # investigator/ollama_client.py) -- unlike agent/, it has no pluggable
        # provider, so this is a verified constant, not a guess.
        "provider": "ollama",
        "model": inv_entry.get("model"),
        "agent_run_id": None,  # investigator/ has no equivalent to agent/audit.py's RUN_ID
        "timestamp": inv_entry.get("investigated_at"),
    }


def _primary_from_audit_entry(entry: dict) -> dict:
    """Primary-proposal fields sourced from the single-shot agent/'s
    proposal -- today's original behavior, unchanged. Already has a fully
    computed gate result (agent/audit.py wrote it), no recomputation needed."""
    return {
        "agent_exception_type": entry["agent_exception_type"],
        "reclassified": int(bool(entry.get("reclassified"))),
        "agent_root_cause": entry.get("agent_root_cause"),
        "agent_recommended_action": entry.get("agent_recommended_action"),
        "agent_confidence": entry.get("agent_confidence"),
        "agent_policy_id": entry.get("agent_policy_id"),
        "policy_id_consistent": int(bool(entry.get("policy_id_consistent"))),
        "agent_sufficient_evidence": int(bool(entry.get("agent_sufficient_evidence"))),
        "gate_final_decision": entry["gate_final_decision"],
        "gate_reasons": entry.get("gate_reasons") or [],
        "gate_condition_checks": entry.get("gate_condition_checks"),
        "amount_at_risk_rupees": entry["gate_amount_at_risk_rupees"],
        "evidence_used": entry.get("agent_evidence_used") or [],
        "provider": entry.get("provider"),
        "model": entry.get("model"),
        "agent_run_id": entry.get("agent_run_id"),
        "timestamp": entry.get("timestamp"),
    }


def _investigation_fields(inv_entry: dict | None) -> tuple:
    """(investigated, summary, drafted_communication, tool_rounds, log_json,
    gate_decision, investigated_at) -- all None/0 if this case was never
    run through investigator/.

    Also treated as "never run" if the only entry on record is a total
    infra failure (e.g. Ollama wasn't running): investigator/loop.py
    writes a placeholder root_cause starting with _TOTAL_FAILURE_MARKER
    when even the final-answer call couldn't reach the model at all --
    that's not a real investigation, just a failed attempt. This is now a
    genuine backstop rather than the only defense: _load_investigations()
    itself already prefers a real entry over a failure one across the
    whole file, so this only fires when EVERY entry on record for a
    transaction is a failure -- never letting a transaction with zero real
    investigations get marked "investigated" with junk content."""
    if inv_entry is None:
        return (0, None, None, None, None, None, None)
    if _is_failure_entry(inv_entry):
        return (0, None, None, None, None, None, None)
    return (
        1,
        inv_entry.get("investigation_summary"),
        inv_entry.get("drafted_communication"),
        inv_entry.get("tool_rounds_used"),
        json.dumps(inv_entry.get("investigation_log") or []),
        inv_entry.get("gate_decision"),
        inv_entry.get("investigated_at"),
    )


def seed(audit_log_path: str = AUDIT_LOG_PATH, data_dir: str = DATA_DIR,
          investigation_log_path: str = INVESTIGATION_LOG_PATH) -> dict:
    db.init_db()

    report, _, _ = run(data_dir)
    # NOTE: report_by_txn's rows are hashed as-is by _canonical_hash() below
    # (report_row goes straight into the hash payload) -- do NOT mutate
    # these dicts (e.g. to restore transaction_id after set_index() drops
    # it) or every existing case's stored hash stops matching a freshly
    # recomputed one, flagging all 603 as "conflicted" (a real regression
    # caught live: an earlier version of this fix did exactly that).
    # _build_evidence_fields_cited() below takes transaction_id as its own
    # explicit argument instead, precisely so this dict can stay untouched.
    report_by_txn = report.set_index("transaction_id").to_dict(orient="index")
    investigations = _load_investigations(investigation_log_path)

    with open(audit_log_path, encoding="utf-8") as f:
        audit_entries = [json.loads(line) for line in f]
    escalated = [e for e in audit_entries if e["gate_final_decision"] == "escalate"]

    conn = db.get_connection()
    inserted, unchanged, conflicts, skipped_no_report, enriched, backfilled = 0, 0, [], [], 0, 0
    inserted_investigator_primary = 0
    try:
        # One upfront query for every existing case, instead of one SELECT
        # per escalated entry inside the loop below -- the same N+1 shape
        # this codebase already measured and fixed for /api/reverify
        # (review_backend/main.py), arguably higher-impact here:
        # run_stream_simulator.py calls seed() every tick (default 3s) over
        # the FULL escalated set (not just newly-arrived entries), so the
        # per-tick query count grows with the backlog across a whole demo
        # run -- tens of thousands of round trips summed over a typical
        # 5-minute stream demo. A snapshot taken once here is safe: this
        # function only ever inserts a transaction_id it doesn't already
        # have in `escalated`, never revisits one later in the same run, so
        # a stale read mid-loop isn't possible. Found via external review.
        existing_rows = conn.execute(
            "SELECT transaction_id, audit_record_hash, investigated, "
            "investigation_gate_decision, agent_decided_at FROM cases"
        ).fetchall()
        existing_by_txn = {r["transaction_id"]: r for r in existing_rows}

        for entry in escalated:
            txn_id = entry["transaction_id"]
            report_row = report_by_txn.get(txn_id)
            if report_row is None:
                skipped_no_report.append(txn_id)
                continue

            record_hash = _canonical_hash(entry, report_row)
            existing = existing_by_txn.get(txn_id)
            inv_fields = _investigation_fields(investigations.get(txn_id))

            if existing is not None:
                if existing["audit_record_hash"] != record_hash:
                    conflicts.append(txn_id)
                    continue
                unchanged += 1
                if existing["agent_decided_at"] is None and entry.get("timestamp"):
                    conn.execute(
                        "UPDATE cases SET agent_decided_at=%s WHERE transaction_id=%s",
                        (entry["timestamp"], txn_id),
                    )
                # The AI-proposal columns are frozen (that's the whole point
                # of the hash check above) -- but investigation enrichment
                # is additive, not a mutation of the original proposal, so
                # a case that gets investigated AFTER its first seed is
                # allowed to pick that up on a later re-seed.
                if inv_fields[0] and not existing["investigated"]:
                    conn.execute(
                        """UPDATE cases SET investigated=%s, investigation_summary=%s,
                           investigation_drafted_communication=%s, investigation_tool_rounds=%s,
                           investigation_log=%s, investigation_gate_decision=%s,
                           investigation_investigated_at=%s, investigation_source=%s
                           WHERE transaction_id=%s""",
                        (*inv_fields, os.path.abspath(investigation_log_path), txn_id),
                    )
                    enriched += 1
                elif (inv_fields[0] and existing["investigated"]
                      and existing["investigation_gate_decision"] is None):
                    # Already investigated before investigation_gate_decision
                    # existed as a column (e.g. the first runs this session) --
                    # backfill just that field rather than re-churning the rest
                    # of the frozen investigation content on every re-seed.
                    conn.execute(
                        "UPDATE cases SET investigation_gate_decision=%s, "
                        "investigation_investigated_at=%s WHERE transaction_id=%s",
                        (inv_fields[5], inv_fields[6], txn_id),
                    )
                    backfilled += 1
                continue

            inv_entry = investigations.get(txn_id)
            has_real_investigation = bool(inv_fields[0])
            if has_real_investigation:
                primary = _primary_from_investigation(inv_entry, report_row)
                resolution_source = "investigator"
            else:
                primary = _primary_from_audit_entry(entry)
                resolution_source = "agent"

            amount = primary["amount_at_risk_rupees"]
            tier = 2 if amount >= MANAGER_APPROVAL_THRESHOLD_RUPEES else 1
            evidence_fields_cited = _build_evidence_fields_cited(
                primary["evidence_used"], report_row, txn_id,
                inv_entry.get("investigation_log") if has_real_investigation else None,
            )

            conn.execute(
                """INSERT INTO cases (
                    transaction_id, merchant_id, settlement_id, matcher_exception_type,
                    agent_exception_type, reclassified, agent_root_cause,
                    agent_recommended_action, agent_confidence, agent_policy_id,
                    policy_id_consistent, agent_sufficient_evidence, gate_final_decision,
                    gate_reasons, gate_condition_checks, amount_at_risk_rupees, required_approval_tier,
                    match_status, match_pass, risk_class, ledger_expected_net_rupees,
                    observed_net_rupees, net_delta_rupees, all_signals,
                    evidence_fields_cited, provider, model, agent_run_id, seeded_at,
                    audit_log_source, audit_record_hash, schema_version,
                    investigated, investigation_summary, investigation_drafted_communication,
                    investigation_tool_rounds, investigation_log, investigation_gate_decision,
                    investigation_investigated_at, investigation_source, agent_decided_at,
                    resolution_source
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    txn_id, entry.get("merchant_id"), entry.get("settlement_id"),
                    report_row.get("final_exception_type"), primary["agent_exception_type"],
                    primary["reclassified"], primary["agent_root_cause"],
                    primary["agent_recommended_action"], primary["agent_confidence"],
                    primary["agent_policy_id"], primary["policy_id_consistent"],
                    primary["agent_sufficient_evidence"], primary["gate_final_decision"],
                    json.dumps(primary["gate_reasons"]),
                    json.dumps(primary["gate_condition_checks"]) if primary["gate_condition_checks"] else None,
                    amount, tier,
                    report_row.get("match_status"), report_row.get("match_pass"),
                    report_row.get("risk_class"), report_row.get("ledger_expected_net_rupees"),
                    report_row.get("observed_net_rupees"), report_row.get("net_delta_rupees"),
                    json.dumps(report_row.get("all_signals") or []),
                    json.dumps(evidence_fields_cited), primary["provider"], primary["model"],
                    primary["agent_run_id"], dt.datetime.now(dt.timezone.utc).isoformat(),
                    os.path.abspath(audit_log_path), record_hash, SCHEMA_VERSION,
                    *inv_fields,
                    os.path.abspath(investigation_log_path) if inv_fields[0] else None,
                    primary["timestamp"],
                    resolution_source,
                ),
            )
            inserted += 1
            if resolution_source == "investigator":
                inserted_investigator_primary += 1
        conn.commit()
    finally:
        conn.close()

    return {
        "escalated_in_audit_log": len(escalated),
        "inserted": inserted,
        "inserted_investigator_primary": inserted_investigator_primary,
        "unchanged": unchanged,
        "enriched_with_investigation": enriched,
        "backfilled_gate_decision": backfilled,
        "conflicts": conflicts,
        "skipped_no_report_row": skipped_no_report,
    }


if __name__ == "__main__":
    result = seed()
    print(f"Escalated cases in audit log: {result['escalated_in_audit_log']}")
    print(f"Newly inserted:               {result['inserted']}")
    print(f"  ...with investigator/ as primary: {result['inserted_investigator_primary']}")
    print(f"Already seeded, unchanged:    {result['unchanged']}")
    print(f"Enriched with investigation:  {result['enriched_with_investigation']}")
    print(f"Backfilled gate_decision:     {result['backfilled_gate_decision']}")
    if result["conflicts"]:
        print(f"\nCONFLICTS (same transaction_id, different underlying data -- NOT overwritten):")
        for txn_id in result["conflicts"]:
            print(f"  {txn_id}")
        print("Investigate before re-seeding these -- the existing case in the DB was")
        print("left untouched, exactly as designed.")
    if result["skipped_no_report_row"]:
        print(f"\nSkipped (no matching report row): {result['skipped_no_report_row']}")
