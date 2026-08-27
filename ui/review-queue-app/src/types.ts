// Mirrors review_backend/main.py's response shapes exactly. Kept as one
// file, hand-written against the real API (not generated) -- small enough
// surface that codegen would be more ceremony than it's worth here.

export type CaseStatus =
  | "auto_resolved"
  | "pending"
  | "pending_manager_approval"
  | "approved"
  | "overridden"
  | "escalated"
  | "auto_closed";

// What a human can submit through the review form.
export type HumanReviewDecision = "approved" | "overridden" | "escalated" | "reverted";
// What can actually appear in a review's history -- includes "auto_closed",
// the closed-loop re-verification job's own decision (see
// review_backend/main.py's POST /api/reverify), which a human never submits
// directly through this UI.
export type ReviewDecision = HumanReviewDecision | "auto_closed";
export type ReviewerRole = "analyst" | "manager";

// 'agent' | 'investigator' -- which source's proposal became this case's
// PRIMARY one (decided once, at first-seed time -- see seed_review_queue.py).
// Distinct from `investigated`: a case can be investigated=true (the
// investigation section has real content) while resolution_source stays
// 'agent', if the investigation only arrived after the case was first
// seeded -- that's enrichment, not a primary-proposal swap.
export type ResolutionSource = "agent" | "investigator";

export interface CaseListItem {
  transaction_id: string;
  matcher_exception_type: string;
  amount_at_risk_rupees: number;
  required_approval_tier: 1 | 2;
  agent_confidence: number;
  gate_final_decision: string;
  status: CaseStatus;
  investigated: boolean;
  resolution_source: ResolutionSource;
}

export interface CaseListResponse {
  items: CaseListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface EvidenceFieldCited {
  field: string;
  value: unknown;
  cited: boolean;
  note?: string;
}

export interface ToolCallRecord {
  step: number;
  tool_name: string;
  arguments: Record<string, unknown>;
  result: unknown;
}

export interface InvestigationDetail {
  investigated: boolean;
  summary: string | null;
  drafted_communication: string | null;
  tool_rounds: number | null;
  log: ToolCallRecord[];
  gate_decision: string | null;
  investigated_at: string | null;
}

export interface GateConditionCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface ActivityItem {
  actor: string;
  actor_type: "ai" | "human" | "system";
  action: string;
  timestamp: string | null;
  detail: string;
}

export interface ReviewHistoryItem {
  id: number;
  review_uuid: string;
  transaction_id: string;
  reviewer_name: string;
  reviewer_role: ReviewerRole;
  decision: ReviewDecision;
  override_field: string | null;
  override_old_value: string | null;
  override_new_value: string | null;
  notes: string | null;
  previous_status: CaseStatus | null;
  resulting_status: CaseStatus;
  created_at: string;
  application_version: string;
}

export interface CaseDetail {
  case: {
    transaction_id: string;
    merchant_id: string;
    settlement_id: string | null;
    matcher_exception_type: string;
    amount_at_risk_rupees: number;
    required_approval_tier: 1 | 2;
    risk_class: string;
  };
  ai_proposal: {
    agent_exception_type: string;
    reclassified: boolean;
    agent_root_cause: string;
    agent_recommended_action: string;
    agent_confidence: number;
    agent_policy_id: string;
    policy_id_consistent: boolean;
    agent_sufficient_evidence: boolean;
    provider: string;
    model: string;
    resolution_source: ResolutionSource;
  };
  gate: {
    final_decision: string;
    reasons: string[];
    condition_checks: GateConditionCheck[] | null;
  };
  evidence: {
    match_status: string | null;
    match_pass: string | null;
    ledger_expected_net_rupees: number | null;
    observed_net_rupees: number | null;
    net_delta_rupees: number | null;
    all_signals: string[];
    fields_cited: EvidenceFieldCited[];
  };
  investigation: InvestigationDetail | null;
  review_state: {
    status: CaseStatus;
    review_count: number;
    awaiting_role: ReviewerRole | null;
  };
  review_history: ReviewHistoryItem[];
  activity: ActivityItem[];
  provenance: {
    seeded_at: string;
    audit_log_source: string;
    audit_record_hash: string;
    schema_version: string;
  };
}

export interface ExceptionTypeBreakdown {
  exception_type: string;
  count: number;
  amount_at_risk_rupees: number;
  investigated_count: number;
}

export interface CashPositionStats {
  as_of: string;
  confirmed_rupees: number;
  in_transit_rupees: number;
  at_risk_rupees: number;
  projected_cash_position_rupees: number;
  automation_rate_pct: number;
}

export interface StatsResponse {
  total_cases: number;
  counts_by_status: Record<CaseStatus, number>;
  amount_at_risk_rupees_by_status: Record<CaseStatus, number>;
  exception_type_breakdown: ExceptionTypeBreakdown[];
  counts_by_tier: { "1": number; "2": number };
  investigated_count: number;
  cash_position: CashPositionStats | null;
  stream_mode: boolean;
}

export interface ReviewSubmission {
  reviewer_name: string;
  reviewer_role: ReviewerRole;
  decision: HumanReviewDecision;
  override_field?: string | null;
  override_old_value?: string | null;
  override_new_value?: string | null;
  notes?: string | null;
  expected_review_count?: number | null;
}

export interface ReviewSubmissionResult {
  review_uuid: string;
  transaction_id: string;
  new_status: CaseStatus;
  created_at: string;
}

export interface ReconciliationDeduction {
  label: string;
  rupees: number;
  count: number;
}

export interface ReconciliationBooksSide {
  books_ending_balance_rupees: number;
  captured_count: number;
  deductions: ReconciliationDeduction[];
  expected_confirmed_balance_rupees: number;
  net_variance_on_confirmed_rupees: number;
  net_variance_on_confirmed_count: number;
  adjusted_confirmed_balance_rupees: number;
  mixed_settlement_adjustment_rupees: number;
  mixed_settlement_adjustment_count: number;
  mixed_settlement_count: number;
  adjusted_confirmed_balance_mixed_aware_rupees: number;
}

export interface OrphanBankRow {
  bank_txn_id: string;
  credit_amount_rupees: number;
  credit_date: string;
  narration: string;
}

export interface ReconciliationBankSide {
  bank_statement_ending_balance_rupees: number;
  matched_confirmed_rupees: number;
  matched_confirmed_count: number;
  matched_other_exception_rupees: number;
  matched_other_exception_count: number;
  ambiguous_rupees: number;
  ambiguous_count: number;
  orphan_rupees: number;
  orphan_count: number;
  orphan_rows: OrphanBankRow[];
  unexplained_rupees: number;
  unexplained_count: number;
}

export interface ReconciliationStatement {
  as_of: string;
  books_side: ReconciliationBooksSide;
  bank_side: ReconciliationBankSide;
  reconciliation_variance_rupees: number;
}

export interface CaseFilters {
  status: CaseStatus | "";
  exception_type: string;
  min_amount: string;
  max_amount: string;
  search: string;
}
