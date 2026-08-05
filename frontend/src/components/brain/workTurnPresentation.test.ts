import { describe, expect, it } from "vitest";

import type { WorkTurnStatus } from "../../types";
import {
  presentWorkTurn,
  presentWorkTurnActivity,
  presentWorkTurnProgress,
} from "./workTurnPresentation";

describe("work turn presentation", () => {
  it.each([
    ["reading_data", "正在核对已导入的数据范围"],
    ["consulting_experts", "正在分析账号的主要问题"],
    ["quality_review", "正在核验结论与数据依据"],
    ["composing_artifact", "正在整理优先运营建议"],
  ] as const)("maps %s to one business activity line", (phase, expected) => {
    expect(presentWorkTurnActivity({ phase, status: "running", steps: [] }))
      .toBe(expected);
  });

  it("collapses completed progress but preserves failed unfinished steps", () => {
    expect(presentWorkTurnProgress({
      status: "completed" satisfies WorkTurnStatus,
      steps: [{ code: "read_data", label: "读取数据", state: "done" }],
    }).mode).toBe("summary");
    expect(presentWorkTurnProgress({
      status: "failed" satisfies WorkTurnStatus,
      steps: [
        { code: "read_data", label: "读取数据", state: "done" },
        { code: "quality_review", label: "质量审核", state: "failed" },
      ],
    }).mode).toBe("expanded");
  });

  it("reserves the activity line for active Turns and gives failures a business status", () => {
    expect(presentWorkTurn({
      status: "waiting_user",
      persistedStatus: "waiting_user",
      hasFinal: false,
      steps: [],
    }).activityLabel).toBeNull();
    expect(presentWorkTurn({
      status: "failed",
      persistedStatus: "failed",
      hasFinal: false,
      steps: [{ code: "quality_review", label: "质量审核", state: "failed" }],
    })).toMatchObject({
      statusLabel: "本次分析未完成",
      progressMode: "expanded",
    });
  });
});
