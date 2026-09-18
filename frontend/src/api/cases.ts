import type { CaseDetail } from "../types";
import { apiClient } from "./client";

export async function createCase(alertId: string): Promise<CaseDetail> {
  const { data } = await apiClient.post<CaseDetail>("/cases", { alert_id: alertId });
  return data;
}

export async function fetchCase(id: string): Promise<CaseDetail> {
  const { data } = await apiClient.get<CaseDetail>(`/cases/${id}`);
  return data;
}
