// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getModelInfrastructure,
  listModelCalls,
  updateModelProvider,
  updateModelRoute,
} from "../api/modelInfrastructure";
import type { ModelInfrastructureOverview } from "../types";
import ModelInfrastructure from "./ModelInfrastructure";

const overview: ModelInfrastructureOverview = {
  summary: {
    providers_total: 2,
    providers_ready: 1,
    routes_total: 2,
    routes_with_fallback: 1,
    calls_24h: 18,
    failures_24h: 2,
  },
  providers: [
    {
      code: "deepseek",
      name: "DeepSeek",
      kind: "direct",
      enabled: true,
      credential_ref: "env:DEEPSEEK_API_KEY",
      credential_configured: true,
      runtime_ready: true,
      endpoint: "https://api.deepseek.com",
      supported_models: ["deepseek-chat", "deepseek-reasoner"],
      note: "直接连接 DeepSeek API。",
      updated_at: null,
    },
    {
      code: "litellm",
      name: "LiteLLM 路由",
      kind: "router",
      enabled: false,
      credential_ref: null,
      credential_configured: null,
      runtime_ready: false,
      endpoint: null,
      supported_models: ["litellm:<provider>/<model>"],
      note: "路由其他模型。",
      updated_at: null,
    },
  ],
  routes: [
    {
      id: 1,
      agent_code: "00-decision",
      agent_name: "运营大脑",
      primary_provider_id: 7,
      fallback_provider_id: 7,
      primary_model: "deepseek-reasoner",
      fallback_model: "deepseek-chat",
      temperature: 0.2,
      max_tokens: 8192,
      timeout_seconds: 120,
      updated_at: null,
    },
    {
      id: 2,
      agent_code: "01-positioning",
      agent_name: "账号定位专家",
      primary_provider_id: 7,
      fallback_provider_id: null,
      primary_model: "deepseek-chat",
      fallback_model: null,
      temperature: 0.4,
      max_tokens: 4096,
      timeout_seconds: 90,
      updated_at: null,
    },
  ],
};

vi.mock("../api/modelInfrastructure", () => ({
  getModelInfrastructure: vi.fn(async () => overview),
  listModelCalls: vi.fn(async () => ({
    total: 1,
    items: [{
      id: 9,
      agent_code: "00-decision",
      agent_name: "运营大脑",
      provider: "deepseek",
      model: "deepseek-reasoner",
      total_tokens: 1680,
      cost_usd: 0.0084,
      latency_ms: 1320,
      status: "error",
      error_summary: "请求超时",
      created_at: "2026-07-17T10:00:00Z",
    }],
  })),
  updateModelProvider: vi.fn(async (_code: string, input: Record<string, unknown>) => ({
    ...overview.providers[0],
    ...input,
  })),
  updateModelRoute: vi.fn(async (_code: string, input: Record<string, unknown>) => ({
    ...overview.routes[0],
    ...input,
  })),
}));

describe("ModelInfrastructure", () => {
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
        <AntApp><ModelInfrastructure /></AntApp>
      </QueryClientProvider>,
    );
  }

  it("presents providers, routing and calls as a dedicated technical workbench", async () => {
    renderPage();

    expect(await screen.findByText("模型基础设施")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "供应商" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "路由策略" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "调用账本" })).toBeInTheDocument();
    expect(screen.getByText("DeepSeek")).toBeInTheDocument();
    expect(screen.queryByText(/sk-secret/i)).not.toBeInTheDocument();
    expect(getModelInfrastructure).toHaveBeenCalledTimes(1);
  });

  it("replaces a failed initial load with a recoverable state", async () => {
    vi.mocked(getModelInfrastructure).mockRejectedValueOnce({ response: { status: 503 } });

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("模型基础设施加载失败");
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByText("DeepSeek")).toBeInTheDocument();
  });

  it("stores only the selected server-side credential reference", async () => {
    renderPage();
    await screen.findByText("DeepSeek");
    fireEvent.click(screen.getByRole("button", { name: "编辑 DeepSeek" }));
    fireEvent.change(screen.getByLabelText("DeepSeek 密钥引用"), {
      target: { value: "vault://dyflow/llm/deepseek-api-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存供应商" }));

    await waitFor(() => expect(updateModelProvider).toHaveBeenCalledWith("deepseek", {
      enabled: true,
      credential_ref: "vault://dyflow/llm/deepseek-api-key",
    }));
  });

  it("edits an expert route without mixing it into business expert management", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "路由策略" }));
    fireEvent.click(screen.getByRole("button", { name: /账号定位专家/ }));
    fireEvent.change(screen.getByLabelText("温度"), { target: { value: "0.6" } });
    fireEvent.click(screen.getByRole("button", { name: "保存路由策略" }));

    await waitFor(() => expect(updateModelRoute).toHaveBeenCalledWith(
      "01-positioning",
      expect.objectContaining({
        primary_provider_id: 7,
        primary_model: "deepseek-chat",
        fallback_provider_id: null,
        fallback_model: null,
        temperature: 0.6,
      }),
    ));
  });

  it("blocks edits for a legacy route that has no provider target", async () => {
    vi.mocked(getModelInfrastructure).mockResolvedValueOnce({
      ...overview,
      routes: [{ ...overview.routes[0], primary_provider_id: null }],
    });

    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "路由策略" }));

    expect(await screen.findByRole("status")).toHaveTextContent("需要先绑定模型供应商");
    expect(screen.queryByRole("button", { name: "保存路由策略" })).not.toBeInTheDocument();
    expect(updateModelRoute).not.toHaveBeenCalled();
  });

  it("filters the immutable call ledger by failure status", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "调用账本" }));
    fireEvent.click(screen.getByRole("radio", { name: "失败" }));

    await waitFor(() => expect(listModelCalls).toHaveBeenLastCalledWith("error", 50));
    expect(await screen.findByText("请求超时")).toBeInTheDocument();
  });

  it("does not present a failed call ledger as no calls", async () => {
    vi.mocked(listModelCalls).mockRejectedValueOnce({ response: { status: 503 } });

    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "调用账本" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("调用账本加载失败");
    expect(screen.queryByText("没有符合条件的调用记录")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByText("请求超时")).toBeInTheDocument();
  });
});
