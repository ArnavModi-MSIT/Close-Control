"""Audit log: every agent decision, one JSON line per exception. This is
the artifact that makes "explainable, bounded, gated" a checkable claim.
"""

import os
import json
import uuid
import datetime as dt

from . import config

# one ID per process run, so repeated runs appending to the same file can
# be filtered/grouped -- generated once at import time
RUN_ID = f"run_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def write_entry(report_row: dict, resolution, gate_result: dict, provider, elapsed_seconds: float = None):
    entry = {
        "agent_run_id": RUN_ID,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "transaction_id": report_row.get("transaction_id"),
        "settlement_id": report_row.get("settlement_id"),
        "merchant_id": report_row.get("merchant_id"),

        # provider/model -- distinguishes ollama/groq/anthropic/mock explicitly,
        # not just a generic "live" vs "offline_mock" flag
        "provider": provider.name,
        "model": provider.model,
        "run_mode": "offline_mock" if config.OFFLINE_MODE else "live",

        # matcher's type is authoritative; agent's may differ (reclassification)
        "matcher_exception_type": gate_result["matcher_exception_type"],
        "matcher_risk_class": report_row.get("risk_class"),
        "agent_exception_type": gate_result["agent_exception_type"],
        "reclassified": gate_result["reclassified"],

        "agent_policy_id": resolution.policy_id,
        "policy_id_consistent": gate_result["policy_id_consistent"],
        "agent_root_cause": resolution.root_cause,
        "agent_evidence_used": resolution.evidence_used,
        "agent_recommended_action": resolution.recommended_action,
        "agent_confidence": resolution.confidence,
        "agent_sufficient_evidence": resolution.sufficient_evidence,

        # explicit status distinguishing WHY an entry looks the way it does --
        # normal_escalation / success / policy_missing / policy_id_mismatch /
        # reclassification_overridden -- so failed-provider or malformed-output
        # cases don't get silently mixed in with legitimate policy escalations
        "agent_status": gate_result["agent_status"],

        "gate_final_decision": gate_result["final_decision"],
        "gate_reasons": gate_result["gate_reasons"],
        # structured per-condition PASS/FAIL breakdown, additive -- see
        # agent/gate.py; absent on any entry written before this field existed,
        # UI consumers must treat that as "unavailable", not "all failed"
        "gate_condition_checks": gate_result.get("gate_condition_checks"),
        "gate_amount_at_risk_rupees": gate_result["amount_at_risk_rupees"],
        # Informational, does not affect gate_final_decision above -- absent
        # on any entry written before this field existed, same "unavailable
        # not all-valid" convention as gate_condition_checks.
        "unknown_evidence_citations": gate_result.get("unknown_evidence_citations"),
        "all_evidence_citations_valid": gate_result.get("all_evidence_citations_valid"),
        # wall-clock time for resolve_exception() itself -- None for the
        # mock provider (no network call, not meaningful to time) and for
        # any entry written before this field existed
        "elapsed_seconds": elapsed_seconds,
    }
    os.makedirs(os.path.dirname(config.AUDIT_LOG_PATH), exist_ok=True)
    with open(config.AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def reset_log():
    os.makedirs(os.path.dirname(config.AUDIT_LOG_PATH), exist_ok=True)
    open(config.AUDIT_LOG_PATH, "w").close()
