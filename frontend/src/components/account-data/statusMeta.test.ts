import { describe, expect, it } from "vitest";

import {
  getCoverageLabel,
  getSourceKindLabel,
  getTemplateLabel,
} from "./statusMeta";

describe("account data business labels", () => {
  it.each([
    ["douyin_daily_play_v1", "抖音日播放数据"],
    ["douyin_single_content_v1", "抖音单作品分析"],
    ["douyin_period_aggregate_v1", "抖音阶段汇总"],
    ["douyin_work_list_v1", "抖音作品列表"],
    ["manual_account_period_v1", "人工账号周期数据"],
    ["manual_audience_dimension_v1", "人工粉丝画像"],
    ["manual_benchmark_v1", "人工对标基准"],
    ["future_template", "其他账号数据"],
  ])("maps template %s to %s", (templateCode, label) => {
    expect(getTemplateLabel(templateCode)).toBe(label);
  });

  it("maps source and coverage values to operator-facing labels", () => {
    expect(getSourceKindLabel("official_api")).toBe("平台接口");
    expect(getSourceKindLabel("platform_export")).toBe("平台导出");
    expect(getSourceKindLabel("screenshot_verified")).toBe("截图佐证");
    expect(getSourceKindLabel("manual_entry")).toBe("人工录入");

    expect(getCoverageLabel("available")).toBe("数据完整");
    expect(getCoverageLabel("partial")).toBe("部分数据");
    expect(getCoverageLabel("missing")).toBe("待补齐");
  });
});
