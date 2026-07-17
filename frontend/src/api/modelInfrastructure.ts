import type {
  ModelCallPage,
  ModelCallStatus,
  ModelInfrastructureOverview,
  ModelProvider,
  ModelProviderCode,
  ModelRoute,
  UpdateModelProviderInput,
  UpdateModelRouteInput,
} from "../types";
import { api } from "./client";

export async function getModelInfrastructure(): Promise<ModelInfrastructureOverview> {
  const { data } = await api.get<ModelInfrastructureOverview>("/model-infrastructure");
  return data;
}

export async function updateModelProvider(
  provider: ModelProviderCode,
  input: UpdateModelProviderInput,
): Promise<ModelProvider> {
  const { data } = await api.put<ModelProvider>(
    `/model-infrastructure/providers/${provider}`,
    input,
  );
  return data;
}

export async function updateModelRoute(
  agentCode: string,
  input: UpdateModelRouteInput,
): Promise<ModelRoute> {
  const { data } = await api.put<ModelRoute>(
    `/model-infrastructure/routes/${agentCode}`,
    input,
  );
  return data;
}

export async function listModelCalls(
  status: ModelCallStatus | null = null,
  limit = 50,
): Promise<ModelCallPage> {
  const { data } = await api.get<ModelCallPage>("/model-infrastructure/calls", {
    params: { status, limit },
  });
  return data;
}
