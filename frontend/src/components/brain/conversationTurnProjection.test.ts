import { describe, expect, it } from "vitest";

import type { ConversationThread } from "../../types";
import { upsertTurnByClientMessageId } from "../../stores/brainConversation";
import {
  applyConversationEvent,
  appendOptimisticTurn,
  isActiveConversationTurnStatus,
  mergeConversationTurn,
  reconcileConversationThread,
  turnDomainKey,
  turnReactKey,
} from "./conversationTurnProjection";

const thread: ConversationThread = {
  id: 81,
  org_id: 1,
  created_by_id: 7,
  client_id: null,
  project_id: null,
  account_id: 3,
  title: "Account conversation",
  turns: [{
    id: null,
    thread_id: 81,
    org_id: 1,
    created_by_id: 7,
    client_message_id: "client-1",
    user_input: "Inspect my account",
    assistant_response: null,
    intent: null,
    status: "queued",
    projections: [],
    created_at: "2026-07-30T00:00:00Z",
    updated_at: "2026-07-30T00:00:00Z",
  }],
  created_at: "2026-07-30T00:00:00Z",
  updated_at: "2026-07-30T00:00:00Z",
};

function frame(
  type: string,
  streamSeq: number,
  extra: Record<string, unknown> = {},
) {
  return {
    id: 41,
    type,
    payload: {
      thread_id: 81,
      turn_id: 101,
      client_message_id: "client-1",
      message_id: "client-1:00-decision:1",
      stream_seq: streamSeq,
      ...extra,
    },
  };
}

