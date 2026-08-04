// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App as AntApp } from "antd";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  approveToolCall,
  createConversation,
  executeDeliverableAction,
  getArtifact,
  getBrainTaskRuntime,
  getConversation,
  listBrainTasks,
  listComposerSkills,
  listConversations,
  sendConversationTurn,
  stopBrainGeneration,
} from "../api/brain";
import {
  deleteConversationAttachment,
  uploadConversationAttachments,
} from "../api/attachments";
import type {
  Account,
  Artifact,
  ConversationAgentRun,
  ConversationThread,
  ConversationTurn,
  DeliverableActionExecution,
  Platform,
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
  const secondaryAccount: Account = {
    ...account,
    id: 4,
    nickname: "切换后的账号",
    external_account_id: "secondary",
  };
  const workspace = {
    clientId: 1 as number | null,
    projectId: 2 as number | null,
    platform: "douyin" as Platform,
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
  const turnEvents = {
    handler: null as ((event: import("../types").ConversationTurnEvent) => void) | null,
    onRecover: null as (() => void) | null,
  };
  return { account, secondaryAccount, workspace, event, turnEvents };
});

vi.mock("../api/shell", () => ({
  getWorkspaceContext: vi.fn(async () => ({
    clients: [],
    selected_client: null,
    projects: [],
    selected_project: null,
    accounts: [mocks.account, mocks.secondaryAccount],
  })),
}));

vi.mock("../api/brain", () => ({
  approveToolCall: vi.fn(async () => undefined),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(async () => undefined),
  executeDeliverableAction: vi.fn(async () => {
    throw new Error("No deliverable action expected in this page-level suite");
  }),
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
  useConversationRuntimeStream: vi.fn(({ onEvent }) => {
    mocks.event.handler = onEvent;
    return { connected: true, connectionState: "connected" };
  }),
}));

