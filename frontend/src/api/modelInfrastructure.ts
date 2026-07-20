import type {
  CreateModelProviderInput,
  ModelCallPage,
  ModelCallStatus,
  ModelInfrastructureOverview,
  ModelProviderDeleteConflict,
  ModelProviderDetail,
  ModelProvider,
  ModelProviderCode,
  ModelProviderDiscoveryResult,
  ModelProviderTemplate,
  ModelProviderVerifyResult,
  ModelRoute,
  PatchModelProviderInput,
  UpdateModelProviderInput,
  UpdateModelRouteInput,
} from "../types";
import { api } from "./client";

export class ModelProviderDeleteConflictError extends Error {
  providerId: number;
  affectedAgents: ModelProviderDeleteConflict["affected_agents"];

  constructor(
    providerId: number,
    affectedAgents: ModelProviderDeleteConflict["affected_agents"],
  ) {
    super("Model provider is still referenced by one or more agent routes.");
    this.name = "ModelProviderDeleteConflictError";
    this.providerId = providerId;
    this.affectedAgents = affectedAgents;
  }
}

export async function getModelInfrastructure(): Promise<ModelInfrastructureOverview> {
  const { data } = await api.get<ModelInfrastructureOverview>("/model-infrastructure");
  return data;
}

export async function getModelProviderTemplates(): Promise<ModelProviderTemplate[]> {
  const { data } = await api.get<ModelProviderTemplate[]>("/model-providers/templates");
  return data;
}

export async function listModelProviders(): Promise<ModelProviderDetail[]> {
  const { data } = await api.get<ModelProviderDetail[]>("/model-providers");
  return data;
}

export async function getModelProvider(providerId: number): Promise<ModelProviderDetail> {
  const { data } = await api.get<ModelProviderDetail>(`/model-providers/${providerId}`);
  return data;
}

export async function createModelProvider(
  input: CreateModelProviderInput,
): Promise<ModelProviderDetail> {
  const { data } = await api.post<ModelProviderDetail>("/model-providers", input);
  return data;
}

export async function updateModelProviderDetails(
  providerId: number,
  input: PatchModelProviderInput,
): Promise<ModelProviderDetail> {
  const { data } = await api.patch<ModelProviderDetail>(
    `/model-providers/${providerId}`,
    input,
  );
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

export async function replaceModelProviderCredential(
  providerId: number,
  apiKey: string,
): Promise<ModelProviderDetail> {
  const { data } = await api.put<ModelProviderDetail>(
    `/model-providers/${providerId}/credential`,
    { api_key: apiKey },
  );
  return data;
}

export async function removeModelProviderCredential(
  providerId: number,
): Promise<ModelProviderDetail> {
  const { data } = await api.delete<ModelProviderDetail>(
    `/model-providers/${providerId}/credential`,
  );
  return data;
}

export async function verifyModelProvider(
  providerId: number,
): Promise<ModelProviderVerifyResult> {
  const { data } = await api.post<ModelProviderVerifyResult>(
    `/model-providers/${providerId}/verify`,
  );
  return data;
}

export async function discoverModelProviderModels(
  providerId: number,
): Promise<ModelProviderDiscoveryResult> {
  const { data } = await api.post<ModelProviderDiscoveryResult>(
    `/model-providers/${providerId}/discover-models`,
  );
  return data;
}

export async function updateModelProviderModels(
  providerId: number,
  models: string[],
): Promise<ModelProviderDetail> {
  const { data } = await api.put<ModelProviderDetail>(
    `/model-providers/${providerId}/models`,
    { models },
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

export async function deleteModelProvider(providerId: number): Promise<void> {
  try {
    await api.delete(`/model-providers/${providerId}`);
  } catch (error) {
    if (isModelProviderDeleteConflict(error)) {
      throw new ModelProviderDeleteConflictError(
        providerId,
        error.response.data.affected_agents,
      );
    }
    throw error;
  }
}

function isModelProviderDeleteConflict(
  error: unknown,
): error is {
  response: {
    status: 409;
    data: ModelProviderDeleteConflict;
  };
} {
  if (!error || typeof error !== "object") {
    return false;
  }
  const response = "response" in error ? error.response : undefined;
  if (!response || typeof response !== "object") {
    return false;
  }
  if (!("status" in response) || response.status !== 409) {
    return false;
  }
  const data = "data" in response ? response.data : undefined;
  return Boolean(
    data
    && typeof data === "object"
    && "affected_agents" in data
    && Array.isArray(data.affected_agents),
  );
}
