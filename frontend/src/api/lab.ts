import type {
  LabDataset,
  LabMappingIn,
  LabModelsResponse,
  LabMonitorResult,
  LabOptions,
  LabRun,
  LabRunIn,
  LabSavedModel,
} from "../types/lab";
import { apiClient } from "./client";

export async function fetchLabOptions(): Promise<LabOptions> {
  const { data } = await apiClient.get<LabOptions>("/lab/options");
  return data;
}

export async function fetchLabDatasets(): Promise<LabDataset[]> {
  const { data } = await apiClient.get<LabDataset[]>("/lab/datasets");
  return data;
}

export async function fetchLabDataset(id: string): Promise<LabDataset> {
  const { data } = await apiClient.get<LabDataset>(`/lab/datasets/${id}`);
  return data;
}

export async function uploadLabDataset(file: File, name: string): Promise<LabDataset> {
  const form = new FormData();
  form.append("file", file);
  if (name.trim()) form.append("name", name.trim());
  const { data } = await apiClient.post<LabDataset>("/lab/datasets", form);
  return data;
}

export async function saveLabMapping(id: string, mapping: LabMappingIn): Promise<LabDataset> {
  const { data } = await apiClient.post<LabDataset>(`/lab/datasets/${id}/mapping`, mapping);
  return data;
}

export async function trainLabModels(id: string, config: LabRunIn): Promise<LabRun> {
  const { data } = await apiClient.post<LabRun>(`/lab/datasets/${id}/runs`, config);
  return data;
}

export async function fetchLabRun(id: string): Promise<LabRun> {
  const { data } = await apiClient.get<LabRun>(`/lab/runs/${id}`);
  return data;
}

export async function saveLabModel(
  runId: string,
  modelKey: string,
  thresholdIndex: number,
): Promise<LabSavedModel> {
  const { data } = await apiClient.post<LabSavedModel>(`/lab/runs/${runId}/save`, {
    model_key: modelKey,
    threshold_index: thresholdIndex,
  });
  return data;
}

export async function fetchLabModels(): Promise<LabModelsResponse> {
  const { data } = await apiClient.get<LabModelsResponse>("/lab/models");
  return data;
}

export async function submitLabModel(id: string, justification: string): Promise<LabModelsResponse> {
  const { data } = await apiClient.post<LabModelsResponse>(`/lab/models/${id}/submit`, { justification });
  return data;
}

export async function approveLabModel(
  id: string,
  note: string,
  modelAlerts: boolean | null,
): Promise<LabModelsResponse> {
  const { data } = await apiClient.post<LabModelsResponse>(`/lab/models/${id}/approve`, {
    note,
    model_alerts: modelAlerts,
  });
  return data;
}

export async function returnLabModel(id: string, reason: string): Promise<LabModelsResponse> {
  const { data } = await apiClient.post<LabModelsResponse>(`/lab/models/${id}/return`, { reason });
  return data;
}

export async function suspendLabModel(id: string, reason: string): Promise<LabModelsResponse> {
  const { data } = await apiClient.post<LabModelsResponse>(`/lab/models/${id}/suspend`, { reason });
  return data;
}

export async function setLabModelAlerts(enabled: boolean, reason: string): Promise<LabModelsResponse> {
  const { data } = await apiClient.post<LabModelsResponse>("/lab/model-alerts", { enabled, reason });
  return data;
}

export async function monitorLabDataset(id: string, file: File): Promise<LabMonitorResult> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<LabMonitorResult>(`/lab/datasets/${id}/monitor`, form);
  return data;
}
