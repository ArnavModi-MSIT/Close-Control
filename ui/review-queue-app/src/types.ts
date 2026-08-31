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
  // review_backend/db.py's agent_confidence column has no NOT NULL
  // constraint, and seed_review_queue.py reads it via a bare .get()
  // (not .get(key, default)) in two places -- a real, not just
  // theoretical, path to a missing value. Was typed as non-nullable with
  // no guard at either render site (found via external review).
  agent_confidence: number | null;
  gate_final_decision: string;
  status: CaseStatus;
  investigated: boolean;
  resolution_source: ResolutionSource;
  sla_days_overdue: number;
  sla_breached: boolean;
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
    agent_confidence: number | null;
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
  sla: CaseSla | null;
  review_history: ReviewHistoryItem[];
  activity: ActivityItem[];
  journal_entry: JournalEntry;
  provenance: {
    seeded_at: string;
    audit_log_source: string;
    audit_record_hash: string;
    schema_version: string;
  };
}

// Mirrors journal_entries.py's build_journal_entry() exactly, as embedded
// in GET /api/cases/{id}. Deterministic, no LLM involved -- see
// journal_entries.py's own module docstring for why.
export interface JournalEntryLine {
  account_code: string;
  account_name: string;
  side: "DR" | "CR";
  amount_rupees: number;
}

export interface JournalEntry {
  transaction_id: string;
  exception_type: string | null;
  narration: string;
  lines: JournalEntryLine[];
  total_debits_rupees: number;
  total_credits_rupees: number;
  balanced: boolean;
  generated_at: string;
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
  // The denominator automation_rate_pct is a percentage OF -- 2,072 ledger
  // transactions, not the 617-case escalated queue shown elsewhere on the
  // same page (a completely different, smaller number).
  automation_numerator: number;
  total_ledger_transactions: number;
}

export interface CycleTimeStageStats {
  count: number;
  mean_days: number;
  median_days: number;
}

export interface CycleTimeStatusEntry {
  completed: CycleTimeStageStats | null;
  currently_open_count: number;
  currently_open_avg_days: number | null;
  oldest_open_transaction_id: string | null;
  oldest_open_days: number | null;
}

export interface CycleTimeSummary {
  as_of: string;
  by_status: Record<string, CycleTimeStatusEntry>;
  bottleneck_status: string | null;
  bottleneck_avg_days: number | null;
}

export interface SlaSummary {
  as_of: string;
  tat_business_days: number;
  open_cases_checked: number;
  breached_count: number;
  total_days_overdue: number;
  compensation_exposure_rupees: number;
  worst_case_transaction_id: string | null;
  worst_case_days_overdue: number;
}

export interface CaseSla {
  sla_deadline: string | null;
  sla_days_overdue: number;
  sla_breached: boolean;
  sla_compensation_accrued_rupees?: number;
}

export interface StatsResponse {
  total_cases: number;
  counts_by_status: Record<CaseStatus, number>;
  amount_at_risk_rupees_by_status: Record<CaseStatus, number>;
  exception_type_breakdown: ExceptionTypeBreakdown[];
  counts_by_tier: { "1": number; "2": number };
  investigated_count: number;
  cash_position: CashPositionStats | null;
  sla: SlaSummary;
  cycle_time: CycleTimeSummary;
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
  // The backend's own healthy/broken judgment (cash_position/reconciliation_statement.py:
  // abs(variance) <= max(₹1, 0.5% of matched-confirmed rupees) -- a deliberately
  // generous, documented tolerance covering the dataset's real, explained ~0.13%
  // residual). The frontend must use this directly, not re-derive its own
  // threshold -- a stricter frontend-side cutoff previously showed amber
  // "warning" styling on this exact healthy state (found via external review).
  reconciliation_tied: boolean;
}

// Mirrors matching/root_cause.py's cluster_escalated_cases() / summarize()
// output exactly, as returned by GET /api/root-cause-clusters.
export interface RootCauseCluster {
  cluster_id: string;
  cluster_key: string;
  cluster_basis: "settlement" | "merchant";
  final_exception_type: string;
  merchant_id: string;
  settlement_id: string | null;
  case_count: number;
  risk_class: string;
  amount_at_risk_rupees: number;
  transaction_ids: string[];
}

export interface RootCauseSummary {
  escalated_cases: number;
  root_cause_clusters: number;
  amplification_factor: number;
  multi_case_clusters: number;
  cases_in_multi_case_clusters: number;
  pct_cases_in_multi_case_clusters: number;
  singleton_clusters: number;
  largest_cluster_case_count: number;
  largest_cluster_id?: string;
  largest_cluster_exception_type?: string;
  total_amount_at_risk_rupees: number;
}

