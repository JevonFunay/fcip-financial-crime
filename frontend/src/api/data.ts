import type {
  DetectionRunSummary,
  Direction,
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

export async function uploadTransactions(file: File): Promise<IngestionSummary> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<IngestionSummary>("/ingestion/transactions", form);
  return data;
}

export async function runDetection(): Promise<DetectionRunSummary> {
  const { data } = await apiClient.post<DetectionRunSummary>("/detection/p02/run");
  return data;
}
