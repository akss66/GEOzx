// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getArtifact } from "../../api/brain";
import type { Artifact, ConversationThread } from "../../types";
import { TurnStream } from "./TurnStream";

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
    fireEvent.click(screen.getByText("查看过程"));
    expect(within(screen.getByLabelText("调用专家摘要")).getByText(/账号定位专家/)).toBeVisible();
    expect(screen.getByText("已使用 账号数据上下文")).toBeVisible();
    expect(screen.queryByText("Tool #8001 · account.data_context · completed")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("技术日志"));
    expect(screen.getByText("Tool #8001 · account.data_context · completed")).toBeVisible();
    expect(screen.getByText("Agent Run：3002")).toBeVisible();
  });

  it("keeps the exact persisted Artifact in its source work turn", async () => {
    render(<TurnStream thread={thread} />);

    await waitFor(() => expect(getArtifact).toHaveBeenCalledWith(5001));
    expect(await screen.findByLabelText("运营内容：账号诊断 · V1")).toBeInTheDocument();
  });

  it("fails closed when the fetched Artifact does not match its source account or turn", async () => {
    vi.mocked(getArtifact).mockResolvedValue({ ...artifact, account_id: 4, turn_id: 102 });
    render(<TurnStream thread={thread} />);

    expect(await screen.findByText("运营内容校验失败，请重试。")).toBeInTheDocument();
    expect(screen.queryByLabelText("运营内容：账号诊断 · V1")).not.toBeInTheDocument();
  });

  it("fails closed for a revision whose persisted identity leaves the source chain", async () => {
    render(<TurnStream thread={thread} revisionArtifacts={{
      5001: [{ ...artifact, id: 5002, version: 2, account_id: 4, title: "不可信修订版" }],
    }} />);

    await screen.findByLabelText("运营内容：账号诊断 · V1");
    expect(screen.getByText("修订版本校验失败，请重试。")).toBeInTheDocument();
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
});
