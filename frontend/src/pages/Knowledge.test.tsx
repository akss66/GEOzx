// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  approveKnowledgeSuggestion,
  listKnowledge,
  listKnowledgeCitations,
  listKnowledgeSuggestions,
} from "../api/knowledge";
import { getWorkspaceContext } from "../api/shell";
import {
  bindWechatKnowledgeBase,
  getWechatKnowledgeBinding,
  listWechatKnowledgeBases,
  unbindWechatKnowledgeBase,
} from "../services/wechatIntegration";
import Knowledge from "./Knowledge";

const currentWorkspace = vi.hoisted(() => ({
  clientId: 1,
  projectId: 2,
  platform: "wechat_official_account",
  accountId: 31,
}));

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false, media: query, onchange: null,
      addListener: vi.fn(), removeListener: vi.fn(),
      addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
    })),
  });
});

const entry = {
  id: 11,
  client_id: 1,
  project_id: 2,
  category: "hot_content" as const,
  title: "对比实测类内容结构",
  content: "先呈现冲突，再用真实实测证据给出结论。",
  payload: {},
  tags: ["数码", "实测"],
  source_type: "manual" as const,
  source_label: "运营团队复盘",
  source_url: null,
  version: 3,
  status: "active" as const,
  created_by_id: 1,
  created_at: "2026-07-16T00:00:00Z",
  updated_at: "2026-07-17T00:00:00Z",
};
const suggestion = {
  id: 20,
  client_id: 1,
  project_id: 2,
  category: "script_library" as const,
  title: "评论区追问回应方式",
  content: "先确认用户场景，再给一条可执行建议。",
  payload: {},
  tags: ["客服"],
  source_agent_code: "08-customer-service",
  source_label: "客服反馈专家建议",
  source_task_id: 9,
  source_deliverable_id: 10,
  status: "pending" as const,
  reviewed_by_id: null,
  reviewed_at: null,
  review_note: null,
  accepted_entry_id: null,
  created_at: "2026-07-17T00:00:00Z",
};

vi.mock("../api/knowledge", () => ({
  approveKnowledgeSuggestion: vi.fn(async () => ({ suggestion: { ...suggestion, status: "approved" }, entry })),
  archiveKnowledge: vi.fn(),
  createKnowledge: vi.fn(),
  listKnowledge: vi.fn(async () => [entry]),
  listKnowledgeCitations: vi.fn(async () => [{
    id: 1, entry_id: 11, project_id: 2, task_id: 9, invocation_id: 3,
    agent_code: "01-positioning", context: "校准账号定位", created_at: "2026-07-17T01:00:00Z",
  }]),
  listKnowledgeSuggestions: vi.fn(async () => [suggestion]),
  rejectKnowledgeSuggestion: vi.fn(),
  updateKnowledge: vi.fn(),
}));

vi.mock("../api/shell", () => ({
  getWorkspaceContext: vi.fn(async () => ({
    clients: [{ id: 1, name: "数码客户" }],
    selected_client: { id: 1, name: "数码客户" },
    projects: [{ id: 2, name: "冷启动项目" }],
    selected_project: { id: 2, name: "冷启动项目" },
    accounts: [],
  })),
}));

vi.mock("../services/wechatIntegration", () => ({
  bindWechatKnowledgeBase: vi.fn(),
  getWechatKnowledgeBinding: vi.fn(async () => null),
  listWechatKnowledgeBases: vi.fn(async () => ({
    data: [],
    pagination: { limit: 100, offset: 0, total: 0 },
  })),
  unbindWechatKnowledgeBase: vi.fn(),
}));

vi.mock("../stores/currentWorkspace", () => ({
  useCurrentWorkspace: vi.fn(() => currentWorkspace),
}));

