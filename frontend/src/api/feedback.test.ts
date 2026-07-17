import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api } from "./client";
import {
  listOptimizationSuggestions,
  sendOptimizationSuggestionToBrain,
  updateOptimizationSuggestion,
} from "./feedback";
import type { BrainTask, OptimizationSuggestion } from "../types";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
    patch: vi.fn(),
    post: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;
const apiPatch = api.patch as unknown as Mock;
const apiPost = api.post as unknown as Mock;

const suggestion = {
  id: 7,
  content_item_id: 3,
  content_title: "内容A",
  source_deliverable_id: null,
  target_stage: "content_direction",
  suggestion: "编导：前3秒增加反差问题",
  status: "suggested",
  note: null,
  accepted_at: null,
  verified_at: null,
  created_at: "2026-07-01T00:00:00Z",
} satisfies OptimizationSuggestion;

const task = {
  id: 12,
  content_item_id: null,
  title: "复盘优化",
  type: "review_optimization",
  status: "pending_confirmation",
  brief: {
    goal: "基于复盘建议生成下一轮优化任务",
    project_id: 1,
    project_name: "项目",
    account_group_id: null,
    account_group_name: null,
    platforms: ["douyin"],
    account_ids: [],
    cycle: "待确认",
    budget: null,
    content_goal: "内容目标",
    risk_constraints: [],
    expected_outputs: [],
    confirmation_actions: [],
  },
  plan: {
    id: 1,
    summary: "计划",
    steps: [],
    quality_gates: [],
    estimated_cost: 0,
    requires_human_confirmation: true,
  },
  progress: 0,
  current_focus: "等待确认",
  risk_count: 0,
  runtime_mode: "legacy",
  thread_id: null,
  context_closed_at: null,
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
} satisfies BrainTask;

describe("feedback api", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("loads and updates optimization suggestions", async () => {
    apiGet.mockResolvedValueOnce({ data: [suggestion] });
    apiPatch.mockResolvedValueOnce({ data: { ...suggestion, status: "accepted" } });

    await expect(listOptimizationSuggestions()).resolves.toEqual([suggestion]);
    await expect(updateOptimizationSuggestion(7, "accepted", "下周期采用")).resolves.toEqual({
      ...suggestion,
      status: "accepted",
    });

    expect(apiGet).toHaveBeenCalledWith("/optimization-suggestions");
    expect(apiPatch).toHaveBeenCalledWith("/optimization-suggestions/7", {
      status: "accepted",
      note: "下周期采用",
    });
  });

  it("sends an optimization suggestion into brain draft creation", async () => {
    apiPost.mockResolvedValueOnce({ data: task });

    await expect(sendOptimizationSuggestionToBrain(7)).resolves.toEqual(task);

    expect(apiPost).toHaveBeenCalledWith("/optimization-suggestions/7/send-to-brain");
  });
});
