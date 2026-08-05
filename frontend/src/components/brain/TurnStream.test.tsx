// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getArtifact } from "../../api/brain";
import type { Artifact, ConversationThread, TurnProjection } from "../../types";
import { TurnStream } from "./TurnStream";

const workTurnSource = readFileSync(resolve(process.cwd(), "src/components/brain/WorkTurnCard.tsx"), "utf8");
const turnStreamSource = readFileSync(resolve(process.cwd(), "src/components/brain/TurnStream.tsx"), "utf8");
const indexStylesSource = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");
const appShellStylesSource = readFileSync(resolve(process.cwd(), "src/styles/app-shell.css"), "utf8");

vi.mock("../../api/brain", () => ({ getArtifact: vi.fn() }));

const artifact = {
  id: 5001,
  account_id: 3,
  thread_id: 81,
  turn_id: 101,
  run_id: 7001,
  skill_run_id: 4001,
  task_id: 21,
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
  title: "账号体检报告",
  version: 1,
  status: "ready_for_review",
  summary: "优先收敛内容主题。",
  sections: [{ key: "core_conclusion", title: "核心结论", content: "优先收敛内容主题。" }],
  evidence_refs: [],
  quality: null,
  created_at: "2026-07-28T00:00:00Z",
} satisfies Artifact;

const thread: ConversationThread = {
  id: 81,
  org_id: 1,
  created_by_id: 7,
  client_id: null,
  project_id: null,
  account_id: 3,
  title: "Account conversation",
  created_at: "2026-07-28T00:00:00Z",
  updated_at: "2026-07-28T00:00:00Z",
  turns: [{
    id: 101,
    thread_id: 81,
    org_id: 1,
    created_by_id: 7,
    client_message_id: "turn-101",
    user_input: "诊断我的账号",
    assistant_response: "已完成初步核对。",
    intent: { mode: "SKILL", route_source: "explicit", skill_code: "account_inspection" },
    status: "completed",
    route_ms: 12,
    first_token_ms: 120,
    completion_ms: 420,
    total_ms: 430,
    model_call_count: 2,
    projections: [{
      type: "artifact",
      turn_id: 101,
      artifact_id: 5001,
      artifact_type: "account_inspection_report",
      skill_run_id: 4001,
      account_id: 3,
      report: {},
    }],
    created_at: "2026-07-28T00:00:00Z",
    updated_at: "2026-07-28T00:00:00Z",
  }],
};

