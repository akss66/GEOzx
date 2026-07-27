import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  approveToolCall,
  approveDeliverableAcceptance,
  closeTaskMemory,
  confirmBrainTask,
  draftBrainTask,
  getBrainTaskRuntime,
  listBrainTasks,
  listDeliverableAcceptances,
  listPendingToolCallApprovals,
  listTaskInvocations,
  listTaskToolCalls,
  rejudgeDeliverableAcceptance,
  rejectDeliverableAcceptance,
  reviseBrainDecision,
  regenerateBrainMessage,
  selectBrainDecision,
  sendBrainMessage,
  stopBrainGeneration,
} from "./brain";
import { api } from "./client";
import type { AgentToolCall, BrainRuntime, BrainTask, DeliverableAcceptance } from "../types";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;
const apiPost = api.post as unknown as Mock;

const task = {
  id: 12,
  content_item_id: 88,
  title: "任务",
  type: "content_creation",
  status: "pending_confirmation",
  brief: {
    goal: "目标",
    project_id: null,
    project_name: null,
    account_group_id: null,
    account_group_name: null,
    platforms: ["douyin"],
    account_ids: [],
    cycle: "本周",
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
  current_focus: "待确认",
  risk_count: 0,
  runtime_mode: "legacy",
  thread_id: null,
  context_closed_at: null,
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
} satisfies BrainTask;

const acceptance = {
  id: 34,
  task_id: 12,
  deliverable_id: 56,
  agent_code: "02-content-director",
  agent_name: "编导文案专家",
  deliverable_type: "video_script",
  title: "脚本",
  version: 1,
  summary: "摘要",
  acceptance_items: [],
  history_versions: [],
  status: "pending",
  reviewer_note: null,
  rerun_scope: null,
  brain_rejudge_summary: null,
  brain_rejudge_basis: [],
} satisfies DeliverableAcceptance;

const toolCall = {
  id: 45,
  org_id: 1,
  task_id: 12,
  invocation_id: 90,
  module: "brain",
  agent_code: "02-content-director",
  tool_code: "brief_builder",
  tool_name: "Brief Builder",
  status: "waiting_approval",
  permission_mode: "confirm",
  requires_human_confirmation: true,
  input_summary: "输入",
  output_summary: "输出",
  error: null,
  latency_ms: 20,
  cost: 0,
  meta: {},
  started_at: null,
  finished_at: null,
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
} satisfies AgentToolCall;

const runtime = {
  task: { ...task, runtime_mode: "langgraph", thread_id: "brain-task-12" },
  thread_id: "brain-task-12",
  status: "waiting_permission",
  timeline: [
    {
      id: 1,
      type: "brain.runtime.started",
      payload: {
        task_id: 12,
        thread_id: "brain-task-12",
        message: "主 Agent 已接收目标，开始建立运行时上下文。",
      },
      created_at: "2026-07-01T00:00:00Z",
    },
  ],
  invocations: [],
  tool_calls: [toolCall],
  acceptances: [acceptance],
  pending_permissions: [toolCall],
  next_actions: ["review_pending_permissions"],
} satisfies BrainRuntime;

describe("brain api", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("calls task list and draft endpoints", async () => {
    apiGet.mockResolvedValueOnce({ data: [task] });
    apiPost.mockResolvedValueOnce({ data: task });
    apiPost.mockResolvedValueOnce({ data: task });

    await expect(listBrainTasks()).resolves.toEqual([task]);
    await expect(draftBrainTask("目标")).resolves.toEqual(task);

    await expect(
      draftBrainTask({
        goal: "矩阵分发",
        project_id: 1,
        account_group_id: 2,
        platforms: ["douyin"],
        account_ids: [3],
      }),
    ).resolves.toEqual(task);

    expect(apiGet).toHaveBeenCalledWith("/brain/tasks");
    expect(apiPost).toHaveBeenCalledWith("/brain/tasks/draft", { goal: "目标" });
    expect(apiPost).toHaveBeenCalledWith(
      "/brain/tasks/draft",
      expect.objectContaining({
        project_id: 1,
        account_group_id: 2,
        platforms: ["douyin"],
        account_ids: [3],
      }),
    );
  });

  it("calls task execution endpoints with task ids", async () => {
    apiPost.mockResolvedValueOnce({ data: task });
    apiGet.mockResolvedValueOnce({ data: runtime });
    apiGet.mockResolvedValueOnce({ data: [] });
    apiGet.mockResolvedValueOnce({ data: [toolCall] });
    apiGet.mockResolvedValueOnce({ data: [acceptance] });

    await expect(confirmBrainTask(task)).resolves.toEqual(task);
    await expect(getBrainTaskRuntime(task.id)).resolves.toEqual(runtime);
    await expect(listTaskInvocations(task.id)).resolves.toEqual([]);
    await expect(listTaskToolCalls(task.id)).resolves.toEqual([toolCall]);
    await expect(listDeliverableAcceptances(task.id)).resolves.toEqual([acceptance]);

    expect(apiPost).toHaveBeenCalledWith("/brain/tasks/12/confirm");
    expect(apiGet).toHaveBeenCalledWith("/brain/tasks/12/runtime");
    expect(apiGet).toHaveBeenCalledWith("/brain/tasks/12/invocations");
    expect(apiGet).toHaveBeenCalledWith("/brain/tasks/12/tool-calls");
    expect(apiGet).toHaveBeenCalledWith("/brain/tasks/12/acceptances");
  });

  it("calls tool approval endpoints", async () => {
    apiGet.mockResolvedValueOnce({ data: [toolCall] });
    apiPost.mockResolvedValueOnce({ data: { ...toolCall, status: "success" } });

    await expect(listPendingToolCallApprovals()).resolves.toEqual([toolCall]);
    await expect(
      approveToolCall({ toolCallId: toolCall.id, approved: true, comment: "通过" }),
    ).resolves.toEqual({ ...toolCall, status: "success" });

    expect(apiGet).toHaveBeenCalledWith("/brain/tool-calls/pending-approvals");
    expect(apiPost).toHaveBeenCalledWith("/brain/tool-calls/45/approve", {
      approved: true,
      comment: "通过",
    });
  });

  it("routes messages and strategy decisions through the smart runtime API", async () => {
    apiPost.mockResolvedValue({ data: runtime });

    await sendBrainMessage({
      message: "分析账号并给我两个内容方向",
      project_id: 7,
      account_id: 3,
      platform: "douyin",
    });
    await selectBrainDecision({ taskId: 12, decisionId: "direction-1", choiceId: "steady" });
    await reviseBrainDecision({
      taskId: 12,
      decisionId: "direction-1",
      comment: "换一组更大胆的方向",
      requestNewOptions: true,
    });

    expect(apiPost).toHaveBeenNthCalledWith(1, "/brain/messages", {
      message: "分析账号并给我两个内容方向",
      project_id: 7,
      account_id: 3,
      platform: "douyin",
    });
    expect(apiPost).toHaveBeenNthCalledWith(
      2,
      "/brain/tasks/12/decisions/direction-1/select",
      { choice_id: "steady" },
    );
    expect(apiPost).toHaveBeenNthCalledWith(
      3,
      "/brain/tasks/12/decisions/direction-1/revise",
      { comment: "换一组更大胆的方向", request_new_options: true },
    );
  });

  it("stops and regenerates a main Agent turn through dedicated runtime endpoints", async () => {
    apiPost
      .mockResolvedValueOnce({
        data: { client_message_id: "turn-1", stop_requested: true },
      })
      .mockResolvedValueOnce({ data: runtime });

    await expect(
      stopBrainGeneration({ clientMessageId: "turn-1", taskId: 12 }),
    ).resolves.toEqual({ client_message_id: "turn-1", stop_requested: true });
    await expect(
      regenerateBrainMessage({ taskId: 12, clientMessageId: "turn-2" }),
    ).resolves.toEqual(runtime);

    expect(apiPost).toHaveBeenNthCalledWith(
      1,
      "/brain/generations/turn-1/stop",
      { task_id: 12 },
    );
    expect(apiPost).toHaveBeenNthCalledWith(
      2,
      "/brain/tasks/12/regenerate",
      { client_message_id: "turn-2" },
    );
  });

  it("calls acceptance action endpoints with acceptance ids", async () => {
    apiPost
      .mockResolvedValueOnce({ data: { ...acceptance, status: "approved" } })
      .mockResolvedValueOnce({ data: { ...acceptance, status: "rerun_requested" } })
      .mockResolvedValueOnce({ data: { ...acceptance, status: "rerun_requested" } })
      .mockResolvedValueOnce({
        data: {
          task_id: task.id,
          closed: true,
          context_closed_at: "2026-07-01T00:00:00Z",
        },
      });

    await approveDeliverableAcceptance(acceptance, "通过");
    await rejectDeliverableAcceptance({
      acceptance,
      reason: "重写钩子",
      rerun_scope: "current_agent",
      ask_brain_rejudge: true,
    });
    await rejudgeDeliverableAcceptance(acceptance);
    await closeTaskMemory(task.id);

    expect(apiPost).toHaveBeenNthCalledWith(1, "/brain/tasks/12/accept", {
      acceptance_id: 34,
      reviewer_note: "通过",
    });
    expect(apiPost).toHaveBeenNthCalledWith(2, "/brain/tasks/12/rerun", {
      acceptance_id: 34,
      reason: "重写钩子",
      rerun_scope: "current_agent",
      ask_brain_rejudge: true,
    });
    expect(apiPost).toHaveBeenNthCalledWith(3, "/brain/tasks/12/rejudge", {
      acceptance_id: 34,
    });
    expect(apiPost).toHaveBeenNthCalledWith(4, "/brain/tasks/12/close-memory");
  });
});
