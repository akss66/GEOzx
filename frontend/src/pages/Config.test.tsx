// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { listAgentManagement, updateAgentManagement } from "../api/agents";
import type { AgentManagement } from "../types";
import Config from "./Config";

const experts: AgentManagement[] = [
  {
    code: "00-decision",
    name: "运营大脑",
    group: "control",
    enabled: true,
    responsibility: "理解目标并调度必要专家。",
    system_prompt: "",
    automation_level: "confirm",
    tool_permissions: { task_planner: "auto" },
    quality_gates: [],
    available_tools: [
      { code: "task_planner", name: "任务规划", description: "生成专家执行计划。" },
    ],
    available_quality_gates: [],
    typical_tasks: ["任务拆解"],
    standard_outputs: ["review_report"],
    updated_at: null,
  },
  {
    code: "02-content-director",
    name: "编导文案专家",
    group: "creative",
    enabled: true,
    responsibility: "围绕账号定位产出抖音脚本。",
    system_prompt: "不编造产品参数。",
    automation_level: "confirm",
    tool_permissions: { brief_builder: "confirm", compliance_precheck: "confirm" },
    quality_gates: ["script_compliance"],
    available_tools: [
      { code: "brief_builder", name: "内容任务整理", description: "整理结构化内容任务。" },
      { code: "compliance_precheck", name: "合规预检", description: "检查内容风险。" },
    ],
    available_quality_gates: [
      { code: "topic_review", name: "选题确认", description: "选题进入脚本前确认。", forced: false },
      { code: "script_compliance", name: "脚本合规", description: "脚本通过合规检查。", forced: true },
    ],
    typical_tasks: ["脚本包", "标题钩子"],
    standard_outputs: ["video_script"],
    updated_at: "2026-07-17T10:00:00Z",
  },
];

vi.mock("../api/agents", () => ({
  listAgentManagement: vi.fn(async () => experts),
  updateAgentManagement: vi.fn(async (code: string, input: Record<string, unknown>) => ({
    ...experts.find((item) => item.code === code),
    ...input,
  })),
}));

describe("Config expert management", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation(() => ({
        matches: false,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });
  afterEach(cleanup);

  function renderPage() {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    return render(
      <QueryClientProvider client={queryClient}>
        <AntApp><Config /></AntApp>
      </QueryClientProvider>,
    );
  }

  it("shows a business expert workspace without provider or model controls", async () => {
    renderPage();

    expect(await screen.findByText("专家管理")).toBeInTheDocument();
    expect(screen.getAllByText("运营大脑").length).toBeGreaterThan(0);
    expect(screen.getByText("编导文案专家")).toBeInTheDocument();
    expect(screen.queryByText(/deepseek/i)).not.toBeInTheDocument();
    expect(screen.queryByText("外部集成")).not.toBeInTheDocument();
  });

  it("shows a recovery action instead of an endless skeleton when loading fails", async () => {
    vi.mocked(listAgentManagement).mockRejectedValueOnce({ response: { status: 503 } });

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("专家配置加载失败");
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByText("编导文案专家")).toBeInTheDocument();
  });

  it("edits responsibilities, permissions and gates as one persisted policy", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /编导文案专家/ }));

    const responsibility = screen.getByLabelText("专家职责");
    fireEvent.change(responsibility, { target: { value: "只产出可直接拍摄的抖音脚本。" } });
    fireEvent.change(screen.getByLabelText("内容任务整理权限"), {
      target: { value: "auto" },
    });
    fireEvent.click(screen.getByLabelText("选题确认"));
    fireEvent.click(screen.getByRole("button", { name: /保存专家配置/ }));

    await waitFor(() => expect(updateAgentManagement).toHaveBeenCalledWith(
      "02-content-director",
      expect.objectContaining({
        enabled: true,
        responsibility: "只产出可直接拍摄的抖音脚本。",
        tool_permissions: {
          brief_builder: "auto",
          compliance_precheck: "confirm",
        },
        quality_gates: ["script_compliance", "topic_review"],
      }),
    ));
  });

  it("makes expert availability explicit", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /编导文案专家/ }));
    fireEvent.click(screen.getByLabelText("启用编导文案专家"));
    fireEvent.click(screen.getByRole("button", { name: /保存专家配置/ }));

    await waitFor(() => expect(updateAgentManagement).toHaveBeenCalledWith(
      "02-content-director",
      expect.objectContaining({ enabled: false }),
    ));
  });

  it("loads management data from the dedicated admin endpoint", async () => {
    renderPage();
    await screen.findByText("专家管理");
    expect(listAgentManagement).toHaveBeenCalledTimes(1);
  });
});
