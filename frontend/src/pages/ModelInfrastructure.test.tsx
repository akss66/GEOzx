// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ModelProviderDeleteConflictError,
  createModelProvider,
  deleteModelProvider,
  discoverModelProviderModels,
  listModelCalls,
  removeModelProviderCredential,
  replaceModelProviderCredential,
  updateModelProviderDetails,
  updateModelProviderModels,
  updateModelRoute,
  verifyModelProvider,
} from "../api/modelInfrastructure";
import type {
  ModelCallPage,
  ModelInfrastructureOverview,
  ModelProviderDetail,
  ModelProviderTemplate,
  ModelRoute,
  UpdateModelRouteInput,
} from "../types";
import ModelInfrastructure from "./ModelInfrastructure";

const templates: ModelProviderTemplate[] = [
  {
    code: "deepseek",
    display_name: "DeepSeek",
    base_url: "https://api.deepseek.com/v1",
    protocol: "openai_compatible",
    models: ["deepseek-chat", "deepseek-reasoner"],
  },
  {
    code: "openai",
    display_name: "OpenAI",
    base_url: "https://api.openai.com/v1",
    protocol: "openai_compatible",
    models: ["gpt-4.1-mini", "gpt-4.1"],
  },
  {
    code: "qwen",
    display_name: "Qwen",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    protocol: "openai_compatible",
    models: ["qwen-plus"],
  },
  {
    code: "doubao",
    display_name: "Doubao",
    base_url: "https://ark.cn-beijing.volces.com/api/v3",
    protocol: "openai_compatible",
    models: ["doubao-pro-32k"],
  },
  {
    code: "zhipu",
    display_name: "Zhipu AI",
    base_url: "https://open.bigmodel.cn/api/paas/v4",
    protocol: "openai_compatible",
    models: ["glm-4-plus"],
  },
  {
    code: "moonshot",
    display_name: "Moonshot AI",
    base_url: "https://api.moonshot.cn/v1",
    protocol: "openai_compatible",
    models: ["moonshot-v1-32k"],
  },
];

