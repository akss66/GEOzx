// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { BrainDecisionRequest } from "../../types";
import { DecisionRequest } from "./DecisionRequest";

afterEach(cleanup);

const decision: BrainDecisionRequest = {
  id: "direction-1",
  title: "下一步先推进哪个方向？",
  summary: "两条路线都可行，请选择本轮优先方向。",
  allow_custom_input: true,
  status: "pending",
  choices: [
    {
      id: "steady",
      title: "稳健验证",
      description: "先用低成本内容验证受众反馈。",
      benefit: "风险低，反馈快",
      tradeoff: "增长速度较慢",
      recommended: true,
    },
    {
      id: "bold",
      title: "强势破圈",
      description: "用高反差选题快速测试传播上限。",
      benefit: "更容易获得增量",
      tradeoff: "内容风险更高",
      recommended: false,
    },
  ],
};

describe("DecisionRequest", () => {
  it("renders structured choices in the conversation and submits the selected choice", () => {
    const onSelect = vi.fn();

    render(
      <DecisionRequest
        decision={decision}
        selecting={false}
        revising={false}
        onSelect={onSelect}
        onRevise={vi.fn()}
      />,
    );

    expect(screen.getByRole("radiogroup", { name: "下一步先推进哪个方向？" })).toBeInTheDocument();
    expect(screen.getByText("推荐")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /强势破圈/ }));
    fireEvent.click(screen.getByRole("button", { name: "按此方案继续" }));

    expect(onSelect).toHaveBeenCalledWith("bold");
  });

  it("accepts a custom direction and can request a new set of choices", () => {
    const onRevise = vi.fn();

    render(
      <DecisionRequest
        decision={decision}
        selecting={false}
        revising={false}
        onSelect={vi.fn()}
        onRevise={onRevise}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "自定义方向" }));
    fireEvent.change(screen.getByRole("textbox", { name: "自定义方向" }), {
      target: { value: "先做账号诊断，再决定内容方向" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交方向" }));
    expect(onRevise).toHaveBeenCalledWith("先做账号诊断，再决定内容方向", false);

    fireEvent.click(screen.getByRole("button", { name: "换一批方案" }));
    expect(onRevise).toHaveBeenCalledWith("请基于当前目标重新生成一组差异更明显的方案", true);
  });
});
