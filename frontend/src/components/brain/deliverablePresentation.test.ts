import { describe, expect, it } from "vitest";

import type { Artifact } from "../../types";
import { presentDeliverable } from "./deliverablePresentation";

function artifact(artifact_type: string): Artifact {
  return {
    id: 1,
    account_id: 3,
    thread_id: 1,
    turn_id: 1,
    run_id: 1,
    skill_run_id: 1,
    task_id: 1,
    artifact_type,
    title: "运营输出",
    version: 1,
    status: "ready_for_review",
    summary: "已完成",
    sections: [],
    evidence_refs: [],
    quality: null,
    created_at: "2026-08-04T00:00:00Z",
  };
}

describe("presentDeliverable", () => {
  it("presents a video script as filming-ready spoken scripts", () => {
    expect(presentDeliverable(artifact("video_script"))).toMatchObject({
      typeLabel: "口播拍摄稿",
      completionLabel: "已生成 5 条可直接拍摄的口播稿",
      primaryAction: { kind: "open", label: "查看 5 条拍摄稿" },
    });
  });
});
