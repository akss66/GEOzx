// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App as AntApp } from "antd";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  approveToolCall,
  createConversation,
  getBrainTaskRuntime,
  getConversation,
  listBrainTasks,
  listComposerSkills,
  sendConversationTurn,
  stopBrainGeneration,
} from "../api/brain";
import {
  deleteConversationAttachment,
  uploadConversationAttachments,
} from "../api/attachments";
import type {
  Account,
  ConversationAgentRun,
  ConversationThread,
  ConversationTurn,
  PublicSkill,
  TurnSubmission,
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
  const workspace = {
    clientId: 1 as number | null,
    projectId: 2 as number | null,
    platform: "douyin" as const,
    accountId: 3 as number | null,
    setAccountId: vi.fn(),
  };
  const event = {
    handler: null as ((event: {
      id?: number;
      type: string;
      payload?: unknown;
    }) => void) | null,
    options: null as { onReconnect?: () => void } | null,
  };
  return { account, workspace, event };
});

vi.mock("../api/shell", () => ({
  getWorkspaceContext: vi.fn(async () => ({
    clients: [],
    selected_client: null,
    projects: [],
    selected_project: null,
    accounts: [mocks.account],
  })),
}));

vi.mock("../api/brain", () => ({
  acceptArtifact: vi.fn(async (artifactId: number) => artifactId),
  approveToolCall: vi.fn(async () => undefined),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(async () => undefined),
  getArtifact: vi.fn(async () => {
    throw new Error("No Artifact expected in this page-level suite");
  }),
  getBrainTaskRuntime: vi.fn(),
  getConversation: vi.fn(),
  listArtifacts: vi.fn(async () => ({
    data: [],
    pagination: { page: 1, page_size: 20, total: 0, pages: 0 },
  })),
  listBrainTasks: vi.fn(),
  listComposerSkills: vi.fn(async () => []),
  listConversations: vi.fn(async () => [{
    id: 81,
    account_id: 3,
    title: "账号运营周会",
    turn_count: 2,
    last_message: "复盘最近一周",
    created_at: "2026-07-28T00:00:00Z",
    updated_at: "2026-07-28T00:02:00Z",
  }]),
  reviseArtifact: vi.fn(async () => {
    throw new Error("No revision expected in this page-level suite");
  }),
  sendConversationTurn: vi.fn(),
  stopBrainGeneration: vi.fn(async () => ({
    client_message_id: "stopped",
    stop_requested: true,
  })),
}));

vi.mock("../api/attachments", () => ({
  deleteConversationAttachment: vi.fn(async () => undefined),
  uploadConversationAttachments: vi.fn(),
}));

