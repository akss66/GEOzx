import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { getAgent, invokeAgent, listAgents } from "./agents";
import { api } from "./client";
import type { AgentProfile } from "../types";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;
const apiPost = api.post as unknown as Mock;

const agent = {
  code: "02-content-director",
  name: "编导文案专家",
  group: "creative",
  one_liner: "把定位转成脚本。",
  model: "deepseek-chat",
  fallback_model: null,
  automation_level: "confirm",
  tools: ["脚本库"],
  typical_tasks: ["脚本包"],
  standard_outputs: ["video_script"],
  current_task: null,
  tool_summary: {
    total_calls: 1,
    pending_approvals: 1,
    failed_calls: 0,
    recent_calls: [
      {
        id: 45,
        task_id: 12,
        tool_code: "brief_builder",
        tool_name: "Brief Builder",
        status: "waiting_approval",
        permission_mode: "confirm",
        requires_human_confirmation: true,
        input_summary: "目标",
        output_summary: "Brief 已生成",
        error: null,
        created_at: "2026-07-01T00:00:00Z",
      },
    ],
  },
} satisfies AgentProfile;

describe("agents api", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("calls list and detail endpoints", async () => {
    apiGet.mockResolvedValueOnce({ data: [agent] });
    apiGet.mockResolvedValueOnce({ data: agent });

    await expect(listAgents()).resolves.toEqual([agent]);
    await expect(getAgent(agent.code)).resolves.toEqual(agent);

    expect(apiGet).toHaveBeenCalledWith("/agents");
    expect(apiGet).toHaveBeenCalledWith("/agents/02-content-director");
  });

  it("invokes an agent then refreshes the profile", async () => {
    apiPost.mockResolvedValueOnce({ data: { message: "ok" } });
    apiGet.mockResolvedValueOnce({ data: agent });

    await expect(invokeAgent(agent.code, "写一个脚本")).resolves.toEqual(agent);

    expect(apiPost).toHaveBeenCalledWith("/agents/02-content-director/invoke", {
      prompt: "写一个脚本",
    });
    expect(apiGet).toHaveBeenCalledWith("/agents/02-content-director");
  });
});
