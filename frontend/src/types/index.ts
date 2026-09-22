export type UserRole = "ROLE_DATA_OPS" | "ROLE_ANALYST" | "ROLE_TRIAGE" | "ROLE_INVESTIGATOR";
export type AlertStatus = "OPEN" | "DISPOSED" | "ESCALATED";
export type CaseStatus = "OPEN" | "IN_PROGRESS" | "CLOSED";
export type DispositionDecision = "false_positive" | "escalate";
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

export interface IngestionSummary {
  file_name: string;
  total_rows: number;
  accepted: number;
  quarantined: number;
  quarantine_preview: { row_number: number; error_reason: string }[];
}

export interface DetectionRunSummary {
  pattern_code: string;
  detection_run_id: string;
  alerts_created: number;
  alerts_already_existing: number;
  alerts: {
    alert_id: string;
    customer_ref: string;
    txn_count_in_band: number;
    aggregate_amount: string;
    created: boolean;
  }[];
}

export interface AlertDetail extends AlertSummary {
  correlation_id: string;
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
