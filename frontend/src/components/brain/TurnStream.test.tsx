// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ConversationThread } from "../../types";
import { TurnStream } from "./TurnStream";

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

  it("keeps an Artifact in its source Turn when later greetings and retries arrive", () => {
    render(<TurnStream thread={thread} />);

    const sourceTurn = screen.getByTestId("conversation-turn-101");
    const greetingTurn = screen.getByTestId("conversation-turn-102");
    const retryTurn = screen.getByTestId("conversation-turn-103");

    expect(within(sourceTurn).getByLabelText("Artifact: Account Inspection Report")).toBeInTheDocument();
    expect(greetingTurn).not.toHaveTextContent("Account Inspection Report");
    expect(retryTurn).not.toHaveTextContent("Account Inspection Report");
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

    expect(screen.getByText("An update is available for this turn.")).toBeInTheDocument();
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