function buildProvider(partial: Partial<ModelProviderDetail> & Pick<ModelProviderDetail, "id" | "code">) {
  return {
    id: partial.id,
    code: partial.code,
    display_name: partial.display_name ?? partial.code,
    provider_type: partial.provider_type ?? "preset",
    template_code: partial.template_code ?? partial.code,
    protocol: partial.protocol ?? "openai_compatible",
    base_url: partial.base_url ?? "https://api.example.com/v1",
    enabled: partial.enabled ?? true,
    sort_order: partial.sort_order ?? partial.id,
    credential_source: partial.credential_source ?? "none",
    key_configured: partial.key_configured ?? false,
    key_last_four: partial.key_last_four ?? null,
    key_fingerprint: partial.key_fingerprint ?? null,
    verification_status: partial.verification_status ?? "pending",
    verified_at: partial.verified_at ?? null,
    verification_error_code: partial.verification_error_code ?? null,
    models: partial.models ?? [],
    models_updated_at: partial.models_updated_at ?? null,
    created_at: partial.created_at ?? "2026-07-20T08:00:00Z",
    updated_at: partial.updated_at ?? "2026-07-20T08:00:00Z",
    referenced_agents: partial.referenced_agents ?? [],
  } satisfies ModelProviderDetail;
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

let providerStore: ModelProviderDetail[];
let routeStore: ModelRoute[];
let callsStore: ModelCallPage;
let nextProviderId = 10;

function buildOverview(): ModelInfrastructureOverview {
  const readyProviders = providerStore.filter((provider) =>
    provider.enabled && provider.verification_status === "verified").length;

  return {
    summary: {
      providers_total: providerStore.length,
      providers_ready: readyProviders,
      routes_total: routeStore.length,
      routes_with_fallback: routeStore.filter((route) => route.fallback_provider_id != null).length,
      calls_24h: callsStore.total,
      failures_24h: callsStore.items.filter((item) => item.status === "error").length,
    },
    providers: [],
    routes: clone(routeStore),
  } as ModelInfrastructureOverview;
}

function seedStores() {
  providerStore = [
    buildProvider({
      id: 1,
      code: "deepseek",
      display_name: "DeepSeek",
      template_code: "deepseek",
      base_url: "https://api.deepseek.com/v1",
      credential_source: "environment",
      key_configured: true,
      key_last_four: "7890",
      key_fingerprint: "fp_deepseek",
      verification_status: "verified",
      verified_at: "2026-07-20T08:15:00Z",
      models: ["deepseek-chat", "deepseek-reasoner"],
      models_updated_at: "2026-07-20T08:15:00Z",
      referenced_agents: [{ agent_code: "00-decision", agent_name: "运营大脑" }],
    }),
    buildProvider({
      id: 2,
      code: "openai-prod",
      display_name: "OpenAI 生产",
      template_code: "openai",
      base_url: "https://api.openai.com/v1",
      credential_source: "encrypted",
      key_configured: true,
      key_last_four: "1122",
      verification_status: "pending",
      models: ["gpt-4.1-mini"],
      models_updated_at: "2026-07-19T22:00:00Z",
    }),
    buildProvider({
      id: 3,
      code: "gateway",
      display_name: "Internal Gateway",
      provider_type: "custom_openai",
      template_code: null,
      base_url: "https://llm.example.com/v1",
      credential_source: "encrypted",
      key_configured: true,
      key_last_four: "2468",
      key_fingerprint: "fp_gateway",
      verification_status: "error",
      verified_at: "2026-07-19T11:30:00Z",
      verification_error_code: "endpoint_unreachable",
      models: ["manual-alpha"],
      models_updated_at: "2026-07-19T11:30:00Z",
    }),
  ];

  routeStore = [
    {
      id: 1,
      agent_code: "00-decision",
      agent_name: "运营大脑",
      primary_provider_id: 1,
      fallback_provider_id: 3,
      primary_model: "deepseek-reasoner",
      fallback_model: "manual-alpha",
      temperature: 0.2,
      max_tokens: 8192,
      timeout_seconds: 120,
      updated_at: "2026-07-20T08:20:00Z",
    },
    {
      id: 2,
      agent_code: "01-positioning",
      agent_name: "定位专家",
      primary_provider_id: 1,
      fallback_provider_id: null,
      primary_model: "deepseek-chat",
      fallback_model: null,
      temperature: 0.4,
      max_tokens: 4096,
      timeout_seconds: 90,
      updated_at: "2026-07-20T08:20:00Z",
    },
  ];

  callsStore = {
    total: 2,
    items: [
      {
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
        created_at: "2026-07-20T09:00:00Z",
      },
      {
        id: 10,
        agent_code: "01-positioning",
        agent_name: "定位专家",
        provider: "deepseek",
        model: "deepseek-chat",
        total_tokens: 960,
        cost_usd: 0.0021,
        latency_ms: 640,
        status: "ok",
        error_summary: null,
        created_at: "2026-07-20T09:10:00Z",
      },
    ],
  };
  nextProviderId = 10;
}

vi.mock("../api/modelInfrastructure", () => ({
  ModelProviderDeleteConflictError: class ModelProviderDeleteConflictError extends Error {
    providerId: number;
    affectedAgents: unknown[];

    constructor(providerId: number, affectedAgents: unknown[]) {
      super("Model provider is still referenced by one or more agent routes.");
      this.name = "ModelProviderDeleteConflictError";
      this.providerId = providerId;
      this.affectedAgents = affectedAgents;
    }
  },
  getModelInfrastructure: vi.fn(async () => buildOverview()),
  getModelProviderTemplates: vi.fn(async () => clone(templates)),
  listModelProviders: vi.fn(async () => clone(providerStore)),
  getModelProvider: vi.fn(async (providerId: number) => {
    const provider = providerStore.find((item) => item.id === providerId);
    if (!provider) throw new Error(`Provider ${providerId} not found`);
    return clone(provider);
  }),
  createModelProvider: vi.fn(async (input: Record<string, unknown>) => {
    if ("template_code" in input) {
      const template = templates.find((item) => item.code === input.template_code);
      const provider = buildProvider({
        id: nextProviderId++,
        code: template?.code === "openai" ? "openai" : String(input.template_code),
        display_name: template?.display_name ?? String(input.template_code),
        template_code: String(input.template_code),
        base_url: template?.base_url ?? null,
        verification_status: "pending",
        models: clone(template?.models ?? []),
        models_updated_at: "2026-07-20T09:20:00Z",
      });
      providerStore = [...providerStore, provider];
      return clone(provider);
    }

    const provider = buildProvider({
      id: nextProviderId++,
      code: `custom-${nextProviderId}`,
      display_name: String(input.display_name),
      provider_type: "custom_openai",
      template_code: null,
      base_url: String(input.base_url),
      verification_status: "pending",
      models: [],
    });
    providerStore = [...providerStore, provider];
    return clone(provider);
  }),
  updateModelProviderDetails: vi.fn(async (providerId: number, input: Partial<ModelProviderDetail>) => {
    const index = providerStore.findIndex((provider) => provider.id === providerId);
    providerStore[index] = {
      ...providerStore[index],
      ...input,
      updated_at: "2026-07-20T09:30:00Z",
    };
    return clone(providerStore[index]);
  }),
  replaceModelProviderCredential: vi.fn(async (providerId: number, apiKey: string) => {
    const index = providerStore.findIndex((provider) => provider.id === providerId);
    providerStore[index] = {
      ...providerStore[index],
      credential_source: "encrypted",
      key_configured: true,
      key_last_four: apiKey.slice(-4),
      verification_status: "pending",
      verification_error_code: null,
      updated_at: "2026-07-20T09:40:00Z",
    };
    return clone(providerStore[index]);
  }),
  removeModelProviderCredential: vi.fn(async (providerId: number) => {
    const index = providerStore.findIndex((provider) => provider.id === providerId);
    providerStore[index] = {
      ...providerStore[index],
      credential_source: "none",
      key_configured: false,
      key_last_four: null,
      key_fingerprint: null,
      verification_status: "pending",
      verification_error_code: null,
      updated_at: "2026-07-20T09:45:00Z",
    };
    return clone(providerStore[index]);
  }),
  verifyModelProvider: vi.fn(async (providerId: number) => {
    const index = providerStore.findIndex((provider) => provider.id === providerId);
    providerStore[index] = {
      ...providerStore[index],
      verification_status: "verified",
      verification_error_code: null,
      verified_at: "2026-07-20T10:00:00Z",
      updated_at: "2026-07-20T10:00:00Z",
    };
    return {
      provider_id: providerId,
      verification_status: "verified",
      verification_error_code: null,
      verified_at: "2026-07-20T10:00:00Z",
      latency_ms: 880,
    };
  }),
  discoverModelProviderModels: vi.fn(async (providerId: number) => {
    const provider = providerStore.find((item) => item.id === providerId);
    return {
      provider_id: providerId,
      models: clone(provider?.models ?? []),
      models_updated_at: provider?.models_updated_at ?? null,
      error_code: "discovery_unsupported",
    };
  }),
  updateModelProviderModels: vi.fn(async (providerId: number, models: string[]) => {
    const index = providerStore.findIndex((provider) => provider.id === providerId);
    providerStore[index] = {
      ...providerStore[index],
      models,
      models_updated_at: "2026-07-20T10:10:00Z",
      updated_at: "2026-07-20T10:10:00Z",
    };
    return clone(providerStore[index]);
  }),
  updateModelRoute: vi.fn(async (agentCode: string, input: UpdateModelRouteInput) => {
    const index = routeStore.findIndex((route) => route.agent_code === agentCode);
    routeStore[index] = {
      ...routeStore[index],
      ...input,
      updated_at: "2026-07-20T10:20:00Z",
    };
    return clone(routeStore[index]);
  }),
  deleteModelProvider: vi.fn(async (providerId: number) => {
    if (providerId === 1) {
      throw new ModelProviderDeleteConflictError(providerId, [
        "运营大脑",
        "定位专家",
      ] as never);
    }
    providerStore = providerStore.filter((provider) => provider.id !== providerId);
  }),
  listModelCalls: vi.fn(async (status: "ok" | "error" | null = null) => ({
    total: status == null
      ? callsStore.items.length
      : callsStore.items.filter((item) => item.status === status).length,
    items: clone(
      status == null
        ? callsStore.items
        : callsStore.items.filter((item) => item.status === status),
    ),
  })),
}));

describe("ModelInfrastructure", () => {
  beforeEach(() => {
    seedStores();
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
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    return render(
      <QueryClientProvider client={queryClient}>
        <AntApp>
          <ModelInfrastructure />
        </AntApp>
      </QueryClientProvider>,
    );
  }

  it("adds built-in and custom OpenAI-compatible providers from the registry", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "模型基础设施" })).toBeInTheDocument();
    for (const template of templates) {
      fireEvent.click(screen.getByRole("button", { name: `添加 ${template.display_name}` }));
      await waitFor(() => {
        expect(createModelProvider).toHaveBeenCalledWith({
          template_code: template.code,
          enabled: true,
        });
      });
    }

    fireEvent.click(screen.getByRole("button", { name: "新建兼容端点" }));
    fireEvent.change(screen.getByLabelText("提供商名称"), {
      target: { value: "Moon Gateway" },
    });
    fireEvent.change(screen.getByLabelText("兼容端点地址"), {
      target: { value: "https://moon.example.com/v1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建自定义提供商" }));

    await waitFor(() => {
      expect(createModelProvider).toHaveBeenLastCalledWith({
        provider_type: "custom_openai",
        display_name: "Moon Gateway",
        base_url: "https://moon.example.com/v1",
        enabled: true,
      });
    });
  });

  it("keeps the API key field blank and clears it after key mutations", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "编辑 Internal Gateway" }));

    const keyInput = await screen.findByLabelText("API Key");
    expect(keyInput).toHaveValue("");
    expect(screen.getByText("已配置 · 尾号 2468")).toBeInTheDocument();
    expect(screen.queryByText("sk-live-secret")).not.toBeInTheDocument();

    fireEvent.change(keyInput, { target: { value: "sk-live-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "替换密钥" }));

    await waitFor(() => expect(replaceModelProviderCredential).toHaveBeenCalledWith(3, "sk-live-secret"));
    expect(await screen.findByLabelText("API Key")).toHaveValue("");

    fireEvent.click(screen.getByRole("button", { name: "移除密钥" }));
    await waitFor(() => expect(removeModelProviderCredential).toHaveBeenCalledWith(3));
    expect(await screen.findByLabelText("API Key")).toHaveValue("");
  });

  it("shows verification metadata and preserves manual models when discovery is unsupported", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "编辑 Internal Gateway" }));
    fireEvent.change(screen.getByLabelText("显示名称"), {
      target: { value: "Internal Gateway CN" },
    });
    fireEvent.change(screen.getByLabelText("基础地址"), {
      target: { value: "https://llm.example.com/v2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存提供商" }));

    await waitFor(() => {
      expect(updateModelProviderDetails).toHaveBeenCalledWith(
        3,
        expect.objectContaining({
          display_name: "Internal Gateway CN",
          base_url: "https://llm.example.com/v2",
        }),
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "验证连接" }));
    await waitFor(() => expect(verifyModelProvider).toHaveBeenCalledWith(3));
    expect((await screen.findAllByText("可用")).length).toBeGreaterThan(0);
    expect(screen.getByText("880 ms")).toBeInTheDocument();
    expect(screen.getByText("上次验证")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("模型目录"), {
      target: { value: "manual-alpha\nmanual-beta" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存模型目录" }));

    await waitFor(() => {
      expect(updateModelProviderModels).toHaveBeenCalledWith(3, ["manual-alpha", "manual-beta"]);
    });

    fireEvent.click(screen.getByRole("button", { name: "自动发现模型" }));
    await waitFor(() => expect(discoverModelProviderModels).toHaveBeenCalledWith(3));
    expect(screen.getByText("当前端点不支持自动发现，已保留手工模型目录。")).toBeInTheDocument();
    expect(screen.getByLabelText("模型目录")).toHaveValue("manual-alpha\nmanual-beta");
  });

  it("reports a structured verification failure instead of claiming success", async () => {
    vi.mocked(verifyModelProvider).mockResolvedValueOnce({
      provider_id: 3,
      verification_status: "error",
      verification_error_code: "authentication_failed",
      verified_at: "2026-07-20T10:00:00Z",
      latency_ms: 420,
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "编辑 Internal Gateway" }));
    fireEvent.click(screen.getByRole("button", { name: "验证连接" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("连接验证未通过：密钥认证失败。");
    expect(screen.queryByText("连接验证成功，可参与新的路由分配。")).not.toBeInTheDocument();
  });

  it("blocks an empty manual model catalog before sending it to the API", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "编辑 Internal Gateway" }));
    fireEvent.change(screen.getByLabelText("模型目录"), { target: { value: "   " } });

    expect(screen.getByRole("alert")).toHaveTextContent("模型目录至少需要一个模型名称。");
    expect(screen.getByRole("button", { name: "保存模型目录" })).toBeDisabled();
    expect(updateModelProviderModels).not.toHaveBeenCalled();
  });

  it("groups route targets by provider and disables unverified options with a reason", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "专家路由" }));
    const primaryTarget = await screen.findByLabelText("运营大脑主路由");
    const primaryRouteSelect = primaryTarget as HTMLSelectElement;

    expect(primaryRouteSelect.querySelector('optgroup[label="DeepSeek"]')).not.toBeNull();
    expect(primaryRouteSelect.querySelector('optgroup[label="OpenAI 生产"]')).not.toBeNull();

    const [disabledOption] = screen.getAllByRole("option", {
      name: "OpenAI 生产 · gpt-4.1-mini（待验证，不可分配）",
    });
    expect(disabledOption).toBeDisabled();

    const [failedOption] = screen.getAllByRole("option", {
      name: "Internal Gateway · manual-alpha（异常，不可分配）",
    });
    expect(failedOption).toBeDisabled();

    fireEvent.change(primaryRouteSelect, { target: { value: "3::manual-alpha" } });
    fireEvent.click(screen.getAllByRole("button", { name: "保存路由设置" })[0]);

    await waitFor(() => {
      expect(updateModelRoute).toHaveBeenCalledWith(
        "00-decision",
        expect.objectContaining({
          primary_provider_id: 3,
          primary_model: "manual-alpha",
        }),
      );
    });
  });

  it("shows delete conflicts inline without using browser confirm or alert", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const alertSpy = vi.spyOn(window, "alert");
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "编辑 DeepSeek" }));
    fireEvent.click(screen.getByRole("button", { name: "删除提供商" }));

    await waitFor(() => expect(deleteModelProvider).toHaveBeenCalledWith(1));
    expect(await screen.findByText("以下专家仍在使用该提供商，请先迁移路由。")).toBeInTheDocument();
    expect(screen.getByText("运营大脑")).toBeInTheDocument();
    expect(screen.getByText("定位专家")).toBeInTheDocument();
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it("retains the read-only call ledger and filters failed calls", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "调用账本" }));
    fireEvent.click(screen.getByRole("radio", { name: "失败" }));

    await waitFor(() => expect(listModelCalls).toHaveBeenLastCalledWith("error", 50));
    expect(await screen.findByText("请求超时")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑 DeepSeek" })).not.toBeInTheDocument();
  });
});
