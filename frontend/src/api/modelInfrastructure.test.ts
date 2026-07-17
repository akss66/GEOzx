import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  getModelInfrastructure,
  listModelCalls,
  updateModelProvider,
  updateModelRoute,
} from "./modelInfrastructure";
import { api } from "./client";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
    put: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;
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

  it("updates the full expert route policy", async () => {
    apiPut.mockResolvedValueOnce({ data: { agent_code: "00-decision" } });
    const input = {
      primary_model: "deepseek-reasoner",
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
});
