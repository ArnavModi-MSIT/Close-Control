"""Run-level audit manifest: pins a pipeline run to the exact bytes and
rules that produced it.

The project already hashes each individual case (seed_review_queue.py's
`audit_record_hash`, over the audit entry + matcher report row) so a seeded
case can never be silently altered. What was missing is the level above
that: given a set of results, *which exact input files and which threshold
values produced them?* Without it, "190 settlements matched" is a number
with no provenance -- you cannot prove afterwards which bank statement it
was computed from, or whether a tolerance had been quietly widened first.

So this records, for every run:
  - SHA-256 + byte size + row count of each source file
  - the actual VALUES of every threshold that can change a matching or
    gate outcome (not just a file hash -- a human auditor should be able to
    read the tolerance that was in force, not have to diff a hash), AND a
    source hash of each config module so an edit anywhere in it is still
    detectable
  - the headline results, when the caller has them

Deliberately stdlib-only (hashlib/json) -- no new dependency for an
auditability feature.

Idea adapted from cxtx/finance-copilot-skills, whose reconciliation output
carries a "Run Parameters" sheet with input hashes, field mappings and rule
versions for exactly this reason.

    from audit_manifest import write_manifest
    write_manifest(DATA_DIR, results={"settlements": 190})
"""

import os
import json
import hashlib
import datetime as dt

MANIFEST_VERSION = "1.0.0"
MANIFEST_FILENAME = "run_manifest.json"

# Every source file a downstream number can derive from. loan_recovery_schedule.csv
# is the fourth: it decides whether a settlement shortfall is a contracted
# Razorpay Capital recovery (auto-resolvable) or genuinely unexplained
# (escalated), so a run's provenance is incomplete without it -- a swapped
# loan book would silently change auto-resolve outcomes. Files that don't
# exist are recorded as {"present": False} rather than omitted, so a dataset
# generated before this source existed still produces a complete, honest
# manifest instead of one that just looks like it has fewer inputs.
SOURCE_FILES = ("gateway.json", "bank_statement.csv", "internal_settlement_ledger.csv",
                 "loan_recovery_schedule.csv")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _row_count(path: str) -> int | None:
    """Rows in the file, so a manifest reader gets a human-meaningful size
    alongside the hash. JSON is a records array; CSV is lines minus header.
    Returns None rather than raising if the shape is unexpected -- a
    manifest should never be the thing that breaks a pipeline run."""
    try:
        if path.endswith(".json"):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return len(data) if isinstance(data, list) else None
        with open(path, encoding="utf-8") as f:
            return max(sum(1 for _ in f) - 1, 0)
    except Exception:  # noqa: BLE001 -- provenance is best-effort, never fatal
        return None


def _module_source_hash(module) -> str | None:
    """SHA-256 of a config module's own source file. The explicit threshold
    values below are what an auditor reads; this catches any OTHER edit in
    the same file that the explicit list doesn't happen to cover."""
    try:
        return _sha256(module.__file__)
    except Exception:  # noqa: BLE001
        return None


def _rule_versions() -> dict:
    """Every constant that can change a matching or gate OUTCOME, by value.

    Imported lazily so this module stays importable from anywhere without
    dragging the whole pipeline in as a side effect."""
    from matching import config as m_config
    from agent import config as a_config
    from cash_position import config as cp_config
    from agent.policy_kb import POLICY_KB
    from review_backend.state_machine import APPLICATION_VERSION

    return {
        "application_version": APPLICATION_VERSION,
        "matching": {
            "date_block_window_days": m_config.DATE_BLOCK_WINDOW_DAYS,
            "amount_block_tolerance_pct": m_config.AMOUNT_BLOCK_TOLERANCE_PCT,
            "exact_match_tolerance_rupees": m_config.EXACT_MATCH_TOLERANCE_RUPEES,
            "shortage_tolerance_min_fraction": m_config.SHORTAGE_TOLERANCE_MIN_FRACTION,
            "overage_tolerance_max_fraction": m_config.OVERAGE_TOLERANCE_MAX_FRACTION,
            "ambiguity_relative_delta": m_config.AMBIGUITY_RELATIVE_DELTA,
            "source_sha256": _module_source_hash(m_config),
        },
        "gate": {
            "auto_resolve_confidence_threshold": a_config.AUTO_RESOLVE_CONFIDENCE_THRESHOLD,
            "auto_resolve_risk_ceiling_rupees": a_config.AUTO_RESOLVE_RISK_CEILING_RUPEES,
            "agent_auto_resolvable_types": sorted(a_config.AGENT_AUTO_RESOLVABLE_TYPES),
            "source_sha256": _module_source_hash(a_config),
        },
        "policy_kb": {
            "policy_count": len(POLICY_KB),
            "policy_ids": sorted(p["policy_id"] for p in POLICY_KB.values()),
            "source_sha256": _module_source_hash(
                __import__("agent.policy_kb", fromlist=["policy_kb"])),
        },
        "cash_position": {
            "default_as_of": cp_config.DEFAULT_AS_OF.isoformat(),
            "forecast_horizon_business_days": cp_config.FORECAST_HORIZON_BUSINESS_DAYS,
            "reconciliation_tie_tolerance_rupees": cp_config.RECONCILIATION_TIE_TOLERANCE_RUPEES,
            "reconciliation_tie_tolerance_pct": cp_config.RECONCILIATION_TIE_TOLERANCE_PCT,
            "source_sha256": _module_source_hash(cp_config),
        },
    }


def build_manifest(data_dir: str, results: dict | None = None) -> dict:
    inputs = {}
    for name in SOURCE_FILES:
        path = os.path.join(data_dir, name)
        if not os.path.exists(path):
            inputs[name] = {"present": False}
            continue
        inputs[name] = {
            "present": True,
            "sha256": _sha256(path),
            "bytes": os.path.getsize(path),
            "rows": _row_count(path),
        }
    return {
        "manifest_version": MANIFEST_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "data_dir": os.path.abspath(data_dir),
        "inputs": inputs,
        "rule_versions": _rule_versions(),
        "results": results or {},
    }


def write_manifest(data_dir: str, results: dict | None = None,
                    out_path: str | None = None,
                    manifest: dict | None = None) -> tuple[str, dict]:
    """Writes the manifest next to the data it describes.

    Returns (path, manifest) so a caller that also wants to print a summary
    doesn't have to call build_manifest() again and re-hash every input
    file. Pass an already-built `manifest` to skip building entirely."""
    manifest = manifest if manifest is not None else build_manifest(data_dir, results)
    out_path = out_path or os.path.join(data_dir, MANIFEST_FILENAME)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)
    return out_path, manifest


def summary_line(manifest: dict) -> str:
    """One-line human summary for a CLI to print after a run."""
    present = [n for n, v in manifest["inputs"].items() if v.get("present")]
    short = {n: manifest["inputs"][n]["sha256"][:12] for n in present}
    return "Inputs: " + ", ".join(f"{n}@{h}" for n, h in short.items())
