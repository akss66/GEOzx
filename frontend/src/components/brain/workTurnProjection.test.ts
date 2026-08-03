import { describe, expect, it } from "vitest";

import type { ConversationTurn, TurnProjection } from "../../types";
import { projectWorkTurn } from "./workTurnProjection";

function turn(overrides: Partial<ConversationTurn> = {}): ConversationTurn {
  return {
    id: 101,
    thread_id: 81,
    org_id: 7,
    created_by_id: 3,
    client_message_id: "client-turn-1",
    user_input: "Review this account",
    assistant_response: null,
    intent: null,
    status: "completed",
    projections: [],
    created_at: "2026-08-04T00:00:00Z",
    updated_at: "2026-08-04T00:00:00Z",
    ...overrides,
  };
}

const executionSummary: Extract<TurnProjection, { type: "execution_summary" }> = {
  type: "execution_summary",
  turn_id: 101,
  run_id: 4,
  skill_code: "account_review",
  skill_run_id: 5,
  status: "completed",
  quality_score: null,
  experts: [{
    id: 6,
    agent_code: "01-positioning",
    agent_name: "Positioning expert",
    status: "done",
  }],
  tools: [],
  artifact_ids: [88],
};

describe("projectWorkTurn", () => {
  it("projects history, optimistic, streaming, and completed Turns into one worker model", () => {
    const historyTurn = turn({
      projections: [{
        type: "progress",
        turn_id: 101,
        skill_run_id: 5,
        stages: [{ code: "analyse", name: "Analyse account", status: "done" }],
      }],
    });
    const optimisticTurn = turn({
      id: null,
      status: "queued",
      assistant_response: null,
      projections: [],
    });
    const streamingTurn = turn({
      status: "running",
      assistant_response: "I am checking the account.",
      projections: [executionSummary],
    });
    const completedTurn = turn({
      assistant_response: "The review is ready.",
      projections: [
        executionSummary,
        {
          type: "artifact",
          artifact_id: 88,
          artifact_type: "account_inspection_report",
          skill_run_id: 5,
          account_id: 11,
          turn_id: 101,
        },
      ],
    });

    for (const source of [historyTurn, optimisticTurn, streamingTurn, completedTurn]) {
      expect(projectWorkTurn(source)).toMatchObject({
        userMessage: "Review this account",
        assistant: { identity: "运营大脑", steps: expect.any(Array) },
      });
    }

    expect(projectWorkTurn(historyTurn).steps).toEqual([{
      code: "analyse",
      label: "Analyse account",
      state: "done",
    }]);
    expect(projectWorkTurn(streamingTurn)).toMatchObject({
      status: "working",
      assistantText: "I am checking the account.",
      experts: [{ name: "Positioning expert", status: "done" }],
      deliverableIds: [88],
    });
    expect(projectWorkTurn(completedTurn).deliverableIds).toEqual([88]);
  });

  it("uses an organization and thread scoped stable key while the server id changes", () => {
    const optimistic = projectWorkTurn(turn({ id: null, status: "queued" }));
    const persisted = projectWorkTurn(turn({ id: 101, status: "running" }));
    const anotherThread = projectWorkTurn(turn({ thread_id: 82 }));

    expect(optimistic.key).toBe(persisted.key);
    expect(persisted.key).toContain("org:7:thread:81");
    expect(anotherThread.key).not.toBe(persisted.key);
  });

  it("lets terminal and waiting phases determine the public worker status", () => {
    expect(projectWorkTurn(turn({ status: "running", turn_phase: "waiting_approval" })).status)
      .toBe("waiting_user");
    expect(projectWorkTurn(turn({ status: "running", turn_phase: "failed" })).status)
      .toBe("failed");
    expect(projectWorkTurn(turn({
      status: "running",
      turn_phase: "completed",
      projections: [{
        type: "progress",
        turn_id: 101,
        skill_run_id: 5,
        stages: [{ code: "analyse", name: "Analyse account", status: "running" }],
      }],
    })).currentActivity).toBeNull();
  });

  it.each([
    ["blocked", "blocked"],
    ["cancelled", "cancelled"],
    ["stopped", "cancelled"],
    ["dead_letter", "failed"],
    ["failed", "failed"],
    ["completed", "completed"],
  ] as const)("lets persisted %s override a stale waiting approval phase", (status, expected) => {
    expect(projectWorkTurn(turn({
      status,
      turn_phase: "waiting_approval",
    }))).toMatchObject({
      status: expected,
      currentActivity: null,
    });
  });

  it("clears current activity when a blocked Turn retains its reading phase", () => {
    expect(projectWorkTurn(turn({
      status: "blocked",
      turn_phase: "reading_data",
    })).currentActivity).toBeNull();
  });

  it.each(["blocked", "cancelled", "stopped", "dead_letter"])(
    "marks a terminal execution summary with %s as failed",
    (status) => {
      expect(projectWorkTurn(turn({
        status: "failed",
        projections: [{ ...executionSummary, status }],
      })).steps).toEqual([{
        code: "account_review",
        label: "account_review",
        state: "failed",
      }]);
    },
  );
});
