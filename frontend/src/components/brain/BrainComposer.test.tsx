// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentToolCall } from "../../types";
import { BrainComposer } from "./BrainComposer";

const accountInspectionSkill = {
  code: "account_inspection",
  version: 1,
  name: "一键账号体检",
  description: "快速了解账号现状与优化重点",
  category: "quick_operations" as const,
  icon: "activity",
  requires_account: true,
  is_available: true,
  unavailable_reason: null,
};

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
  it("places the capability launcher at the left of the composer and hides it for permission confirmation", () => {
    const { rerender } = render(
      <BrainComposer
        value=""
        disabled={false}
        loading={false}
        pendingPermission={null}
        approvalComment=""
        approving={false}
        skills={[accountInspectionSkill]}
        onChange={vi.fn()}
        onApprovalCommentChange={vi.fn()}
        onApprovePermission={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "添加能力或材料" })).toBeInTheDocument();

    rerender(
      <BrainComposer
        value=""
        disabled={false}
        loading={false}
        pendingPermission={permission}
        approvalComment=""
        approving={false}
        skills={[accountInspectionSkill]}
        onChange={vi.fn()}
        onApprovalCommentChange={vi.fn()}
        onApprovePermission={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "添加能力或材料" })).not.toBeInTheDocument();

    rerender(
      <BrainComposer
        value=""
        disabled={false}
        loading={false}
        pendingPermission={null}
        approvalComment=""
        approving={false}
        skills={[accountInspectionSkill]}
        onChange={vi.fn()}
        onApprovalCommentChange={vi.fn()}
        onApprovePermission={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "添加能力或材料" })).toBeInTheDocument();
  });

  it("disables the capability launcher while the composer is disabled or generating", () => {
    const props = {
      value: "",
      pendingPermission: null,
      approvalComment: "",
      approving: false,
      skills: [accountInspectionSkill],
      onSelectSkill: vi.fn(),
      onChange: vi.fn(),
      onApprovalCommentChange: vi.fn(),
      onApprovePermission: vi.fn(),
      onSubmit: vi.fn(),
    };
    const { rerender } = render(<BrainComposer {...props} disabled loading={false} />);

    expect(screen.getByRole("button", { name: "添加能力或材料" })).toBeDisabled();

    rerender(<BrainComposer {...props} disabled={false} loading />);
    expect(screen.getByRole("button", { name: "添加能力或材料" })).toBeDisabled();
  });

  it("keeps the single-line message composer slim and vertically centered", () => {
    const styles = readFileSync(
      join(process.cwd(), "src/styles/brain-v2.css"),
      "utf8",
    );

    expect(styles).toMatch(
      /\.dy-brain-composer-box\[data-mode="message"\]\s*{[^}]*min-height:\s*56px;[^}]*align-items:\s*center;/s,
    );
    expect(styles).toMatch(
      /\.dy-brain-composer-box\[data-mode="message"\]\s+\.dy-brain-composer-tools\s*{[^}]*top:\s*50%;[^}]*transform:\s*translateY\(-50%\);/s,
    );
    expect(styles).toMatch(
      /\.dy-brain-input textarea\.ant-input\s*{[^}]*padding:\s*0;/s,
    );
  });

  it("starts compact and grows through six rows", () => {
    render(
      <BrainComposer
        value=""
        disabled={false}
        loading={false}
        pendingPermission={null}
        approvalComment=""
        approving={false}
        onChange={vi.fn()}
        onApprovalCommentChange={vi.fn()}
        onApprovePermission={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const input = screen.getByRole("textbox", { name: "运营大脑消息" });
    expect(input).not.toHaveAttribute("placeholder");
    expect(input).toHaveAttribute("data-autosize-min-rows", "1");
    expect(input).toHaveAttribute("data-autosize-max-rows", "6");
    expect(screen.getByRole("button", { name: "发送给运营大脑" })).toHaveClass(
      "dy-brain-send-button",
    );
  });

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

    const input = screen.getByRole("textbox", { name: "运营大脑消息" });
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

    expect(screen.queryByRole("button", { name: "发送给运营大脑" })).not.toBeInTheDocument();
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

    const composer = screen.getByRole("region", { name: "运营大脑输入区" });
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
