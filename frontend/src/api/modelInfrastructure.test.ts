import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  createModelProvider,
  deleteModelProvider,
  discoverModelProviderModels,
  getModelProvider,
  getModelInfrastructure,
  getModelProviderTemplates,
  listModelProviders,
  listModelCalls,
  removeModelProviderCredential,
  replaceModelProviderCredential,
  updateModelProviderDetails,
  updateModelProviderModels,
  updateModelProvider,
  updateModelRoute,
  verifyModelProvider,
} from "./modelInfrastructure";
import { api } from "./client";
import type {
  CreateModelProviderInput,
  PatchModelProviderInput,
  UpdateModelRouteInput,
} from "../types";

const presetProviderInput: CreateModelProviderInput = { template_code: "openai" };
const customProviderInput: CreateModelProviderInput = {
  provider_type: "custom_openai",
  display_name: "Internal Gateway",
  base_url: "https://llm.example.com/v1",
};
const providerPatchInput: PatchModelProviderInput = { enabled: false };
const providerRouteInput: UpdateModelRouteInput = {
  primary_provider_id: 7,
  primary_model: "deepseek-reasoner",
  fallback_provider_id: null,
  fallback_model: null,
  temperature: 0.2,
  max_tokens: 8192,
  timeout_seconds: 120,
};

void [presetProviderInput, customProviderInput, providerPatchInput, providerRouteInput];

// @ts-expect-error Provider creation must choose a preset or provide a complete custom provider.
const invalidEmptyProvider: CreateModelProviderInput = {};
// @ts-expect-error Provider patches must change at least one editable field.
const invalidEmptyPatch: PatchModelProviderInput = {};
// @ts-expect-error Route updates must retain the structural provider target.
const invalidLegacyRoute: UpdateModelRouteInput = {
  primary_model: "deepseek-chat",
  fallback_model: null,
  temperature: 0.2,
  max_tokens: 4096,
  timeout_seconds: 90,
};

void [invalidEmptyProvider, invalidEmptyPatch, invalidLegacyRoute];

vi.mock("./client", () => ({
  api: {
    delete: vi.fn(),
    get: vi.fn(),
    patch: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
  },
}));

const apiDelete = api.delete as unknown as Mock;
const apiGet = api.get as unknown as Mock;
const apiPatch = api.patch as unknown as Mock;
const apiPost = api.post as unknown as Mock;
const apiPut = api.put as unknown as Mock;

