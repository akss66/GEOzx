import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  getAgent,
  handoffAgentRun,
  invokeAgent,
  listAgentManagement,
  listAgentRuns,
  listAgents,
  suggestAgentRunKnowledge,
  updateAgentManagement,
} from "./agents";
import { api } from "./client";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;
const apiPost = api.post as unknown as Mock;
const apiPut = api.put as unknown as Mock;

describe("agents api", () => {
  beforeEach(() => vi.resetAllMocks());

  it("calls list and detail endpoints", async () => {
    apiGet.mockResolvedValueOnce({ data: [] });
    apiGet.mockResolvedValueOnce({ data: { code: "01-positioning" } });

    await listAgents();
    await getAgent("01-positioning");

    expect(apiGet).toHaveBeenCalledWith("/agents");
    expect(apiGet).toHaveBeenCalledWith("/agents/01-positioning");
  });

  it("invokes an expert inside the explicit project and account scope", async () => {
    const run = { task: { id: 12 } };
    apiPost.mockResolvedValueOnce({ data: run });

    const result = await invokeAgent("02-content-director", {
      prompt: "生成脚本",
      projectId: 7,
      accountId: 9,
      sourceTaskId: 11,
    });

    expect(apiPost).toHaveBeenCalledWith("/agents/02-content-director/invoke", {
      prompt: "生成脚本",
      project_id: 7,
      account_id: 9,
      source_task_id: 11,
    });
    expect(result).toEqual(run);
  });

  it("lists scoped runs and creates an audited handoff", async () => {
    apiGet.mockResolvedValueOnce({ data: [] });
    apiPost.mockResolvedValueOnce({ data: { task_id: 12, prompt: "继续执行" } });

    await listAgentRuns("01-positioning", 7, 9);
    await handoffAgentRun(12);

    expect(apiGet).toHaveBeenCalledWith("/agents/01-positioning/runs", {
      params: { project_id: 7, account_id: 9 },
    });
    expect(apiPost).toHaveBeenCalledWith("/agents/runs/12/handoff");
  });

  it("sends an expert result to the pending knowledge suggestion queue", async () => {
    const suggestion = { id: 21, status: "pending" };
    apiPost.mockResolvedValueOnce({ data: suggestion });

    const result = await suggestAgentRunKnowledge(12);

    expect(apiPost).toHaveBeenCalledWith("/agents/runs/12/knowledge-suggestion");
    expect(result).toEqual(suggestion);
  });

  it("loads and updates business expert management without model infrastructure fields", async () => {
    apiGet.mockResolvedValueOnce({ data: [] });
    apiPut.mockResolvedValueOnce({ data: { code: "02-content-director" } });
    const input = {
      enabled: true,
      responsibility: "负责内容策划",
      system_prompt: "不编造数据",
      tool_permissions: { brief_builder: "confirm" as const },
      quality_gates: ["script_compliance"],
    };

    await listAgentManagement();
    await updateAgentManagement("02-content-director", input);

    expect(apiGet).toHaveBeenCalledWith("/agents/management");
    expect(apiPut).toHaveBeenCalledWith("/agents/02-content-director/management", input);
  });
});
