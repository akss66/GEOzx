import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  approveToolCall,
  acceptArtifact,
  approveDeliverableAcceptance,
  closeTaskMemory,
  confirmBrainTask,
  createConversation,
  deleteConversation,
  draftBrainTask,
  executeDeliverableAction,
  getConversation,
  getArtifact,
  getBrainTaskRuntime,
  listArtifacts,
  listBrainTasks,
  listComposerSkills,
  listConversations,
  listConversationEvents,
  listDeliverableAcceptances,
  listPendingToolCallApprovals,
  listConversationTurnInterrupts,
  listTaskInvocations,
  listTaskToolCalls,
  rejudgeDeliverableAcceptance,
  rejectDeliverableAcceptance,
  reviseBrainDecision,
  regenerateBrainMessage,
  resolveTurnInterrupt,
  refreshBrainObservation,
  reviseArtifact,
  selectBrainDecision,
  sendConversationTurn,
  sendBrainMessage,
  stopBrainGeneration,
  stopConversationTurn,
  verifyBrainExperienceCandidate,
} from "./brain";
import { api } from "./client";
import type { AgentToolCall, BrainRuntime, BrainTask, DeliverableAcceptance } from "../types";
import {
  clearActiveConversationThreadId,
  getActiveBrainTaskId,
  getActiveConversationThreadId,
  setActiveBrainTaskId,
  setActiveConversationThreadId,
} from "../stores/brainConversation";
import { installLocalStorage } from "../test/storage";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;
const apiPost = api.post as unknown as Mock;
const apiDelete = api.delete as unknown as Mock;

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
    installLocalStorage();
  });

  it("lists durable conversation events after the supplied recovery cursor", async () => {
    const signal = new AbortController().signal;
    const events = [{
      id: 81,
      sequence: 2,
      type: "brain.runtime.message_done",
      payload: { content: "done" },
      thread_id: 21,
      turn_id: 31,
      run_id: 41,
      skill_run_id: null,
      created_at: "2026-08-04T00:00:00Z",
    }];
    apiGet.mockResolvedValueOnce({ data: { data: events } });

    await expect(listConversationEvents(21, 80, signal)).resolves.toEqual(events);
    expect(apiGet).toHaveBeenCalledWith("/conversation-threads/21/events", {
      params: { after_id: 80 },
      signal,
    });
  });

  it("calls the account-scoped conversation endpoints with an idempotent turn", async () => {
    const thread = { id: 21, account_id: 3, turns: [] };
    const submission = {
      turn: { id: 31, thread_id: 21, projections: [] },
      run: { id: 41, thread_id: 21, turn_id: 31 },
      task_id: null,
      projections: [],
    };
    apiPost
      .mockResolvedValueOnce({ data: thread })
      .mockResolvedValueOnce({ data: submission });
    apiGet.mockResolvedValueOnce({ data: thread });

    await expect(
      createConversation({ account_id: 3, title: "账号运营" }),
    ).resolves.toEqual(thread);
    await expect(
      sendConversationTurn(21, {
        client_message_id: "turn-1",
        message: "体检这个账号",
        requested_skill_code: "account_inspection",
        execution_preference: "AUTO",
      }),
    ).resolves.toEqual(submission);
    await expect(getConversation(21)).resolves.toEqual(thread);

    expect(apiPost).toHaveBeenNthCalledWith(1, "/brain/conversations", {
      account_id: 3,
      title: "账号运营",
    });
    expect(apiPost).toHaveBeenNthCalledWith(
      2,
      "/brain/conversations/21/turns",
      {
        client_message_id: "turn-1",
        message: "体检这个账号",
        requested_skill_code: "account_inspection",
        execution_preference: "AUTO",
        attachment_ids: [],
      },
    );
    expect(apiGet).toHaveBeenCalledWith("/brain/conversations/21");
  });

  it("lists and permanently deletes owned conversation history", async () => {
    const conversations = [{
      id: 21,
      account_id: 3,
      title: "账号体检",
      turn_count: 2,
      last_message: "帮我体检账号",
      created_at: "2026-07-29T00:00:00Z",
      updated_at: "2026-07-29T00:01:00Z",
    }];
    apiGet.mockResolvedValueOnce({ data: { data: conversations } });
    const deletionSummary = {
      messages_deleted: 2,
      events_deleted: 5,
      llm_calls_deleted: 1,
      attachments_deleted: 1,
      draft_artifacts_deleted: 1,
      retained_audit_categories: ["approval", "cost", "publish"],
    };
    apiDelete.mockResolvedValueOnce({ data: deletionSummary });

    await expect(listConversations(3)).resolves.toEqual(conversations);
    await expect(deleteConversation(21)).resolves.toEqual(deletionSummary);

    expect(apiGet).toHaveBeenCalledWith("/brain/conversations", {
      params: { account_id: 3 },
    });
    expect(apiDelete).toHaveBeenCalledWith("/brain/conversations/21");
  });

  it("lists only public composer Skills and paginated account artifacts", async () => {
    const skills = [
      {
        code: "account_inspection",
        version: 1,
        name: "一键账号体检",
        description: "体检",
        category: "quick_operations",
        icon: "activity",
        requires_account: true,
        availability: "available",
        reason: null,
        required_context: ["account"],
        is_available: true,
        unavailable_reason: null,
      },
    ];
    const artifacts = {
      data: [],
      pagination: { page: 2, page_size: 10, total: 0, pages: 0 },
    };
    apiGet
      .mockResolvedValueOnce({ data: { data: skills } })
      .mockResolvedValueOnce({ data: artifacts });

    await expect(listComposerSkills("douyin", 3)).resolves.toEqual(skills);
    await expect(
      listArtifacts({
        accountId: 3,
        artifactType: "account_inspection_report",
        status: "ready_for_review",
        page: 2,
        pageSize: 10,
      }),
    ).resolves.toEqual(artifacts);

    expect(apiGet).toHaveBeenNthCalledWith(1, "/skills", {
      params: { platform: "douyin", surface: "composer", account_id: 3 },
    });
    expect(apiGet).toHaveBeenNthCalledWith(2, "/artifacts", {
      params: {
        account_id: 3,
        artifact_type: "account_inspection_report",
        status: "ready_for_review",
        page: 2,
        page_size: 10,
      },
    });
  });

  it("encodes multi-value business artifact filters for server-side pagination", async () => {
    const artifacts = { data: [], pagination: { page: 1, page_size: 20, total: 0, pages: 0 } };
    apiGet.mockResolvedValueOnce({ data: artifacts });

    await expect(listArtifacts({
      accountId: 3,
      artifactTypes: ["topic_plan", "video_script"],
    })).resolves.toEqual(artifacts);

    expect(apiGet).toHaveBeenCalledWith("/artifacts", {
      params: {
        account_id: 3,
        artifact_type: undefined,
        artifact_types: ["topic_plan", "video_script"],
        status: undefined,
        page: 1,
        page_size: 20,
      },
    });
  });

  it("sends UTC calendar-day artifact boundaries to the server", async () => {
    const artifacts = { data: [], pagination: { page: 1, page_size: 20, total: 0, pages: 0 } };
    apiGet.mockResolvedValueOnce({ data: artifacts });

    await expect(listArtifacts({
      accountId: 3,
      createdFrom: "2026-07-10",
      createdTo: "2026-07-12",
    })).resolves.toEqual(artifacts);

    expect(apiGet).toHaveBeenCalledWith("/artifacts", {
      params: {
        account_id: 3,
        artifact_type: undefined,
        status: undefined,
        created_from: "2026-07-10",
        created_to: "2026-07-12",
        page: 1,
        page_size: 20,
      },
    });
  });

  it("retrieves and acts on the exact persisted Artifact identity", async () => {
    const artifact = { id: 5001, account_id: 3, status: "ready_for_review" };
    apiGet.mockResolvedValueOnce({ data: artifact });
    apiPost
      .mockResolvedValueOnce({ data: { ...artifact, status: "accepted" } })
      .mockResolvedValueOnce({ data: { ...artifact, id: 5002, version: 2 } });

    await expect(getArtifact(5001)).resolves.toEqual(artifact);
    await expect(acceptArtifact(5001)).resolves.toEqual({ ...artifact, status: "accepted" });
    await expect(reviseArtifact({
      artifactId: 5001,
      payload: { core_conclusion: "补充选题" },
      note: "请补充下周选题。",
    })).resolves.toEqual({ ...artifact, id: 5002, version: 2 });

    expect(apiGet).toHaveBeenCalledWith("/artifacts/5001");
    expect(apiPost).toHaveBeenNthCalledWith(1, "/artifact-acceptances", { artifact_id: 5001 });
    expect(apiPost).toHaveBeenNthCalledWith(2, "/artifact-revisions", {
      artifact_id: 5001,
      payload: { core_conclusion: "补充选题" },
      note: "请补充下周选题。",
    });
  });

  it("executes a typed deliverable action with an explicit idempotency key", async () => {
    const execution = {
      execution_id: 901,
      artifact_id: 5001,
      artifact_version: 1,
      action_code: "create_shoot_task",
      status: "succeeded",
      resource: { type: "shoot_task", id: 77 },
      result: { message: "拍摄任务已创建" },
      replayed: false,
    };
    apiPost.mockResolvedValueOnce({ data: execution });

    await expect(executeDeliverableAction({
      artifactId: 5001,
      actionCode: "create_shoot_task",
      idempotencyKey: "artifact-action-test-key",
      input: { confirmed: true, note: "补拍三个镜头" },
    })).resolves.toEqual(execution);

    expect(apiPost).toHaveBeenCalledWith(
      "/artifacts/5001/actions/create_shoot_task",
      { confirmed: true, note: "补拍三个镜头" },
      { headers: { "Idempotency-Key": "artifact-action-test-key" } },
    );
  });

  it("isolates active conversation Threads by account without replacing legacy tasks", () => {
    setActiveBrainTaskId(1, 101);
    setActiveConversationThreadId(1, 201);
    setActiveConversationThreadId(2, 202);

    expect(getActiveConversationThreadId(1)).toBe(201);
    expect(getActiveConversationThreadId(2)).toBe(202);
    expect(getActiveBrainTaskId(1)).toBe(101);

    clearActiveConversationThreadId(2);
    expect(getActiveConversationThreadId(2)).toBeNull();
    expect(getActiveConversationThreadId(1)).toBe(201);
    expect(getActiveBrainTaskId(1)).toBe(101);
  });

  it("ignores malformed or non-positive active conversation storage", () => {
    localStorage.setItem(
      "tongzhouxing_brain_active_conversation_threads",
      JSON.stringify({
        version: 1,
        accounts: { "1": -4, "2": 0, "3": "bad", nope: 7 },
      }),
    );
    expect(getActiveConversationThreadId(1)).toBeNull();
    expect(getActiveConversationThreadId(2)).toBeNull();
    expect(getActiveConversationThreadId(3)).toBeNull();

    localStorage.setItem(
      "tongzhouxing_brain_active_conversation_threads",
      "{broken",
    );
    expect(getActiveConversationThreadId(1)).toBeNull();
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

  it("lists and resolves canonical turn interrupts with optimistic versioning", async () => {
    const interrupt = {
      id: 71,
      account_id: 3,
      thread_id: 81,
      turn_id: 101,
      run_id: 31,
      kind: "approval",
      status: "pending",
      public_message: "Publish this draft?",
      action_label: "Publish",
      response_schema: {},
      version: 2,
      resolved_at: null,
      created_at: "2026-08-04T00:00:00Z",
      updated_at: "2026-08-04T00:00:00Z",
    };
    apiGet.mockResolvedValueOnce({ data: [interrupt] });
    apiPost.mockResolvedValueOnce({ data: { interrupt: { ...interrupt, status: "resolved" }, run_id: 31 } });

    await expect(listConversationTurnInterrupts(81)).resolves.toEqual([interrupt]);
    await resolveTurnInterrupt({
      interruptId: 71,
      expectedVersion: 2,
      resolution: { approved: true },
      idempotencyKey: "interrupt-71-approve",
    });

    expect(apiGet).toHaveBeenCalledWith(
      "/brain/conversations/81/turn-interrupts",
      { params: { status: "pending" } },
    );
    expect(apiPost).toHaveBeenCalledWith(
      "/turn-interrupts/71/resolve",
      { expected_version: 2, resolution: { approved: true } },
      { headers: { "Idempotency-Key": "interrupt-71-approve" } },
    );
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

  it("stops a persisted conversation Turn through the canonical scoped endpoint", async () => {
    const stopped = {
      thread_id: 81,
      turn_id: 501,
      run_id: 701,
      stopped: true,
      dispatch_deferred: false,
    };
    apiPost.mockResolvedValueOnce({ data: stopped });

    await expect(stopConversationTurn({
      threadId: 81,
      turnId: 501,
      reason: "Operator stopped this turn",
      idempotencyKey: "stop-thread-81-turn-501",
    })).resolves.toEqual(stopped);

    expect(apiPost).toHaveBeenCalledWith(
      "/brain/conversations/81/turns/501/stop",
      { reason: "Operator stopped this turn" },
      { headers: { "Idempotency-Key": "stop-thread-81-turn-501" } },
    );
  });

  it("refreshes real observations and verifies experience candidates through dedicated endpoints", async () => {
    const reflection = {
      id: 61,
      status: "observed",
      goal_snapshot: {},
      expected_outcome: {},
      observed_outcome: {},
      evidence_refs: [],
      diagnosis: [],
      conclusion: "真实效果已经回收。",
      next_strategy: {},
      experience_candidates: [],
      measured_at: "2026-07-27T08:00:00Z",
    };
    const memory = {
      id: 71,
      status: "verified",
      industry: "家居建材",
      action: "提高真实案例内容占比",
      condition: "账号处于增长期",
      result: "有效咨询提升",
      confidence: 0.86,
      source_refs: [],
      verification_method: "manual_confirmation",
      verification_note: "已由运营负责人复核。",
      verified_at: "2026-07-27T08:05:00Z",
    };
    apiPost
      .mockResolvedValueOnce({ data: reflection })
      .mockResolvedValueOnce({ data: memory });

    await expect(refreshBrainObservation(12)).resolves.toEqual(reflection);
    await expect(
      verifyBrainExperienceCandidate({
        taskId: 12,
        candidateKey: "case-content-growth",
        verificationNote: "已由运营负责人复核。",
      }),
    ).resolves.toEqual(memory);

    expect(apiPost).toHaveBeenNthCalledWith(
      1,
      "/brain/tasks/12/observation/refresh",
    );
    expect(apiPost).toHaveBeenNthCalledWith(
      2,
      "/brain/tasks/12/experience-candidates/case-content-growth/verify",
      {
        candidate_key: "case-content-growth",
        verification_note: "已由运营负责人复核。",
      },
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