describe("model infrastructure api", () => {
  beforeEach(() => vi.resetAllMocks());

  it("loads the admin infrastructure overview and filtered call ledger", async () => {
    apiGet.mockResolvedValueOnce({ data: { summary: {}, providers: [], routes: [] } });
    apiGet.mockResolvedValueOnce({ data: { total: 0, items: [] } });

    await getModelInfrastructure();
    await listModelCalls("error", 30);

    expect(apiGet).toHaveBeenNthCalledWith(1, "/model-infrastructure");
    expect(apiGet).toHaveBeenNthCalledWith(2, "/model-infrastructure/calls", {
      params: { status: "error", limit: 30 },
    });
  });

  it("loads provider templates, registry rows, and provider detail", async () => {
    apiGet.mockResolvedValueOnce({ data: [{ code: "deepseek" }] });
    apiGet.mockResolvedValueOnce({ data: [{ id: 7, code: "deepseek" }] });
    apiGet.mockResolvedValueOnce({ data: { id: 7, code: "deepseek" } });

    await getModelProviderTemplates();
    await listModelProviders();
    await getModelProvider(7);

    expect(apiGet).toHaveBeenNthCalledWith(1, "/model-providers/templates");
    expect(apiGet).toHaveBeenNthCalledWith(2, "/model-providers");
    expect(apiGet).toHaveBeenNthCalledWith(3, "/model-providers/7");
  });

  it("creates, edits, and deletes provider registry rows through dedicated endpoints", async () => {
    apiPost.mockResolvedValueOnce({ data: { id: 9, code: "openai" } });
    apiPatch.mockResolvedValueOnce({ data: { id: 9, display_name: "OpenAI Prod" } });
    apiDelete.mockResolvedValueOnce({ data: undefined });

    await createModelProvider({
      template_code: "openai",
      enabled: true,
    });
    await updateModelProviderDetails(9, {
      display_name: "OpenAI Prod",
      enabled: false,
    });
    await deleteModelProvider(9);

    expect(apiPost).toHaveBeenCalledWith("/model-providers", {
      template_code: "openai",
      enabled: true,
    });
    expect(apiPatch).toHaveBeenCalledWith("/model-providers/9", {
      display_name: "OpenAI Prod",
      enabled: false,
    });
    expect(apiDelete).toHaveBeenCalledWith("/model-providers/9");
  });

  it("updates providers using a safe server reference instead of a secret value", async () => {
    apiPut.mockResolvedValueOnce({ data: { code: "deepseek" } });

    await updateModelProvider("deepseek", {
      enabled: true,
      credential_ref: "env:DEEPSEEK_API_KEY",
    });

    expect(apiPut).toHaveBeenCalledWith("/model-infrastructure/providers/deepseek", {
      enabled: true,
      credential_ref: "env:DEEPSEEK_API_KEY",
    });
  });

  it("submits API keys only in a write body", async () => {
    apiPut.mockResolvedValueOnce({ data: { id: 7, key_configured: true } });
    apiDelete.mockResolvedValueOnce({ data: { id: 7, key_configured: false } });

    await replaceModelProviderCredential(7, "sk-sensitive");
    await removeModelProviderCredential(7);

    expect(apiPut).toHaveBeenCalledWith("/model-providers/7/credential", {
      api_key: "sk-sensitive",
    });
    expect(apiDelete).toHaveBeenCalledWith("/model-providers/7/credential");
  });

  it("verifies, discovers, and manually updates provider models without leaking credentials", async () => {
    apiPost.mockResolvedValueOnce({ data: { provider_id: 7, verification_status: "verified" } });
    apiPost.mockResolvedValueOnce({ data: { provider_id: 7, models: ["gpt-4.1-mini"] } });
    apiPut.mockResolvedValueOnce({ data: { id: 7, models: ["gpt-4.1-mini", "gpt-4.1"] } });

    await verifyModelProvider(7);
    await discoverModelProviderModels(7);
    await updateModelProviderModels(7, ["gpt-4.1-mini", "gpt-4.1"]);

    expect(apiPost).toHaveBeenNthCalledWith(1, "/model-providers/7/verify");
    expect(apiPost).toHaveBeenNthCalledWith(2, "/model-providers/7/discover-models");
    expect(apiPut).toHaveBeenCalledWith("/model-providers/7/models", {
      models: ["gpt-4.1-mini", "gpt-4.1"],
    });
  });

  it("updates the full expert route policy", async () => {
    apiPut.mockResolvedValueOnce({ data: { agent_code: "00-decision" } });
    const input = {
      primary_provider_id: 7,
      primary_model: "deepseek-reasoner",
      fallback_provider_id: 8,
      fallback_model: "deepseek-chat",
      temperature: 0.2,
      max_tokens: 8192,
      timeout_seconds: 120,
    };

    await updateModelRoute("00-decision", input);

    expect(apiPut).toHaveBeenCalledWith(
      "/model-infrastructure/routes/00-decision",
      input,
    );
  });

  it("parses referenced-provider deletion conflicts into a structured client error", async () => {
    apiDelete.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          affected_agents: [{ agent_code: "00-decision", agent_name: "Decision" }],
        },
      },
    });

    await expect(deleteModelProvider(7)).rejects.toMatchObject({
      name: "ModelProviderDeleteConflictError",
      providerId: 7,
      affectedAgents: [{ agent_code: "00-decision", agent_name: "Decision" }],
    });
  });
});