vi.mock("../hooks/useConversationTurnEvents", () => ({
  useConversationTurnEvents: vi.fn((options) => {
    mocks.turnEvents.handler = options.onEvent;
    mocks.turnEvents.onRecover = options.onRecover;
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
    mocks.workspace.platform = "douyin";
    mocks.account.platform = "douyin";
    mocks.account.auth_status = "manual";
    mocks.event.handler = null;
    mocks.event.options = null;
    mocks.turnEvents.handler = null;
    mocks.turnEvents.onRecover = null;
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

  it("keeps history and plans-and-content entry points on the empty account workspace", async () => {
    renderBrainHome();

    expect(await screen.findByRole("heading", { name: "今天，想推进什么？" }))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /历史会话/ }));
    expect(await screen.findByText("账号运营周会")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("tab", { name: "方案与内容" }));
    expect(await screen.findByRole("region", { name: "方案与内容" })).toBeInTheDocument();
  });

  it("exposes four real top-level workspace entries and navigates to account data and pending approvals", async () => {
    renderBrainHome();

    await screen.findByRole("heading", { name: "今天，想推进什么？" });
    fireEvent.click(screen.getByRole("button", { name: /历史会话/ }));
    await screen.findByText("账号运营周会");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    const conversation = await screen.findByRole("tab", { name: "对话" });
    expect(conversation).toHaveAttribute("aria-selected", "true");
    expect(conversation).toHaveAttribute("aria-controls", "brain-conversation-panel");
    const plans = screen.getByRole("tab", { name: "方案与内容" });
    fireEvent.click(plans);
    await waitFor(() => expect(screen.getByRole("tab", { name: "方案与内容" })).toHaveAttribute("aria-selected", "true"));
    expect(screen.getByRole("tabpanel", { name: "方案与内容" })).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("tab", { name: "方案与内容" }), { key: "ArrowLeft" });
    await waitFor(() => expect(screen.getByRole("tab", { name: "对话" })).toHaveAttribute("aria-selected", "true"));
    fireEvent.click(screen.getByRole("button", { name: "抖音数据" }));
    expect(await screen.findByTestId("location")).toHaveTextContent("/accounts/3/data");
    fireEvent.click(screen.getByRole("button", { name: "待处理" }));
    expect(await screen.findByTestId("location")).toHaveTextContent("/approvals");
  });

  it("keeps a preloaded next-account plans cache while removing the prior account cache", async () => {
    const view = renderBrainHome();
    await screen.findByRole("heading", { name: "今天，想推进什么？" });
    view.queryClient.setQueryData(["account-artifacts", 3], "account-a");
    view.queryClient.setQueryData(["account-artifacts", 4], "account-b");

    mocks.workspace.accountId = 4;
    fireEvent.click(screen.getByRole("tab", { name: "方案与内容" }));

    await waitFor(() => expect(view.queryClient.getQueryData(["account-artifacts", 3])).toBeUndefined());
    expect(view.queryClient.getQueryData(["account-artifacts", 4])).toBe("account-b");
  });

  it("executes the server-advertised next action and retains safe failure feedback", async () => {
    const source = presentationArtifact();
    const turn = {
      ...persistedTurn(501, "artifact-client", "检查账号", "已完成账号诊断"),
      projections: [{
        type: "artifact" as const,
        turn_id: 501,
        artifact_id: source.id,
        artifact_type: source.artifact_type,
        skill_run_id: source.skill_run_id!,
        account_id: source.account_id,
      }],
    };
    saveThread(3, 81);
    vi.mocked(getConversation).mockResolvedValue(thread(81, [turn]));
    vi.mocked(getArtifact).mockResolvedValue(source);
    vi.mocked(executeDeliverableAction).mockResolvedValue(actionExecution(source, {
      actionCode: "generate_next_iteration",
      status: "queued",
      resource: { type: "conversation_turn", id: 777 },
    }));

    renderBrainHome();

    fireEvent.click(await screen.findByRole("button", { name: "生成下一轮优化方案" }));
    await waitFor(() => expect(executeDeliverableAction).toHaveBeenCalledWith(expect.objectContaining({
      artifactId: source.id,
      actionCode: "generate_next_iteration",
      idempotencyKey: expect.stringContaining(`artifact-${source.id}-generate_next_iteration-`),
      input: {},
    })));
    expect(await screen.findByText("生成下一轮优化方案已进入执行队列")).toBeInTheDocument();

    vi.mocked(executeDeliverableAction).mockRejectedValueOnce(new Error("network down"));
    await waitFor(() => expect(
      screen.getByRole("button", { name: /生成下一轮优化方案/ }),
    ).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: /生成下一轮优化方案/ }));
    expect(await screen.findByText("网络连接中断，请检查连接后重试。")).toBeInTheDocument();
  });

  it("silently discards a delayed artifact action from the previous account", async () => {
    const source = presentationArtifact();
    const request = deferred<DeliverableActionExecution>();
    const turn = {
      ...persistedTurn(501, "artifact-client", "检查账号", "已完成账号诊断"),
      projections: [{
        type: "artifact" as const,
        turn_id: 501,
        artifact_id: source.id,
        artifact_type: source.artifact_type,
        skill_run_id: source.skill_run_id!,
        account_id: source.account_id,
      }],
    };
    saveThread(3, 81);
    vi.mocked(getConversation).mockResolvedValue(thread(81, [turn]));
    vi.mocked(getArtifact).mockResolvedValue(source);
    vi.mocked(executeDeliverableAction).mockReturnValue(request.promise);

    renderBrainHome();

    fireEvent.click(await screen.findByRole("button", { name: "生成下一轮优化方案" }));
    await waitFor(() => expect(executeDeliverableAction).toHaveBeenCalled());

    mocks.workspace.accountId = 4;
    fireEvent.click(screen.getByRole("tab", { name: "方案与内容" }));
    fireEvent.click(screen.getByRole("tab", { name: "对话" }));
    await waitFor(() => expect(screen.getByLabelText("运营大脑消息")).toHaveValue(""));

    await act(async () => request.resolve(actionExecution(source, {
      actionCode: "generate_next_iteration",
      status: "queued",
      resource: { type: "conversation_turn", id: 778 },
    })));

    expect(screen.getByLabelText("运营大脑消息")).toHaveValue("");
    expect(screen.queryByText("生成下一轮优化方案已进入执行队列")).not.toBeInTheDocument();
  });

  it("keeps a video artifact presentation format when requesting a revision", async () => {
    const source: Artifact = {
      ...presentationArtifact(),
      artifact_type: "video_script",
      presentation_format: "product_video",
    };
    const turn = {
      ...persistedTurn(501, "artifact-client", "检查账号", "已完成账号诊断"),
      projections: [{
        type: "artifact" as const,
        turn_id: 501,
        artifact_id: source.id,
        artifact_type: source.artifact_type,
        skill_run_id: source.skill_run_id!,
        account_id: source.account_id,
      }],
    };
    saveThread(3, 81);
    vi.mocked(getConversation).mockResolvedValue(thread(81, [turn]));
    const revision = {
      ...source,
      id: 5002,
      version: 2,
    };
    vi.mocked(getArtifact)
      .mockResolvedValueOnce(source)
      .mockResolvedValueOnce(revision);
    vi.mocked(executeDeliverableAction).mockResolvedValue(actionExecution(source, {
      actionCode: "request_revision",
      status: "succeeded",
      resource: { type: "artifact", id: revision.id },
    }));

    renderBrainHome();

    fireEvent.click(await screen.findByRole("button", { name: "提出修改" }));
    fireEvent.change(screen.getByRole("textbox", { name: "修改说明" }), {
      target: { value: "补充产品卖点镜头" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交修改" }));

    await waitFor(() => expect(executeDeliverableAction).toHaveBeenCalledWith(expect.objectContaining({
      artifactId: source.id,
      actionCode: "request_revision",
      input: expect.objectContaining({
        payload: expect.objectContaining({ presentation_format: "product_video" }),
        note: "补充产品卖点镜头",
      }),
    })));
  });

  it("offers a direct return to the latest message after the reader scrolls away", async () => {
    saveThread(3, 81);
    vi.mocked(getConversation).mockResolvedValue(thread(81, [
      persistedTurn(501, "scroll-client", "复盘一下", "这是最新回复"),
    ]));
    renderBrainHome();

    const conversation = await screen.findByRole("tabpanel", { name: "对话" });
    const scrollTo = vi.fn();
    Object.defineProperties(conversation, {
      clientHeight: { configurable: true, value: 400 },
      scrollHeight: { configurable: true, value: 1200 },
      scrollTop: { configurable: true, writable: true, value: 200 },
      scrollTo: { configurable: true, value: scrollTo },
    });

    fireEvent.scroll(conversation);
    const jumpButton = await screen.findByRole("button", { name: "回到最新消息" });
    fireEvent.click(jumpButton);

    expect(scrollTo).toHaveBeenLastCalledWith({ top: 1200, behavior: "smooth" });
    expect(screen.queryByRole("button", { name: "回到最新消息" })).not.toBeInTheDocument();
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

  it("loads the composer catalog for the selected account platform", async () => {
    mocks.workspace.platform = "xiaohongshu";
    mocks.account.platform = "xiaohongshu";

    renderBrainHome();

    await waitFor(() => expect(listComposerSkills).toHaveBeenCalledWith("xiaohongshu", 3));
    expect(listComposerSkills).not.toHaveBeenCalledWith("douyin", 3);
  });

  it("exposes operation Skills from the catalog and keeps blocked actions non-executable", async () => {
    vi.mocked(listComposerSkills).mockResolvedValue([
      operationSkill("topic_planning", "选题策划"),
      operationSkill("content_publishing", "内容发布", {
        availability: "needs_connection",
        reason: "请先连接抖音发布能力",
        is_available: false,
        unavailable_reason: "请先连接抖音发布能力",
      }),
    ]);

    renderBrainHome();

    await waitFor(() => expect(listComposerSkills).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole("button", { name: "添加能力或材料" }));
    expect(await screen.findByRole("menuitem", { name: /内容发布/ })).toBeDisabled();
    expect(await screen.findByText("请先连接抖音发布能力")).toBeVisible();
    fireEvent.click(await screen.findByRole("menuitem", { name: /选题策划/ }));

    await waitFor(() => expect(sendConversationTurn).toHaveBeenCalledWith(
      82,
      expect.objectContaining({
        message: "选题策划",
        requested_skill_code: "topic_planning",
      }),
    ));
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

  it("restores the input when creating the first conversation fails in the current scope", async () => {
    vi.mocked(createConversation).mockRejectedValueOnce(new Error("network"));

    renderBrainHome();
    const composer = await screen.findByLabelText("运营大脑消息");
    fireEvent.change(composer, { target: { value: "首次建会话失败仍要保留" } });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));

    await waitFor(() => expect(composer).toHaveValue("首次建会话失败仍要保留"));
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("drops a delayed new-thread creation after the user selects another Thread", async () => {
    const created = deferred<ConversationThread>();
    vi.mocked(createConversation).mockReturnValue(created.promise);
    vi.mocked(getConversation).mockImplementation(async (threadId) =>
      thread(threadId, threadId === 81
        ? [persistedTurn(501, "history-client", "历史请求", "历史回复")]
        : []),
    );
    const view = renderBrainHome();
    fireEvent.change(await screen.findByLabelText("运营大脑消息"), {
      target: { value: "旧创建请求不能拉回会话" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    await waitFor(() => expect(createConversation).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: /历史会话/ }));
    fireEvent.click(await screen.findByRole("button", { name: "账号运营周会" }));
    expect(await screen.findByText("历史回复")).toBeInTheDocument();
    const setQueryData = vi.spyOn(view.queryClient, "setQueryData");
    const setCalls = setQueryData.mock.calls.length;

    await act(async () => created.resolve(thread(82, [])));

    expect(sendConversationTurn).not.toHaveBeenCalled();
    expect(setQueryData).toHaveBeenCalledTimes(setCalls);
    expect(screen.getByText("历史回复")).toBeInTheDocument();
    expect(screen.getByLabelText("运营大脑消息")).toHaveValue("");
  });

  it("drops a delayed saved-thread load after the user selects another Thread", async () => {
    saveThread(3, 82);
    vi.mocked(getConversation).mockImplementation(async (threadId) => thread(threadId, []));
    const view = renderBrainHome();
    await waitFor(() => expect(getConversation).toHaveBeenCalledWith(82));
    const loaded = deferred<ConversationThread>();
    vi.mocked(getConversation).mockReturnValueOnce(loaded.promise);
    fireEvent.change(await screen.findByLabelText("运营大脑消息"), {
      target: { value: "旧加载请求不能拉回会话" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    await waitFor(() => expect(getConversation).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole("button", { name: /历史会话/ }));
    fireEvent.click(await screen.findByRole("button", { name: "账号运营周会" }));
    const setQueryData = vi.spyOn(view.queryClient, "setQueryData");
    const setCalls = setQueryData.mock.calls.length;

    await act(async () => loaded.resolve(thread(82, [])));

    expect(sendConversationTurn).not.toHaveBeenCalled();
    expect(setQueryData).toHaveBeenCalledTimes(setCalls);
    expect(screen.getByLabelText("运营大脑消息")).toHaveValue("");
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
    const optimisticMessage = await screen.findByText("诊断内容方向");
    const optimisticArticle = optimisticMessage.closest("article");
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
    expect(screen.getByRole("article")).toBe(optimisticArticle);
    expect(optimisticArticle).toHaveAttribute("data-turn-id", "301");
  });

  it("does not write cache or invalidate when a delayed success belongs to another active Thread", async () => {
    const request = deferred<TurnSubmission>();
    vi.mocked(sendConversationTurn).mockReturnValue(request.promise);
    vi.mocked(getConversation).mockImplementation(async (threadId) =>
      thread(threadId, threadId === 81 ? [persistedTurn(501, "history-client", "历史请求", "历史回复")] : []),
    );

    const view = renderBrainHome();
    fireEvent.change(await screen.findByLabelText("运营大脑消息"), {
      target: { value: "延迟成功不应写旧缓存" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    await waitFor(() => expect(sendConversationTurn).toHaveBeenCalled());
    const clientMessageId = vi.mocked(sendConversationTurn).mock.calls[0][1].client_message_id;

    fireEvent.click(screen.getByRole("button", { name: /历史会话/ }));
    fireEvent.click(await screen.findByRole("button", { name: "账号运营周会" }));
    expect(await screen.findByText("历史回复")).toBeInTheDocument();

    const setQueryData = vi.spyOn(view.queryClient, "setQueryData");
    const invalidateQueries = vi.spyOn(view.queryClient, "invalidateQueries");
    const setCalls = setQueryData.mock.calls.length;
    const invalidateCalls = invalidateQueries.mock.calls.length;
    await act(async () => request.resolve(submission(
      persistedTurn(301, clientMessageId, "延迟成功不应写旧缓存", "旧线程成功"),
    )));

    expect(setQueryData).toHaveBeenCalledTimes(setCalls);
    expect(invalidateQueries).toHaveBeenCalledTimes(invalidateCalls);
    expect(screen.queryByText("旧线程成功")).not.toBeInTheDocument();
  });

  it("does not write cache or invalidate when a delayed success belongs to another account", async () => {
    const request = deferred<TurnSubmission>();
    vi.mocked(sendConversationTurn).mockReturnValue(request.promise);

    const view = renderBrainHome();
    fireEvent.change(await screen.findByLabelText("运营大脑消息"), {
      target: { value: "旧账号响应不能污染新账号" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    await waitFor(() => expect(sendConversationTurn).toHaveBeenCalled());
    const clientMessageId = vi.mocked(sendConversationTurn).mock.calls[0][1].client_message_id;

    mocks.workspace.accountId = 4;
    fireEvent.click(screen.getByRole("tab", { name: "方案与内容" }));
    fireEvent.click(screen.getByRole("tab", { name: "对话" }));
    await waitFor(() => expect(screen.getByLabelText("运营大脑消息")).toHaveValue(""));

    const setQueryData = vi.spyOn(view.queryClient, "setQueryData");
    const invalidateQueries = vi.spyOn(view.queryClient, "invalidateQueries");
    const setCalls = setQueryData.mock.calls.length;
    const invalidateCalls = invalidateQueries.mock.calls.length;
    await act(async () => request.resolve(submission(
      persistedTurn(301, clientMessageId, "旧账号响应不能污染新账号", "旧账号成功"),
    )));

    expect(setQueryData).toHaveBeenCalledTimes(setCalls);
    expect(invalidateQueries).toHaveBeenCalledTimes(invalidateCalls);
    expect(screen.queryByText("旧账号成功")).not.toBeInTheDocument();
  });

  it("preserves streamed text when a delayed stale conversation response reconciles", async () => {
    const stale = deferred<ConversationThread>();
    const request = deferred<TurnSubmission>();
    vi.mocked(sendConversationTurn).mockReturnValue(request.promise);
    vi.mocked(getConversation).mockImplementation(async (threadId) => {
      if (threadId === 82) return stale.promise;
      return thread(threadId, []);
    });

    const view = renderBrainHome();
    fireEvent.change(await screen.findByLabelText("运营大脑消息"), {
      target: { value: "流式文本不能被陈旧读取覆盖" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    await waitFor(() => expect(sendConversationTurn).toHaveBeenCalled());
    const clientMessageId = vi.mocked(sendConversationTurn).mock.calls[0][1].client_message_id;
    const serverTurn = persistedTurn(401, clientMessageId, "流式文本不能被陈旧读取覆盖", null, "queued");
    await act(async () => request.resolve(submission(serverTurn)));
    void view.queryClient.invalidateQueries({ queryKey: ["brain-conversation", 82] });
    await waitFor(() => expect(getConversation).toHaveBeenCalledWith(82));

    act(() => {
      mocks.event.handler?.({
        id: 96,
        type: "brain.runtime.message_start",
        payload: runtimePayload(82, 401, clientMessageId, 0),
      });
      mocks.event.handler?.({
        id: 97,
        type: "brain.runtime.message_delta",
        payload: { ...runtimePayload(82, 401, clientMessageId, 1), delta: "先到的 token" },
      });
    });
    expect(await screen.findByText("先到的 token")).toBeInTheDocument();

    await act(async () => stale.resolve(thread(82, [serverTurn])));

    expect(await screen.findByText("先到的 token")).toBeInTheDocument();
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

  it("keeps a Skill phase in the existing work turn while answer deltas stream in", async () => {
    const request = deferred<TurnSubmission>();
    vi.mocked(sendConversationTurn).mockReturnValue(request.promise);

    renderBrainHome();
    fireEvent.change(await screen.findByLabelText("运营大脑消息"), {
      target: { value: "检查账号" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    const optimistic = await screen.findByText("检查账号");
    const article = optimistic.closest("article");
    const clientMessageId = vi.mocked(sendConversationTurn).mock.calls[0][1].client_message_id;

    await act(async () => {
      mocks.event.handler?.({
        id: 92,
        type: "brain.runtime.message_start",
        payload: runtimePayload(82, 401, clientMessageId, 0),
      });
    });
    await waitFor(() => expect(article).toHaveAttribute("data-turn-status", "running"));
    await act(async () => {
      mocks.event.handler?.({
        id: 93,
        type: "brain.runtime.subagent_started",
        payload: {
          ...runtimePayload(82, 401, clientMessageId, 1),
          agent_code: "01-positioning",
          turn_phase: "consulting_experts",
        },
      });
      mocks.event.handler?.({
        id: 94,
        type: "brain.runtime.message_delta",
        payload: { ...runtimePayload(82, 401, clientMessageId, 2), delta: "账号情况正常" },
      });
    });

    await waitFor(() => expect(within(article as HTMLElement).getByText("账号情况正常")).toBeInTheDocument());
    expect(screen.getByRole("article")).toBe(article);
    expect(within(article as HTMLElement).getByText("正在咨询专家")).toBeInTheDocument();
    expect(screen.queryByText("思考中")).not.toBeInTheDocument();
  });

  it("does not restore a failed request into the composer after selecting another Thread", async () => {
    const request = deferred<TurnSubmission>();
    vi.mocked(sendConversationTurn).mockReturnValue(request.promise);
    vi.mocked(getConversation).mockImplementation(async (threadId) =>
      thread(threadId, threadId === 81 ? [persistedTurn(501, "history-client", "历史请求", "历史回复")] : []),
    );

    renderBrainHome();
    fireEvent.change(await screen.findByLabelText("运营大脑消息"), {
      target: { value: "不应回填到新会话" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    await screen.findByText("不应回填到新会话");

    fireEvent.click(screen.getByRole("button", { name: /历史会话/ }));
    fireEvent.click(await screen.findByRole("button", { name: "账号运营周会" }));
    expect(await screen.findByText("历史回复")).toBeInTheDocument();

    await act(async () => request.reject(new Error("network")));

    await waitFor(() => expect(screen.getByLabelText("运营大脑消息")).toHaveValue(""));
    expect(screen.queryByText("不应回填到新会话")).not.toBeInTheDocument();
  });

  it("removes a failed optimistic Turn before returning to its Thread", async () => {
    const request = deferred<TurnSubmission>();
    vi.mocked(sendConversationTurn).mockReturnValue(request.promise);
    vi.mocked(listConversations).mockResolvedValue([
      {
        id: 81,
        account_id: 3,
        title: "账号运营周会",
        turn_count: 1,
        last_message: "历史回复",
        created_at: "2026-07-28T00:00:00Z",
        updated_at: "2026-07-28T00:02:00Z",
      },
      {
        id: 82,
        account_id: 3,
        title: "刚才的会话",
        turn_count: 1,
        last_message: "待发送",
        created_at: "2026-07-28T00:00:00Z",
        updated_at: "2026-07-28T00:02:00Z",
      },
    ]);
    vi.mocked(getConversation).mockImplementation(async (threadId) =>
      thread(threadId, threadId === 81 ? [persistedTurn(501, "history-client", "历史请求", "历史回复")] : []),
    );

    renderBrainHome();
    fireEvent.change(await screen.findByLabelText("运营大脑消息"), {
      target: { value: "失败后不能成为幽灵消息" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    await screen.findByText("失败后不能成为幽灵消息");

    fireEvent.click(screen.getByRole("button", { name: /历史会话/ }));
    fireEvent.click(await screen.findByRole("button", { name: "账号运营周会" }));
    await screen.findByText("历史回复");
    await act(async () => request.reject(new Error("network")));

    fireEvent.click(screen.getByRole("button", { name: /历史会话/ }));
    fireEvent.click(await screen.findByRole("button", { name: "刚才的会话" }));
    await waitFor(() => expect(screen.queryByText("失败后不能成为幽灵消息")).not.toBeInTheDocument());
  });

  it("does not let repeated current-thread failures leave optimistic Turns behind", async () => {
    saveThread(3, 82);
    vi.mocked(sendConversationTurn).mockRejectedValue(new Error("network"));
    renderBrainHome();
    const composer = await screen.findByLabelText("运营大脑消息");

    fireEvent.change(composer, { target: { value: "第一次失败" } });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    await waitFor(() => expect(composer).toHaveValue("第一次失败"));
    expect(screen.queryAllByRole("article")).toHaveLength(0);

    fireEvent.change(composer, { target: { value: "第二次失败" } });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    await waitFor(() => expect(composer).toHaveValue("第二次失败"));
    expect(screen.queryAllByRole("article")).toHaveLength(0);
  });

  it("does not remove a fresh retry when an old off-scope failure is reconciled", async () => {
    const first = deferred<TurnSubmission>();
    const retry = deferred<TurnSubmission>();
    vi.mocked(sendConversationTurn)
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(retry.promise);
    vi.mocked(getConversation).mockImplementation(async (threadId) =>
      thread(threadId, threadId === 81
        ? [persistedTurn(501, "history-client", "历史请求", "历史回复")]
        : []),
    );
    vi.mocked(listConversations).mockResolvedValue([
      {
        id: 81,
        account_id: 3,
        title: "账号运营周会",
        turn_count: 1,
        last_message: "历史回复",
        created_at: "2026-07-28T00:00:00Z",
        updated_at: "2026-07-28T00:02:00Z",
      },
      {
        id: 82,
        account_id: 3,
        title: "刚才的会话",
        turn_count: 0,
        last_message: "",
        created_at: "2026-07-28T00:00:00Z",
        updated_at: "2026-07-28T00:02:00Z",
      },
    ]);
    renderBrainHome();
    fireEvent.change(await screen.findByLabelText("运营大脑消息"), {
      target: { value: "旧失败请求" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    await screen.findByText("旧失败请求");

    fireEvent.click(screen.getByRole("button", { name: /历史会话/ }));
    fireEvent.click(await screen.findByRole("button", { name: "账号运营周会" }));
    await act(async () => first.reject(new Error("network")));

    fireEvent.click(screen.getByRole("button", { name: /历史会话/ }));
    fireEvent.click(await screen.findByRole("button", { name: "刚才的会话" }));
    await waitFor(() => expect(screen.queryByText("旧失败请求")).not.toBeInTheDocument());
    const composer = screen.getByLabelText("运营大脑消息");
    fireEvent.change(composer, { target: { value: "新的重试请求" } });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    expect(await screen.findByText("新的重试请求")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /历史会话/ }));
    fireEvent.click(await screen.findByRole("button", { name: "账号运营周会" }));
    fireEvent.click(screen.getByRole("button", { name: /历史会话/ }));
    fireEvent.click(await screen.findByRole("button", { name: "刚才的会话" }));
    expect(await screen.findByText("新的重试请求")).toBeInTheDocument();
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
    await act(async () => mocks.turnEvents.onRecover?.());

    expect(await screen.findByText("服务端最终复盘")).toBeInTheDocument();
    expect(screen.queryByText("临时流式内容")).not.toBeInTheDocument();
  });

  it("projects durable Turn progress in place without invalidating the conversation per event", async () => {
    const running = persistedTurn(501, "durable-client", "复盘", null, "running");
    saveThread(3, 81);
    vi.mocked(getConversation).mockResolvedValue(thread(81, [running]));
    const view = renderBrainHome();
    await screen.findByText("复盘");
    const invalidateQueries = vi.spyOn(view.queryClient, "invalidateQueries");
    const initialInvalidations = invalidateQueries.mock.calls.length;

    act(() => {
      mocks.turnEvents.handler?.(durableTurnEvent("turn.received", 1, 1, 81, 501));
      mocks.turnEvents.handler?.(durableTurnEvent("step.started", 2, 2, 81, 501, {
        step: "read_data",
      }));
      mocks.turnEvents.handler?.(durableTurnEvent("step.completed", 3, 3, 81, 501, {
        step: "read_data",
      }));
      mocks.turnEvents.handler?.(durableTurnEvent("deliverable.updated", 4, 4, 81, 501, {
        deliverable_id: 88,
      }));
      mocks.turnEvents.handler?.(durableTurnEvent("turn.completed", 5, 5, 81, 501, {
        status: "completed",
      }));
    });

    expect(view.queryClient.getQueryData<ConversationThread>(["brain-conversation", 81])?.turns[0])
      .toMatchObject({
        status: "completed",
        runtime_overlay: {
          deliverableIds: [88],
          steps: { read_data: { state: "done" } },
        },
      });
    expect(invalidateQueries).toHaveBeenCalledTimes(initialInvalidations);
  });

  it("coalesces concurrent durable sequence-gap recovery into one scoped conversation read", async () => {
    const running = persistedTurn(501, "gap-client", "复盘", null, "running");
    const recovery = deferred<ConversationThread>();
    saveThread(3, 81);
    vi.mocked(getConversation)
      .mockResolvedValueOnce(thread(81, [running]))
      .mockReturnValueOnce(recovery.promise);
    const view = renderBrainHome();
    await screen.findByText("复盘");
    const initialCalls = vi.mocked(getConversation).mock.calls.length;
    const invalidateQueries = vi.spyOn(view.queryClient, "invalidateQueries");
    const initialInvalidations = invalidateQueries.mock.calls.length;

    act(() => {
      mocks.turnEvents.onRecover?.();
      mocks.turnEvents.onRecover?.();
    });
    expect(getConversation).toHaveBeenCalledTimes(initialCalls + 1);
    await act(async () => recovery.resolve(thread(81, [{ ...running, status: "completed" }])));

    expect(view.queryClient.getQueryData<ConversationThread>(["brain-conversation", 81])?.turns[0].status)
      .toBe("completed");
    expect(invalidateQueries).toHaveBeenCalledTimes(initialInvalidations);
  });

  it("recovers a paused Turn exactly once from the scoped conversation snapshot", async () => {
    const running = persistedTurn(501, "paused-client", "Prepare publishing", null, "running");
    const approval = pendingApproval(901);
    const recovered = {
      ...persistedTurn(
        501,
        "paused-client",
        "Prepare publishing",
        "Please approve publishing before I continue.",
        "waiting_permission",
      ),
      projections: [{ type: "approval" as const, turn_id: 501, approval }],
    };
    saveThread(3, 81);
    vi.mocked(getConversation)
      .mockResolvedValueOnce(thread(81, [running]))
      .mockResolvedValueOnce(thread(81, [recovered]));
    const view = renderBrainHome();
    await screen.findByText("Prepare publishing");
    const initialCalls = vi.mocked(getConversation).mock.calls.length;

    act(() => {
      mocks.turnEvents.handler?.(durableTurnEvent("turn.paused", 2, 2, 81, 501, {
        status: "waiting_permission",
        message: "Please approve publishing before I continue.",
      }));
      mocks.turnEvents.handler?.(durableTurnEvent("turn.paused", 2, 2, 81, 501, {
        status: "waiting_permission",
        message: "Please approve publishing before I continue.",
      }));
    });

    await waitFor(() => expect(getConversation).toHaveBeenCalledTimes(initialCalls + 1));
    expect(view.queryClient.getQueryData<ConversationThread>(["brain-conversation", 81])?.turns[0])
      .toMatchObject({
        status: "waiting_permission",
        assistant_response: "Please approve publishing before I continue.",
      });
    await waitFor(() => expect(getConversation).toHaveBeenCalledTimes(initialCalls + 1));
  });

  it("recovers a missing deliverable projection once and does not refetch an existing card", async () => {
    const running = persistedTurn(501, "deliverable-client", "Inspect account", null, "running");
    const projection = {
      type: "artifact" as const,
      turn_id: 501,
      artifact_id: 88,
      artifact_type: "account_inspection_report",
      skill_run_id: 4,
      account_id: 3,
    };
    const recovered = { ...running, projections: [projection] };
    saveThread(3, 81);
    vi.mocked(getConversation)
      .mockResolvedValueOnce(thread(81, [running]))
      .mockResolvedValueOnce(thread(81, [recovered]));
    vi.mocked(getArtifact).mockResolvedValue({ ...presentationArtifact(), id: 88 });
    const view = renderBrainHome();
    await screen.findByText("Inspect account");
    const initialCalls = vi.mocked(getConversation).mock.calls.length;

    act(() => mocks.turnEvents.handler?.(durableTurnEvent("deliverable.updated", 2, 2, 81, 501, {
      deliverable_id: 88,
    })));
    await waitFor(() => expect(getConversation).toHaveBeenCalledTimes(initialCalls + 1));
    expect(await screen.findByTestId("projection-artifact-88")).toBeInTheDocument();
    expect(view.queryClient.getQueryData<ConversationThread>(["brain-conversation", 81])?.turns[0].projections)
      .toHaveLength(1);

    act(() => mocks.turnEvents.handler?.(durableTurnEvent("deliverable.updated", 3, 3, 81, 501, {
      deliverable_id: 88,
    })));
    expect(getConversation).toHaveBeenCalledTimes(initialCalls + 1);
  });

  it("discards an off-scope snapshot returned while recovering a queued durable pause", async () => {
    const running = persistedTurn(501, "scope-client", "Inspect account", null, "running");
    const foreign = persistedTurn(501, "foreign-client", "Foreign account", "Do not show", "completed");
    const initial = deferred<ConversationThread>();
    const recovery = deferred<ConversationThread>();
    saveThread(3, 81);
    vi.mocked(getConversation)
      .mockReturnValueOnce(initial.promise)
      .mockReturnValueOnce(recovery.promise);
    const view = renderBrainHome();
    await screen.findByText("Loading conversation…");

    act(() => mocks.turnEvents.handler?.(durableTurnEvent("turn.paused", 2, 2, 81, 501, {
      status: "waiting_user",
      message: "Please provide the missing goal.",
    })));

    await waitFor(() => expect(getConversation).toHaveBeenCalledTimes(2));
    await act(async () => initial.resolve(thread(81, [running])));
    await act(async () => recovery.resolve(thread(81, [foreign], 4)));
    expect(view.queryClient.getQueryData<ConversationThread>(["brain-conversation", 81]))
      .toMatchObject({ account_id: 3, turns: [{ user_input: "Inspect account" }] });
    expect(screen.queryByText("Foreign account")).not.toBeInTheDocument();
  });

  it("replays a durable event received before the initial conversation snapshot materializes", async () => {
    const snapshot = deferred<ConversationThread>();
    saveThread(3, 81);
    vi.mocked(getConversation).mockReturnValueOnce(snapshot.promise);
    const view = renderBrainHome();
    await screen.findByText("Loading conversation…");

    act(() => mocks.turnEvents.handler?.(durableTurnEvent("step.completed", 1, 1, 81, 501, {
      step: "read_data",
    })));
    await act(async () => snapshot.resolve(thread(81, [
      persistedTurn(501, "snapshot-client", "复盘", null, "running"),
    ])));

    expect(view.queryClient.getQueryData<ConversationThread>(["brain-conversation", 81])?.turns[0])
      .toMatchObject({ runtime_overlay: { steps: { read_data: { state: "done" } } } });
  });

  it("replays a queued steering notice onto its target card without changing the answer", async () => {
    const snapshot = deferred<ConversationThread>();
    saveThread(3, 81);
    vi.mocked(getConversation).mockReturnValueOnce(snapshot.promise);
    const view = renderBrainHome();
    await screen.findByText("Loading conversation…");

    act(() => mocks.turnEvents.handler?.(durableTurnEvent("turn.steered", 4, 4, 81, 501, {
      message: "第一条不要讲价格",
      reason: "用户补充了内容要求",
      metadata: { category: "steering", label: "supplement", source_id: 502 },
    })));
    await act(async () => snapshot.resolve(thread(81, [
      persistedTurn(501, "steering-client", "生成三条获客脚本", "正在处理原任务", "running"),
    ])));

    expect(view.queryClient.getQueryData<ConversationThread>(["brain-conversation", 81])?.turns[0])
      .toMatchObject({
        status: "running",
        assistant_response: "正在处理原任务",
        runtime_overlay: {
          steering_notice: { label: "supplement", source_turn_id: 502 },
        },
      });
    expect(await screen.findByText("已补充要求")).toBeInTheDocument();
    expect(screen.getByText("第一条不要讲价格")).toBeInTheDocument();
    expect(screen.getAllByText("第一条不要讲价格")).toHaveLength(1);
    expect(screen.getAllByTestId("work-turn")).toHaveLength(1);
  });

  it("ignores a steering event from another conversation scope", async () => {
    const running = persistedTurn(501, "scope-steering", "生成三条获客脚本", null, "running");
    saveThread(3, 81);
    vi.mocked(getConversation).mockResolvedValue(thread(81, [running]));
    const view = renderBrainHome();
    await screen.findByText("生成三条获客脚本");

    act(() => mocks.turnEvents.handler?.(durableTurnEvent("turn.steered", 5, 5, 999, 501, {
      metadata: { category: "steering", label: "stop", source_id: 502 },
    })));

    expect(view.queryClient.getQueryData<ConversationThread>(["brain-conversation", 81])?.turns[0].runtime_overlay)
      .toBeUndefined();
    expect(screen.queryByText("已请求停止")).not.toBeInTheDocument();
  });

  it("recovers queued paused and deliverable events into one scoped approval and artifact snapshot", async () => {
    const initial = deferred<ConversationThread>();
    const recovered = deferred<ConversationThread>();
    const approval = pendingApproval(901);
    const source = { ...presentationArtifact(), id: 88 };
    const snapshotTurn = persistedTurn(501, "queued-recovery", "Prepare publishing", null, "running");
    const recoveredTurn = {
      ...persistedTurn(
        501,
        "queued-recovery",
        "Prepare publishing",
        "Please approve publishing before I continue.",
        "waiting_permission",
      ),
      projections: [
        { type: "approval" as const, turn_id: 501, approval },
        {
          type: "artifact" as const,
          turn_id: 501,
          artifact_id: 88,
          artifact_type: source.artifact_type,
          skill_run_id: source.skill_run_id!,
          account_id: 3,
        },
      ],
    };
    saveThread(3, 81);
    vi.mocked(getConversation)
      .mockReturnValueOnce(initial.promise)
      .mockReturnValueOnce(recovered.promise);
    vi.mocked(getArtifact).mockResolvedValue(source);

    renderBrainHome();
    await screen.findByText("Loading conversation…");
    act(() => {
      mocks.turnEvents.handler?.(durableTurnEvent("turn.paused", 2, 2, 81, 501, {
        status: "waiting_permission",
        message: "Please approve publishing before I continue.",
      }));
      mocks.turnEvents.handler?.(durableTurnEvent("deliverable.updated", 3, 3, 81, 501, {
        deliverable_id: 88,
      }));
    });
    await waitFor(() => expect(getConversation).toHaveBeenCalledTimes(2));

    await act(async () => initial.resolve(thread(81, [snapshotTurn])));
    await act(async () => recovered.resolve(thread(81, [recoveredTurn])));

    expect(await screen.findByLabelText("Approval required")).toBeInTheDocument();
    expect(await screen.findByTestId("projection-artifact-88")).toBeInTheDocument();
    await waitFor(() => expect(getConversation).toHaveBeenCalledTimes(2));
  });

  it("uses one queued recovery follow-up only when the first snapshot still lacks its approval and artifact", async () => {
    const initial = deferred<ConversationThread>();
    const firstRecovery = deferred<ConversationThread>();
    const followUp = deferred<ConversationThread>();
    const approval = pendingApproval(901);
    const source = { ...presentationArtifact(), id: 88 };
    const snapshotTurn = persistedTurn(501, "queued-follow-up", "Prepare publishing", null, "running");
    const incompleteRecoveryTurn = {
      ...persistedTurn(
        501,
        "queued-follow-up",
        "Prepare publishing",
        "Please approve publishing before I continue.",
        "waiting_permission",
      ),
      projections: [],
    };
    const completedRecoveryTurn = {
      ...incompleteRecoveryTurn,
      projections: [
        { type: "approval" as const, turn_id: 501, approval },
        {
          type: "artifact" as const,
          turn_id: 501,
          artifact_id: 88,
          artifact_type: source.artifact_type,
          skill_run_id: source.skill_run_id!,
          account_id: 3,
        },
      ],
    };
    saveThread(3, 81);
    vi.mocked(getConversation)
      .mockReturnValueOnce(initial.promise)
      .mockReturnValueOnce(firstRecovery.promise)
      .mockReturnValueOnce(followUp.promise);
    vi.mocked(getArtifact).mockResolvedValue(source);

    renderBrainHome();
    await waitFor(() => expect(getConversation).toHaveBeenCalledTimes(1));
    act(() => {
      mocks.turnEvents.handler?.(durableTurnEvent("turn.paused", 2, 2, 81, 501, {
        status: "waiting_permission",
        message: "Please approve publishing before I continue.",
      }));
      mocks.turnEvents.handler?.(durableTurnEvent("deliverable.updated", 3, 3, 81, 501, {
        deliverable_id: 88,
      }));
    });
    await waitFor(() => expect(getConversation).toHaveBeenCalledTimes(2));

    await act(async () => initial.resolve(thread(81, [snapshotTurn])));
    await act(async () => firstRecovery.resolve(thread(81, [incompleteRecoveryTurn])));
    await waitFor(() => expect(getConversation).toHaveBeenCalledTimes(3));

    await act(async () => followUp.resolve(thread(81, [completedRecoveryTurn])));
    expect(await screen.findByLabelText("Approval required")).toBeInTheDocument();
    expect(await screen.findByTestId("projection-artifact-88")).toBeInTheDocument();
    await waitFor(() => expect(getConversation).toHaveBeenCalledTimes(3));
  });

  it("replays a durable event received before an optimistic Turn binds its server id", async () => {
    const request = deferred<TurnSubmission>();
    vi.mocked(sendConversationTurn).mockReturnValue(request.promise);
    const view = renderBrainHome();
    fireEvent.change(await screen.findByLabelText("运营大脑消息"), {
      target: { value: "生成复盘" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送给运营大脑" }));
    await waitFor(() => expect(sendConversationTurn).toHaveBeenCalled());
    const clientMessageId = vi.mocked(sendConversationTurn).mock.calls[0][1].client_message_id;

    act(() => {
      mocks.turnEvents.handler?.(durableTurnEvent("step.started", 1, 1, 82, 401, {
        step: "read_data",
      }));
      mocks.turnEvents.handler?.(durableTurnEvent("deliverable.updated", 2, 2, 82, 401, {
        deliverable_id: 88,
      }));
      mocks.turnEvents.handler?.(durableTurnEvent("turn.completed", 3, 3, 82, 401, {
        status: "completed",
      }));
    });
    await act(async () => request.resolve(submission(
      persistedTurn(401, clientMessageId, "生成复盘", null, "running"),
    )));

    expect(view.queryClient.getQueryData<ConversationThread>(["brain-conversation", 82])?.turns[0])
      .toMatchObject({
        status: "completed",
        runtime_overlay: {
          deliverableIds: [88],
          steps: { read_data: { state: "active" } },
        },
      });
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
  return {
    queryClient,
    ...render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AntApp>
          <BrainHome />
          <LocationProbe />
        </AntApp>
      </MemoryRouter>
    </QueryClientProvider>,
    ),
  };
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}</output>;
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

function presentationArtifact(): Artifact {
  return {
    id: 5001,
    account_id: 3,
    thread_id: 81,
    turn_id: 501,
    run_id: 7001,
    skill_run_id: 4001,
    task_id: null,
    artifact_type: "account_inspection_report",
    presentation: {
      type_label: "账号诊断",
      completion_label: "已完成当前账号运营诊断",
      status_label: "待确认",
      detail_action_label: "查看账号诊断",
    },
    next_actions: [
      {
        code: "generate_next_iteration",
        label: "生成下一轮优化方案",
        requires_confirmation: false,
      },
      { code: "request_revision", label: "提出修改", requires_confirmation: false },
      { code: "export", label: "导出内容", requires_confirmation: false },
    ],
    title: "不应展示的服务端标题",
    version: 1,
    status: "ready_for_review",
    summary: "账号诊断已完成。",
    sections: [{ key: "core_conclusion", title: "核心结论", content: "建议持续跟进内容表现。" }],
    evidence_refs: [],
    quality: null,
    created_at: "2026-07-28T00:00:00Z",
  };
}

function actionExecution(
  artifact: Artifact,
  overrides: {
    actionCode: DeliverableActionExecution["action_code"];
    status: DeliverableActionExecution["status"];
    resource: DeliverableActionExecution["resource"];
  },
): DeliverableActionExecution {
  return {
    execution_id: 901,
    artifact_id: artifact.id,
    artifact_version: artifact.version,
    action_code: overrides.actionCode,
    status: overrides.status,
    resource: overrides.resource,
    result: {},
    replayed: false,
  };
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
    availability: "available",
    reason: null,
    required_context: ["account"],
    is_available: true,
    unavailable_reason: null,
  };
}

function operationSkill(
  code: string,
  name: string,
  overrides: Partial<PublicSkill> = {},
): PublicSkill {
  return {
    code,
    version: 1,
    name,
    description: `${name}能力`,
    category: "quick_operations",
    icon: "operation",
    requires_account: true,
    availability: "available",
    reason: null,
    required_context: ["account"],
    is_available: true,
    unavailable_reason: null,
    ...overrides,
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

function durableTurnEvent(
  type: string,
  id: number,
  sequence: number,
  threadId: number,
  turnId: number,
  payload: Record<string, unknown> = {},
) {
  return {
    id,
    sequence,
    type,
    payload,
    thread_id: threadId,
    turn_id: turnId,
    run_id: 3,
    skill_run_id: null,
    created_at: "2026-08-04T00:00:00Z",
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
