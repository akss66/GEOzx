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
import Knowledge from "./Knowledge";

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

vi.mock("../stores/currentWorkspace", () => ({
  useCurrentWorkspace: vi.fn(() => ({ clientId: 1, projectId: 2, platform: "douyin", accountId: null })),
}));

describe("Knowledge", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(cleanup);

  function renderPage() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><AntApp><Knowledge /></AntApp></QueryClientProvider>);
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
});
