import { api } from "./client";
import type { CostOverview, TechnicalCostOverview } from "../types";

export async function getCostOverview(input: {
  clientId: number;
  projectId: number | null;
  days: number;
}): Promise<CostOverview> {
  const { data } = await api.get<CostOverview>("/costs/overview", {
    params: {
      client_id: input.clientId,
      ...(input.projectId != null ? { project_id: input.projectId } : {}),
      days: input.days,
    },
  });
  return data;
}

export async function getTechnicalCostOverview(days: number): Promise<TechnicalCostOverview> {
  const { data } = await api.get<TechnicalCostOverview>("/costs/technical", {
    params: { days },
  });
  return data;
}
