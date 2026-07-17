import { describe, expect, it } from "vitest";

import { businessToolName, safeTaskTitle } from "./costPresentation";

describe("business cost presentation", () => {
  it.each([
    ["account_context", "Account Context", "账号上下文"],
    ["profile_snapshot", "Profile Snapshot", "账号资料快照"],
    ["brief_builder", "Brief Builder", "任务目标整理"],
    ["compliance_check", "Compliance Check", "合规检查"],
  ])("translates %s into business language", (code, name, expected) => {
    expect(businessToolName(code, name)).toBe(expected);
  });

  it("preserves an already readable tool name", () => {
    expect(businessToolName("custom_operation_tool", "自定义运营工具")).toBe("自定义运营工具");
  });

  it("replaces corrupt legacy task titles without hiding their identity", () => {
    expect(safeTaskTitle("????????????????", 42)).toBe("历史运营任务 #42");
    expect(safeTaskTitle("标题��????", 9)).toBe("历史运营任务 #9");
    expect(safeTaskTitle("七月冷启动内容", 12)).toBe("七月冷启动内容");
  });
});
