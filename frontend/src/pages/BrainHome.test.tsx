// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App as AntApp } from "antd";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  approveDeliverableAcceptance,
  approveToolCall,
  getBrainTaskRuntime,
  listBrainTasks,
  rejectDeliverableAcceptance,
  regenerateBrainMessage,
  refreshBrainObservation,
  sendBrainMessage,
  stopBrainGeneration,
  verifyBrainExperienceCandidate,
} from "../api/brain";
import { getWorkspaceContext } from "../api/shell";
import { useAuth } from "../stores/auth";
import type {
  Account,
  AgentInvocation,
  AgentToolCall,
  BrainRuntime,
  BrainTask,
  DeliverableAcceptance,
} from "../types";
import BrainHome from "./BrainHome";

const mocks = vi.hoisted(() => {
  const account: Account = {
    id: 3,
    nickname: "本地开发账号",
    platform: "douyin",
    group_id: null,
    project_id: null,
    status: "active",
    external_account_id: "local",
    integration_status: "connected",
    auth_status: "manual",
    data_sync_status: "manual",
    created_at: "2026-07-01T00:00:00Z",
  };

  const taskWithRuntime = {
    id: 12,
    content_item_id: null,
    title: "账号定位任务",
    type: "content_creation",
    status: "running",
    brief: {
      goal: "分析当前账号定位，生成下周内容计划",
      project_id: null,
      project_name: null,
      account_group_id: null,
      account_group_name: null,
      platforms: ["douyin"],
      account_ids: [3],
      cycle: "本周",
      budget: null,
      content_goal: "生成内容计划",
      risk_constraints: ["发布前人工确认"],
      expected_outputs: [],
      confirmation_actions: [],
    },
    plan: {
      id: 1,
      summary: "主 Agent 调度专家",
      steps: [
        {
          id: "step-positioning",
          agent_code: "01-positioning",
          agent_name: "账号定位专家",
          phase: "定位",
          intent: "分析账号定位和人群",
          status: "done",
          depends_on: [],
          expected_output: "定位结论",
          risk_level: "low",
          execution_kind: "account_diagnosis",
          human_gate: false,
          tool_codes: [],
        },
        {
          id: "step-content",
          agent_code: "02-content-director",
          agent_name: "内容策略专家",
          phase: "策略",
          intent: "生成内容方向",
          status: "planned",
          depends_on: ["step-positioning"],
          expected_output: "内容计划",
          risk_level: "medium",
          execution_kind: "content_strategy",
          human_gate: false,
          tool_codes: [],
        },
      ],
      quality_gates: [],
      estimated_cost: 0.68,
      requires_human_confirmation: true,
    },
    progress: 38,
    current_focus: "主 Agent 已生成专家执行计划",
    risk_count: 1,
    runtime_mode: "langgraph",
    thread_id: "brain-task-12",
    context_closed_at: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
  } satisfies BrainTask;

  const matrixTask = {
    ...taskWithRuntime,
    id: 13,
    title: "矩阵分发任务",
    type: "matrix_distribution",
    status: "pending_acceptance",
    runtime_mode: "legacy",
    thread_id: null,
    brief: {
      ...taskWithRuntime.brief,
      goal: "把三条素材发到 A/B 账号",
      account_ids: [3, 4],
      content_goal: "准备矩阵发布包",
    },
    plan: {
      ...taskWithRuntime.plan,
      id: 2,
      estimated_cost: 0.2,
      steps: [
        {
          id: "step-operation",
          agent_code: "06-operator",
          agent_name: "发布准备专家",
          phase: "发布包",
          intent: "准备发布包",
          status: "planned",
          depends_on: [],
          expected_output: "发布包",
          risk_level: "medium",
          execution_kind: "publish_readiness",
          human_gate: true,
          tool_codes: ["publish_package_prepare"],
        },
      ],
    },
    progress: 80,
    current_focus: "等待人工审批",
    risk_count: 0,
  } satisfies BrainTask;

  const toolCall = {
    id: 45,
    org_id: 1,
    task_id: 12,
    invocation_id: 90,
    module: "brain",
    agent_code: "02-content-director",
    tool_code: "publish_package_prepare",
    tool_name: "生成发布包",
    status: "waiting_approval",
    permission_mode: "confirm",
    requires_human_confirmation: true,
    input_summary: "是否允许生成发布包并进入人工审批？",
    output_summary: "这里需要你确认，是否允许生成发布包 / 进入人工审批？",
    error: null,
    latency_ms: 20,
    cost: 0,
    meta: {},
    started_at: null,
    finished_at: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
  } satisfies AgentToolCall;

  const invocation = {
    id: 90,
    task_id: 12,
    agent_code: "01-positioning",
    agent_name: "账号定位专家",
    status: "done",
    input_summary: "判断目标、账号人设和平台是否匹配。",
    output_summary:
      '{"account_persona":"一个敢说真话的数码产品评测老炮","target_audience":"25-35岁、理性消费、追求性价比的数码爱好者","differentiation":["拒绝厂商充值，用真实体验做判断","用冲突反转结构对比热门产品"],"content_pillars":["热门数码产品对比实测","差评安抚与售后维权技巧"]}',
    model: "deepseek-chat",
    token_count: 0,
    cost: 0,
    failure_reason: null,
    upstream: [],
    started_at: "2026-07-01T00:00:01Z",
    finished_at: "2026-07-01T00:01:00Z",
  } satisfies AgentInvocation;

  const acceptance = {
    id: 71,
    task_id: 12,
    deliverable_id: 18,
    agent_code: "01-positioning",
    agent_name: "账号定位专家",
    deliverable_type: "positioning_strategy",
    title: "账号定位诊断",
    version: 1,
    summary: "建议以真实数码体验和理性选购建议建立账号差异化。",
    acceptance_items: [
      { label: "目标人群", status: "pass", note: "已明确核心年龄与消费偏好" },
      { label: "内容支柱", status: "warn", note: "需要补充售后维权案例" },
    ],
    history_versions: [],
    status: "pending",
    reviewer_note: null,
    rerun_scope: null,
    brain_rejudge_summary: null,
    brain_rejudge_basis: [],
  } satisfies DeliverableAcceptance;

  const runtime = {
    task: taskWithRuntime,
    thread_id: "brain-task-12",
    status: "waiting_permission",
    timeline: [
      {
        id: 0,
        type: "brain.runtime.user_message",
        payload: {
          task_id: 12,
          message: "分析当前账号定位，生成下周内容计划",
        },
        created_at: "2026-07-01T00:00:00Z",
      },
      {
        id: 1,
        type: "brain.runtime.message_done",
        payload: {
          task_id: 12,
          message_id: "00-decision:1",
          agent_code: "00-decision",
          agent_name: "主 Agent",
          content: "好的，我先理解目标，然后调用账号定位专家。",
          model: "deepseek-chat",
        },
        created_at: "2026-07-01T00:00:00Z",
      },
      {
        id: 2,
        type: "brain.runtime.subagent_started",
        payload: {
          message: "账号定位专家开始分析...",
          task_id: 12,
          thread_id: "brain-task-12",
          invocation_id: 90,
        },
        created_at: "2026-07-01T00:00:01Z",
      },
      {
        id: 3,
        type: "brain.runtime.subagent_completed",
        payload: {
          message: "账号定位专家完成，下一步交给内容策略专家...",
          task_id: 12,
          thread_id: "brain-task-12",
          invocation_id: 90,
        },
        created_at: "2026-07-01T00:01:00Z",
      },
    ],
    invocations: [invocation],
    tool_calls: [toolCall],
    acceptances: [acceptance],
    pending_permissions: [toolCall],
    next_actions: ["review_pending_permissions"],
  } satisfies BrainRuntime;

  const workspace = {
    clientId: 1 as number | null,
    projectId: 2 as number | null,
    platform: "douyin" as const,
    accountId: 3 as number | null,
    setAccountId: vi.fn(),
  };
  const contextAccounts: Account[] = [account];

  return {
    eventHandler: null as ((event: {
      type: string;
      payload?: unknown;
    }) => void) | null,
    streamOptions: null as { onReconnect?: () => void } | null,
    acceptance,
    account,
    invocation,
    matrixTask,
    runtime,
    taskWithRuntime,
    toolCall,
    workspace,
    contextAccounts,
  };
});

