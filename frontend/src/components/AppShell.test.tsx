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
  it("shows valid Chinese navigation without deferred modules", () => {
    expect(menuKeys(false)).toEqual([
      "/", "/agents", "/accounts", "/tasks", "/approvals", "/review", "/cost", "/knowledge",
    ]);
    expect(menuLabels(false)).toEqual([
      "AI 运营", "运营大脑", "专家团", "运营执行", "账号矩阵", "内容生产",
      "人工审批", "运营复盘", "系统资产", "使用成本", "知识库",
    ]);
    expect(menuLabels(false).join("")).not.toContain("????");
  });

  it("adds the approved administration routes for admins", () => {
    expect(menuKeys(true)).toEqual([
      "/", "/agents", "/accounts", "/tasks", "/approvals", "/review", "/cost",
      "/knowledge", "/users", "/config", "/models",
    ]);
    expect(menuLabels(true)).toContain("管理中心");
    expect(menuLabels(true)).toContain("用户管理");
    expect(menuLabels(true)).toContain("专家管理");
    expect(menuLabels(true)).toContain("模型基础设施");
  });
});
