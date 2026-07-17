import { describe, expect, it } from "vitest";

import type { ApprovalQueueItem } from "../../types";
import {
  approvalFindingCopy,
  filterApprovalItems,
  readApprovalPublishPackage,
} from "./approvalPresentation";

const base: ApprovalQueueItem = {
  key: "tool_call:1",
  kind: "tool_call",
  source_id: 1,
  project_id: 2,
  project_name: "项目",
  account_id: 3,
  account_name: "账号",
  content_item_id: 4,
  content_title: "内容",
  task_id: 5,
  category: "发布包确认",
  title: "发布确认",
  summary: "等待确认",
  risk_level: "high",
  risk_reasons: [],
  impact: [],
  agent_explanation: "说明",
  preview: {
    publish_package: {
      platform: "douyin",
      account_id: 3,
      content_type: "video",
      title: "标题",
      body: "正文",
      topics: [],
      scheduled_at: null,
      material_ids: [7],
      cover_material_id: null,
      visibility: "public",
      allow_comment: true,
      execution_mode: "manual_checklist",
      manual_steps: ["核对账号"],
    },
  },
  can_decide: true,
  created_at: "2026-07-17T00:00:00Z",
};

describe("approval presentation", () => {
  it("filters queue by risk and business type", () => {
    const gate = { ...base, key: "gate:2", kind: "gate" as const, risk_level: "medium" as const };
    expect(filterApprovalItems([base, gate], "high_risk")).toEqual([base]);
    expect(filterApprovalItems([base, gate], "content")).toEqual([gate]);
    expect(filterApprovalItems([base, gate], "external")).toEqual([base]);
  });

  it("reads a typed publish package without exposing raw meta", () => {
    expect(readApprovalPublishPackage(base)?.material_ids).toEqual([7]);
    expect(approvalFindingCopy("title.long", "fallback")).toBe(
      "标题偏长，发布前需要再次确认。",
    );
  });
});
