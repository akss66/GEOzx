// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PublicSkill } from "../../types";
import { CapabilityLauncher } from "./CapabilityLauncher";

afterEach(cleanup);

const accountInspection: PublicSkill = {
  code: "account_inspection",
  version: 1,
  name: "一键账号体检",
  description: "快速了解账号现状与优化重点",
  category: "quick_operations",
  icon: "activity",
  requires_account: true,
  is_available: true,
  unavailable_reason: null,
};

const expertHelp: PublicSkill = {
  code: "strategy_review",
  version: 1,
  name: "策略复盘协助",
  description: "获得运营策略建议",
  category: "expert_help",
  icon: "compass",
  requires_account: false,
  is_available: true,
  unavailable_reason: null,
};

describe("CapabilityLauncher", () => {
  it("renders business groups in order and selects account inspection without showing its code", () => {
    const onSelectSkill = vi.fn();
    render(
      <CapabilityLauncher
        skills={[expertHelp, accountInspection]}
        onSelectSkill={onSelectSkill}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "添加能力或材料" }));

    expect(screen.getAllByRole("heading", { level: 3 }).map((item) => item.textContent)).toEqual([
      "快捷运营",
      "添加上下文",
      "专家协助",
    ]);
    expect(screen.getAllByRole("menuitem")[0]).toHaveAccessibleName(/一键账号体检/);

    fireEvent.click(screen.getByRole("menuitem", { name: /一键账号体检/ }));
    expect(onSelectSkill).toHaveBeenCalledWith("account_inspection");
    expect(screen.queryByText("account_inspection")).not.toBeInTheDocument();
  });

  it("keeps unavailable Skills visible with their business reason", () => {
    render(
      <CapabilityLauncher
        skills={[{ ...accountInspection, is_available: false, unavailable_reason: "请先选择账号" }]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "添加能力或材料" }));

    expect(screen.getByRole("menuitem", { name: /一键账号体检/ })).toBeDisabled();
    expect(screen.getByText("请先选择账号")).toBeVisible();
  });

  it("delegates every context action through its typed callback", () => {
    const onAddFilesAndMaterials = vi.fn();
    const onAddAccountDataPackage = vi.fn();
    const onAddHistoricalArtifacts = vi.fn();
    const onSelectAccount = vi.fn();
    render(
      <CapabilityLauncher
        onAddFilesAndMaterials={onAddFilesAndMaterials}
        onAddAccountDataPackage={onAddAccountDataPackage}
        onAddHistoricalArtifacts={onAddHistoricalArtifacts}
        onSelectAccount={onSelectAccount}
      />,
    );

    const openMenu = () => fireEvent.click(screen.getByRole("button", { name: "添加能力或材料" }));
    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: "添加文件或素材" }));
    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: "添加账号数据包" }));
    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: "添加历史产物" }));
    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: "选择账号" }));

    expect(onAddFilesAndMaterials).toHaveBeenCalledOnce();
    expect(onAddAccountDataPackage).toHaveBeenCalledOnce();
    expect(onAddHistoricalArtifacts).toHaveBeenCalledOnce();
    expect(onSelectAccount).toHaveBeenCalledOnce();
  });

  it("supports arrow navigation and returns focus to the trigger after Escape", () => {
    render(<CapabilityLauncher skills={[accountInspection]} onAddFilesAndMaterials={vi.fn()} />);

    const trigger = screen.getByRole("button", { name: "添加能力或材料" });
    trigger.focus();
    fireEvent.keyDown(trigger, { key: "Enter" });
    expect(screen.getByRole("menuitem", { name: /一键账号体检/ })).toHaveFocus();

    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(screen.getByRole("menuitem", { name: "添加文件或素材" })).toHaveFocus();

    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(trigger).toHaveFocus();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