describe("TurnStream", () => {
  afterEach(cleanup);
  beforeEach(() => vi.mocked(getArtifact).mockResolvedValue(artifact));

  it("keeps production work-turn sources free of legacy chat and agent presentation classes", () => {
    expect(workTurnSource).toMatch(/className="tz-work-turn"/);
    expect(workTurnSource).not.toMatch(/dy-chat-/);
    expect(workTurnSource).not.toMatch(/dy-chat-(message|bubble|expert)/);
    expect(turnStreamSource).not.toMatch(/dy-chat-(message|bubble|expert)/);
    expect(indexStylesSource).not.toMatch(/dy-agent-/);
    expect(appShellStylesSource).not.toMatch(/dy-agent-/);
  });

  it("renders one continuous work-turn card instead of segmented chat messages", () => {
    const runningThread: ConversationThread = {
      ...thread,
      turns: [{
        ...thread.turns[0],
        id: 103,
        client_message_id: "turn-103",
        status: "running",
        turn_phase: "consulting_experts",
        assistant_response: null,
        projections: [{
          type: "progress",
          turn_id: 103,
          skill_run_id: 4002,
          stages: [{ code: "review", name: "核对账号数据", status: "running" }],
        }],
      }],
    };
    const view = render(<TurnStream thread={runningThread} />);
    const root = screen.getByTestId("work-turn");

    expect(screen.getAllByTestId("work-turn")).toHaveLength(1);
    expect(screen.getAllByText("运营大脑")).toHaveLength(1);
    expect(screen.queryByText("思考中")).not.toBeInTheDocument();
    expect(root.querySelector(".dy-chat-bubble")).toBeNull();

    view.rerender(<TurnStream thread={{
      ...runningThread,
      turns: [{ ...runningThread.turns[0], status: "completed", turn_phase: "completed" }],
    }} />);

    expect(screen.getByTestId("work-turn")).toBe(root);
    expect(root).toHaveAttribute("data-turn-status", "completed");
  });

  it("keeps an optimistic work-turn root when the server binds its id", () => {
    const optimisticThread: ConversationThread = {
      ...thread,
      turns: [{ ...thread.turns[0], id: null, client_message_id: "optimistic-1", status: "queued" }],
    };
    const view = render(<TurnStream thread={optimisticThread} />);
    const root = screen.getByTestId("work-turn");

    view.rerender(<TurnStream thread={{
      ...optimisticThread,
      turns: [{ ...optimisticThread.turns[0], id: 104, status: "running" }],
    }} />);

    expect(screen.getByTestId("work-turn")).toBe(root);
    expect(root).toHaveAttribute("data-turn-id", "104");
    expect(root).toHaveAttribute("data-turn-key", "org:1:thread:81:message:optimistic-1");
  });

  it.each([
    ["failed", "重新开始本轮"],
    ["dead_letter", "重新开始本轮"],
    ["cancelled", "重新开始本轮"],
    ["stopped", "重新开始本轮"],
  ])("renders the %s recovery action inside its source work-turn", (status, label) => {
    const onRestartTurn = vi.fn();
    const failedTurn = { ...thread.turns[0], status, projections: [] };
    render(<TurnStream
      thread={{ ...thread, turns: [failedTurn] }}
      onRestartTurn={onRestartTurn}
    />);

    const root = screen.getByTestId("work-turn");
    fireEvent.click(within(root).getByRole("button", { name: label }));

    expect(onRestartTurn).toHaveBeenCalledWith(failedTurn);
  });

  it("reveals blocked recovery guidance without calling the restart handler", () => {
    const onRestartTurn = vi.fn();
    render(<TurnStream
      thread={{
        ...thread,
        turns: [{
          ...thread.turns[0],
          status: "blocked",
          projections: [{
            type: "execution_blocked",
            turn_id: 101,
            skill_run_id: 302,
            code: "ACCOUNT_AUTH_REQUIRED",
            recovery_action: "请先重新授权当前抖音账号。",
          }],
        }],
      }}
      onRestartTurn={onRestartTurn}
    />);

    const root = screen.getByTestId("work-turn");
    const guidanceButton = within(root).getByRole("button", { name: "查看如何继续" });
    expect(guidanceButton).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(guidanceButton);
    expect(guidanceButton).toHaveAttribute("aria-expanded", "true");
    expect(within(
      within(root).getByRole("region", { name: "恢复指引" }),
    ).getByText("请先重新授权当前抖音账号。")).toBeVisible();
    expect(onRestartTurn).not.toHaveBeenCalled();
  });

  it.each(["queued", "running", "completed", "waiting_permission"])(
    "does not render a recovery action for a %s work-turn",
    (status) => {
      render(<TurnStream
        thread={{
          ...thread,
          turns: [{ ...thread.turns[0], status, projections: [] }],
        }}
        onRestartTurn={vi.fn()}
      />);

      expect(screen.queryByRole("button", { name: "重试未完成部分" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "查看如何继续" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "重新开始本轮" })).not.toBeInTheDocument();
    },
  );

  it("keeps experts out of chat and renders technical details only after both disclosures open", () => {
    render(<TurnStream thread={{
      ...thread,
      turns: [{
        ...thread.turns[0],
        status: "running",
        turn_phase: "consulting_experts",
        projections: [{
          type: "execution_summary",
          turn_id: 101,
          run_id: 3002,
          skill_code: "account_inspection",
          skill_run_id: 4002,
          status: "completed",
          quality_score: 0.91,
          experts: [{ id: 7001, agent_code: "01-positioning", agent_name: "账号定位专家", status: "completed" }],
          tools: [{ id: 8001, tool_code: "account.data_context", tool_name: "账号数据上下文", status: "completed" }],
        }],
      }],
    }} />);

    expect(screen.queryByLabelText("Expert update")).not.toBeInTheDocument();
    expect(screen.queryByText("Tool #8001 · account.data_context · completed")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看分析过程" }));
    expect(within(screen.getByLabelText("调用专家摘要")).getByText(/账号定位专家/)).toBeVisible();
    expect(screen.getByText("数据来源：账号数据上下文")).toBeVisible();
    expect(screen.queryByText("Tool #8001 · account.data_context · completed")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "技术详情" }));
    expect(screen.getByText("Tool #8001 · account.data_context · completed")).toBeVisible();
    expect(screen.getByText("Agent Run：3002")).toBeVisible();
  });

  it("summarizes the data source, period, and completeness in the first disclosure", () => {
    render(<TurnStream thread={{
      ...thread,
      turns: [{
        ...thread.turns[0],
        projections: [{
          type: "account_data",
          turn_id: 101,
          account_id: 3,
          skill_code: "account_inspection",
          skill_run_id: 4001,
          data: {
            data_status: "pending_import",
            pending_imports: [{
              batch_id: 91,
              status: "validated",
              template_code: "douyin_account_daily",
              row_count: 31,
              period_start: "2026-07-01",
              period_end: "2026-07-31",
            }],
          },
        }],
      }],
    }} />);

    fireEvent.click(screen.getByRole("button", { name: "查看分析过程" }));
    const summary = screen.getByRole("region", { name: "数据与质量摘要" });
    expect(summary).toHaveTextContent("数据完整性：存在待确认导入");
    expect(summary).toHaveTextContent("数据来源：douyin_account_daily（31 行）");
    expect(summary).toHaveTextContent("数据周期：2026-07-01 至 2026-07-31");
  });

  it("labels grounded account-analysis evidence as analysis evidence", () => {
    render(<TurnStream thread={{
      ...thread,
      turns: [{
        ...thread.turns[0],
        status: "completed",
        projections: [{
          type: "execution_summary",
          turn_id: 101,
          run_id: 3003,
          skill_code: "account_data_analysis",
          skill_run_id: 4003,
          status: "completed",
          quality_score: 0.94,
          experts: [],
          tools: [],
          evidence_ids: [91, 92],
        }],
      }],
    }} />);

    fireEvent.click(screen.getByRole("button", { name: "查看已完成过程" }));
    const summary = screen.getByRole("region", { name: "数据与质量摘要" });
    expect(summary).toHaveTextContent("分析依据：2 项");
    expect(summary).not.toHaveTextContent("业务依据：2 项");
  });

  it("keeps the exact persisted Artifact in its source work turn", async () => {
    render(<TurnStream thread={thread} />);

    await waitFor(() => expect(getArtifact).toHaveBeenCalledWith(5001));
    expect(await screen.findByLabelText("运营内容：账号诊断 · V1")).toBeInTheDocument();
  });

  it("keeps a source Artifact in its original work-turn when later greeting and retry turns arrive", async () => {
    render(<TurnStream thread={{
      ...thread,
      turns: [
        thread.turns[0],
        { ...thread.turns[0], id: 102, client_message_id: "turn-102", user_input: "你好", projections: [] },
        { ...thread.turns[0], id: 103, client_message_id: "turn-103", user_input: "重试诊断", projections: [] },
      ],
    }} />);

    await screen.findByLabelText("运营内容：账号诊断 · V1");
    const roots = screen.getAllByTestId("work-turn");
    const source = roots.find((root) => root.getAttribute("data-turn-id") === "101");
    const greeting = roots.find((root) => root.getAttribute("data-turn-id") === "102");
    const retry = roots.find((root) => root.getAttribute("data-turn-id") === "103");

    expect(within(source as HTMLElement).getByLabelText("运营内容：账号诊断 · V1")).toBeInTheDocument();
    expect(greeting).not.toHaveTextContent("账号诊断");
    expect(retry).not.toHaveTextContent("账号诊断");
  });

  it("fails closed when the fetched Artifact does not match its source account or turn", async () => {
    vi.mocked(getArtifact).mockResolvedValue({ ...artifact, account_id: 4, turn_id: 102 });
    render(<TurnStream thread={thread} />);

    expect(await screen.findByText("运营内容校验失败，请重试。")).toBeInTheDocument();
    expect(screen.queryByLabelText("运营内容：账号诊断 · V1")).not.toBeInTheDocument();
  });

  it("fails closed when an Artifact projection belongs to a different account than the active thread", async () => {
    vi.mocked(getArtifact).mockResolvedValue({ ...artifact, account_id: 4 });
    render(<TurnStream thread={{
      ...thread,
      turns: [{
        ...thread.turns[0],
        projections: [{
          type: "artifact",
          turn_id: 101,
          artifact_id: 5001,
          artifact_type: "account_inspection_report",
          skill_run_id: 4001,
          account_id: 4,
          report: {},
        }],
      }],
    }} />);

    expect(await screen.findByText("运营内容校验失败，请重试。")).toBeInTheDocument();
    expect(screen.queryByLabelText("运营内容：账号诊断 · V1")).not.toBeInTheDocument();
  });

  it("refreshes the exact source Artifact when its refresh key changes", async () => {
    const callsBefore = vi.mocked(getArtifact).mock.calls.length;
    vi.mocked(getArtifact)
      .mockResolvedValueOnce(artifact)
      .mockResolvedValueOnce({ ...artifact, status: "accepted" });
    const view = render(<TurnStream thread={thread} artifactRefreshKey={0} />);

    const source = await screen.findByLabelText("运营内容：账号诊断 · V1");
    view.rerender(<TurnStream thread={thread} artifactRefreshKey={1} />);

    await waitFor(() => expect(getArtifact).toHaveBeenCalledTimes(callsBefore + 2));
    expect(vi.mocked(getArtifact).mock.calls.slice(callsBefore)).toEqual([[5001], [5001]]);
    expect(within(source).getByText("已完成")).toBeInTheDocument();
  });

  it("renders verified persisted V1 and V2 in the same work-turn", async () => {
    const revision: Artifact = {
      ...artifact,
      id: 5002,
      version: 2,
      title: "账号体检报告（修订版）",
      status: "ready_for_review",
      summary: "已补充转化数据。",
    };
    render(<TurnStream thread={thread} revisionArtifacts={{ 5001: [revision] }} />);

    expect(await screen.findByLabelText("运营内容：账号诊断 · V1")).toBeInTheDocument();
    expect(screen.getByLabelText("运营内容：账号诊断 · V2")).toBeInTheDocument();
    expect(screen.getByText("修订后的最新版本 V2")).toBeInTheDocument();
  });

  it("fails closed for a revision whose persisted identity leaves the source chain", async () => {
    render(<TurnStream thread={thread} revisionArtifacts={{
      5001: [{ ...artifact, id: 5002, version: 2, account_id: 4, title: "不可信修订版" }],
    }} />);

    await screen.findByLabelText("运营内容：账号诊断 · V1");
    expect(screen.getByText("修订版本校验失败，请重试。")).toBeInTheDocument();
  });

  it("preserves verified V1 and V2 with the authoritative source override after a refresh fails", async () => {
    const revision: Artifact = {
      ...artifact,
      id: 5002,
      version: 2,
      title: "账号体检报告（修订版）",
      status: "ready_for_review",
    };
    const sourceOverride: Artifact = { ...artifact, status: "superseded" };
    vi.mocked(getArtifact)
      .mockResolvedValueOnce(artifact)
      .mockRejectedValueOnce(new Error("network"));
    const view = render(
      <TurnStream
        thread={thread}
        revisionArtifacts={{ 5001: [revision] }}
        sourceArtifactOverrides={{ 5001: sourceOverride }}
        artifactRefreshKey={0}
      />,
    );

    expect(await screen.findByLabelText("运营内容：账号诊断 · V2")).toBeInTheDocument();
    view.rerender(
      <TurnStream
        thread={thread}
        revisionArtifacts={{ 5001: [revision] }}
        sourceArtifactOverrides={{ 5001: sourceOverride }}
        artifactRefreshKey={1}
      />,
    );

    expect(await screen.findByText("运营内容更新失败，已保留已验证版本。")).toBeInTheDocument();
    const v1 = screen.getByLabelText("运营内容：账号诊断 · V1");
    expect(within(v1).getByText("已完成")).toBeInTheDocument();
    expect(screen.getByLabelText("运营内容：账号诊断 · V2")).toBeInTheDocument();
  });

  it("routes an approval from its source work turn through the supplied business callback", () => {
    const onApprove = vi.fn();
    render(<TurnStream thread={{
      ...thread,
      turns: [{
        ...thread.turns[0],
        projections: [{
          type: "approval",
          turn_id: 101,
          approval: {
            id: 9001,
            task_id: 21,
            tool_name: "Review action",
            tool_code: "review",
            status: "waiting_approval",
            permission_mode: "confirm",
            requires_human_confirmation: true,
          },
        }],
      }],
    }} onApprove={onApprove} />);

    fireEvent.click(screen.getByRole("button", { name: /允\s*许/ }));
    expect(onApprove).toHaveBeenCalledWith(expect.objectContaining({ id: 9001 }), true);
  });

  it("renders the canonical pending interrupt instead of a legacy approval projection", () => {
    const onResolveInterrupt = vi.fn();
    render(<TurnStream thread={{
      ...thread,
      turns: [{
        ...thread.turns[0],
        status: "waiting_permission",
        pending_interrupt: {
          id: 71,
          account_id: 3,
          thread_id: 81,
          turn_id: 101,
          run_id: 31,
          kind: "approval",
          status: "pending",
          public_message: "Publish the approved draft now?",
          action_label: "Publish",
          response_schema: {},
          version: 2,
          resolved_at: null,
          created_at: "2026-08-04T00:00:00Z",
          updated_at: "2026-08-04T00:00:00Z",
        },
        projections: [{
          type: "approval",
          turn_id: 101,
          approval: {
            id: 9001,
            task_id: 21,
            tool_name: "Legacy approval",
            tool_code: "legacy",
            status: "waiting_approval",
            permission_mode: "confirm",
            requires_human_confirmation: true,
          },
        }],
      }],
    }} onResolveInterrupt={onResolveInterrupt} />);

    expect(screen.getByText("Publish the approved draft now?")).toBeVisible();
    expect(screen.queryByText(/Legacy approval/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    expect(onResolveInterrupt).toHaveBeenCalledWith(
      expect.objectContaining({ id: 71, version: 2 }),
      { approved: true },
    );
  });

  it("keeps a pending interrupt prompt visible when the action handler is unavailable", () => {
    render(<TurnStream thread={{
      ...thread,
      turns: [{
        ...thread.turns[0],
        status: "waiting_permission",
        pending_interrupt: {
          id: 73,
          account_id: 3,
          thread_id: 81,
          turn_id: 101,
          run_id: 31,
          kind: "approval",
          status: "pending",
          public_message: "请确认是否发布这份内容。",
          action_label: "确认发布",
          response_schema: {},
          version: 1,
          resolved_at: null,
          created_at: "2026-08-04T00:00:00Z",
          updated_at: "2026-08-04T00:00:00Z",
        },
      }],
    }} />);

    expect(screen.getByText("请确认是否发布这份内容。")).toBeVisible();
  });

  it("submits a clarification answer through its canonical interrupt callback", () => {
    const onResolveInterrupt = vi.fn();
    render(<TurnStream thread={{
      ...thread,
      turns: [{
        ...thread.turns[0],
        status: "waiting_user",
        pending_interrupt: {
          id: 72,
          account_id: 3,
          thread_id: 81,
          turn_id: 101,
          run_id: 31,
          kind: "clarification",
          status: "pending",
          public_message: "Who is the primary audience?",
          action_label: "Continue",
          response_schema: {},
          version: 1,
          resolved_at: null,
          created_at: "2026-08-04T00:00:00Z",
          updated_at: "2026-08-04T00:00:00Z",
        },
      }],
    }} onResolveInterrupt={onResolveInterrupt} />);

    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "New parents" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(onResolveInterrupt).toHaveBeenCalledWith(
      expect.objectContaining({ id: 72 }),
      { answer: "New parents" },
    );
  });

  it("keeps the server order and durable projection identity", () => {
    const { container } = render(<TurnStream thread={{
      ...thread,
      turns: [
        thread.turns[0],
        { ...thread.turns[0], id: 102, client_message_id: "turn-102", projections: [] },
        { ...thread.turns[0], id: 103, client_message_id: "turn-103", projections: [{
          type: "progress", turn_id: 103, skill_run_id: 4002, stages: [],
        }] },
      ],
    }} />);

    expect([...container.querySelectorAll<HTMLElement>("[data-turn-id]")].map((node) => node.dataset.turnId))
      .toEqual(["101", "102", "103"]);
    expect(screen.getByTestId("projection-artifact-5001")).toHaveAttribute("data-projection-key", "artifact-5001");
  });

  it("keeps an unknown projection out of business UI and exposes only a sanitized technical detail", () => {
    const unknownProjection = {
      type: "future_event<script>",
      turn_id: 101,
    } as unknown as TurnProjection;
    render(<TurnStream thread={{
      ...thread,
      turns: [{ ...thread.turns[0], projections: [unknownProjection] }],
    }} />);

    expect(screen.queryByText(/请刷新后查看/)).not.toBeInTheDocument();
    expect(screen.queryByText(/未识别事件/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看分析过程" }));
    expect(screen.queryByText(/未识别事件/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "技术详情" }));
    expect(screen.getByText("未识别事件：future_eventscript")).toBeVisible();
    expect(screen.queryByText(/<script>/)).not.toBeInTheDocument();
  });
});
