"""Request/response schemas for the review-queue API."""

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from .state_machine import OVERRIDABLE_FIELDS


class ReviewSubmission(BaseModel):
    reviewer_name: str = Field(min_length=1, max_length=200)
    reviewer_role: Literal["analyst", "manager"]
    decision: Literal["approved", "overridden", "escalated", "reverted", "auto_closed"]
    override_field: Optional[str] = None
    override_old_value: Optional[str] = None
    override_new_value: Optional[str] = None
    notes: Optional[str] = None
    # optimistic concurrency: the number of review rows the client saw for
    # this case when it loaded the detail panel. If another reviewer's
    # action landed in between, the server-side count will have moved and
    # the submission is rejected (409) rather than silently accepted on
    # stale state. Optional -- omit to skip the check.
    expected_review_count: Optional[int] = None

    # created_at / review_uuid / application_version are deliberately NOT
    # accepted from the client -- server-generated only, per the design
    # review's "never trust created_at from the client" finding.

    @model_validator(mode="after")
    def _cross_field_rules(self):
        if self.decision == "overridden":
            missing = [f for f in ("override_field", "override_old_value", "override_new_value")
                       if getattr(self, f) is None]
            if missing:
                raise ValueError(f"override requires {missing}")
            if self.override_field not in OVERRIDABLE_FIELDS:
                raise ValueError(
                    f"override_field must be one of {sorted(OVERRIDABLE_FIELDS)}, "
                    f"got {self.override_field!r}"
                )
            if not self.notes or not self.notes.strip():
                raise ValueError("override requires a non-empty reason in notes")

        if self.decision == "escalated":
            if not self.notes or not self.notes.strip():
                raise ValueError("escalation requires a non-empty reason in notes")

        if self.decision == "reverted":
            if not self.notes or not self.notes.strip():
                raise ValueError("reverting an AI auto-resolve requires a non-empty reason in notes")
            if self.override_field or self.override_old_value or self.override_new_value:
                raise ValueError("override_* fields must be empty for a 'reverted' decision -- "
                                  "revert first, then override in a separate action if needed")

        if self.decision == "approved":
            if self.override_field or self.override_old_value or self.override_new_value:
                raise ValueError("override_* fields must be empty for an 'approved' decision")

        if self.decision == "auto_closed":
            if not self.notes or not self.notes.strip():
                raise ValueError("auto_closed requires a non-empty reason in notes")
            if self.override_field or self.override_old_value or self.override_new_value:
                raise ValueError("override_* fields must be empty for an 'auto_closed' decision")

        return self


class ReverificationRequest(BaseModel):
    """Body for POST /api/reverify. dry_run=True previews which cases would
    be auto-closed without writing anything."""
    dry_run: bool = False
