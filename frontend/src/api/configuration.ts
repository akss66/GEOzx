import { api } from "./client";
import type { ModelConfig } from "../types";

export async function listModelConfigs(): Promise<ModelConfig[]> {
  const { data } = await api.get<ModelConfig[]>("/model-configs");
  return data;
}

export async function updateModelConfig(
  id: number,
  patch: { primary_model?: string; fallback_model?: string | null },
): Promise<ModelConfig> {
  const { data } = await api.patch<ModelConfig>(`/model-configs/${id}`, patch);
  return data;
}
