import type { AlertDetail, AlertListResponse, AlertStatus, DispositionDecision } from "../types";
import { apiClient } from "./client";

export async function fetchAlerts(status?: AlertStatus): Promise<AlertListResponse> {
  const { data } = await apiClient.get<AlertListResponse>("/alerts", {
    params: { status, limit: 200 },
  });
  return data;
}

export async function fetchAlert(id: string): Promise<AlertDetail> {
  const { data } = await apiClient.get<AlertDetail>(`/alerts/${id}`);
  return data;
}

export async function disposeAlert(
  id: string,
  decision: DispositionDecision,
  reason: string,
): Promise<AlertDetail> {
  const { data } = await apiClient.post<AlertDetail>(`/alerts/${id}/disposition`, { decision, reason });
  return data;
}
