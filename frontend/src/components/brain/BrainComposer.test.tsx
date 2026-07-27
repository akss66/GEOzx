// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentToolCall } from "../../types";
import { BrainComposer } from "./BrainComposer";

afterEach(cleanup);

const permission: AgentToolCall = {
  id: 45,
  org_id: 1,
  task_id: 12,
  invocation_id: 90,
  module: "brain",
  agent_code: "06-operator",
  tool_code: "publish_package_prepare",
  tool_name: "生成发布包",
  status: "waiting_approval",
  permission_mode: "confirm",
  requires_human_confirmation: true,
  input_summary: "整理发布字段并进入人工审批",
  output_summary: "即将为当前抖音账号生成发布包。",
  error: null,
  latency_ms: null,
  cost: 0,
  meta: {},
  started_at: null,
  finished_at: null,
  created_at: "2026-07-17T00:00:00Z",
  updated_at: "2026-07-17T00:00:00Z",
};

describe("BrainComposer", () => {
  it("submits with Enter but keeps Shift+Enter for a newline", () => {
    const onSubmit = vi.fn();
    render(
      <BrainComposer
        value="分析当前账号"
        disabled={false}
        loading={false}
        pendingPermission={null}
        approvalComment=""
        approving={false}
        onChange={vi.fn()}
        onApprovalCommentChange={vi.fn()}
        onApprovePermission={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    const input = screen.getByPlaceholderText("输入目标、补充要求、打断指令，或直接问一个问题。");
    fireEvent.keyDown(input, { key: "Enter", code: "Enter", shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it("replaces the send action with a real stop action while generating", () => {
    const onStop = vi.fn();
    render(
      <BrainComposer
        value=""
        disabled
        loading
        pendingPermission={null}
        approvalComment=""
        approving={false}
        onChange={vi.fn()}
        onApprovalCommentChange={vi.fn()}
        onApprovePermission={vi.fn()}
        onSubmit={vi.fn()}
        onStop={onStop}
      />,
    );

    expect(screen.queryByRole("button", { name: "发送给主 Agent" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "停止生成" }));
    expect(onStop).toHaveBeenCalledOnce();
  });

  it("uses a business name instead of an internal tool name", () => {
    render(
      <BrainComposer
        value=""
        disabled={false}
        loading={false}
        pendingPermission={{
          ...permission,
          tool_code: "brief_builder",
          tool_name: "Brief Builder",
          output_summary: "已整理当前任务目标，等待确认后继续。",
        }}
        approvalComment=""
        approving={false}
        onChange={vi.fn()}
        onApprovalCommentChange={vi.fn()}
        onApprovePermission={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText("整理任务目标")).toBeVisible();
    expect(screen.queryByText("Brief Builder")).not.toBeInTheDocument();
  });

  it("morphs the composer into an in-place permission mode without a dialog", () => {
    const onApprovePermission = vi.fn();

    render(
      <BrainComposer
        value=""
        disabled={false}
        loading={false}
        pendingPermission={permission}
        approvalComment=""
        approving={false}
        onChange={vi.fn()}
        onApprovalCommentChange={vi.fn()}
        onApprovePermission={onApprovePermission}
        onSubmit={vi.fn()}
      />,
    );

    const composer = screen.getByRole("region", { name: "主 Agent 输入区" });
    expect(composer).toHaveAttribute("data-mode", "permission");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/输入目标/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "允许" }));
    expect(onApprovePermission).toHaveBeenCalledWith(45, true, undefined);
  });

  it("opens revision input inside the same composer and rejects with the comment", () => {
    const onApprovalCommentChange = vi.fn();
    const onApprovePermission = vi.fn();

    const { rerender } = render(
      <BrainComposer
        value=""
        disabled={false}
        loading={false}
        pendingPermission={permission}
        approvalComment=""
        approving={false}
        onChange={vi.fn()}
        onApprovalCommentChange={onApprovalCommentChange}
        onApprovePermission={onApprovePermission}
        onSubmit={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "修改要求" }));
    fireEvent.change(screen.getByRole("textbox", { name: "修改要求" }), {
      target: { value: "标题再克制一点" },
    });
    expect(onApprovalCommentChange).toHaveBeenCalledWith("标题再克制一点");

    rerender(
      <BrainComposer
        value=""
        disabled={false}
        loading={false}
        pendingPermission={permission}
        approvalComment="标题再克制一点"
        approving={false}
        onChange={vi.fn()}
        onApprovalCommentChange={onApprovalCommentChange}
        onApprovePermission={onApprovePermission}
        onSubmit={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "驳回并重做" }));
    expect(onApprovePermission).toHaveBeenCalledWith(45, false, "标题再克制一点");
  });
});
