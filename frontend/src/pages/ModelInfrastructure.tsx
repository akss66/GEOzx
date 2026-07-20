import {
  ApiOutlined,
  CheckCircleFilled,
  ClockCircleOutlined,
  DatabaseOutlined,
  EditOutlined,
  ExclamationCircleFilled,
  SaveOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import { App as AntApp, Button, Skeleton, Switch, Tabs } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import {
  getModelInfrastructure,
  listModelCalls,
  updateModelProvider,
  updateModelRoute,
} from "../api/modelInfrastructure";
import { presentApiError } from "../api/errors";
import { OperationalState } from "../components/ui";
import type {
  ModelCallStatus,
  ModelInfrastructureOverview,
  ModelProvider,
  ModelProviderCode,
  ModelRoute,
  UpdateModelProviderInput,
  UpdateModelRouteInput,
} from "../types";

type SectionKey = "providers" | "routes" | "calls";
type CallFilter = "all" | ModelCallStatus;

const CREDENTIAL_OPTIONS: Record<ModelProviderCode, Array<{ value: string; label: string }>> = {
  deepseek: [
    { value: "env:DEEPSEEK_API_KEY", label: "服务器环境变量 · DEEPSEEK_API_KEY" },
    { value: "vault://dyflow/llm/deepseek-api-key", label: "Vault · dyflow/llm/deepseek-api-key" },
  ],
  litellm: [],
};

export default function ModelInfrastructure() {
  const { message } = AntApp.useApp();
  const queryClient = useQueryClient();
  const [section, setSection] = useState<SectionKey>("providers");
  const [selectedProviderCode, setSelectedProviderCode] = useState<ModelProviderCode | null>(null);
  const [providerDraft, setProviderDraft] = useState<UpdateModelProviderInput | null>(null);
  const [selectedRouteCode, setSelectedRouteCode] = useState<string | null>(null);
  const [routeDraft, setRouteDraft] = useState<UpdateModelRouteInput | null>(null);
  const [callFilter, setCallFilter] = useState<CallFilter>("all");

  const overviewQuery = useQuery({
    queryKey: ["model-infrastructure"],
    queryFn: getModelInfrastructure,
  });
  const overview = overviewQuery.data;
  const selectedProvider = useMemo(
    () => overview?.providers.find((provider) => provider.code === selectedProviderCode) ?? null,
    [overview?.providers, selectedProviderCode],
  );
  const selectedRoute = useMemo(
    () => overview?.routes.find((route) => route.agent_code === selectedRouteCode)
      ?? overview?.routes[0]
      ?? null,
    [overview?.routes, selectedRouteCode],
  );

  useEffect(() => {
    if (selectedProvider) setProviderDraft(providerToDraft(selectedProvider));
  }, [selectedProvider]);

  useEffect(() => {
    if (!selectedRouteCode && overview?.routes[0]) {
      setSelectedRouteCode(overview.routes[0].agent_code);
    }
  }, [overview?.routes, selectedRouteCode]);

  useEffect(() => {
    setRouteDraft(selectedRoute ? routeToDraft(selectedRoute) : null);
  }, [selectedRoute]);

  const callsQuery = useQuery({
    queryKey: ["model-infrastructure-calls", callFilter],
    queryFn: () => listModelCalls(callFilter === "all" ? null : callFilter, 50),
    enabled: section === "calls",
  });

  const providerMutation = useMutation({
    mutationFn: ({ code, input }: { code: ModelProviderCode; input: UpdateModelProviderInput }) =>
      updateModelProvider(code, input),
    onSuccess: (saved) => {
      updateOverview(queryClient, (current) => ({
        ...current,
        providers: current.providers.map((provider) => provider.code === saved.code ? saved : provider),
      }));
      setProviderDraft(providerToDraft(saved));
      message.success("供应商运行策略已保存");
    },
    onError: () => message.error("供应商配置保存失败"),
  });

  const routeMutation = useMutation({
    mutationFn: ({ code, input }: { code: string; input: UpdateModelRouteInput }) =>
      updateModelRoute(code, input),
    onSuccess: (saved) => {
      updateOverview(queryClient, (current) => ({
        ...current,
        routes: current.routes.map((route) => route.agent_code === saved.agent_code ? saved : route),
      }));
      setRouteDraft(routeToDraft(saved));
      message.success("专家路由策略已保存");
    },
    onError: () => message.error("路由策略保存失败，请检查模型名称和参数"),
  });

  if (overviewQuery.isError) {
    const failure = presentApiError(overviewQuery.error, "模型基础设施暂时不可用。");
    return (
      <main className="model-infra">
        <OperationalState
          kind="error"
          title="模型基础设施加载失败"
          description={failure.message}
          diagnostic={failure.diagnostic}
          actionLabel="重新加载"
          onAction={() => void overviewQuery.refetch()}
        />
      </main>
    );
  }

  if (overviewQuery.isLoading || !overview) {
    return <div className="model-infra-loading"><Skeleton active paragraph={{ rows: 14 }} /></div>;
  }

  return (
    <main className="model-infra">
      <header className="model-infra__masthead">
        <div>
          <span>模型运行</span>
          <h1>模型基础设施</h1>
          <p>管理模型供应商、专家路由和不可变调用账本。密钥值始终留在服务器。</p>
        </div>
        <InfrastructureSummary overview={overview} />
      </header>

      <div className="model-infra__tabs">
        <Tabs
          activeKey={section}
          onChange={(key) => setSection(key as SectionKey)}
          items={[
            { key: "providers", label: "供应商" },
            { key: "routes", label: "路由策略" },
            { key: "calls", label: "调用账本" },
          ]}
        />
      </div>

      {section === "providers" && (
        <ProviderWorkspace
          providers={overview.providers}
          selected={selectedProvider}
          draft={providerDraft}
          saving={providerMutation.isPending}
          onEdit={(provider) => setSelectedProviderCode(provider.code)}
          onChange={setProviderDraft}
          onClose={() => setSelectedProviderCode(null)}
          onSave={() => {
            if (selectedProvider && providerDraft) {
              providerMutation.mutate({ code: selectedProvider.code, input: providerDraft });
            }
          }}
        />
      )}

      {section === "routes" && selectedRoute && (
        routeDraft ? (
          <RouteWorkspace
            routes={overview.routes}
            selected={selectedRoute}
            draft={routeDraft}
            saving={routeMutation.isPending}
            onSelect={setSelectedRouteCode}
            onChange={setRouteDraft}
            onReset={() => setRouteDraft(routeToDraft(selectedRoute))}
            onSave={() => routeMutation.mutate({ code: selectedRoute.agent_code, input: routeDraft })}
          />
        ) : (
          <OperationalState
            kind="blocked"
            title="需要先绑定模型供应商"
            description="这条遗留路由缺少供应商标识，暂时不能保存。请先在供应商工作台完成迁移。"
          />
        )
      )}

      {section === "calls" && (
        <CallLedger
          filter={callFilter}
          onFilter={setCallFilter}
          loading={callsQuery.isLoading}
          retrying={callsQuery.isFetching}
          error={callsQuery.isError
            ? presentApiError(callsQuery.error, "调用账本暂时不可用，请稍后重新加载。")
            : null}
          onRetry={() => void callsQuery.refetch()}
          calls={callsQuery.data?.items ?? []}
          total={callsQuery.data?.total ?? 0}
        />
      )}
    </main>
  );
}

function InfrastructureSummary({ overview }: { overview: ModelInfrastructureOverview }) {
  const { summary } = overview;
  return (
    <dl className="model-infra__summary" aria-label="模型基础设施概览">
      <div><dt>就绪供应商</dt><dd>{summary.providers_ready}<small> / {summary.providers_total}</small></dd></div>
      <div><dt>专家路由</dt><dd>{summary.routes_total}</dd></div>
      <div><dt>24h 调用</dt><dd>{summary.calls_24h}</dd></div>
      <div className={summary.failures_24h > 0 ? "is-alert" : ""}><dt>24h 失败</dt><dd>{summary.failures_24h}</dd></div>
    </dl>
  );
}

function ProviderWorkspace({
  providers,
  selected,
  draft,
  saving,
  onEdit,
  onChange,
  onClose,
  onSave,
}: {
  providers: ModelProvider[];
  selected: ModelProvider | null;
  draft: UpdateModelProviderInput | null;
  saving: boolean;
  onEdit: (provider: ModelProvider) => void;
  onChange: (draft: UpdateModelProviderInput) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  return (
    <section className={`model-infra__provider-workspace${selected ? " is-editing" : ""}`}>
      <div className="model-infra__provider-list">
        <header><strong>运行供应商</strong><span>凭证仅显示安全引用，不读取密钥值</span></header>
        {providers.map((provider) => (
          <article key={provider.code} className={provider.runtime_ready ? "is-ready" : ""}>
            <span className="model-infra__provider-icon"><ApiOutlined /></span>
            <div className="model-infra__provider-copy">
              <div><h2>{provider.name}</h2><span>{provider.kind === "direct" ? "DIRECT" : "ROUTER"}</span></div>
              <p>{provider.note}</p>
              <small>{provider.supported_models.join(" · ")}</small>
            </div>
            <div className="model-infra__provider-state">
              <strong>{provider.runtime_ready ? <CheckCircleFilled /> : <ExclamationCircleFilled />}{provider.runtime_ready ? "运行就绪" : provider.enabled ? "等待凭证" : "已停用"}</strong>
              <span>{provider.credential_ref ?? "由路由服务管理凭证"}</span>
            </div>
            <Button aria-label={`编辑 ${provider.name}`} icon={<EditOutlined />} onClick={() => onEdit(provider)}>配置</Button>
          </article>
        ))}
      </div>

      {selected && draft && (
        <aside className="model-infra__provider-editor">
          <header><span>PROVIDER POLICY</span><h2>{selected.name}</h2><p>这里只保存服务器侧引用，前端永远不接收明文密钥。</p></header>
          <label className="model-infra__switch-row">
            <span><strong>启用供应商</strong><small>停用后，该供应商的模型不会被运行时调用。</small></span>
            <Switch checked={draft.enabled} onChange={(enabled) => onChange({ ...draft, enabled })} />
          </label>
          <label className="model-infra__field">
            <span>{selected.name} 密钥引用</span>
            {CREDENTIAL_OPTIONS[selected.code].length ? (
              <select
                aria-label={`${selected.name} 密钥引用`}
                value={draft.credential_ref ?? ""}
                onChange={(event) => onChange({ ...draft, credential_ref: event.target.value || null })}
              >
                {CREDENTIAL_OPTIONS[selected.code].map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            ) : (
              <input aria-label={`${selected.name} 密钥引用`} value="由服务器路由服务管理" disabled />
            )}
          </label>
          <div className="model-infra__readonly">
            <span>服务端点</span><strong>{selected.endpoint ?? "由 LiteLLM 服务配置"}</strong>
          </div>
          <div className="model-infra__provider-actions">
            <Button onClick={onClose}>关闭</Button>
            <Button aria-label="保存供应商" type="primary" icon={<SaveOutlined />} loading={saving} onClick={onSave}>保存供应商</Button>
          </div>
        </aside>
      )}
    </section>
  );
}

function RouteWorkspace({
  routes,
  selected,
  draft,
  saving,
  onSelect,
  onChange,
  onReset,
  onSave,
}: {
  routes: ModelRoute[];
  selected: ModelRoute;
  draft: UpdateModelRouteInput;
  saving: boolean;
  onSelect: (code: string) => void;
  onChange: (draft: UpdateModelRouteInput) => void;
  onReset: () => void;
  onSave: () => void;
}) {
  const dirty = JSON.stringify(draft) !== JSON.stringify(routeToDraft(selected));
  const knownModels = Array.from(new Set([
    "deepseek-chat",
    "deepseek-reasoner",
    ...routes.flatMap((route) => [route.primary_model, route.fallback_model].filter(
      (model): model is string => Boolean(model),
    )),
  ]));
  return (
    <section className="model-infra__route-workspace">
      <aside className="model-infra__route-directory">
        <header><strong>专家路由</strong><span>{routes.length}</span></header>
        <nav aria-label="专家模型路由">
          {routes.map((route, index) => (
            <button
              key={route.agent_code}
              type="button"
              className={route.agent_code === selected.agent_code ? "is-active" : ""}
              aria-label={`${route.agent_name}，${route.primary_model}`}
              onClick={() => onSelect(route.agent_code)}
            >
              <span>{String(index + 1).padStart(2, "0")}</span>
              <div><strong>{route.agent_name}</strong><small>{route.primary_model}</small></div>
              {route.fallback_model ? <SwapOutlined title="已配置兜底" /> : <i />}
            </button>
          ))}
        </nav>
      </aside>
      <div className="model-infra__route-editor">
        <header><span>AGENT ROUTE</span><h2>{selected.agent_name}</h2><p>{selected.agent_code}</p></header>
        <datalist id="model-catalog">
          {knownModels.map((model) => <option key={model} value={model} />)}
        </datalist>
        <div className="model-infra__route-grid">
          <label><span>首选模型</span><input list="model-catalog" value={draft.primary_model} onChange={(event) => onChange({ ...draft, primary_model: event.target.value })} /></label>
          <label><span>兜底模型</span><input list="model-catalog" placeholder="不启用兜底" value={draft.fallback_model ?? ""} onChange={(event) => onChange({ ...draft, fallback_model: event.target.value || null })} /></label>
          <label><span>温度</span><input aria-label="温度" type="number" min="0" max="2" step="0.1" value={draft.temperature} onChange={(event) => onChange({ ...draft, temperature: Number(event.target.value) })} /></label>
          <label><span>最大输出 Token</span><input type="number" min="256" max="32768" step="256" value={draft.max_tokens} onChange={(event) => onChange({ ...draft, max_tokens: Number(event.target.value) })} /></label>
          <label><span>超时时间（秒）</span><input type="number" min="5" max="300" step="5" value={draft.timeout_seconds} onChange={(event) => onChange({ ...draft, timeout_seconds: Number(event.target.value) })} /></label>
        </div>
        <div className="model-infra__route-note"><DatabaseOutlined /><span>保存后只影响该专家的新调用；历史账本不会被改写。</span></div>
        <footer><Button disabled={!dirty || saving} onClick={onReset}>撤销更改</Button><Button aria-label="保存路由策略" type="primary" icon={<SaveOutlined />} disabled={!dirty} loading={saving} onClick={onSave}>保存路由策略</Button></footer>
      </div>
    </section>
  );
}

function CallLedger({ filter, onFilter, loading, retrying, error, onRetry, calls, total }: {
  filter: CallFilter;
  onFilter: (filter: CallFilter) => void;
  loading: boolean;
  retrying: boolean;
  error: ReturnType<typeof presentApiError> | null;
  onRetry: () => void;
  calls: Awaited<ReturnType<typeof listModelCalls>>["items"];
  total: number;
}) {
  return (
    <section className="model-infra__ledger">
      <header>
        <div><strong>不可变调用账本</strong><span>最近 {total} 条记录</span></div>
        <div className="model-infra__ledger-filter" role="radiogroup" aria-label="调用状态">
          {(["all", "ok", "error"] as const).map((value) => (
            <button key={value} type="button" role="radio" aria-checked={filter === value} onClick={() => onFilter(value)}>{value === "all" ? "全部" : value === "ok" ? "成功" : "失败"}</button>
          ))}
        </div>
      </header>
      {loading ? <Skeleton active paragraph={{ rows: 8 }} /> : error ? (
        <OperationalState
          kind="error"
          title="调用账本加载失败"
          description={`${error.message} 当前状态筛选不会被修改。`}
          diagnostic={error.diagnostic}
          actionLabel="重新加载"
          actionLoading={retrying}
          onAction={onRetry}
        />
      ) : calls.length === 0 ? (
        <div className="model-infra__empty"><DatabaseOutlined /><strong>没有符合条件的调用记录</strong><span>产生真实模型调用后，这里会显示路由、耗时、Token 与成本。</span></div>
      ) : (
        <div className="model-infra__table-wrap">
          <table>
            <thead><tr><th>时间</th><th>调用者</th><th>供应商 / 模型</th><th>Token</th><th>耗时</th><th>成本</th><th>结果</th></tr></thead>
            <tbody>{calls.map((call) => (
              <tr key={call.id}>
                <td><ClockCircleOutlined /> {formatTimestamp(call.created_at)}</td>
                <td><strong>{call.agent_name}</strong><small>{call.agent_code ?? "system"}</small></td>
                <td><strong>{call.provider}</strong><small>{call.model}</small></td>
                <td>{call.total_tokens.toLocaleString()}</td>
                <td>{call.latency_ms.toLocaleString()} ms</td>
                <td>${call.cost_usd.toFixed(4)}</td>
                <td><span className={`model-infra__call-state is-${call.status}`}>{call.status === "ok" ? "成功" : "失败"}</span>{call.error_summary && <small className="model-infra__error-summary">{call.error_summary}</small>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function providerToDraft(provider: ModelProvider): UpdateModelProviderInput {
  return { enabled: provider.enabled, credential_ref: provider.credential_ref };
}

function routeToDraft(route: ModelRoute): UpdateModelRouteInput | null {
  if (route.primary_provider_id === null) return null;
  return {
    primary_provider_id: route.primary_provider_id,
    primary_model: route.primary_model,
    fallback_provider_id: route.fallback_provider_id,
    fallback_model: route.fallback_model,
    temperature: route.temperature,
    max_tokens: route.max_tokens,
    timeout_seconds: route.timeout_seconds,
  };
}

function updateOverview(
  queryClient: ReturnType<typeof useQueryClient>,
  updater: (current: ModelInfrastructureOverview) => ModelInfrastructureOverview,
) {
  queryClient.setQueryData<ModelInfrastructureOverview>(["model-infrastructure"], (current) => current ? updater(current) : current);
}

function formatTimestamp(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