vi.mock("../api/shell", () => ({
  getWorkspaceContext: vi.fn(async () => ({
    clients: [],
    selected_client: null,
    projects: [],
    selected_project: null,
    accounts: mocks.contextAccounts,
  })),
}));

vi.mock("../api/brain", () => ({
  approveDeliverableAcceptance: vi.fn(async () => ({ ...mocks.acceptance, status: "approved" })),
  approveToolCall: vi.fn(async () => mocks.toolCall),
  getBrainTaskRuntime: vi.fn(async () => mocks.runtime),
  listBrainTasks: vi.fn(async () => [mocks.taskWithRuntime, mocks.matrixTask]),
  rejectDeliverableAcceptance: vi.fn(async () => ({ ...mocks.acceptance, status: "rerun_requested" })),
  regenerateBrainMessage: vi.fn(async () => mocks.runtime),
  refreshBrainObservation: vi.fn(async () => ({
    id: 61,
    status: "observed",
    goal_snapshot: {},
    expected_outcome: {},
    observed_outcome: {},
    evidence_refs: [],
    diagnosis: [],
    conclusion: "真实效果已经回收。",
    next_strategy: {},
    experience_candidates: [
      {
        key: "case-content-growth",
        industry: "家居建材",
        action: "提高真实案例内容占比",
        condition: "账号处于增长期",
        result: "有效咨询提升",
        confidence: 0.86,
        source_refs: [{ source_type: "account_metric_snapshot", source_id: "snapshot:2" }],
      },
    ],
    measured_at: "2026-07-27T08:00:00Z",
  })),
  reviseBrainDecision: vi.fn(async () => mocks.runtime),
  selectBrainDecision: vi.fn(async () => mocks.runtime),
  sendBrainMessage: vi.fn(async () => mocks.runtime),
  stopBrainGeneration: vi.fn(async () => ({
    client_message_id: "pending-turn",
    stop_requested: true,
  })),
  verifyBrainExperienceCandidate: vi.fn(async () => ({
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
  })),
}));

vi.mock("../hooks/useEventStream", () => ({
  useEventStream: vi.fn((handler, options) => {
    mocks.eventHandler = handler;
    mocks.streamOptions = options;
    return { connected: true, connectionState: "connected", last: null };
  }),
}));

vi.mock("../stores/currentWorkspace", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../stores/currentWorkspace")>();
  return {
    ...actual,
    useCurrentWorkspace: vi.fn(() => mocks.workspace),
  };
});