export interface RootCauseClustersResponse {
  summary: RootCauseSummary;
  clusters: RootCauseCluster[];
}

// Mirrors GET /api/matcher-auto-resolved exactly -- the ~58 transactions
// the deterministic MATCHER itself resolved (timing_lag_beyond_t2,
// fee_variance, loan_recovery_deduction), zero LLM/human involvement,
// and never entering the review queue at all since only cases the
// matcher could NOT resolve escalate there. loan_id/
// loan_recovery_amount_rupees are only ever non-null for
// loan_recovery_deduction rows -- the 4th data source (Razorpay
// Capital's recovery ledger), otherwise invisible anywhere in this UI.
export interface MatcherAutoResolvedItem {
  transaction_id: string;
  merchant_id: string;
  final_exception_type: string;
  ledger_expected_net_rupees: number;
  observed_net_rupees: number;
  net_delta_rupees: number;
  loan_id: string | null;
  loan_recovery_amount_rupees: number | null;
}

export interface MatcherAutoResolvedResponse {
  total_matcher_auto_resolved: number;
  by_exception_type: Record<string, number>;
  items: MatcherAutoResolvedItem[];
}

// Mirrors GET /api/corrections exactly -- corrections.py's correction
// memory (past human overrides fed back into future AI prompts), exposed
// for the first time. Full history per exception_type, in file order --
// not just the single most-recent entry the prompt-building path itself
// uses.
export interface Correction {
  transaction_id: string;
  matcher_exception_type: string;
  override_field: string;
  override_old_value: string;
  override_new_value: string;
  reason: string;
  reviewer_name: string;
  created_at: string;
}

export interface CorrectionsResponse {
  total_corrections: number;
  by_exception_type: Record<string, Correction[]>;
}

// Mirrors GET /api/audit-chain/verify exactly -- re-derives every review
// row's chain_hash from scratch and compares against what's stored, so a
// silently altered, deleted, or reordered historical row is caught, not
// just a single row's own hash. Deliberately never cached (see the
// endpoint's own docstring) -- caching an integrity check would defeat
// the point of it re-deriving the answer every time.
export interface AuditChainRow {
  id: number;
  transaction_id: string;
  reviewer_name: string;
  decision: string;
  resulting_status: string;
  created_at: string;
  // null = pre-chain row (predates the chain_hash column, no hash to
  // check at all -- distinct from false, which means a real check ran
  // and failed).
  verified: boolean | null;
}

export interface AuditChainVerification {
  total_rows: number;
  pre_chain_rows: number;
  checked: number;
  intact: boolean;
  broken_at: { id: number; review_uuid: string; transaction_id: string;
               expected: string; stored: string } | null;
  rows: AuditChainRow[];
}

// What GET /api/cases/bulk-review accepts -- deliberately a NARROWER
// decision set than a single-case review (see review_backend/models.py's
// BulkReviewRequest docstring for why "overridden"/"reverted" are excluded).
export type BulkReviewDecision = "approved" | "escalated";

export interface BulkReviewRequest {
  transaction_ids: string[];
  reviewer_name: string;
  reviewer_role: ReviewerRole;
  decision: BulkReviewDecision;
  notes?: string | null;
}

// Mirrors qa_agent/schema.py exactly, as returned by POST /api/qa. Reuses
// ToolCallRecord (defined above for InvestigationDetail) rather than a
// second near-duplicate type -- same {step, tool_name, arguments, result}
// shape investigator/'s own tool trace already uses.
export interface QAGroundingCheck {
  claimed_numbers: number[];
  ungrounded_numbers: number[];
  all_grounded: boolean;
}

export interface QAResult {
  question: string;
  answer: string;
  citations: string[];
  grounding: QAGroundingCheck;
  tool_log: ToolCallRecord[];
  tool_rounds_used: number;
  stopped_reason: string;
  elapsed_seconds: number;
  model: string | null;
}

export interface BulkReviewCaseResult {
  transaction_id: string;
  outcome: "reviewed" | "skipped";
  new_status: CaseStatus | null;
  reason: string | null;
}

export interface BulkReviewResult {
  requested: number;
  reviewed_count: number;
  skipped_count: number;
  results: BulkReviewCaseResult[];
}

export interface CaseFilters {
  status: CaseStatus | "";
  exception_type: string;
  min_amount: string;
  max_amount: string;
  search: string;
}
