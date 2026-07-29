// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getArtifact } from "../../api/brain";
import type { Artifact, ConversationThread } from "../../types";
import { TurnStream } from "./TurnStream";

vi.mock("../../api/brain", () => ({
  getArtifact: vi.fn(),
}));

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
  turns: [
    {
      id: 101,
      thread_id: 81,
      org_id: 1,
      created_by_id: 7,
      client_message_id: "turn-101",
      user_input: "Inspect my account",
      assistant_response: "Inspection has started.",
      intent: { mode: "SKILL", status: "completed" },
      projections: [
        {
          type: "artifact",
          turn_id: 101,
          artifact_id: 5001,
          artifact_type: "account_inspection_report",
          skill_run_id: 4001,
          account_id: 3,
          report: {
            summary: "The account has enough data for a useful review.",
            recommendations: ["Publish two comparison posts."],
          },
        },
      ],
      created_at: "2026-07-28T00:00:00Z",
      updated_at: "2026-07-28T00:00:00Z",
    },
    {
      id: 102,
      thread_id: 81,
      org_id: 1,
      created_by_id: 7,
      client_message_id: "turn-102",
      user_input: "Hello again",
      assistant_response: "Hello! What would you like to do next?",
      intent: { mode: "ANSWER" },
      projections: [],
      created_at: "2026-07-28T00:01:00Z",
      updated_at: "2026-07-28T00:01:00Z",
    },
    {
      id: 103,
      thread_id: 81,
      org_id: 1,
      created_by_id: 7,
      client_message_id: "turn-103",
      user_input: "Retry the review",
      assistant_response: "Retry is queued.",
      intent: { mode: "SKILL", status: "running" },
      projections: [
        {
          type: "progress",
          turn_id: 103,
          skill_run_id: 4002,
          stages: [{ code: "review", name: "Review account data", status: "running" }],
        },
      ],
      created_at: "2026-07-28T00:02:00Z",
      updated_at: "2026-07-28T00:02:00Z",
    },
  ],
};