describe("BrainHome", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    localStorage.clear();
    mocks.workspace.accountId = 3;
    mocks.eventHandler = null;
    mocks.streamOptions = null;
    mocks.account.auth_status = "manual";
    mocks.contextAccounts.splice(0, mocks.contextAccounts.length, mocks.account);
    mocks.runtime.timeline[0].payload.message = "分析当前账号定位，生成下周内容计划";
    mocks.runtime.timeline[1].payload.agent_name = "主 Agent";
    mocks.runtime.timeline[1].payload.content = "好的，我先理解目标，然后调用账号定位专家。";
  });

  it("does not show preset prompt shortcuts in the composer", async () => {
    renderBrainHome();

    expect(await screen.findByText("本地开发账号")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "账号定位诊断" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "冷启动内容" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "脚本生成" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "发布前检查" })).not.toBeInTheDocument();
  });

  it("requires the account selected in the global shell instead of silently picking one", async () => {
    mocks.workspace.accountId = null;

    renderBrainHome();

    expect(await screen.findByText("尚未选择抖音账号")).toBeInTheDocument();
    expect(screen.getByText("运营任务必须绑定真实账号，系统不会替你默认选择。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /发送给运营大脑/ })).toBeDisabled();
    expect(listBrainTasks).not.toHaveBeenCalled();
    expect(getBrainTaskRuntime).not.toHaveBeenCalled();
    expect(sendBrainMessage).not.toHaveBeenCalled();
  });

  it("shows a recoverable state when the account context cannot be loaded", async () => {
    vi.mocked(getWorkspaceContext).mockRejectedValueOnce({
      response: { status: 503, headers: { "x-request-id": "brain-context-1" } },
    });

    renderBrainHome();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("运营上下文加载失败");
    expect(alert).toHaveTextContent("当前账号选择不会被替换");
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByText("本地开发账号")).toBeInTheDocument();
  });

  it("does not present a failed task ledger as a new empty conversation", async () => {
    vi.mocked(listBrainTasks).mockRejectedValueOnce({ response: { status: 503 } });

    renderBrainHome();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("任务记录加载失败");
    expect(screen.queryByText(/你可以直接输入运营目标/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /发送给运营大脑/ })).toBeDisabled();

    fireEvent.click(within(alert).getByRole("button", { name: /重\s*试/ }));
    expect(await screen.findByText("本地开发账号")).toBeInTheDocument();
    expect(await screen.findByText(/先告诉我目标/)).toBeInTheDocument();
  });

  it("keeps an explicitly active task visible when its runtime fails to load", async () => {
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: { 3: 12 } }),
    );
    vi.mocked(getBrainTaskRuntime).mockRejectedValueOnce({ response: { status: 503 } });

    renderBrainHome();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("任务运行时加载失败");
    expect(screen.queryByText(/你可以直接输入运营目标/)).not.toBeInTheDocument();
    expect(localStorage.getItem("tongzhouxing_brain_active_tasks")).toContain('"3":12');

    fireEvent.click(within(alert).getByRole("button", { name: /重\s*试/ }));
    expect(await screen.findByText("好的，我先理解目标，然后调用账号定位专家。")).toBeInTheDocument();
  });

  it("does not use the first account when the stored account selection is stale", async () => {
    mocks.workspace.accountId = 999;

    renderBrainHome();

    expect(await screen.findByText("尚未选择抖音账号")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /发送给运营大脑/ })).toBeDisabled();
    expect(listBrainTasks).not.toHaveBeenCalled();
    expect(getBrainTaskRuntime).not.toHaveBeenCalled();
    expect(sendBrainMessage).not.toHaveBeenCalled();
  });

  it("uses the same client and project account set as the global account switcher", async () => {
    mocks.contextAccounts.splice(0, mocks.contextAccounts.length);

    renderBrainHome();

    await waitFor(() => expect(getWorkspaceContext).toHaveBeenCalledWith(1, 2));
    expect(await screen.findByText("尚未选择抖音账号")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /发送给运营大脑/ })).toBeDisabled();
    expect(sendBrainMessage).not.toHaveBeenCalled();
  });

  it("keeps an unauthorized selected account visible but blocks formal execution", async () => {
    mocks.account.auth_status = "unauthorized";

    renderBrainHome();

    expect(await screen.findByText("本地开发账号")).toBeInTheDocument();
    expect(screen.getByText("当前账号尚未完成授权，请先到账号矩阵完成抖音授权。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /发送给运营大脑/ })).toBeDisabled();
  });

  it("does not auto-open historical runtime tasks as the current conversation", async () => {
    renderBrainHome();

    const conversation = await screen.findByLabelText("运营大脑对话流");
    expect(conversation).toBeInTheDocument();
    expect(within(conversation).getByText("运营大脑")).toBeInTheDocument();
    expect(await screen.findByText("今天，想推进什么？")).toBeInTheDocument();
    expect(
      document.querySelector(".tz-brain-welcome__agent img"),
    ).toHaveAttribute("src", "/main-agent-avatar.png");
    expect(conversation).not.toHaveTextContent("分析当前账号定位，生成下周内容计划");
    expect(conversation).not.toHaveTextContent("帮我诊断账号数据");
    expect(screen.queryByLabelText("需要用户确认")).not.toBeInTheDocument();
    expect(screen.queryByText(/Brief/i)).not.toBeInTheDocument();
    expect(screen.queryByText("运行状态")).not.toBeInTheDocument();
    expect(screen.queryByText("专家接力")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /执行详情/ })).not.toBeInTheDocument();
  });

  it("uses the dedicated avatar for the main Agent identity", async () => {
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: { 3: 12 } }),
    );

    renderBrainHome();

    await screen.findByLabelText("运营大脑对话流");
    await screen.findByText("好的，我先理解目标，然后调用账号定位专家。");
    const identities = screen.getAllByRole("img", { name: "运营大脑" });
    expect(identities.length).toBeGreaterThan(0);
    expect(identities[0].querySelector("img")).toHaveAttribute(
      "src",
      "/main-agent-avatar.png",
    );
  });

  it("normalizes legacy system identity copy without rewriting the user message", async () => {
    mocks.runtime.timeline[0].payload.message = "请解释主 Agent 和专家的分工";
    mocks.runtime.timeline[1].payload.agent_name = "主 Agent";
    mocks.runtime.timeline[1].payload.content = "主 Agent 正在理解目标";
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: { 3: 12 } }),
    );

    renderBrainHome();

    const conversation = await screen.findByLabelText("运营大脑对话流");
    expect(await within(conversation).findByText("请解释主 Agent 和专家的分工"))
      .toBeInTheDocument();
    expect(within(conversation).getByText("运营大脑")).toBeInTheDocument();
    expect(within(conversation).getByText("运营大脑正在理解目标"))
      .toBeInTheDocument();
  });

  it("restores only the active task explicitly saved for the selected account", async () => {
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: { 3: 12 } }),
    );

    renderBrainHome();

    const conversation = await screen.findByLabelText("运营大脑对话流");
    expect(await within(conversation).findByText(
      "好的，我先理解目标，然后调用账号定位专家。",
    )).toBeInTheDocument();
    expect(conversation).toHaveTextContent("账号定位专家");
    expect(sendBrainMessage).not.toHaveBeenCalled();
  });

  it("clears the saved active task when the user starts a new conversation", async () => {
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: { 3: 12 } }),
    );
    renderBrainHome();
    await screen.findByText("好的，我先理解目标，然后调用账号定位专家。");

    fireEvent.click(screen.getByRole("button", { name: /新对话/ }));

    await waitFor(() => {
      expect(screen.getByText(/今天，想推进什么/)).toBeInTheDocument();
      expect(localStorage.getItem("tongzhouxing_brain_active_tasks")).toBe(
        JSON.stringify({ version: 1, accounts: {} }),
      );
    });
  });

  it("opens runtime status and expert handoff only when execution details are requested", async () => {
    renderBrainHome();

    const input = await screen.findByRole("textbox", { name: "运营大脑消息" });
    fireEvent.change(input, { target: { value: "帮我诊断这个账号，并生成下周内容计划" } });
    fireEvent.click(screen.getByRole("button", { name: /发送给运营大脑/ }));
    await screen.findByText("好的，我先理解目标，然后调用账号定位专家。");

    fireEvent.click(await screen.findByRole("button", { name: /执行详情/ }));

    expect(await screen.findByRole("dialog", { name: "执行详情" })).toBeInTheDocument();
    expect(screen.getByText("运行状态")).toBeInTheDocument();
    expect(screen.getByText("专家接力")).toBeInTheDocument();
  });

  it("keeps the composer at the bottom and starts a workflow directly", async () => {
    renderBrainHome();

    const input = await screen.findByRole("textbox", { name: "运营大脑消息" });
    fireEvent.change(input, { target: { value: "帮我诊断这个账号，并生成下周内容计划" } });
    fireEvent.click(screen.getByRole("button", { name: /发送给运营大脑/ }));

    await waitFor(() => {
      expect(vi.mocked(sendBrainMessage).mock.calls[0]?.[0]).toEqual({
        message: "帮我诊断这个账号，并生成下周内容计划",
        client_message_id: expect.any(String),
        task_id: undefined,
        project_id: 2,
        account_id: 3,
        platform: "douyin",
      });
    });
    expect(await screen.findByText("好的，我先理解目标，然后调用账号定位专家。")).toBeInTheDocument();
    expect(screen.getAllByText("账号定位专家").length).toBeGreaterThan(0);
    expect(screen.getAllByText("等待你确认：生成发布前检查清单").length).toBeGreaterThan(0);
    expect(screen.getByRole("article", { name: "正式成果：账号定位诊断" })).toHaveTextContent(
      "建议以真实数码体验和理性选购建议建立账号差异化。",
    );
    expect(screen.queryByText("deepseek-chat")).not.toBeInTheDocument();
    expect(screen.queryByText(/account_persona/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "采用成果" }));
    await waitFor(() => {
      expect(vi.mocked(approveDeliverableAcceptance).mock.calls[0]?.[0]).toEqual(
        mocks.acceptance,
      );
    });
  });

  it("shows the submitted turn and thinking state before the request completes", async () => {
    let finishRequest: ((runtime: BrainRuntime) => void) | undefined;
    vi.mocked(listBrainTasks).mockResolvedValueOnce([]);
    vi.mocked(sendBrainMessage).mockImplementationOnce(
      () => new Promise((resolve) => { finishRequest = resolve; }),
    );

    renderBrainHome();
    const input = await screen.findByRole("textbox", { name: "运营大脑消息" });
    fireEvent.change(input, { target: { value: "分析这个账号最近的内容表现" } });
    fireEvent.click(screen.getByRole("button", { name: /发送给运营大脑/ }));

    expect(await screen.findByText("分析这个账号最近的内容表现")).toBeVisible();
    expect(input).toHaveValue("");
    expect(screen.getByText("正在思考...")).toBeVisible();
    expect(finishRequest).toBeDefined();

    await act(async () => finishRequest?.(mocks.runtime));
  });

  it("lets the user stop the exact in-flight generation", async () => {
    vi.mocked(listBrainTasks).mockResolvedValueOnce([]);
    vi.mocked(sendBrainMessage).mockImplementationOnce(() => new Promise(() => undefined));

    renderBrainHome();
    const input = await screen.findByRole("textbox", { name: "运营大脑消息" });
    fireEvent.change(input, { target: { value: "先生成三个内容方向" } });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    await waitFor(() => expect(sendBrainMessage).toHaveBeenCalledOnce());
    const request = vi.mocked(sendBrainMessage).mock.calls[0][0];

    fireEvent.click(await screen.findByRole("button", { name: "停止生成" }));

    await waitFor(() => expect(stopBrainGeneration).toHaveBeenCalledOnce());
    expect(vi.mocked(stopBrainGeneration).mock.calls[0][0]).toEqual({
      clientMessageId: request.client_message_id,
      taskId: null,
    });
  });

  it("regenerates the last main Agent turn without submitting a duplicate user message", async () => {
    const completedRuntime: BrainRuntime = {
      ...mocks.runtime,
      status: "completed",
      pending_permissions: [],
      next_actions: [],
    };
    vi.mocked(getBrainTaskRuntime).mockResolvedValue(completedRuntime);
    vi.mocked(regenerateBrainMessage).mockResolvedValue(completedRuntime);
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: { 3: 12 } }),
    );

    renderBrainHome();
    await screen.findByText("好的，我先理解目标，然后调用账号定位专家。");
    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));

    await waitFor(() => expect(regenerateBrainMessage).toHaveBeenCalledOnce());
    expect(vi.mocked(regenerateBrainMessage).mock.calls[0][0]).toEqual({
      taskId: 12,
      clientMessageId: expect.any(String),
    });
    expect(sendBrainMessage).not.toHaveBeenCalled();
  });

  it("refetches durable runtime state after the event socket reconnects", async () => {
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: { 3: 12 } }),
    );
    renderBrainHome();
    await screen.findByText("好的，我先理解目标，然后调用账号定位专家。");
    const callsBeforeReconnect = vi.mocked(getBrainTaskRuntime).mock.calls.length;

    await act(async () => mocks.streamOptions?.onReconnect?.());

    await waitFor(() => {
      expect(vi.mocked(getBrainTaskRuntime).mock.calls.length).toBeGreaterThan(
        callsBeforeReconnect,
      );
    });
  });

  it("shows matching token deltas before the message request has completed", async () => {
    const streamingTask = {
      ...mocks.taskWithRuntime,
      id: 88,
      title: "流式诊断任务",
      thread_id: "brain-task-88",
      brief: {
        ...mocks.taskWithRuntime.brief,
        goal: "流式分析当前账号",
      },
    } satisfies BrainTask;
    const streamingRuntime: BrainRuntime = {
      ...mocks.runtime,
      task: streamingTask,
      thread_id: "brain-task-88",
      timeline: [
        {
          id: 880,
          type: "brain.runtime.user_message",
          payload: { message: "流式分析当前账号", content: "流式分析当前账号" },
          created_at: "2026-07-01T00:00:00Z",
        },
        {
          id: 881,
          type: "brain.runtime.message_done",
          payload: {
            task_id: 88,
            message_id: "previous-turn:00-decision:1",
            agent_code: "00-decision",
            agent_name: "主 Agent",
            content: "这是上一轮已经完成的回复。",
            model: "deepseek-chat",
          },
          created_at: "2026-07-01T00:00:01Z",
        },
      ],
      invocations: [],
      tool_calls: [],
      acceptances: [],
      pending_permissions: [],
      pending_decisions: [],
    };
    let finishRequest: ((runtime: BrainRuntime) => void) | undefined;
    vi.mocked(sendBrainMessage).mockImplementationOnce(
      () => new Promise((resolve) => { finishRequest = resolve; }),
    );
    vi.mocked(listBrainTasks)
      .mockResolvedValueOnce([streamingTask])
      .mockResolvedValueOnce([streamingTask]);
    vi.mocked(getBrainTaskRuntime).mockResolvedValue(streamingRuntime);
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: { 3: 88 } }),
    );

    renderBrainHome();
    expect(await screen.findByText("这是上一轮已经完成的回复。")).toBeInTheDocument();
    const input = await screen.findByRole("textbox", { name: "运营大脑消息" });
    fireEvent.change(input, { target: { value: "继续流式分析" } });
    fireEvent.click(screen.getByRole("button", { name: /发送给运营大脑/ }));

    await waitFor(() => expect(sendBrainMessage).toHaveBeenCalledOnce());
    const request = vi.mocked(sendBrainMessage).mock.calls[0]?.[0] as {
      client_message_id?: string;
    };
    expect(request.client_message_id).toBeTruthy();

    await act(async () => {
      mocks.eventHandler?.({
        type: "brain.runtime.message_start",
        payload: {
          task_id: 88,
          client_message_id: request.client_message_id,
          message_id: `${request.client_message_id}:00-decision:1`,
          agent_code: "00-decision",
          agent_name: "主 Agent",
          model: "deepseek-chat",
        },
      });
    });

    const thinkingMessage = await screen.findByText("正在思考...");
    const pendingUserMessage = screen.getByText("继续流式分析");
    expect(
      pendingUserMessage.compareDocumentPosition(thinkingMessage)
        & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    await act(async () => {
      mocks.eventHandler?.({
        type: "brain.runtime.message_delta",
        payload: {
          task_id: 88,
          client_message_id: request.client_message_id,
          message_id: `${request.client_message_id}:00-decision:1`,
          agent_code: "00-decision",
          agent_name: "主 Agent",
          model: "deepseek-chat",
          delta: "我正在逐字分析",
        },
      });
    });

    expect(await screen.findByText("我正在逐字分析")).toBeInTheDocument();
    expect(finishRequest).toBeDefined();
    await act(async () => finishRequest?.(streamingRuntime));
    vi.mocked(getBrainTaskRuntime).mockResolvedValue(mocks.runtime);
  });

  it("follows new messages at the bottom but pauses while the user reads history", async () => {
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: { 3: 12 } }),
    );

    renderBrainHome();
    await screen.findByText("好的，我先理解目标，然后调用账号定位专家。");

    const conversation = screen.getByRole("region", { name: "运营大脑对话流" });
    let scrollTop = 580;
    Object.defineProperties(conversation, {
      clientHeight: { configurable: true, value: 400 },
      scrollHeight: { configurable: true, value: 1000 },
      scrollTop: {
        configurable: true,
        get: () => scrollTop,
        set: (value: number) => { scrollTop = value; },
      },
    });
    const scrollTo = vi.fn(({ top }: ScrollToOptions) => {
      if (typeof top === "number") scrollTop = top;
    });
    Object.defineProperty(conversation, "scrollTo", {
      configurable: true,
      value: scrollTo,
    });

    fireEvent.scroll(conversation);
    await act(async () => {
      mocks.eventHandler?.({
        type: "brain.runtime.message_delta",
        payload: {
          task_id: 12,
          message_id: "auto-follow:00-decision:1",
          agent_code: "00-decision",
          agent_name: "主 Agent",
          delta: "继续生成最新内容",
        },
      });
    });
    await waitFor(() => expect(scrollTo).toHaveBeenCalled());

    scrollTo.mockClear();
    scrollTop = 120;
    fireEvent.scroll(conversation);
    await act(async () => {
      mocks.eventHandler?.({
        type: "brain.runtime.message_delta",
        payload: {
          task_id: 12,
          message_id: "auto-follow:00-decision:1",
          agent_code: "00-decision",
          agent_name: "主 Agent",
          delta: "，但不要打断历史阅读",
        },
      });
    });
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it("sends artifact revision feedback through the rerun workflow", async () => {
    renderBrainHome();

    const input = await screen.findByRole("textbox", { name: "运营大脑消息" });
    fireEvent.change(input, { target: { value: "帮我诊断这个账号，并生成下周内容计划" } });
    fireEvent.click(screen.getByRole("button", { name: /发送给运营大脑/ }));
    await screen.findByRole("article", { name: "正式成果：账号定位诊断" });

    fireEvent.click(screen.getByRole("button", { name: "修改并重做" }));
    fireEvent.change(screen.getByPlaceholderText("写下需要调整的具体内容"), {
      target: { value: "补充三个更具体的内容支柱。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交重做" }));

    await waitFor(() => {
      expect(rejectDeliverableAcceptance).toHaveBeenCalledWith({
        acceptance: mocks.acceptance,
        reason: "补充三个更具体的内容支柱。",
        rerun_scope: "current_agent",
        ask_brain_rejudge: true,
      });
    });
  });

  it("answers a greeting without creating a workflow or dispatching experts", async () => {
    vi.mocked(listBrainTasks).mockResolvedValueOnce([]);
    const greetingRuntime: BrainRuntime = {
      ...mocks.runtime,
      status: "completed",
      intent: {
        intent: "conversation",
        confidence: 1,
        reason: "普通问候",
        missing_field: null,
        clarifying_question: null,
        suggested_expert_codes: [],
        requires_account_context: false,
      },
      timeline: [
        {
          id: 101,
          type: "brain.runtime.user_message",
          payload: { task_id: 12, message: "你好" },
          created_at: "2026-07-01T00:00:00Z",
        },
        {
          id: 102,
          type: "brain.runtime.message_done",
          payload: {
            task_id: 12,
            message_id: "main-hello",
            agent_code: "00-decision",
            agent_name: "主 Agent",
            content: "你好，我在。今天想先聊聊什么？",
          },
          created_at: "2026-07-01T00:00:01Z",
        },
      ],
      invocations: [],
      tool_calls: [],
      acceptances: [],
      pending_permissions: [],
      pending_decisions: [],
      next_actions: [],
    };
    vi.mocked(sendBrainMessage).mockResolvedValueOnce(greetingRuntime);
    vi.mocked(getBrainTaskRuntime).mockResolvedValueOnce(greetingRuntime);
    renderBrainHome();

    const input = await screen.findByRole("textbox", { name: "运营大脑消息" });
    fireEvent.change(input, { target: { value: "你好" } });
    fireEvent.click(screen.getByRole("button", { name: /发送给运营大脑/ }));

    const conversation = await screen.findByLabelText("运营大脑对话流");
    const userMessage = within(conversation).getByRole("article", { name: "你的消息" });
    expect(within(userMessage).getByText("你好")).toBeInTheDocument();
    expect(within(userMessage).queryByText("你")).not.toBeInTheDocument();
    expect(conversation).toHaveTextContent("你好，我在。今天想先聊聊什么？");
    expect(conversation).not.toHaveTextContent("本轮专家协作已完成");
    expect(within(conversation).queryByText("账号定位专家")).not.toBeInTheDocument();
    expect(vi.mocked(sendBrainMessage).mock.calls[0]?.[0]).toEqual({
      message: "你好",
      client_message_id: expect.any(String),
      task_id: undefined,
      project_id: 2,
      account_id: 3,
      platform: "douyin",
    });
    expect(screen.queryByLabelText("需要用户确认")).not.toBeInTheDocument();
  });

  it("submits confirmation comments from the inline approval panel", async () => {
    renderBrainHome();

    const input = await screen.findByRole("textbox", { name: "运营大脑消息" });
    fireEvent.change(input, { target: { value: "帮我诊断这个账号，并生成下周内容计划" } });
    fireEvent.click(screen.getByRole("button", { name: /发送给运营大脑/ }));

    const composer = await screen.findByLabelText("运营大脑输入区");
    fireEvent.click(within(composer).getByRole("button", { name: "修改要求" }));
    const comment = within(composer).getByPlaceholderText("写下希望专家如何调整；驳回后会按此要求重做");
    fireEvent.change(comment, { target: { value: "标题更克制，先不要进入发布。" } });
    fireEvent.click(within(composer).getByLabelText("驳回并重做"));

    await waitFor(() => {
      expect(vi.mocked(approveToolCall).mock.calls[0]?.[0]).toEqual({
        toolCallId: 45,
        approved: false,
        comment: "标题更克制，先不要进入发布。",
      });
    });
  });

  it("projects one expert turn from its start and completion events", async () => {
    vi.mocked(getBrainTaskRuntime).mockResolvedValueOnce({
      ...mocks.runtime,
      timeline: [
        ...mocks.runtime.timeline,
        {
          id: 4,
          type: "brain.runtime.message_done",
          payload: {
            task_id: 12,
            message_id: "01-positioning:1",
            agent_code: "01-positioning",
            agent_name: "账号定位专家",
            content: "RAW_EXPERT_STREAM_SHOULD_COLLAPSE",
          },
          created_at: "2026-07-01T00:01:00Z",
        },
      ],
    });
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: { 3: 12 } }),
    );

    renderBrainHome();

    const conversation = await screen.findByLabelText("运营大脑对话流");
    await within(conversation).findByText("好的，我先理解目标，然后调用账号定位专家。");
    expect(
      within(conversation).getAllByRole("article", { name: "专家：账号定位专家" }),
    ).toHaveLength(1);
    expect(conversation).toHaveTextContent("账号定位专家完成，下一步交给内容策略专家");
    expect(conversation).not.toHaveTextContent("RAW_EXPERT_STREAM_SHOULD_COLLAPSE");
  });

  it("presents AI COO strategy, quality and reflection as readable conversation records", async () => {
    vi.mocked(getBrainTaskRuntime).mockResolvedValueOnce({
      ...mocks.runtime,
      strategy: {
        id: 31,
        task_id: 12,
        status: "active",
        version: 1,
        goal: "提升有效咨询",
        situation_snapshot: { data_sufficiency: "partial" },
        strategy: { period_days: 30, primary_action: "提高真实案例内容占比" },
        kpis: [{ metric: "qualified_leads", target: 20 }],
        risks: ["样本窗口较短"],
        evidence_refs: [{ source_type: "account_metric_snapshot", source_id: "snapshot:1" }],
        rationale_summary: "真实案例内容的咨询效率高于普通产品介绍。",
      },
      decisions: [
        {
          id: 41,
          trace_key: "strategy-1",
          goal: "提升有效咨询",
          evidence_refs: [],
          alternatives: [],
          selected_option: { title: "提高真实案例内容占比" },
          decision_reason: "近两周真实案例带来的咨询效率更高。",
          action_summary: "下一周期优先制作真实案例内容。",
          outcome: {},
          status: "decided",
        },
      ],
      quality_scores: [
        {
          id: 51,
          score: 86,
          dimensions: { factual_accuracy: 92 },
          issues: ["观察窗口较短"],
          suggestions: ["继续跟踪有效咨询"],
          passed: true,
          iteration: 0,
          evidence_refs: [],
          critic_model: "deepseek-chat",
        },
      ],
      reflection: {
        id: 61,
        status: "observed",
        goal_snapshot: {},
        expected_outcome: {},
        observed_outcome: {},
        evidence_refs: [],
        diagnosis: [],
        conclusion: "真实案例内容带来的有效咨询达到目标。",
        next_strategy: { action: "continue_and_expand_observation" },
        experience_candidates: [],
        measured_at: "2026-07-27T08:00:00Z",
      },
      operation_intelligence: {
        task_id: 12,
        score: 84,
        components: {
          strategy_quality: 88,
          evidence_quality: 80,
          execution_effect: 86,
          learning_quality: 78,
        },
        weights: {
          strategy_quality: 0.3,
          evidence_quality: 0.25,
          execution_effect: 0.25,
          learning_quality: 0.2,
        },
        basis: ["策略版本 1"],
        data_sufficiency: "sufficient",
        calculated_at: "2026-07-27T08:00:00Z",
      },
    });
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: { 3: 12 } }),
    );

    renderBrainHome();

    const conversation = await screen.findByLabelText("运营大脑对话流");
    expect(await within(conversation).findByText("30 天运营策略")).toBeInTheDocument();
    expect(conversation).toHaveTextContent("提升有效咨询");
    expect(conversation).toHaveTextContent("质量审核 86 分");
    expect(conversation).toHaveTextContent("真实案例内容带来的有效咨询达到目标");
    expect(conversation).not.toHaveTextContent('"primary_action"');

    fireEvent.click(screen.getByRole("button", { name: /执行详情/ }));
    expect(await screen.findByText("运营智能评分")).toBeInTheDocument();
    expect(screen.getByText("84")).toBeInTheDocument();
  });

  it("checks real performance and verifies an evidence-backed experience from execution details", async () => {
    vi.mocked(getBrainTaskRuntime).mockResolvedValueOnce({
      ...mocks.runtime,
      reflection: {
        id: 61,
        status: "observed",
        goal_snapshot: {},
        expected_outcome: {},
        observed_outcome: {},
        evidence_refs: [{ source_type: "account_metric_snapshot", source_id: "snapshot:2" }],
        diagnosis: [],
        conclusion: "真实案例内容带来的有效咨询达到目标。",
        next_strategy: {},
        experience_candidates: [
          {
            key: "case-content-growth",
            industry: "家居建材",
            action: "提高真实案例内容占比",
            condition: "账号处于增长期",
            result: "有效咨询提升",
            confidence: 0.86,
            source_refs: [{ source_type: "account_metric_snapshot", source_id: "snapshot:2" }],
          },
        ],
        measured_at: "2026-07-27T08:00:00Z",
      },
      experience_memories: [],
    });
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: { 3: 12 } }),
    );

    renderBrainHome();
    await screen.findByText("好的，我先理解目标，然后调用账号定位专家。");
    fireEvent.click(screen.getByRole("button", { name: /执行详情/ }));

    fireEvent.click(await screen.findByRole("button", { name: "检查最新效果" }));
    await waitFor(() => expect(refreshBrainObservation).toHaveBeenCalledWith(12));

    const note = screen.getByPlaceholderText("写下人工核验依据");
    fireEvent.change(note, { target: { value: "已由运营负责人复核。" } });
    fireEvent.click(screen.getByRole("button", { name: "确认沉淀经验" }));

    await waitFor(() => {
      expect(verifyBrainExperienceCandidate).toHaveBeenCalledWith({
        taskId: 12,
        candidateKey: "case-content-growth",
        verificationNote: "已由运营负责人复核。",
      });
    });
  });

  it("continues an active runtime in the same task thread", async () => {
    vi.mocked(getBrainTaskRuntime).mockResolvedValueOnce({
      ...mocks.runtime,
      status: "running",
      pending_permissions: [],
      next_actions: [],
    });
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: { 3: 12 } }),
    );
    renderBrainHome();
    await screen.findByText("好的，我先理解目标，然后调用账号定位专家。");

    const input = screen.getByRole("textbox", { name: "运营大脑消息" });
    fireEvent.change(input, { target: { value: "先补充三个更年轻化的选题" } });
    fireEvent.click(screen.getByRole("button", { name: /发送给运营大脑/ }));

    await waitFor(() => expect(vi.mocked(sendBrainMessage).mock.calls.at(-1)?.[0]).toEqual({
      message: "先补充三个更年轻化的选题",
      client_message_id: expect.any(String),
      task_id: 12,
      project_id: 2,
      account_id: 3,
      platform: "douyin",
    }));
  });
  it("shows sanitized model-call audit only to administrators", async () => {
    useAuth.setState({
      token: "admin-token",
      user: {
        id: 1,
        email: "admin@tzxai.top",
        display_name: "系统管理员",
        role: "admin",
        is_active: true,
      },
    });
    vi.mocked(getBrainTaskRuntime).mockResolvedValueOnce({
      ...mocks.runtime,
      strategy: {
        id: 31,
        task_id: 12,
        status: "active",
        version: 1,
        goal: "提升有效咨询",
        situation_snapshot: {},
        strategy: {},
        kpis: [],
        risks: [],
        evidence_refs: [],
        rationale_summary: "基于账号真实表现制定。",
        prompt_id: "main-agent.strategy-planning",
        prompt_version: "v1",
        prompt_hash: "strategy-hash",
      },
      quality_scores: [
        {
          id: 41,
          score: 86,
          dimensions: {},
          issues: [],
          suggestions: [],
          passed: true,
          iteration: 1,
          evidence_refs: [],
          critic_prompt_id: "main-agent.critic",
          critic_prompt_version: "v1",
          critic_prompt_hash: "critic-hash",
          critic_model: "deepseek-v4",
        },
      ],
      llm_calls: [
        {
          id: 51,
          invocation_id: 90,
          trace_id: "trace-51",
          agent_code: "01-positioning",
          prompt_id: "expert.positioning",
          prompt_version: "v3",
          prompt_hash: "positioning-hash",
          prompt_schema_version: "1",
          provider: "deepseek",
          model: "deepseek-v4",
          prompt_tokens: 120,
          completion_tokens: 80,
          total_tokens: 200,
          cost_usd: 0.012,
          latency_ms: 850,
          status: "failed",
          error: "上游超时",
          created_at: "2026-07-27T08:00:00Z",
        },
        {
          id: 52,
          invocation_id: null,
          trace_id: "trace-52",
          agent_code: "00-decision",
          prompt_id: "main-agent.acknowledgement",
          prompt_version: "1.0.0",
          prompt_hash: "acknowledgement-hash",
          prompt_schema_version: null,
          provider: "deepseek",
          model: "deepseek-v4-pro",
          prompt_tokens: 42,
          completion_tokens: 20,
          total_tokens: 62,
          cost_usd: 0,
          latency_ms: 4123,
          status: "ok",
          error: null,
          created_at: "2026-07-27T08:01:00Z",
        },
      ],
    });
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: { 3: 12 } }),
    );

    renderBrainHome();
    await waitFor(() => expect(getBrainTaskRuntime).toHaveBeenCalledWith(12));
    const detailsButton = document.querySelector<HTMLButtonElement>(
      ".tz-brain-toolbar-actions button:last-child",
    );
    expect(detailsButton).not.toBeNull();
    fireEvent.click(detailsButton!);

    expect(await screen.findByText("模型调用审计")).toBeInTheDocument();
    expect(screen.getByText("main-agent.strategy-planning · v1")).toBeInTheDocument();
    expect(screen.getByText("main-agent.critic · v1")).toBeInTheDocument();
    expect(screen.getByText("expert.positioning · v3")).toBeInTheDocument();
    expect(screen.getByText("200 Token")).toBeInTheDocument();
    expect(screen.getByText("$0.0120")).toBeInTheDocument();
    expect(screen.getByText("上游超时")).toBeInTheDocument();
    expect(screen.getByText("成功")).toBeInTheDocument();

    cleanup();
    useAuth.setState({
      token: "member-token",
      user: {
        id: 2,
        email: "member@tzxai.top",
        display_name: "运营成员",
        role: "user",
        is_active: true,
      },
    });
    vi.mocked(getBrainTaskRuntime).mockResolvedValueOnce({
      ...mocks.runtime,
      llm_calls: [],
    });
    renderBrainHome();
    await waitFor(() => expect(getBrainTaskRuntime).toHaveBeenCalledTimes(2));
    const memberDetailsButton = document.querySelector<HTMLButtonElement>(
      ".tz-brain-toolbar-actions button:last-child",
    );
    expect(memberDetailsButton).not.toBeNull();
    fireEvent.click(memberDetailsButton!);
    expect(screen.queryByText("模型调用审计")).not.toBeInTheDocument();

    useAuth.setState({ token: null, user: null });
  });
});

function renderBrainHome() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <AntApp>
          <BrainHome />
        </AntApp>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}
