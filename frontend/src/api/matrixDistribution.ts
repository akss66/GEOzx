import { api } from "./client";
import type {
  CreateMatrixDistributionPlanInput,
  MatrixDistributionPlan,
} from "../types";

export async function listMatrixDistributionPlans(): Promise<MatrixDistributionPlan[]> {
  const { data } = await api.get<MatrixDistributionPlan[]>("/matrix-distribution-plans");
  return data;
}

export async function createMatrixDistributionPlan(
  input: CreateMatrixDistributionPlanInput,
): Promise<MatrixDistributionPlan> {
  const { data } = await api.post<MatrixDistributionPlan>(
    "/matrix-distribution-plans",
    input,
  );
  return data;
}
