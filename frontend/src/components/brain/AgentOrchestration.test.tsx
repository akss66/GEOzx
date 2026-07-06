// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AgentOrchestration } from "./AgentOrchestration";

describe("AgentOrchestration", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("shows an empty state before the user enters a goal", () => {
    render(<AgentOrchestration goal="" />);

    expect(screen.getByText("输入运营目标后，这里会显示主 Agent 调度专家的过程")).toBeInTheDocument();
  });

  it("reveals expert handoff cards as a relay instead of a graph", async () => {
    vi.useFakeTimers();

    render(<AgentOrchestration goal="为抖音账号做一轮冷启动内容规划" />);

    expect(screen.getByText("主 Agent")).toBeInTheDocument();
    expect(screen.getByText("账号定位专家")).toBeInTheDocument();
    expect(screen.queryByText("内容策略专家")).not.toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(1200);
    });

    expect(screen.getByText("内容策略专家")).toBeInTheDocument();
    expect(screen.getByText("账号定位专家已完成定位分析，接下来交给内容策略专家处理。")).toBeInTheDocument();
  });
});
