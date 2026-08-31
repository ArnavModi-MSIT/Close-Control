"""Correction memory: past human overrides of the AI's classification,
surfaced back into FUTURE prompts as a few-shot example -- so the same
correction a reviewer already made once doesn't have to be made again by
hand every time a similar case comes up.

WHERE THIS SITS, ARCHITECTURALLY
review_backend/ is downstream of agent/ and investigator/ (it consumes
their output via audit_log.jsonl / investigation_log.jsonl; neither ever
imports review_backend/ or talks to Postgres). Idea adapted from
HighRadius's "AI learns from patterns and corrections over time" claim --
checked against this project's code first (nothing like it existed), then
built to fit the EXISTING file-based interface between layers rather than
inverting that dependency: review_backend/main.py's submit_review()
APPENDS a correction record to data/correction_log.jsonl (mirroring
audit_log.jsonl / investigation_log.jsonl's own append-only pattern)
whenever a human overrides an AI proposal; agent/client.py and
investigator/loop.py READ that file (optional, best-effort, exactly like
investigation_log.jsonl's own "most cases won't have this" tolerance) when
building a prompt. review_backend/ still never gets imported by agent/ or
investigator/ -- the file is the interface, same as everywhere else in
this pipeline.

WHAT THIS DOES NOT TOUCH -- "AI proposes, deterministic code disposes"
This changes PROMPT CONTENT ONLY: what the AI sees before it proposes
something. It has zero effect on agent/gate.py's 7-condition auto-resolve
check, zero effect on matching/'s deterministic classification, and zero
effect on what final_exception_type a case carries -- the matcher's type
stays authoritative regardless of what a human corrected in the AI's
proposal for a past, different case. A correction can only ever nudge a
future PROPOSAL; it can never itself authorize anything.

KEYED BY matcher_exception_type, DELIBERATELY, NOT agent_exception_type.
The point is "help the AI do better on a similar underlying PROBLEM next
time" -- the matcher's exception_type is the objective, deterministic
fact every future similar case will also carry, whereas the AI's own
(possibly reclassified) agent_exception_type could vary run to run for
what is structurally the same problem.
"""

import json
import os

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CORRECTION_LOG_FILENAME = "correction_log.jsonl"

# Keep the prompt small and the signal recent -- the single MOST RECENT
# correction for a given exception_type is what's included, not a growing
# unbounded history. A human's most recent judgment call on a given
# exception type is the most relevant one to surface; older corrections on
# the same type are superseded by it, not additive with it.
MAX_CORRECTIONS_PER_TYPE = 1


def append_correction(data_dir: str, *, transaction_id: str, matcher_exception_type: str,
                       override_field: str, override_old_value: str, override_new_value: str,
                       reason: str, reviewer_name: str, created_at: str) -> None:
    """Called from review_backend/main.py's submit_review() whenever a
    human overrides an AI proposal. Append-only, same durability contract
    as audit_log.jsonl/investigation_log.jsonl -- never rewrites or
    deletes a prior correction, even a superseded one; MAX_CORRECTIONS_PER_TYPE
    is applied at READ time (load_corrections), not by discarding history
    here."""
    path = os.path.join(data_dir, CORRECTION_LOG_FILENAME)
    entry = {
        "transaction_id": transaction_id,
        "matcher_exception_type": matcher_exception_type,
        "override_field": override_field,
        "override_old_value": override_old_value,
        "override_new_value": override_new_value,
        "reason": reason,
        "reviewer_name": reviewer_name,
        "created_at": created_at,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def load_corrections(data_dir: str = DEFAULT_DATA_DIR) -> dict:
    """{matcher_exception_type: [correction, ...]} in file (append) order.
    Returns {} if the file doesn't exist yet -- this feature is optional,
    the same tolerance investigation_log.jsonl gets: most exception types
    won't have a correction on record, especially early on, and that's a
    normal state, not an error."""
    path = os.path.join(data_dir, CORRECTION_LOG_FILENAME)
    if not os.path.exists(path):
        return {}
    by_type: dict = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            by_type.setdefault(entry["matcher_exception_type"], []).append(entry)
    return by_type


def correction_block_for(exception_type: str, data_dir: str = DEFAULT_DATA_DIR) -> str:
    """Prompt-ready text, or "" if no correction exists for this
    exception_type -- callers append this directly after their policy
    block; an empty string means "nothing to add," never a formatting
    placeholder or an error the caller has to branch on."""
    corrections = load_corrections(data_dir).get(exception_type, [])
    corrections = corrections[-MAX_CORRECTIONS_PER_TYPE:]
    if not corrections:
        return ""

    lines = [
        "\nA HUMAN REVIEWER PREVIOUSLY CORRECTED A SIMILAR CASE OF THIS SAME "
        "EXCEPTION TYPE -- learn from the pattern below, but still ground your "
        "own answer entirely in THIS case's own evidence. Never copy a value "
        "across cases; the correction tells you WHAT KIND of mistake to avoid "
        "repeating, not an answer to reuse.",
    ]
    for c in corrections:
        lines.append(
            f"- On a past case, the field '{c['override_field']}' was corrected "
            f"from {c['override_old_value']!r} to {c['override_new_value']!r}. "
            f"The reviewer's stated reason: \"{c['reason']}\""
        )
    return "\n".join(lines) + "\n"
