// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getContentWorkspace, listContentItems } from "../../api/orchestrator";
import { ContentWorkspaceView } from "./ContentWorkspace";

vi.mock("../../api/orchestrator", () => ({
  createContentItem: vi.fn(),
  createDeliverableRevision: vi.fn(),
  getContentWorkspace: vi.fn(),
  listContentItems: vi.fn(async () => []),
  rerunStage: vi.fn(),
  rollbackDeliverable: vi.fn(),
  startPipeline: vi.fn(),
}));

vi.mock("../../hooks/useEventStream", () => ({
  useEventStream: vi.fn(),
}));

describe("ContentWorkspaceView", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows a retry state when the project content list fails", async () => {
    vi.mocked(listContentItems).mockRejectedValueOnce({ response: { status: 503 } });

    renderWorkspace();

    expect(await screen.findByRole("alert")).toHaveTextContent("内容列表加载失败");
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByText("项目里还没有内容")).toBeInTheDocument();
  });

  it("keeps the content rail while a selected content workspace is retried", async () => {
    vi.mocked(listContentItems).mockResolvedValueOnce([{
      id: 7,
      project_id: 2,
      account_id: 3,
      title: "新品评测",
      current_stage: "positioning",
      status: "draft",
      created_at: "2026-07-17T00:00:00Z",
    }]);
    vi.mocked(getContentWorkspace).mockRejectedValueOnce({ response: { status: 503 } });
    vi.mocked(getContentWorkspace).mockResolvedValueOnce({
      content_item: {
        id: 7,
        project_id: 2,
        account_id: 3,
        title: "新品评测",
        current_stage: "positioning",
        status: "draft",
        created_at: "2026-07-17T00:00:00Z",
      },
      project_name: "新品项目",
      account: { id: 3, nickname: "测试账号", platform: "douyin", auth_status: "authorized" },
      tasks: [],
      deliverables: [],
      gates: [],
      compliance: [],
      materials: [],
      publish_tool_calls: [],
    });

    renderWorkspace();

    expect(await screen.findByText("新品评测")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("内容工作区加载失败");
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(getContentWorkspace).toHaveBeenCalledTimes(2);
  });
});

function renderWorkspace() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp><ContentWorkspaceView projectId={2} accountId={3} /></AntApp>
    </QueryClientProvider>,
  );
}
