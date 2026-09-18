export type UserRole = "ROLE_DATA_OPS" | "ROLE_ANALYST" | "ROLE_TRIAGE" | "ROLE_INVESTIGATOR";
export type AlertStatus = "OPEN" | "DISPOSED" | "ESCALATED";
export type CaseStatus = "OPEN" | "IN_PROGRESS" | "CLOSED";
export type DispositionDecision = "false_positive" | "escalate";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
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
  direction: "CREDIT" | "DEBIT";
  channel: string;
  counterparty_ref: string | null;
  description: string | null;
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
