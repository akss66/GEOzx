import { describe, expect, it } from "vitest";

import type { Artifact } from "../../types";
import { presentDeliverable } from "./deliverablePresentation";

function artifact(
  artifact_type: Artifact["artifact_type"],
  overrides: Partial<Artifact> = {},
): Artifact {
  return {
    id: 1,
    account_id: 3,
    thread_id: 1,
    turn_id: 1,
    run_id: 1,
    skill_run_id: 1,
    task_id: 1,
    artifact_type,
    title: "不应展示的服务端标题",
    version: 1,
    status: "ready_for_review",
    summary: "已完成",
    sections: [],
    evidence_refs: [],
    quality: null,
    created_at: "2026-08-04T00:00:00Z",
    ...overrides,
  };
}

describe("presentDeliverable", () => {
  it("uses the storyboard name when a script only has scene structure", () => {
    expect(presentDeliverable(artifact("video_script"))).toMatchObject({
      typeLabel: "分镜拍摄稿",
      completionLabel: "已生成可直接拍摄的分镜拍摄稿",
      primaryAction: { kind: "open", label: "查看分镜拍摄稿" },
    });
  });

  it.each([
    ["spoken", "口播拍摄稿"],
    ["storyboard", "分镜拍摄稿"],
    ["product_video", "产品视频拍摄稿"],
    ["image_post", "图文发布稿"],
    ["live_flow", "直播流程与话术稿"],
  ])("uses the explicit %s content format instead of guessing from the title", (presentation_format, typeLabel) => {
    expect(presentDeliverable({
      ...artifact("video_script", { title: "脚本生成中" }),
      presentation_format: presentation_format as Artifact["presentation_format"],
    })).toMatchObject({
      typeLabel,
      primaryAction: { kind: "open", label: `查看${typeLabel}` },
    });
  });

  it.each([1, 5, 20])("uses the actual topic count of %i", (count) => {
    expect(presentDeliverable(artifact("topic_plan", {
      sections: [{ key: "topics", title: "选题清单", content: Array.from({ length: count }, (_, index) => ({ id: index })) }],
    }))).toMatchObject({
      completionLabel: `已规划 ${count} 个可执行选题`,
      primaryAction: { kind: "open", label: `查看 ${count} 个选题` },
    });
  });

  it("uses a structured schedule duration and omits it when unavailable", () => {
    expect(presentDeliverable(artifact("content_calendar", {
      sections: [{ key: "days", title: "排期天数", content: 20 }],
    }))).toMatchObject({
      completionLabel: "已排好未来 20 天内容",
      primaryAction: { kind: "open", label: "查看 20 天排期" },
    });
    expect(presentDeliverable(artifact("content_calendar"))).toMatchObject({
      completionLabel: "已完成内容排期",
      primaryAction: { kind: "open", label: "查看内容排期" },
    });
  });

  it("fails closed to a fixed business category for unknown or unsafe titles", () => {
    expect(presentDeliverable(artifact("unknown_type", { title: "脚本生成中" }))).toMatchObject({
      typeLabel: "账号运营分析",
      completionLabel: "已完成账号运营分析",
      primaryAction: { kind: "open", label: "查看账号运营分析" },
    });
  });
});
