import type {
  DetectionRunAll,
  Direction,
  IngestionBatchListResponse,
  IngestionSummary,
  OverviewSummary,
  QuarantineListResponse,
  TransactionListResponse,
} from "../types";
import { apiClient } from "./client";

export async function fetchOverview(): Promise<OverviewSummary> {
  const { data } = await apiClient.get<OverviewSummary>("/overview");
  return data;
}

export async function fetchTransactions(params: {
  search?: string;
  direction?: Direction;
  limit: number;
  offset: number;
}): Promise<TransactionListResponse> {
  const { data } = await apiClient.get<TransactionListResponse>("/transactions", { params });
  return data;
}

export async function fetchQuarantine(limit = 25): Promise<QuarantineListResponse> {
  const { data } = await apiClient.get<QuarantineListResponse>("/ingestion/quarantine", {
    params: { limit },
  });
  return data;
}

/** FR-101: the upload registers a batch, so the caller supplies the batch's
 *  source system, business date and (optionally) the record count it expects. */
export async function uploadTransactions(
  file: File,
  batch: { source_system?: string; business_date?: string; expected_records?: string } = {},
): Promise<IngestionSummary> {
  const form = new FormData();
  form.append("file", file);
  for (const [key, value] of Object.entries(batch)) {
    if (value !== undefined && value.trim() !== "") {
      form.append(key, value.trim());
    }
  }
  const { data } = await apiClient.post<IngestionSummary>("/ingestion/transactions", form);
  return data;
}

export async function fetchBatches(limit = 10): Promise<IngestionBatchListResponse> {
  const { data } = await apiClient.get<IngestionBatchListResponse>("/ingestion/batches", {
    params: { limit },
  });
  return data;
}

/** Every ACTIVE rule, then every ACTIVE model version. Scoring the full
 *  dataset takes about a minute, so the request is given room. */
export async function runDetection(): Promise<DetectionRunAll> {
  const { data } = await apiClient.post<DetectionRunAll>("/detection/run", undefined, { timeout: 600_000 });
  return data;
}
