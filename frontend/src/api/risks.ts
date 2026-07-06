import { api } from "./client";
import type { RiskQueueItem } from "../types";

export async function listRiskQueue(): Promise<RiskQueueItem[]> {
  const { data } = await api.get<RiskQueueItem[]>("/risks/queue");
  return data;
}