describe("conversation Turn projection", () => {
  it("upserts a server Turn through the optimistic client identity", () => {
    const result = upsertTurnByClientMessageId(
      thread,
      81,
      "client-1",
      (current) => ({
        ...current!,
        id: 101,
        status: "running",
      }),
    );

    expect(result.turns).toHaveLength(1);
    expect(result.turns[0]).toMatchObject({
      id: 101,
      client_message_id: "client-1",
      status: "running",
    });
  });

  it("adds one optimistic Turn and reuses it for the same client message", () => {
    const first = appendOptimisticTurn(thread, "client-2", "Plan next week");
    const repeated = appendOptimisticTurn(first, "client-2", "Plan next week");

    expect(first.turns).toHaveLength(2);
    expect(repeated.turns).toHaveLength(2);
    expect(repeated.turns[1]).toMatchObject({
      id: null,
      thread_id: 81,
      client_message_id: "client-2",
      user_input: "Plan next week",
      status: "queued",
    });
  });

  it("keeps one Turn and a stable React key when HTTP binds the server id", () => {
    const merged = mergeConversationTurn(thread, {
      ...thread.turns[0],
      id: 101,
      status: "running",
    });

    expect(merged.turns).toHaveLength(1);
    expect(merged.turns[0].id).toBe(101);
    expect(turnReactKey({ threadId: 81, turnId: null, clientMessageId: "client-1" }))
      .toBe(turnReactKey({ threadId: 81, turnId: 101, clientMessageId: "client-1" }));
    expect(turnDomainKey({ threadId: 81, turnId: 101, clientMessageId: "client-1" }))
      .toBe("81:101:client-1");
  });

  it("keeps a fast terminal stream result when the queued HTTP response arrives later", () => {
    const done = applyConversationEvent(
      thread,
      frame("brain.runtime.message_done", 3, { content: "流式最终答案" }),
    );

    const merged = mergeConversationTurn(done, {
      ...thread.turns[0],
      id: 101,
      status: "queued",
    });

    expect(merged.turns[0]).toMatchObject({
      id: 101,
      assistant_response: "流式最终答案",
      status: "completed",
      stream_state: {
        lastSequence: 3,
        terminal: true,
      },
    });
  });

  it("reconciles a stale GET without discarding the newer streamed overlay", () => {
    const streamed = applyConversationEvent(
      thread,
      frame("brain.runtime.message_delta", 7, { delta: "newer streamed token" }),
    );
    const reconciled = reconcileConversationThread(streamed, {
      ...thread,
      turns: [{ ...thread.turns[0], id: 101, status: "running" }],
    });

    expect(reconciled.turns[0]).toMatchObject({
      id: 101,
      client_message_id: "client-1",
      assistant_response: "newer streamed token",
      stream_state: { lastSequence: 7 },
    });
  });

  it("lets a durable terminal snapshot win while retaining the stable client identity", () => {
    const streamed = applyConversationEvent(
      thread,
      frame("brain.runtime.message_delta", 7, { delta: "partial" }),
    );
    const reconciled = reconcileConversationThread(streamed, {
      ...thread,
      turns: [{
        ...thread.turns[0],
        id: 101,
        client_message_id: null,
        status: "completed",
        assistant_response: "durable final",
      }],
    });

    expect(reconciled.turns[0]).toMatchObject({
      id: 101,
      client_message_id: "client-1",
      status: "completed",
      assistant_response: "durable final",
    });
  });

  it.each([
    "claimed",
    "waiting_predecessor",
    "queued",
    "running",
    "retry_wait",
  ])("treats %s as an active durable Turn status", (status) => {
    expect(isActiveConversationTurnStatus(status)).toBe(true);
  });

  it.each([
    "waiting_permission",
    "completed",
    "blocked",
    "failed",
    "stopped",
  ])("does not treat %s as an active durable Turn status", (status) => {
    expect(isActiveConversationTurnStatus(status)).toBe(false);
  });

  it("applies duplicate text deltas in sequence and lets done replace the overlay", () => {
    const started = applyConversationEvent(thread, frame("brain.runtime.message_start", 0));
    const first = applyConversationEvent(
      started,
      frame("brain.runtime.message_delta", 1, { delta: "好" }),
    );
    const second = applyConversationEvent(
      first,
      frame("brain.runtime.message_delta", 2, { delta: "好" }),
    );
    const done = applyConversationEvent(
      second,
      frame("brain.runtime.message_done", 3, { content: "好好，完成" }),
    );

    expect(first.turns[0].assistant_response).toBe("好");
    expect(second.turns[0].assistant_response).toBe("好好");
    expect(done.turns[0].assistant_response).toBe("好好，完成");
    expect(done.turns[0].status).toBe("completed");
  });

  it("replaces the current public phase instead of stacking status messages", () => {
    const reading = applyConversationEvent(
      thread,
      frame("brain.runtime.tool_started", 0, { turn_phase: "reading_data" }),
    );
    const consulting = applyConversationEvent(
      reading,
      frame("brain.runtime.subagent_started", 1, {
        turn_phase: "consulting_experts",
        agent_code: "01-positioning",
      }),
    );

    expect(reading.turns[0].turn_phase).toBe("reading_data");
    expect(consulting.turns[0].turn_phase).toBe("consulting_experts");
  });

  it("does not clear a delta when a late start arrives", () => {
    const delta = applyConversationEvent(
      thread,
      frame("brain.runtime.message_delta", 1, { delta: "已输出" }),
    );
    const lateStart = applyConversationEvent(delta, frame("brain.runtime.message_start", 0));

    expect(lateStart.turns[0].assistant_response).toBe("已输出");
  });

  it("accepts the same sequence when a new streamed message begins", () => {
    const first = applyConversationEvent(
      thread,
      frame("brain.runtime.message_delta", 1, { delta: "first" }),
    );
    const second = applyConversationEvent(
      first,
      frame("brain.runtime.message_delta", 1, {
        message_id: "client-1:00-decision:2",
        delta: " second",
      }),
    );

    expect(second.turns[0]).toMatchObject({
      assistant_response: "first second",
      stream_state: {
        messageId: "client-1:00-decision:2",
        lastSequence: 1,
      },
    });
  });

  it("completes without start and ignores deltas after done", () => {
    const done = applyConversationEvent(
      thread,
      frame("brain.runtime.message_done", 3, { content: "完整答案" }),
    );
    const lateDelta = applyConversationEvent(
      done,
      frame("brain.runtime.message_delta", 4, { delta: "不应追加" }),
    );

    expect(done.turns[0].assistant_response).toBe("完整答案");
    expect(lateDelta.turns[0].assistant_response).toBe("完整答案");
  });

  it.each([
    ["another Thread", { thread_id: 82 }],
    ["another Turn", { turn_id: 102 }],
    ["another client", { client_message_id: "client-2" }],
  ])("ignores %s frames", (_label, mismatch) => {
    const result = applyConversationEvent(
      { ...thread, turns: [{ ...thread.turns[0], id: 101 }] },
      frame("brain.runtime.message_delta", 1, { delta: "污染", ...mismatch }),
    );

    expect(result.turns[0].assistant_response).toBeNull();
  });
});
