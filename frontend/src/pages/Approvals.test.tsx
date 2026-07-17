// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { approveToolCall } from "../api/brain";
import type { ApprovalQueueItem, ApprovalWorkspace } from "../types";
import Approvals from "./Approvals";

const mocks = vi.hoisted(() => {
  const publishItem: ApprovalQueueItem = {
    key: "tool_call:46",
    kind: "tool_call",
    source_id: 46,
    project_id: 2,
    project_name: "同舟行抖音项目",
    account_id: 6,
    account_name: "阿桑",
    content_item_id: 88,
    content_title: "新品发布内容",
    task_id: 13,
    category: "发布准备",
    title: "新品发布内容",
    summary: "发布包已经整理完成，等待人工确认。",
    risk_level: "high",
    risk_reasons: ["即将形成平台发布动作"],
    impact: ["允许后进入人工发布清单"],
    agent_explanation: "本次只允许进入人工发布流程，不会自动发布到抖音。",
    preview: {
      tool_name: "发布准备",
      input_summary: "整理抖音视频发布包",
      output_summary: "发布包已准备完成",
      publish_package: {
        platform: "douyin",
        account_id: 6,
        content_type: "video",
        title: "新品发布标题",
        body: "新品发布正文",
        topics: ["数码评测", "新品体验"],
        scheduled_at: null,
        material_ids: [7, 8],
        cover_material_id: 8,
        visibility: "friends",
        allow_comment: false,
        execution_mode: "manual_checklist",
        manual_steps: ["打开抖音创作者服务中心", "上传素材并核对封面"],
      },
      findings: [{ level: "pass", code: "material.ok", message: "Material is ready." }],
    },
    can_decide: true,
    created_at: "2026-07-17T00:00:00Z",
  };
  const gateItem: ApprovalQueueItem = {
    ...publishItem,
    key: "gate:51",
    kind: "gate",
    source_id: 51,
    account_id: 6,
    title: "脚本合规确认",
    category: "脚本合规",
    summary: "脚本需要人工确认后进入成片阶段。",
    risk_level: "medium",
    preview: {
      tool_name: "脚本合规检查",
      input_summary: "检查脚本表达与平台规则",
      output_summary: "未发现阻断项",
    },
  };
  const workspace: ApprovalWorkspace = {
    items: [publishItem, gateItem],
    counts: { total: 2, critical: 0, high: 1, medium: 1 },
    can_decide: true,
    generated_at: "2026-07-17T00:00:00Z",
  };
  return { gateItem, publishItem, workspace };
});

vi.mock("../api/approvals", () => ({
  getApprovalWorkspace: vi.fn(async () => mocks.workspace),
}));

vi.mock("../api/brain", () => ({
  approveDeliverableAcceptance: vi.fn(),
  approveToolCall: vi.fn(async () => ({ id: 46, status: "success" })),
  rejectDeliverableAcceptance: vi.fn(),
}));

vi.mock("../api/orchestrator", () => ({
  approveGate: vi.fn(async () => ({ id: 51, status: "approved" })),
}));

vi.mock("../hooks/useEventStream", () => ({
  useEventStream: vi.fn(),
}));

vi.mock("../stores/currentWorkspace", () => ({
  useCurrentWorkspace: vi.fn(() => ({ clientId: 1, projectId: 2, accountId: 6 })),
}));

function renderApprovals() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AntApp><Approvals /></AntApp>
    </QueryClientProvider>,
  );
}

describe("Approvals", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders the unified three-pane approval workbench with a typed publish package", async () => {
    renderApprovals();

    const queue = await screen.findByRole("complementary", { name: "待审批队列" });
    const preview = document.querySelector("main.approval-preview");
    const decision = screen.getByRole("complementary", { name: "风险与审批操作" });

    expect(await within(queue).findByText("新品发布内容")).toBeInTheDocument();
    expect(preview).not.toBeNull();
    expect(await within(preview as HTMLElement).findByText("新品发布标题")).toBeInTheDocument();
    expect(within(preview as HTMLElement).getByText("朋友可见")).toBeInTheDocument();
    expect(within(preview as HTMLElement).getByText(/素材 #7 \/ #8/)).toBeInTheDocument();
    expect(within(preview as HTMLElement).getByText("打开抖音创作者服务中心")).toBeInTheDocument();
    expect(decision).toHaveTextContent("高风险");
    expect(decision).toHaveTextContent("不会自动发布到抖音");
    expect(within(decision).getByRole("button", { name: /允许执行/ })).toBeEnabled();
  });

  it("requires a modification note for rejection and advances after approval", async () => {
    renderApprovals();

    const decision = await screen.findByRole("complementary", { name: "风险与审批操作" });
    fireEvent.click(await within(decision).findByRole("button", { name: /驳回/ }));
    expect(screen.getByText("请先写明修改意见，再驳回并重跑。")).toBeInTheDocument();
    expect(approveToolCall).not.toHaveBeenCalled();

    fireEvent.change(within(decision).getByLabelText("修改意见"), {
      target: { value: "标题需要更克制，并补充品牌风险说明。" },
    });
    fireEvent.click(within(decision).getByRole("button", { name: /允许执行/ }));

    await waitFor(() => expect(approveToolCall).toHaveBeenCalledWith({
      toolCallId: 46,
      approved: true,
      comment: "标题需要更克制，并补充品牌风险说明。",
    }));
    const preview = document.querySelector("main.approval-preview");
    await waitFor(() => expect(within(preview as HTMLElement).getByText("脚本合规确认")).toBeInTheDocument());
  });
});
