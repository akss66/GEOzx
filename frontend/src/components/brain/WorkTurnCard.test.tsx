// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { WorkTurnViewModel } from "../../types";
import { WorkTurnCard } from "./WorkTurnCard";

const workingTurn: WorkTurnViewModel = {
  key: "org:1:thread:81:message:turn-103",
  turnId: 103,
  userMessage: "请诊断账号问题",
  status: "working",
  currentActivity: "正在咨询专家",
  assistantText: "已完成初步数据核对。",
  steps: [{ code: "review", label: "核对账号数据", state: "active" }],
  experts: [{ name: "账号定位专家", status: "completed" }],
  deliverableIds: [],
  assistant: {
    identity: "运营大脑",
    steps: [{ code: "review", label: "核对账号数据", state: "active" }],
  },
};

describe("WorkTurnCard", () => {
  afterEach(cleanup);

  it("keeps one work-turn root while a working turn completes", () => {
    const view = render(<WorkTurnCard view={workingTurn} />);
    const root = screen.getByTestId("work-turn");

    expect(screen.getAllByTestId("work-turn")).toHaveLength(1);
    expect(screen.getAllByText("运营大脑")).toHaveLength(1);
    expect(screen.queryByText("思考中")).not.toBeInTheDocument();
    expect(screen.getByText("正在咨询专家")).toBeInTheDocument();

    view.rerender(
      <WorkTurnCard
        view={{
          ...workingTurn,
          status: "completed",
          currentActivity: null,
          steps: [{ code: "review", label: "核对账号数据", state: "done" }],
        }}
      />,
    );

    expect(screen.getByTestId("work-turn")).toBe(root);
    expect(root).toHaveAttribute("data-turn-status", "completed");
    expect(screen.queryByText("正在咨询专家")).not.toBeInTheDocument();
  });

  it("reveals business process before technical logs and does not render logs by default", () => {
    render(
      <WorkTurnCard
        view={workingTurn}
        evidenceSummary={["已核验 2 条业务依据"]}
        technicalLog={["Tool #8001 · account.data_context", "内部消息：103"]}
      />,
    );

    expect(screen.queryByText("Tool #8001 · account.data_context")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("查看过程"));
    expect(screen.getByText(/账号定位专家/)).toBeVisible();
    expect(screen.getByText("已核验 2 条业务依据")).toBeVisible();
    expect(screen.queryByText("Tool #8001 · account.data_context")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("技术日志"));
    expect(screen.getByText("Tool #8001 · account.data_context")).toBeVisible();
    expect(screen.getByText("内部消息：103")).toBeVisible();
  });
});
