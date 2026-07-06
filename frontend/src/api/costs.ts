import { api } from "./client";
import type { CostOverview } from "../types";

export async function getCostOverview(): Promise<CostOverview> {
  const { data } = await api.get<CostOverview>("/costs/overview");
  return data;
}
