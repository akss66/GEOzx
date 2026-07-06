import { api } from "./client";
import type { BrainTask, OptimizationSuggestion, OptimizationSuggestionStatus } from "../types";

export async function listOptimizationSuggestions(): Promise<OptimizationSuggestion[]> {
  const { data } = await api.get<OptimizationSuggestion[]>("/optimization-suggestions");
  return data;
}

export async function updateOptimizationSuggestion(
  id: number,
  status: OptimizationSuggestionStatus,
  note?: string,
): Promise<OptimizationSuggestion> {
  const { data } = await api.patch<OptimizationSuggestion>(`/optimization-suggestions/${id}`, {
    status,
    note,
  });
  return data;
}

export async function sendOptimizationSuggestionToBrain(id: number): Promise<BrainTask> {
  const { data } = await api.post<BrainTask>(`/optimization-suggestions/${id}/send-to-brain`);
  return data;
}
