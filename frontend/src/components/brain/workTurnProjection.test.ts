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

const wechatExecutionSummary: Extract<TurnProjection, { type: "execution_summary" }> = {
  type: "execution_summary",
  turn_id: 101,
  run_id: 9,
  skill_code: "wechat_article_production",
  skill_run_id: 10,
  status: "waiting_user",
  quality_score: null,
  experts: [],
  tools: [],
};

const wechatWorkspaceProjection = (
  overrides: Partial<Extract<TurnProjection, { type: "wechat_article_workspace" }>> = {},
): Extract<TurnProjection, { type: "wechat_article_workspace" }> => ({
  type: "wechat_article_workspace",
  turn_id: 101,
  skill_run_id: 10,
  account_id: 11,
  article_id: 42,
  article_version_id: 4101,
  status: "waiting_user",
  current_action: "produce",
  available_actions: ["generate_images", "sync_draft"],
  ...overrides,
});

describe("projectWorkTurn", () => {
  it("uses workspace current_action for downstream WeChat activity and keeps unknown runtime steps generic", () => {
    const model = projectWorkTurn(turn({
      status: "running",
      projections: [
        wechatExecutionSummary,
        wechatWorkspaceProjection({ current_action: "generate_images" }),
      ],
      runtime_overlay: {
        lastEventId: 11,
        lastSequence: 11,
        steps: {
          brief_resolution: { state: "done", attempt: 1 },
          unknown_internal_key: { state: "waiting", attempt: 7 },
        },
        deliverableIds: [],
      },
    }), { threadAccountId: 11 });

    expect(model.currentActivity).toBe("正在生成所选图片");
    expect(model.steps).toEqual(expect.arrayContaining([
      { code: "brief_resolution", label: "正在确认文章目标", state: "done" },
      { code: "unknown_internal_key", label: "执行任务", state: "waiting" },
    ]));
  });

  it.each([
    ["clarification", "needs_input"],
    ["approval", "needs_approval"],
    ["manual_pause", "paused"],
  ] as const)("projects a pending %s interrupt without flattening it", (kind, expected) => {
    const model = projectWorkTurn(turn({
      status: "waiting_permission",
      turn_phase: "waiting_approval",
      pending_interrupt: {
        id: 71,
        account_id: 11,
        thread_id: 81,
        turn_id: 101,
        run_id: 4,
        kind,
        status: "pending",
        public_message: "Please confirm the next step.",
        action_label: "Continue",
        response_schema: {},
        version: 1,
        resolved_at: null,
        created_at: "2026-08-04T00:00:00Z",
        updated_at: "2026-08-04T00:00:00Z",
      },
    }), { threadAccountId: 11 });

    expect(model).toMatchObject({
      status: expected,
      currentActivity: "Please confirm the next step.",
    });
  });

  it("keeps a WeChat handoff in the same waiting WorkTurn and projects a workspace link only for a trusted account projection", () => {
    const model = projectWorkTurn(turn({
      status: "waiting_permission",
      turn_phase: "waiting_approval",
      assistant_response: "raw runtime response should not be the main handoff",
      projections: [
        wechatExecutionSummary,
        wechatWorkspaceProjection(),
      ],
      pending_interrupt: {
        id: 72,
        account_id: 11,
        thread_id: 81,
        turn_id: 101,
        run_id: 9,
        kind: "clarification",
        status: "pending",
        public_message: "Choose the next article action.",
        action_label: "Open article workspace",
        response_schema: {
          type: "object",
          required: ["action"],
          properties: {
            action: {
              type: "string",
              enum: ["generate_images", "sync_draft"],
            },
          },
        },
        version: 1,
        resolved_at: null,
        created_at: "2026-08-04T00:00:00Z",
        updated_at: "2026-08-04T00:00:00Z",
      },
    }));

    expect(model.status).toBe("waiting_user");
    expect(model.assistantText).toBe("文章初稿已生成");
    expect(model.articleWorkspaceAction).toEqual({
      articleId: 42,
      href: "/wechat-articles/42",
      label: "打开文章工作台",
      title: "文章初稿已生成",
    });
  });

  it("projects the WeChat workspace handoff from a dedicated workspace projection plus clarification schema only", () => {
    const model = projectWorkTurn(turn({
      status: "waiting_permission",
      turn_phase: "waiting_approval",
      assistant_response: "Article draft is ready.",
      projections: [
        wechatExecutionSummary,
        {
          type: "wechat_article_workspace",
          turn_id: 101,
          skill_run_id: 10,
          account_id: 11,
          article_id: 42,
          article_version_id: 4101,
          status: "waiting_user",
          current_action: "produce",
          available_actions: ["generate_images", "sync_draft"],
        } as unknown as TurnProjection,
      ],
      pending_interrupt: {
        id: 72,
        account_id: 11,
        thread_id: 81,
        turn_id: 101,
        run_id: 9,
        kind: "clarification",
        status: "pending",
        public_message: "Choose the next article action.",
        action_label: "Open article workspace",
        response_schema: {
          type: "object",
          required: ["action"],
          properties: {
            action: {
              type: "string",
              enum: ["generate_images", "sync_draft"],
            },
          },
        },
        version: 1,
        resolved_at: null,
        created_at: "2026-08-04T00:00:00Z",
        updated_at: "2026-08-04T00:00:00Z",
      },
    }));

    expect(model.status).toBe("waiting_user");
    expect(model.assistantText).toBe("文章初稿已生成");
    expect(model.articleWorkspaceAction).toEqual({
      articleId: 42,
      href: "/wechat-articles/42",
      label: "打开文章工作台",
      title: "文章初稿已生成",
    });
  });

  it("rejects cross-turn and invalid WeChat article ids so the handoff never exposes a workspace link", () => {
    const crossTurn = projectWorkTurn(turn({
      projections: [
        wechatExecutionSummary,
        wechatWorkspaceProjection({ turn_id: 999, article_id: 77 }),
      ],
    }));
    const invalidId = projectWorkTurn(turn({
      projections: [
        wechatExecutionSummary,
        wechatWorkspaceProjection({ article_id: 0 }),
      ],
    }));

    expect(crossTurn.articleWorkspaceAction).toBeNull();
    expect(invalidId.articleWorkspaceAction).toBeNull();
  });

  it("keeps the article workspace link for blocked WeChat recovery states without replacing truthful failure copy", () => {
    const recoveryCopy = "Image generation is not configured. Open the article workspace to upload or refine prompts.";
    const model = projectWorkTurn(turn({
      status: "blocked",
      assistant_response: recoveryCopy,
      projections: [
        {
          ...wechatExecutionSummary,
          status: "blocked",
        },
        wechatWorkspaceProjection({ current_action: "generate_images" }),
        {
          type: "execution_blocked",
          turn_id: 101,
          skill_run_id: 10,
          code: "WECHAT_IMAGE_GENERATION_UNAVAILABLE",
          recovery_action: recoveryCopy,
        },
      ],
    }));

    expect(model.articleWorkspaceAction?.articleId).toBe(42);
    expect(model.articleWorkspaceAction?.href).toBe("/wechat-articles/42");
    expect(model.assistantText).toBe(recoveryCopy);
  });

  it("keeps the article workspace link after handoff when the same turn continues into downstream WeChat work", () => {
    const model = projectWorkTurn(turn({
      status: "running",
      assistant_response: "Syncing the draft to the official account.",
      projections: [
        {
          ...wechatExecutionSummary,
          status: "running",
        },
        wechatWorkspaceProjection({ current_action: "sync_draft" }),
      ],
    }));

    expect(model.articleWorkspaceAction?.articleId).toBe(42);
    expect(model.articleWorkspaceAction?.href).toBe("/wechat-articles/42");
    expect(model.assistantText).toBe("Syncing the draft to the official account.");
  });

  it("keeps the article workspace link for draft reconciliation without replacing truthful conflict copy", () => {
    const conflictCopy = "The article changed after this run started. Open the article workspace to review the latest version before syncing again.";
    const model = projectWorkTurn(turn({
      status: "blocked",
      assistant_response: conflictCopy,
      projections: [
        {
          ...wechatExecutionSummary,
          status: "blocked",
        },
        wechatWorkspaceProjection({ current_action: "sync_draft" }),
        {
          type: "execution_blocked",
          turn_id: 101,
          skill_run_id: 10,
          code: "WECHAT_DRAFT_RECONCILIATION_REQUIRED",
          recovery_action: conflictCopy,
        },
      ],
    }));

    expect(model.articleWorkspaceAction?.articleId).toBe(42);
    expect(model.articleWorkspaceAction?.href).toBe("/wechat-articles/42");
    expect(model.assistantText).toBe(conflictCopy);
  });

  it("keeps the legacy waiting fallback only when a snapshot has no pending interrupt", () => {
    expect(projectWorkTurn(turn({
      status: "waiting_permission",
      turn_phase: "waiting_approval",
    })).status).toBe("waiting_user");
  });

  it.each([
    ["supplement", "已补充要求"],
    ["stop", "已请求停止"],
    ["replace_goal", "已换目标"],
  ] as const)("maps %s steering into a dedicated inline notice", (label, copy) => {
    const model = projectWorkTurn(turn({
      status: "running",
      assistant_response: "原有回复不应被替换",
      runtime_overlay: {
        lastEventId: 9,
        lastSequence: 9,
        steps: {},
        deliverableIds: [],
        steering_notice: {
          label,
          reason: "用户在执行中调整了要求",
        },
      },
    }));

    expect(model.steeringNotice).toEqual({
      label,
      copy,
      reason: "用户在执行中调整了要求",
    });
    expect(model.assistantText).toBe("原有回复不应被替换");
  });

  it("keeps a message-only steering explanation in the dedicated notice model", () => {
    const model = projectWorkTurn(turn({
      runtime_overlay: {
        lastEventId: 10,
        lastSequence: 10,
        steps: {},
        deliverableIds: [],
        steering_notice: {
          label: "supplement",
          message: "第一条不要讲价格",
        },
      },
    }));

    expect(model.steeringNotice).toEqual({
      label: "supplement",
      copy: "已补充要求",
      message: "第一条不要讲价格",
    });
  });

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
    expect(projectWorkTurn(turn({
      status: "running",
      turn_phase: "reading_data",
    }))).toMatchObject({
      status: "working",
      phase: "reading_data",
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

  it("overlays durable runtime steps and deliverables without discarding persisted projections", () => {
    const model = projectWorkTurn(turn({
      status: "running",
      projections: [executionSummary],
      runtime_overlay: {
        lastEventId: 9,
        lastSequence: 9,
        steps: {
          read_data: { state: "done", detail: "Account data read", attempt: 1 },
          unknown_internal_key: { state: "active", attempt: 7 },
        },
        deliverableIds: [99],
      },
    }));

    expect(model.steps).toEqual(expect.arrayContaining([
      { code: "read_data", label: "读取账号数据", state: "done", detail: "Account data read" },
      { code: "unknown_internal_key", label: "执行任务", state: "active" },
    ]));
    expect(model.deliverableIds).toEqual([88, 99]);
    expect(model.steps.find((step) => step.code === "unknown_internal_key")?.label)
      .not.toContain("unknown_internal_key");
  });

  it("maps durable WeChat stages into business language and falls back safely for unknown runtime steps", () => {
    const active = projectWorkTurn(turn({
      status: "running",
      projections: [wechatExecutionSummary],
      runtime_overlay: {
        lastEventId: 9,
        lastSequence: 9,
        steps: {
          brief_resolution: { state: "active", attempt: 1 },
          unknown_internal_key: { state: "waiting", attempt: 7 },
        },
        deliverableIds: [],
      },
    }));
    const planned = projectWorkTurn(turn({
      status: "running",
      projections: [wechatExecutionSummary],
      runtime_overlay: {
        lastEventId: 10,
        lastSequence: 10,
        steps: {
          visual_planning: { state: "done", attempt: 1 },
        },
        deliverableIds: [],
      },
    }));

    expect(active.currentActivity).toBe("正在确认文章目标");
    expect(active.steps).toEqual(expect.arrayContaining([
      { code: "brief_resolution", label: "正在确认文章目标", state: "active" },
      { code: "unknown_internal_key", label: "执行任务", state: "waiting" },
    ]));
    expect(planned.steps).toEqual([
      { code: "visual_planning", label: "已规划配图位置", state: "done" },
    ]);
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

  it("never shows activity copy after a final answer", () => {
    const model = projectWorkTurn(turn({
      status: "completed",
      turn_phase: "reading_data",
      assistant_response: "诊断已完成。",
    }));

    expect(model.presentation.showActivity).toBe(false);
    expect(model.presentation.showFinal).toBe(true);
  });

  it("treats assistant text as final even when persisted status lags", () => {
    const model = projectWorkTurn(turn({
      status: "running",
      turn_phase: "reading_data",
      assistant_response: "诊断已完成。",
    }));

    expect(model.presentation.showFinal).toBe(true);
    expect(model.presentation.showActivity).toBe(false);
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
