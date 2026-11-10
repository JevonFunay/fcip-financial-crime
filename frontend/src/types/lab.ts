// Model lab (framework prototype): mirrors backend/app/schemas/lab.py.

export type LabModelKind = "supervised" | "unsupervised";

export interface LabFieldSpec {
  key: string;
  label: string;
  required: boolean;
  help: string;
}

export interface LabFeatureGroup {
  key: string;
  label: string;
  description: string;
  default: boolean;
  features: string[];
}

export interface LabModelSpec {
  key: string;
  name: string;
  kind: LabModelKind;
  description: string;
  default: boolean;
}

export interface LabOptions {
  fields: LabFieldSpec[];
  feature_groups: LabFeatureGroup[];
  models: LabModelSpec[];
  max_upload_bytes: number;
  min_per_class: number;
  synthetic_notice: string;
}

export interface LabSuggestion {
  fields: Record<string, string>;
  label_column: string | null;
  positive_values: string[];
  category_column: string | null;
  date_format: "iso" | "month_first";
  low_cardinality: Record<string, string[]>;
}

export interface LabMappingIn {
  fields: Record<string, string | null>;
  label_column: string;
  positive_values: string[];
  category_column: string | null;
  date_format: "iso" | "month_first";
  default_currency: string | null;
}

export interface LabValidation {
  total_rows: number;
  accepted: number;
  quarantined: number;
  quarantine_reasons: { reason: string; rows: number }[];
  usable_rows: number;
  missing_label: number;
  excluded_other_currency: number;
  positives: number;
  negatives: number;
  positive_rate: number;
  currency: string;
  date_from: string | null;
  date_to: string | null;
  accounts: number;
  timestamp_precision: Record<string, number>;
  category_counts: Record<string, number> | null;
  notes: string[];
  ready: boolean;
}

export interface LabRunSummary {
  id: string;
  created_at: string;
  seconds: number;
  best_model: string | null;
  models: string[];
}

export interface LabEvaluation {
  state: "UNCONFIGURED" | "SEALED" | "REVEALED" | "LEGACY_EXPOSED";
  protocol_id?: string | null;
  source_sha256?: string;
  mapping_sha256?: string | null;
  train_share?: number | null;
  final_run_id?: string | null;
  model_key?: string | null;
  threshold_index?: number | null;
  version?: string | null;
  evaluated_at?: string | null;
  selected_final?: {
    model_key: string;
    version: string;
    threshold_index: number;
    at_threshold: LabTestResult;
    by_category: { category: string; positives: number; caught: number }[] | null;
  } | null;
}

export interface LabDataset {
  id: string;
  name: string;
  file_name: string;
  file_sha256: string;
  file_size_bytes: number;
  file_format: "csv" | "parquet";
  row_count: number;
  columns: string[];
  preview: Record<string, string>[];
  suggestion: LabSuggestion;
  mapping: LabMappingIn | null;
  validation: LabValidation | null;
  status: "UPLOADED" | "MAPPED";
  uploaded_by_email: string | null;
  created_at: string;
  runs: LabRunSummary[];
  evaluation: LabEvaluation;
}

export interface LabRunIn {
  feature_groups: string[];
  models: string[];
  train_share: number;
}

export interface LabCurvePoint {
  threshold: number;
  tp: number;
  fp: number;
  fn: number;
  tn: number;
  flagged: number;
  precision: number | null;
  recall: number | null;
  f1: number;
  flagged_per_10k: number;
}

export interface LabTestResult extends LabCurvePoint {
  average_precision: number | null;
  roc_auc: number | null;
}

export interface LabModelResult {
  key: string;
  name: string;
  kind: LabModelKind;
  description: string;
  seconds: number;
  default_index: number;
  validation_ranking: { average_precision: number | null; roc_auc: number | null };
  validation_curve: LabCurvePoint[];
  validation_by_category?: { category: string; positives: number; caught: number }[] | null;
  validation_category_curve?: { category: string; positives: number; caught: number }[][] | null;
  top_features: { feature: string; share: number; direction: string | null; definition: string }[];
}

export interface LabSplitPart {
  rows: number;
  positives?: number;
  date_from: string | null;
  date_to: string | null;
}

export interface LabRunResults {
  protocol: Record<string, unknown> & { train_share: number };
  split: { train: LabSplitPart; validation: LabSplitPart; test: LabSplitPart };
  features: {
    requested_groups: string[];
    used: string[];
    unusable: string[];
    definitions: Record<string, string>;
    feature_seconds: number;
  };
  models: LabModelResult[];
  positive_category_coverage?: { train?: Record<string, number>; validation?: Record<string, number> };
  best_model: string;
  seconds: number;
}

export interface LabRun {
  id: string;
  dataset_id: string;
  dataset_name: string;
  created_at: string;
  seconds: number;
  config: LabRunIn;
  results: LabRunResults;
  saved_versions: { model_key: string; version: string; threshold: number }[];
  evaluation: LabEvaluation;
  synthetic_notice: string;
}

export interface LabSavedModel {
  model_version_id: string;
  version: string;
  state: string;
  threshold: number;
  model_key: string;
  model_name: string;
  validation: LabCurvePoint;
  test: LabCurvePoint;
  created_at: string;
  already_saved: boolean;
}

export type LabModelState = "DRAFT" | "IN_SIMULATION" | "PENDING_APPROVAL" | "ACTIVE" | "SUSPENDED" | "RETIRED";

export interface LabHistoryEntry {
  at: string;
  actor_email: string | null;
  actor_role: string | null;
  from_state: string | null;
  to_state: string;
  action: string | null;
  reason: string | null;
}

export interface LabModelVersion {
  id: string;
  version: string;
  state: LabModelState;
  artefact_type: string;
  model_key: string | null;
  model_name: string;
  training_kind: string;
  threshold: number;
  dataset_id: string | null;
  dataset_name: string | null;
  lab_run_id: string | null;
  validation: Partial<LabCurvePoint>;
  test: Partial<LabCurvePoint>;
  created_by_email: string | null;
  submitted_by_email: string | null;
  approved_by_email: string | null;
  approved_at: string | null;
  superseded_by_version: string | null;
  created_at: string;
  history: LabHistoryEntry[];
  evaluation_state?: "REVEALED" | "LEGACY_EXPOSED";
}

export interface LabModelAlertsState {
  rule_ref: string;
  version: number | null;
  state: string | null;
  active: boolean;
  reason_code: string | null;
  severity: string | null;
}

export interface LabModelsResponse {
  items: LabModelVersion[];
  model_alerts: LabModelAlertsState;
  min_reason_length: number;
  synthetic_notice: string;
  signal_notice: string;
}

export interface LabMonitorModelRun {
  model_version: string;
  unit: string;
  threshold: number;
  units_scored: number;
  units_flagged: number;
  scores_stored: number;
  observations_skipped_currency: number;
}

export interface LabMonitorResult {
  file_name: string;
  file_format: string;
  mapping_report: { notes: string[]; quarantine_reasons: { reason: string; rows: number }[] };
  batch_ref: string | null;
  batch_status: string | null;
  total_rows: number;
  accepted: number;
  quarantined: number;
  quarantine_preview: { row_number: number; error_reason: string }[];
  account_proxies_created: number;
  rule_alerts_created: number;
  models: LabMonitorModelRun[];
  model_alerts_created: number;
  model_alerts_active: boolean;
  seconds: number;
  signal_notice: string;
  synthetic_notice: string;
}
