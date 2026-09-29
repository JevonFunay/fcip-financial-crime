export type UserRole = "ROLE_DATA_OPS" | "ROLE_ANALYST" | "ROLE_TRIAGE" | "ROLE_INVESTIGATOR";
export type AlertStatus = "OPEN" | "DISPOSED" | "ESCALATED";
export type CaseStatus = "OPEN" | "IN_PROGRESS" | "CLOSED";
export type DispositionDecision = "false_positive" | "escalate";
export type RuleState =
  | "DRAFT"
  | "IN_SIMULATION"
  | "PENDING_APPROVAL"
  | "ACTIVE"
  | "SUSPENDED"
  | "RETIRED"
  | "REJECTED";
// FRD §8.0 severity scale.
export type RuleSeverity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type Direction = "CREDIT" | "DEBIT";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface AlertSummary {
  id: string;
  pattern_code: string;
  status: AlertStatus;
  customer_id: string;
  customer_ref: string;
  customer_name: string;
  reason: string;
  case_id: string | null;
  created_at: string;
}

export interface TransactionOut {
  id: string;
  transaction_ref: string;
  account_number: string;
  transaction_date: string;
  amount: string;
  currency: string;
  direction: Direction;
  channel: string;
  counterparty_ref: string | null;
  description: string | null;
}

export interface TransactionRow extends TransactionOut {
  customer_id: string;
  customer_ref: string;
  customer_name: string;
}

export interface TransactionListResponse {
  items: TransactionRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface QuarantineItem {
  id: string;
  source_file_name: string;
  row_number: number;
  raw_row: Record<string, unknown>;
  error_reason: string;
  created_at: string;
}

export interface QuarantineListResponse {
  items: QuarantineItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface OverviewSummary {
  customers: number;
  accounts: number;
  transactions: number;
  quarantined: number;
  alerts_open: number;
  alerts_escalated: number;
  alerts_disposed: number;
  cases: number;
  latest_transaction_date: string | null;
}

export type IngestionBatchStatus = "REGISTERED" | "COMPLETED" | "NEEDS_REVIEW" | "FAILED";
export type AuditObjectType = "ALERT" | "CASE" | "BATCH" | "DETECTION_RUN" | "AUDIT_EXPORT" | "RULE_VERSION";

export interface IngestionSummary {
  file_name: string;
  total_rows: number;
  accepted: number;
  quarantined: number;
  batch_ref: string | null;
  batch_status: IngestionBatchStatus | null;
  quarantine_preview: { row_number: number; error_reason: string }[];
}

export interface IngestionBatch {
  id: string;
  batch_ref: string;
  source_system: string;
  business_date: string;
  file_name: string;
  file_checksum: string;
  file_size_bytes: number;
  expected_records: number | null;
  status: IngestionBatchStatus;
  total_rows: number;
  accepted_rows: number;
  quarantined_rows: number;
  correlation_id: string;
  registered_by_email: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface IngestionBatchListResponse {
  items: IngestionBatch[];
  total: number;
  limit: number;
  offset: number;
}

export interface AuditEvent {
  id: string;
  correlation_id: string;
  actor_user_id: string | null;
  actor_email: string | null;
  actor_role: string | null;
  object_type: AuditObjectType;
  object_id: string;
  from_state: string | null;
  to_state: string;
  reason: string | null;
  created_at: string;
}

export interface AuditListResponse {
  items: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface AuditFilters {
  correlation_id?: string;
  object_type?: string;
  object_id?: string;
  actor_email?: string;
}

export interface DetectionRunSummary {
  pattern_code: string;
  detection_run_id: string;
  rules_evaluated: { rule_ref: string; version: number }[];
  alerts_created: number;
  alerts_already_existing: number;
  alerts: {
    alert_id: string;
    customer_ref: string;
    rule_ref: string;
    rule_version: number;
    txn_count_in_band: number;
    aggregate_amount: string;
    created: boolean;
  }[];
}

/** The exact rule version that raised an alert (FR-302), as it was then. */
export interface AlertRule {
  rule_id: string;
  rule_ref: string;
  version: number;
  state: RuleState;
  template_version: string;
  reason_code: string;
  severity: RuleSeverity;
  description: string;
  parameters: Record<string, unknown>;
  window_type: "ROLLING" | "CALENDAR";
  /** ISO-8601 duration, e.g. "P7D". */
  window_length: string;
}

export interface AlertDetail extends AlertSummary {
  correlation_id: string;
  rule: AlertRule;
  detection_details: Record<string, unknown>;
  disposed_by: string | null;
  disposed_by_email: string | null;
  disposed_at: string | null;
  disposition_reason: string | null;
  transactions: TransactionOut[];
}

export interface AlertListResponse {
  items: AlertSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface CaseDetail {
  id: string;
  case_number: string;
  status: CaseStatus;
  correlation_id: string;
  opened_by: string;
  opened_by_email: string;
  assigned_to: string | null;
  created_at: string;
  updated_at: string;
  alerts: AlertSummary[];
}
