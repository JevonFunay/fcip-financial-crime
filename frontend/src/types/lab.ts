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

export interface LabDataset {
  id: string;
  name: string;
  file_name: string;
  file_sha256: string;
  file_size_bytes: number;
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
  test: LabTestResult;
  test_by_category: { category: string; positives: number; caught: number }[] | null;
  top_features: { feature: string; share: number; direction: string | null; definition: string }[];
}

export interface LabSplitPart {
  rows: number;
  positives: number;
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
  positive_rate_test: number;
  models: LabModelResult[];
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
