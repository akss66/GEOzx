// @vitest-environment jsdom

import { describe, expect, it } from "vitest";

import { buildAppShellMenuItems } from "./AppShell";

function menuKeys(isAdmin: boolean) {
  return buildAppShellMenuItems(isAdmin)
    .map((item) => item && "key" in item ? item.key : undefined)
    .filter(Boolean);
}

function menuLabels(isAdmin: boolean) {
  return buildAppShellMenuItems(isAdmin)
    .map((item) => item && "label" in item ? item.label : undefined)
    .filter(Boolean);
}

describe("AppShell navigation", () => {
  it("keeps deferred modules out of the primary navigation", () => {
    expect(menuKeys(false)).not.toContain("/advertising");
    expect(menuKeys(false)).not.toContain("/customer-service");
    expect(menuKeys(false)).not.toContain("/risks");
    expect(menuLabels(false)).not.toContain("投流");
    expect(menuLabels(false)).not.toContain("客服");
    expect(menuLabels(false)).not.toContain("风险队列");
  });

  it("keeps the AI operations navigation focused for members", () => {
    expect(menuKeys(false)).toEqual([
      "/",
      "/agents",
      "/accounts",
      "/tasks",
      "/approvals",
      "/review",
      "/cost",
      "/knowledge",
    ]);

    expect(menuLabels(false)).toEqual([
      "AI 运营",
      "运营大脑",
      "专家团",
      "运营执行",
      "账号矩阵",
      "内容生产",
      "人工审批",
      "运营复盘",
      "系统资产",
      "使用成本",
      "知识库",
    ]);
  });

  it("adds privileged system routes only for admins", () => {
    expect(menuKeys(false)).not.toContain("/config");
    expect(menuKeys(false)).not.toContain("/users");

    expect(menuKeys(true)).toEqual([
      "/",
      "/agents",
      "/accounts",
      "/tasks",
      "/approvals",
      "/review",
      "/cost",
      "/knowledge",
      "/users",
      "/config",
    ]);
  });
});