describe("TurnStream", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.mocked(getArtifact).mockResolvedValue(artifact);
  });

  it("uses the established chat anatomy for persisted user and operations-brain messages", () => {
    render(<TurnStream thread={thread} />);

    const sourceTurn = screen.getByTestId("conversation-turn-101");
    const userMessage = within(sourceTurn).getByLabelText("User message");
    const assistantMessage = within(sourceTurn).getByLabelText("Assistant response");

    expect(userMessage).toHaveClass("dy-chat-message", "dy-chat-message-user");
    expect(userMessage.querySelector(".dy-chat-bubble")).not.toBeNull();
    expect(assistantMessage).toHaveClass("dy-chat-message", "dy-chat-message-agent");
    expect(within(assistantMessage).getByRole("img", { name: "运营大脑" })).toBeInTheDocument();
    expect(within(assistantMessage).getByText("运营大脑")).toBeInTheDocument();
    expect(assistantMessage.querySelector(".dy-chat-bubble")).not.toBeNull();
  });

  it("keeps the exact persisted Artifact in its source Turn when later greetings and retries arrive", async () => {
    render(<TurnStream thread={thread} />);

    const sourceTurn = await screen.findByTestId("conversation-turn-101");
    const greetingTurn = screen.getByTestId("conversation-turn-102");
    const retryTurn = screen.getByTestId("conversation-turn-103");

    await waitFor(() => expect(getArtifact).toHaveBeenCalledWith(5001));
    expect(within(sourceTurn).getByLabelText("Artifact: 账号体检报告")).toBeInTheDocument();
    expect(greetingTurn).not.toHaveTextContent("账号体检报告");
    expect(retryTurn).not.toHaveTextContent("账号体检报告");
  });

  it("fails closed when the fetched Artifact does not match the source account or Turn", async () => {
    vi.mocked(getArtifact).mockResolvedValue({ ...artifact, account_id: 4, turn_id: 102 });

    render(<TurnStream thread={thread} />);

    expect(await screen.findByText("成果校验失败，请重试。")) .toBeInTheDocument();
    expect(screen.queryByText("账号体检报告")).not.toBeInTheDocument();
  });

  it("rejects a projection account that differs from the active Thread account", async () => {
    vi.mocked(getArtifact).mockResolvedValue({ ...artifact, account_id: 4 });
    const mismatchedProjectionThread: ConversationThread = {
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
    };

    render(<TurnStream thread={mismatchedProjectionThread} />);

    expect(await screen.findByText("成果校验失败，请重试。")).toBeInTheDocument();
    expect(screen.queryByText("账号体检报告")).not.toBeInTheDocument();
  });

  it("refreshes the exact Artifact after a completed business action", async () => {
    const callsBefore = vi.mocked(getArtifact).mock.calls.length;
    vi.mocked(getArtifact)
      .mockResolvedValueOnce(artifact)
      .mockResolvedValueOnce({ ...artifact, title: "已采用的账号体检报告", status: "accepted" });
    const view = render(<TurnStream thread={thread} artifactRefreshKey={0} />);

    await screen.findByText("账号体检报告");
    view.rerender(<TurnStream thread={thread} artifactRefreshKey={1} />);

    expect(await screen.findByText("已采用的账号体检报告")).toBeInTheDocument();
    expect(getArtifact).toHaveBeenCalledTimes(callsBefore + 2);
  });

  it("keeps source V1 readable and presents the persisted V2 returned by revision", async () => {
    const revisedArtifact: Artifact = {
      ...artifact,
      id: 5002,
      title: "账号体检报告（修订版）",
      version: 2,
      status: "ready_for_review",
      summary: "已补充转化数据。",
    };

    render(
      <TurnStream
        thread={thread}
        revisionArtifacts={{ 5001: [revisedArtifact] }}
      />,
    );

    expect(await screen.findByLabelText("Artifact: 账号体检报告")).toBeInTheDocument();
    expect(screen.getByLabelText("Artifact: 账号体检报告（修订版）")).toBeInTheDocument();
    expect(screen.getByText("修订后的最新版本 V2")).toBeInTheDocument();
  });

  it("fails closed for a returned revision whose persisted identity does not match the source chain", async () => {
    render(
      <TurnStream
        thread={thread}
        revisionArtifacts={{
          5001: [{ ...artifact, id: 5002, version: 2, account_id: 4, title: "不可信修订版" }],
        }}
      />,
    );

    expect(await screen.findByLabelText("Artifact: 账号体检报告")).toBeInTheDocument();
    expect(screen.getByText("修订版本校验失败，请重试。")) .toBeInTheDocument();
    expect(screen.queryByLabelText("Artifact: 不可信修订版")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重\s*试/ })).toBeInTheDocument();
  });

  it("preserves verified V1 and V2 when a later source refresh fails", async () => {
    const revision: Artifact = {
      ...artifact,
      id: 5002,
      title: "账号体检报告（修订版）",
      version: 2,
      status: "ready_for_review",
    };
    vi.mocked(getArtifact)
      .mockResolvedValueOnce(artifact)
      .mockRejectedValueOnce(new Error("network"));
    const sourceOverride: Artifact = { ...artifact, status: "superseded" };
    const view = render(
      <TurnStream
        thread={thread}
        revisionArtifacts={{ 5001: [revision] }}
        sourceArtifactOverrides={{ 5001: sourceOverride }}
        artifactRefreshKey={0}
      />,
    );

    expect(await screen.findByLabelText("Artifact: 账号体检报告（修订版）")).toBeInTheDocument();
    view.rerender(
      <TurnStream
        thread={thread}
        revisionArtifacts={{ 5001: [revision] }}
        sourceArtifactOverrides={{ 5001: sourceOverride }}
        artifactRefreshKey={1}
      />,
    );

    expect(await screen.findByText("成果更新失败，已保留已验证版本。")) .toBeInTheDocument();
    const v1Card = screen.getByLabelText("Artifact: 账号体检报告");
    expect(within(v1Card).getByText("已更新")).toBeInTheDocument();
    expect(within(v1Card).queryByRole("button", { name: "仅采用报告" })).not.toBeInTheDocument();
    expect(within(v1Card).queryByRole("button", { name: "提出修改" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Artifact: 账号体检报告（修订版）")).toBeInTheDocument();
  });

  it("uses the server Turn order and durable IDs for Turn and projection identity", () => {
    const { container } = render(<TurnStream thread={thread} />);

    expect(
      [...container.querySelectorAll<HTMLElement>("[data-turn-id]")].map((node) => node.dataset.turnId),
    ).toEqual(["101", "102", "103"]);
    expect(screen.getByTestId("conversation-turn-101")).toHaveAttribute("data-turn-key", "turn-101");
    expect(screen.getByTestId("projection-artifact-5001")).toHaveAttribute(
      "data-projection-key",
      "artifact-5001",
    );
    expect(screen.getByTestId("projection-progress-4002")).toHaveAttribute(
      "data-projection-key",
      "progress-4002",
    );
  });

  it("keeps technical Turn and route metadata behind an explicit disclosure", () => {
    render(<TurnStream thread={thread} />);

    const details = screen.getAllByText("技术日志")[0].closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(within(details as HTMLElement).getByText("消息编号：101")).not.toBeVisible();
    expect(within(details as HTMLElement).getByText("路由：SKILL")).not.toBeVisible();
    fireEvent.click(within(details as HTMLElement).getByText("技术日志"));

    expect(within(details as HTMLElement).getByText("消息编号：101")).toBeVisible();
    expect(within(details as HTMLElement).getByText("路由：SKILL")).toBeVisible();
  });

  it("renders an unknown projection as a compact safe fallback without raw payload data", () => {
    const unknownThread: ConversationThread = {
      ...thread,
      turns: [{
        ...thread.turns[0],
        projections: [{
          type: "future_projection",
          turn_id: 101,
          provider_trace: "Traceback: secret-token",
          nested: { raw: "must never render" },
        } as never],
      }],
    };

    render(<TurnStream thread={unknownThread} />);

    expect(screen.getByText("本轮有一条新进展，请刷新后查看。")).toBeInTheDocument();
    expect(screen.queryByText("future_projection")).not.toBeInTheDocument();
    expect(screen.queryByText("Traceback: secret-token")).not.toBeInTheDocument();
    expect(screen.queryByText("must never render")).not.toBeInTheDocument();
  });

  it("routes an approval from its source Turn through the supplied business callback", () => {
    const onApprove = vi.fn();
    const approvalThread: ConversationThread = {
      ...thread,
      turns: [{
        ...thread.turns[0],
        projections: [{
          type: "approval",
          turn_id: 101,
          approval: { id: 9001, tool_name: "Review action", tool_code: "review" },
        } as never],
      }],
    };

    render(<TurnStream thread={approvalThread} onApprove={onApprove} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(onApprove).toHaveBeenCalledWith(expect.objectContaining({ id: 9001 }), true);
  });
});