describe("Knowledge", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    currentWorkspace.accountId = 31;
    vi.mocked(getWechatKnowledgeBinding).mockResolvedValue(null);
    vi.mocked(listWechatKnowledgeBases).mockResolvedValue({
      data: [],
      pagination: { limit: 100, offset: 0, total: 0 },
    });
  });
  afterEach(cleanup);

  function renderPage() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    return render(<QueryClientProvider client={client}><AntApp><Knowledge /></AntApp></QueryClientProvider>);
  }

  it("renders a scoped document with provenance, version, and citations", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "对比实测类内容结构" })).toBeInTheDocument();
    expect(screen.getByText("先呈现冲突，再用真实实测证据给出结论。")).toBeInTheDocument();
    expect(screen.getByText("运营团队复盘")).toBeInTheDocument();
    expect(screen.getAllByText(/V3/).length).toBeGreaterThan(0);
    expect(await screen.findByText("校准账号定位")).toBeInTheDocument();
    expect(listKnowledge).toHaveBeenCalledWith(1, 2, "hot_content");
    expect(listKnowledgeCitations).toHaveBeenCalledWith(11, 1, 2);
  });

  it("requires a confirmation before an agent suggestion enters the library", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /待确认建议/ }));
    expect(await screen.findByRole("heading", { name: "评论区追问回应方式" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "采用建议" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认写入" }));

    await waitFor(() => expect(approveKnowledgeSuggestion).toHaveBeenCalledWith(20, ""));
    expect(listKnowledgeSuggestions).toHaveBeenCalledWith(1, 2);
  });

  it("does not present a failed workspace request as an unselected customer", async () => {
    vi.mocked(getWorkspaceContext).mockRejectedValueOnce({ response: { status: 503 } });

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("知识库上下文加载失败");
    expect(screen.queryByText("先选择知识所属客户")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByRole("heading", { name: "对比实测类内容结构" })).toBeInTheDocument();
  });

  it("does not present a failed knowledge request as an empty collection", async () => {
    vi.mocked(listKnowledge).mockRejectedValueOnce({ response: { status: 503 } });

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("知识文档加载失败");
    expect(screen.queryByText("这个集合还没有知识")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByRole("heading", { name: "对比实测类内容结构" })).toBeInTheDocument();
  });

  it("does not present a failed citation request as no citations", async () => {
    vi.mocked(listKnowledgeCitations).mockRejectedValueOnce({ response: { status: 503 } });

    renderPage();

    expect(await screen.findByRole("heading", { name: "对比实测类内容结构" })).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("引用记录加载失败");
    expect(screen.queryByText("尚未被 Agent 引用")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重\s*试/ }));
    expect(await screen.findByText("校准账号定位")).toBeInTheDocument();
  });

  it("selects an eligible brand from a later page and excludes shared bases", async () => {
    vi.mocked(listWechatKnowledgeBases).mockResolvedValueOnce({
      data: [
        { id: 42, clientId: null, kind: "organization_shared", name: "组织共享库", description: null, status: "active", version: 1 },
        { id: 41, clientId: 1, kind: "brand", name: "第二页品牌事实库", description: null, status: "active", version: 1 },
      ],
      pagination: { limit: 100, offset: 0, total: 2 },
    });
    vi.mocked(bindWechatKnowledgeBase).mockResolvedValueOnce({
      id: 9, accountId: 31, knowledgeBaseId: 41, knowledgeBaseKind: "brand",
      clientId: 1, bindingType: "primary_brand", status: "active", boundAt: "2026-08-12T00:00:00Z",
    });

    renderPage();

    expect(await screen.findByText("公众号主品牌知识库")).toBeInTheDocument();
    expect(screen.queryByText("组织共享库")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "绑定主品牌 第二页品牌事实库" }));
    await waitFor(() => expect(bindWechatKnowledgeBase).toHaveBeenCalledWith(31, 41));
  });

  it("requires named confirmation before replacing a primary brand and cancel does not mutate", async () => {
    vi.mocked(listWechatKnowledgeBases).mockResolvedValueOnce({
      data: [
        { id: 41, clientId: 1, kind: "brand", name: "现有品牌库", description: null, status: "active", version: 1 },
        { id: 43, clientId: 1, kind: "brand", name: "目标品牌库", description: null, status: "active", version: 1 },
      ],
      pagination: { limit: 100, offset: 0, total: 2 },
    });
    vi.mocked(getWechatKnowledgeBinding).mockResolvedValueOnce({
      id: 9, accountId: 31, knowledgeBaseId: 41, knowledgeBaseKind: "brand",
      clientId: 1, bindingType: "primary_brand", status: "active", boundAt: "2026-08-12T00:00:00Z",
    });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "替换为主品牌 目标品牌库" }));
    expect(screen.getByText("将“现有品牌库”替换为“目标品牌库”？")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消替换" }));
    expect(bindWechatKnowledgeBase).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "替换为主品牌 目标品牌库" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认替换" }));
    await waitFor(() => expect(bindWechatKnowledgeBase).toHaveBeenCalledWith(31, 43));
    expect(unbindWechatKnowledgeBase).not.toHaveBeenCalled();
  });

  it("isolates primary-brand binding by current account key", async () => {
    vi.mocked(listWechatKnowledgeBases).mockResolvedValue({
      data: [
        { id: 41, clientId: 1, kind: "brand", name: "甲品牌库", description: null, status: "active", version: 1 },
        { id: 43, clientId: 1, kind: "brand", name: "乙品牌库", description: null, status: "active", version: 1 },
      ],
      pagination: { limit: 100, offset: 0, total: 2 },
    });
    vi.mocked(getWechatKnowledgeBinding).mockImplementation(async (accountId) => ({
      id: accountId, accountId, knowledgeBaseId: accountId === 31 ? 41 : 43,
      knowledgeBaseKind: "brand", clientId: 1, bindingType: "primary_brand",
      status: "active", boundAt: "2026-08-12T00:00:00Z",
    }));

    const view = renderPage();
    expect(await screen.findByText("当前：甲品牌库")).toBeInTheDocument();
    currentWorkspace.accountId = 32;
    view.rerender(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AntApp><Knowledge /></AntApp></QueryClientProvider>);

    expect(await screen.findByText("当前：乙品牌库")).toBeInTheDocument();
    expect(screen.queryByText("当前：甲品牌库")).not.toBeInTheDocument();
    expect(getWechatKnowledgeBinding).toHaveBeenCalledWith(31);
    expect(getWechatKnowledgeBinding).toHaveBeenCalledWith(32);
  });

  it("announces an empty primary-brand choice and offers retry on load failure", async () => {
    renderPage();
    expect(await screen.findByText("当前客户没有可绑定的品牌知识库")).toHaveAttribute("role", "status");
    cleanup();
    vi.mocked(listWechatKnowledgeBases).mockRejectedValueOnce(new Error("secret backend detail"));
    renderPage();
    expect(await screen.findByRole("button", { name: "主品牌绑定加载失败，重新加载" })).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("secret backend detail");
  });
});
