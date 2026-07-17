import { describe, expect, it } from "vitest";

import type { Deliverable } from "../../types";
import {
  canOperateContent,
  displayContentTitle,
  deliverableSections,
  latestDeliverables,
  stageLabel,
  statusLabel,
} from "./contentPresentation";

const deliverables: Deliverable[] = [
  {
    id: 1,
    agent_code: "02-content",
    type: "video_script",
    version: 1,
    status: "superseded",
    payload: { title: "旧版", hook: "旧钩子", scenes: ["旧场景"], duration_seconds: 30 },
    created_at: "2026-07-16T00:00:00Z",
  },
  {
    id: 2,
    agent_code: "02-content",
    type: "video_script",
    version: 2,
    status: "pending_review",
    payload: {
      title: "新版",
      hook: "真实钩子",
      scenes: ["开场", "实测"],
      duration_seconds: 45,
      bgm_suggestion: "轻快",
    },
    created_at: "2026-07-17T00:00:00Z",
  },
];

describe("content presentation", () => {
  it("uses business-facing stage and status copy", () => {
    expect(stageLabel("content_direction")).toBe("脚本策划");
    expect(statusLabel("blocked")).toBe("等待处理");
  });

  it("selects the latest live deliverable for every type", () => {
    expect(latestDeliverables(deliverables)).toEqual([deliverables[1]]);
  });

  it("turns a script payload into readable document sections", () => {
    expect(deliverableSections(deliverables[1])).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ label: "开场钩子", value: "真实钩子" }),
        expect.objectContaining({ label: "镜头结构", items: ["开场", "实测"] }),
      ]),
    );
  });

  it("replaces corrupted historical titles with an explicit repair label", () => {
    expect(displayContentTitle("????????????,??????")).toBe("标题编码异常");
    expect(displayContentTitle("  小米 13 温度实测  ")).toBe("小米 13 温度实测");
  });

  it("only enables content operations for the explicitly selected matching account", () => {
    expect(canOperateContent(3, 3)).toBe(true);
    expect(canOperateContent(3, null)).toBe(false);
    expect(canOperateContent(null, 3)).toBe(false);
    expect(canOperateContent(3, 4)).toBe(false);
  });
});
