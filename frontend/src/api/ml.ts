import type { ModelUnit, ModelVersionOut, ScoreListResponse } from "../types";
import { apiClient } from "./client";

export async function fetchScores(params: {
  unit?: ModelUnit;
  search?: string;
  limit: number;
  offset: number;
}): Promise<ScoreListResponse> {
  const { data } = await apiClient.get<ScoreListResponse>("/ml/scores", { params: { ...params, flagged: true } });
  return data;
}

export async function fetchModels(): Promise<ModelVersionOut[]> {
  const { data } = await apiClient.get<ModelVersionOut[]>("/ml/models");
  return data;
}