vi.mock("../hooks/useEventStream", () => ({
  useEventStream: vi.fn((handler, options) => {
    mocks.event.handler = handler;
    mocks.event.options = options;
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

describe("BrainHome V3 conversation projection", () => {
  beforeEach(() => {
    cleanup();
    vi.clearAllMocks();
    localStorage.clear();
    mocks.workspace.accountId = 3;
    mocks.account.auth_status = "manual";
    mocks.event.handler = null;
    mocks.event.options = null;
    vi.mocked(listComposerSkills).mockResolvedValue([]);
    vi.mocked(createConversation).mockResolvedValue(thread(82, []));
    vi.mocked(getConversation).mockImplementation(async (threadId) => thread(threadId, []));
    vi.mocked(sendConversationTurn).mockResolvedValue(
      submission(persistedTurn(201, "default-client", "默认消息", "默认回复")),
    );
    vi.mocked(uploadConversationAttachments).mockResolvedValue([{
      id: 91,
      account_id: 3,
      thread_id: 82,
      filename: "context.txt",
      mime_type: "text/plain",
      size_bytes: 7,
      sha256: "sha256",
      scan_status: "clean",
      parse_status: "ready",
      parsed_context: { text: "context" },
      created_at: "2026-08-03T00:00:00Z",
    }]);
  });
  afterEach(cleanup);

  it("keeps history and result-center entry points on the empty account workspace", async () => {
    renderBrainHome();

    expect(await screen.findByRole("heading", { name: "今天，想推进什么？" }))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /历史会话/ }));
    expect(await screen.findByText("账号运营周会")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: "成果视图" }));
    expect(await screen.findByRole("region", { name: "成果中心" })).toBeInTheDocument();
  });

  it("uses the public Skill catalog and creates one account-scoped Turn", async () => {
    vi.mocked(listComposerSkills).mockResolvedValue([inspectionSkill()]);

    renderBrainHome();

    fireEvent.click(await screen.findByRole("button", { name: "添加能力或材料" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /一键账号体检/ }));

    await waitFor(() => expect(createConversation).toHaveBeenCalledWith({ account_id: 3 }));
    expect(sendConversationTurn).toHaveBeenCalledWith(82, expect.objectContaining({
      message: "一键账号体检",
      requested_skill_code: "account_inspection",
      execution_preference: "AUTO",
      attachment_ids: [],
    }));
    expect(localStorage.getItem("tongzhouxing_brain_active_conversation_threads"))
      .toContain('"3":82');
  });

  it("uploads composer files and binds their immutable ids to the submitted Turn", async () => {
    saveThread(3, 82);
    renderBrainHome();
    await waitFor(() => expect(getConversation).toHaveBeenCalledWith(82));

    const file = new File(["context"], "context.txt", { type: "text/plain" });
    fireEvent.change(await screen.findByLabelText("选择对话附件"), {
      target: { files: [file] },
    });
    await waitFor(() => expect(uploadConversationAttachments).toHaveBeenCalledWith(82, [file]));
    expect(await screen.findByText("context.txt")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("运营大脑消息"), {
      target: { value: "根据附件诊断账号" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));

    await waitFor(() => expect(sendConversationTurn).toHaveBeenCalledWith(
      82,
      expect.objectContaining({ attachment_ids: [91] }),
    ));
    await waitFor(() => expect(screen.queryByText("context.txt")).not.toBeInTheDocument());
    expect(deleteConversationAttachment).not.toHaveBeenCalled();
  });

  it("renders a new-thread optimistic request immediately and never fetches an empty copy on mount", async () => {
    const request = deferred<TurnSubmission>();
    vi.mocked(sendConversationTurn).mockReturnValue(request.promise);
    vi.mocked(getConversation).mockRejectedValue(
      new Error("newly-created Thread must use its seeded query cache"),
    );

    renderBrainHome();
    fireEvent.change(await screen.findByLabelText("运营大脑消息"), {
      target: { value: "分析这个账号" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));

    const turnStream = await screen.findByLabelText("Conversation turns");
    const optimistic = within(turnStream).getByText("分析这个账号");
    const article = optimistic.closest("article");
    expect(article).toHaveAttribute("data-turn-status", "queued");
    expect(within(article as HTMLElement).getByLabelText("Assistant response"))
      .toHaveAttribute("aria-busy", "true");
    expect(getConversation).not.toHaveBeenCalled();
  });

  it("restores the composer when the typed runtime rollout gate is closed", async () => {
    vi.mocked(sendConversationTurn).mockRejectedValue({
      response: { status: 503, headers: {} },
    });

    renderBrainHome();
    const composer = await screen.findByRole("textbox");
    fireEvent.change(composer, { target: { value: "继续分析账号" } });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => expect(composer).toHaveValue("继续分析账号"));
    expect(screen.queryAllByRole("article")).toHaveLength(0);
  });

  it("binds the HTTP Turn to the optimistic client identity without a duplicate user message", async () => {
    const request = deferred<TurnSubmission>();
    vi.mocked(sendConversationTurn).mockReturnValue(request.promise);
    vi.mocked(getConversation).mockImplementation(async () => thread(82, []));

    renderBrainHome();
    fireEvent.change(await screen.findByLabelText("运营大脑消息"), {
      target: { value: "诊断内容方向" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    await screen.findByText("诊断内容方向");
    const submittedInput = vi.mocked(sendConversationTurn).mock.calls[0][1];
    const serverTurn = persistedTurn(
      301,
      submittedInput.client_message_id,
      "诊断内容方向",
      "已完成诊断",
    );
    vi.mocked(getConversation).mockResolvedValue(thread(82, [serverTurn]));

    await act(async () => request.resolve(submission(serverTurn)));

    await waitFor(() => expect(screen.getByText("已完成诊断")).toBeInTheDocument());
    expect(screen.getAllByText("诊断内容方向")).toHaveLength(1);
    expect(screen.getAllByRole("article")).toHaveLength(1);
    expect(screen.getByRole("article")).toHaveAttribute("data-turn-id", "301");
  });

  it("projects SSE deltas into that same Turn and ignores another Thread", async () => {
    const request = deferred<TurnSubmission>();
    vi.mocked(sendConversationTurn).mockReturnValue(request.promise);

    renderBrainHome();
    fireEvent.change(await screen.findByLabelText("运营大脑消息"), {
      target: { value: "给我建议" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    const optimistic = await screen.findByText("给我建议");
    const article = optimistic.closest("article");
    const clientMessageId = vi.mocked(sendConversationTurn).mock.calls[0][1].client_message_id;

    act(() => {
      mocks.event.handler?.({
        id: 91,
        type: "brain.runtime.message_start",
        payload: runtimePayload(99, 401, clientMessageId, 0),
      });
      mocks.event.handler?.({
        id: 91,
        type: "brain.runtime.message_delta",
        payload: { ...runtimePayload(99, 401, clientMessageId, 1), delta: "污染" },
      });
    });
    expect(article).toHaveAttribute("data-turn-status", "queued");

    await act(async () => {
      mocks.event.handler?.({
        id: 92,
        type: "brain.runtime.message_start",
        payload: runtimePayload(82, 401, clientMessageId, 0),
      });
    });
    await waitFor(() => {
      expect(article).toHaveAttribute("data-turn-id", "401");
      expect(article).toHaveAttribute("data-turn-status", "running");
    });
    await act(async () => {
      mocks.event.handler?.({
        id: 92,
        type: "brain.runtime.message_delta",
        payload: { ...runtimePayload(82, 401, clientMessageId, 1), delta: "正在分析" },
      });
    });

    await waitFor(() =>
      expect(within(article as HTMLElement).getByText("正在分析")).toBeInTheDocument()
    );
    expect(screen.queryByText("污染")).not.toBeInTheDocument();
    expect(screen.getAllByRole("article")).toHaveLength(1);
  });

  it("replaces an incomplete live overlay with the durable Thread after reconnect", async () => {
    const queued = persistedTurn(501, "reconnect-client", "复盘一下", null, "running");
    saveThread(3, 81);
    vi.mocked(getConversation).mockResolvedValue(thread(81, [queued]));

    renderBrainHome();
    await screen.findByText("复盘一下");
    act(() => {
      mocks.event.handler?.({
        id: 100,
        type: "brain.runtime.message_start",
        payload: runtimePayload(81, 501, "reconnect-client", 0),
      });
      mocks.event.handler?.({
        id: 100,
        type: "brain.runtime.message_delta",
        payload: {
          ...runtimePayload(81, 501, "reconnect-client", 1),
          delta: "临时流式内容",
        },
      });
    });
    expect(await screen.findByText("临时流式内容")).toBeInTheDocument();

    const durable = persistedTurn(
      501,
      "reconnect-client",
      "复盘一下",
      "服务端最终复盘",
    );
    vi.mocked(getConversation).mockResolvedValue(thread(81, [durable]));
    await act(async () => mocks.event.options?.onReconnect?.());

    expect(await screen.findByText("服务端最终复盘")).toBeInTheDocument();
    expect(screen.queryByText("临时流式内容")).not.toBeInTheDocument();
  });

  it("keeps the stop action wired to the active optimistic Turn", async () => {
    const request = deferred<TurnSubmission>();
    vi.mocked(sendConversationTurn).mockReturnValue(request.promise);

    renderBrainHome();
    fireEvent.change(await screen.findByLabelText("运营大脑消息"), {
      target: { value: "生成一份长报告" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    await screen.findByText("生成一份长报告");
    const clientMessageId = vi.mocked(sendConversationTurn).mock.calls[0][1].client_message_id;

    fireEvent.click(screen.getByRole("button", { name: "停止生成" }));

    await waitFor(() => expect(vi.mocked(stopBrainGeneration).mock.calls[0]?.[0]).toEqual({
      clientMessageId,
      taskId: null,
    }));
  });

  it("restores a waiting approval and its controls from durable conversation history", async () => {
    const approval = pendingApproval(901);
    const waiting = {
      ...persistedTurn(
        501,
        "approval-client",
        "准备发布",
        "发布前需要确认",
        "waiting_permission",
      ),
      projections: [{
        type: "approval" as const,
        turn_id: 501,
        approval,
      }],
    };
    saveThread(3, 81);
    vi.mocked(getConversation).mockResolvedValue(thread(81, [waiting]));

    renderBrainHome();

    expect(await screen.findByLabelText("Approval required")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "允许" }));
    await waitFor(() => expect(vi.mocked(approveToolCall).mock.calls[0]?.[0]).toEqual({
      toolCallId: 901,
      approved: true,
      comment: undefined,
    }));
  });

  it("uses a durable active Turn to disable input and stop after reload", async () => {
    saveThread(3, 81);
    vi.mocked(getConversation).mockResolvedValue(thread(81, [
      persistedTurn(501, "durable-running", "生成长报告", null, "running"),
    ]));

    renderBrainHome();

    expect(await screen.findByText("生成长报告")).toBeInTheDocument();
    expect(screen.getByLabelText("运营大脑消息")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "停止生成" }));
    await waitFor(() => expect(vi.mocked(stopBrainGeneration).mock.calls[0]?.[0]).toEqual({
      clientMessageId: "durable-running",
      taskId: null,
    }));
  });

  it("never restores the removed legacy Task runtime even when stale local storage exists", async () => {
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: { 3: 999 } }),
    );

    renderBrainHome();

    expect(await screen.findByText("今天，想推进什么？")).toBeInTheDocument();
    expect(listBrainTasks).not.toHaveBeenCalled();
    expect(getBrainTaskRuntime).not.toHaveBeenCalled();
    expect(screen.queryByText(/Task 999|执行详情/)).not.toBeInTheDocument();
  });

  it("fails closed and clears a saved Thread owned by another account", async () => {
    saveThread(3, 81);
    vi.mocked(getConversation).mockResolvedValue(thread(81, [], 4));

    renderBrainHome();

    await waitFor(() => {
      expect(localStorage.getItem("tongzhouxing_brain_active_conversation_threads"))
        .not.toContain('"3":81');
    });
    expect(screen.queryByLabelText("Conversation turns")).not.toBeInTheDocument();
    expect(await screen.findByText("今天，想推进什么？")).toBeInTheDocument();
  });
});

function renderBrainHome() {
  window.matchMedia ??= vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AntApp>
          <BrainHome />
        </AntApp>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function thread(
  id: number,
  turns: ConversationTurn[],
  accountId = 3,
): ConversationThread {
  return {
    id,
    org_id: 1,
    created_by_id: 2,
    client_id: null,
    project_id: 2,
    account_id: accountId,
    title: "账号运营对话",
    turns,
    created_at: "2026-07-28T00:00:00Z",
    updated_at: "2026-07-28T00:02:00Z",
  };
}

function persistedTurn(
  id: number,
  clientMessageId: string,
  userInput: string,
  assistantResponse: string | null,
  status = "completed",
): ConversationTurn {
  return {
    id,
    thread_id: id === 201 || id === 301 || id === 401 ? 82 : 81,
    org_id: 1,
    created_by_id: 2,
    client_message_id: clientMessageId,
    user_input: userInput,
    assistant_response: assistantResponse,
    intent: { mode: "ANSWER", route_source: "deterministic", skill_code: null },
    status,
    projections: [],
    created_at: "2026-07-28T00:00:00Z",
    updated_at: "2026-07-28T00:00:01Z",
  };
}

function submission(turn: ConversationTurn): TurnSubmission {
  const run: ConversationAgentRun = {
    id: 800,
    org_id: 1,
    requested_by_id: 2,
    task_id: null,
    thread_id: turn.thread_id,
    turn_id: turn.id,
    client_message_id: turn.client_message_id ?? "missing",
    status: turn.status,
    phase: turn.status === "completed" ? "complete" : "execute",
    created_at: "2026-07-28T00:00:00Z",
    updated_at: "2026-07-28T00:00:01Z",
  };
  return { turn, run, task_id: null, projections: [] };
}

function inspectionSkill(): PublicSkill {
  return {
    code: "account_inspection",
    version: 1,
    name: "一键账号体检",
    description: "检查账号当前状态",
    category: "quick_operations",
    icon: "inspection",
    requires_account: true,
    is_available: true,
    unavailable_reason: null,
  };
}

function pendingApproval(id: number) {
  return {
    id,
    org_id: 1,
    task_id: 700,
    invocation_id: null,
    module: "brain",
    agent_code: "00-decision",
    tool_code: "publish_package_prepare",
    tool_name: "生成发布包并进入人工审批",
    status: "waiting_approval" as const,
    permission_mode: "confirm",
    requires_human_confirmation: true,
    input_summary: "准备发布内容",
    output_summary: "确认后生成发布包",
    error: null,
    latency_ms: null,
    cost: 0,
    meta: {},
    started_at: null,
    finished_at: null,
    created_at: "2026-07-28T00:00:00Z",
    updated_at: "2026-07-28T00:00:01Z",
  };
}

function runtimePayload(
  threadId: number,
  turnId: number,
  clientMessageId: string,
  sequence: number,
) {
  return {
    thread_id: threadId,
    turn_id: turnId,
    client_message_id: clientMessageId,
    message_id: `${clientMessageId}:00-decision:1`,
    agent_code: "00-decision",
    stream_seq: sequence,
  };
}

function saveThread(accountId: number, threadId: number) {
  localStorage.setItem(
    "tongzhouxing_brain_active_conversation_threads",
    JSON.stringify({ version: 1, accounts: { [accountId]: threadId } }),
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}
