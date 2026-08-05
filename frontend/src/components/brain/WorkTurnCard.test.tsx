// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
  presentation: {
    isActive: true,
    statusLabel: null,
    activityLabel: "正在咨询专家",
    showActivity: true,
    showFinal: false,
    progressMode: "expanded",
    processLabel: "查看分析过程",
  },
  steps: [{ code: "review", label: "核对账号数据", state: "active" }],
  experts: [{ name: "账号定位专家", status: "completed" }],
  deliverableIds: [],
  assistant: {
    identity: "运营大脑",
    steps: [{ code: "review", label: "核对账号数据", state: "active" }],
  },
};

const completedTurn: WorkTurnViewModel = {
  ...workingTurn,
  status: "completed",
  currentActivity: null,
  assistantText: "账号数据完整，建议优先收敛内容主题。",
  presentation: {
    isActive: false,
    statusLabel: "已完成",
    activityLabel: null,
    showActivity: false,
    showFinal: true,
    progressMode: "summary",
    processLabel: "查看已完成过程",
  },
  steps: [
    { code: "read", label: "读取账号数据", state: "done" },
    { code: "check", label: "核对数据完整性", state: "done" },
    { code: "analyze", label: "分析主要问题", state: "done" },
    { code: "recommend", label: "整理运营建议", state: "done" },
  ],
};

describe("WorkTurnCard", () => {
  afterEach(cleanup);

  it("keeps one assistant surface from activity through final answer", () => {
    const view = render(<WorkTurnCard view={workingTurn} sourceStatus="completed" />);
    const root = screen.getByTestId("work-turn");
    const operator = screen.getByRole("region", { name: "运营大脑工作回合" });

    expect(screen.getAllByTestId("work-turn")).toHaveLength(1);
    expect(within(operator).getAllByText("运营大脑")).toHaveLength(1);
    expect(operator).toHaveAttribute("aria-busy", "true");
    expect(operator).toHaveAttribute("data-thinking", "true");

    view.rerender(<WorkTurnCard sourceStatus="running" view={completedTurn} />);

    expect(screen.getByTestId("work-turn")).toBe(root);
    expect(screen.getByRole("region", { name: "运营大脑工作回合" })).toBe(operator);
    expect(root).toHaveAttribute("data-turn-status", "running");
    expect(within(operator).queryByText(/正在/)).not.toBeInTheDocument();
    expect(within(operator).getByText(completedTurn.assistantText!)).toBeVisible();
    expect(operator).toHaveAttribute("aria-busy", "false");
    expect(operator).not.toHaveAttribute("data-thinking");
  });

  it("shows one live status while the avatar breathes", () => {
    render(<WorkTurnCard view={workingTurn} />);

    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.queryByText("思考中")).not.toBeInTheDocument();
  });

  it("summarizes finished progress and expands it on demand", () => {
    render(<WorkTurnCard view={completedTurn} />);

    const toggle = screen.getByRole("button", { name: "已完成 4 项检查" });
    expect(toggle).toBeVisible();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("读取账号数据")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("读取账号数据")).toBeVisible();
  });

  it("groups unresolved steps as unfinished when the turn fails without a failed step event", () => {
    render(<WorkTurnCard view={{
      ...completedTurn,
      status: "failed",
      assistantText: null,
      presentation: {
        isActive: false,
        statusLabel: "本次分析未完成",
        activityLabel: null,
        showActivity: false,
        showFinal: false,
        progressMode: "expanded",
        processLabel: "查看分析过程",
      },
      steps: [
        { code: "read", label: "读取账号数据", state: "done" },
        { code: "analyze", label: "分析主要问题", state: "active" },
        { code: "recommend", label: "整理运营建议", state: "waiting" },
      ],
    }} />);

    expect(screen.queryByRole("button", { name: /项检查/ })).not.toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "已完成" })).getByText("读取账号数据")).toBeVisible();
    const unfinished = screen.getByRole("region", { name: "未完成" });
    expect(within(unfinished).getByText("分析主要问题")).toBeVisible();
    expect(within(unfinished).getByText("整理运营建议")).toBeVisible();
    expect([...unfinished.querySelectorAll("small")].map((node) => node.textContent))
      .toEqual(["未完成", "未完成"]);
    expect(within(unfinished).queryByText("进行中")).not.toBeInTheDocument();
    expect(within(unfinished).queryByText("待执行")).not.toBeInTheDocument();
  });

  it("shows steering inside the existing work card without creating a bubble or thinking copy", () => {
    render(
      <WorkTurnCard
        view={{
          ...workingTurn,
          steeringNotice: {
            label: "supplement",
            copy: "已补充要求",
            message: "第一条不要讲价格",
            reason: "不会优先展示的内部原因",
          },
        }}
      />,
    );

    expect(screen.getAllByTestId("work-turn")).toHaveLength(1);
    expect(screen.getByRole("note", { name: "任务调整" })).toHaveTextContent("已补充要求");
    expect(screen.getByRole("note", { name: "任务调整" })).toHaveTextContent("第一条不要讲价格");
    expect(screen.getAllByText("第一条不要讲价格")).toHaveLength(1);
    expect(screen.queryByText("不会优先展示的内部原因")).not.toBeInTheDocument();
    expect(screen.queryByText("思考中")).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: "查看分析过程" }));
    expect(screen.getByText(/账号定位专家/)).toBeVisible();
    expect(screen.getByText("已核验 2 条业务依据")).toBeVisible();
    expect(screen.queryByText("Tool #8001 · account.data_context")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "技术详情" }));
    expect(screen.getByText("Tool #8001 · account.data_context")).toBeVisible();
    expect(screen.getByText("内部消息：103")).toBeVisible();
  });

  it("uses nested native disclosures with independently observable open state", () => {
    render(
      <WorkTurnCard
        view={workingTurn}
        evidenceSummary={["已核验 2 条业务依据"]}
        technicalLog={["Tool #8001 · account.data_context"]}
      />,
    );

    const process = screen.getByRole("button", { name: "查看分析过程" });
    expect(process).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("技术详情")).not.toBeInTheDocument();

    fireEvent.click(process);
    expect(process).toHaveAttribute("aria-expanded", "true");
    const technical = screen.getByRole("button", { name: "技术详情" });
    expect(technical).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(technical);
    expect(technical).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Tool #8001 · account.data_context")).toBeVisible();
  });

  it("keeps progressive disclosures keyboard-focusable with observable expanded state", () => {
    render(
      <WorkTurnCard
        view={workingTurn}
        evidenceSummary={["已核验 2 条业务依据"]}
        technicalLog={["Tool #8001 · account.data_context"]}
      />,
    );

    const processToggle = screen.getByRole("button", { name: "查看分析过程" });
    processToggle.focus();
    expect(processToggle).toHaveFocus();
    expect(processToggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.keyDown(processToggle, { key: "Enter" });
    fireEvent.click(processToggle);
    expect(processToggle).toHaveAttribute("aria-expanded", "true");

    const technicalToggle = screen.getByRole("button", { name: "技术详情" });
    technicalToggle.focus();
    expect(technicalToggle).toHaveFocus();
    expect(technicalToggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.keyDown(technicalToggle, { key: " " });
    fireEvent.click(technicalToggle);
    expect(technicalToggle).toHaveAttribute("aria-expanded", "true");
  });
});
